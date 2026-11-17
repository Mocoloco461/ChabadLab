"""Search logic: full-text (FTS5), semantic (embeddings) and hybrid (RRF)."""
import html
import json
import os
import re
import sqlite3
import threading
from pathlib import Path

import numpy as np

from hebrew import build_fts_query, clean, heb_year, highlight_terms

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("IGROT_DATA", ROOT / "data"))
DB_PATH = DATA / "search.db"

LIST_COLS = ("l.id, l.volume, l.volume_heb, l.letter_number, l.letter_number_heb, l.date_raw, "
             "l.date_editorial, l.hebrew_year, l.hebrew_year_heb, l.gregorian_date, l.recipient, l.recipient_note, "
             "l.language, l.n_chars, l.url")

# very common function words: dropped from "any word" (OR) queries so they don't dominate
STOPWORDS = set("""של את על עם הוא היא הם הן זה זו זאת מה כי אם לא גם או אשר כל יש אין אל מן
ב ל מ ש ה ו כ עד אף רק כן לו לי לה להם בו בה בזה כמו אבל וכו וכו' ע"ד שלו שלה איך למה מדוע
האם ביותר הרבי הרב מה ומה אודות בנוגע בהנוגע שאלה תשובה כתב כותב אמר""".split())

_local = threading.local()


def db():
    con = getattr(_local, "con", None)
    if con is None:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        _local.con = con
    return con


# ------------------------------------------------------------------ filters
def filter_sql(f):
    """Filters dict -> (sql fragment on alias l, params)."""
    where, params = [], []

    def num(key):
        v = f.get(key)
        try:
            return int(v) if v not in (None, "") else None
        except ValueError:
            return None

    for key, col, op in (("vol_from", "l.volume", ">="), ("vol_to", "l.volume", "<="),
                         ("year_from", "l.hebrew_year", ">="), ("year_to", "l.hebrew_year", "<="),
                         ("greg_from", "l.gregorian_year", ">="), ("greg_to", "l.gregorian_year", "<="),
                         ("num_from", "l.letter_number", ">="), ("num_to", "l.letter_number", "<=")):
        v = num(key)
        if v is not None:
            where.append(f"{col} {op} ?")
            params.append(v)
    if f.get("lang"):
        langs = [x for x in str(f["lang"]).split(",") if x]
        where.append(f"l.language IN ({','.join('?' * len(langs))})")
        params += langs
    if f.get("month"):
        where.append("l.hebrew_month = ?")
        params.append(f["month"])
    if f.get("recipient"):
        # every word of the recipient filter must appear in the address / addressee note
        for w in clean(f["recipient"]).replace('"', " ").replace("'", " ").split():
            where.append("REPLACE(REPLACE(l.recipient_info, '\"', ''), '''', '') LIKE ?")
            params.append(f"%{w}%")
    if f.get("dated") in ("1", 1, True, "true"):
        where.append("l.hebrew_year IS NOT NULL")
    return (" AND ".join(where), params)


def _escape_snippet(s):
    s = html.escape(s or "", quote=False)
    return s.replace("\x01", "<mark>").replace("\x02", "</mark>")


def row_to_result(r, snippet=None, score=None, extra=None):
    d = {k: r[k] for k in r.keys() if k not in ("snip",)}
    if snippet is not None:
        d["snippet"] = snippet
    if score is not None:
        d["score"] = round(float(score), 4)
    if extra:
        d.update(extra)
    return d


# ------------------------------------------------------------------ text search
SORTS = {
    "relevance": "score",
    "date": "l.gregorian_date IS NULL, l.gregorian_date, l.volume, l.letter_number",
    "date_desc": "l.gregorian_date IS NULL, l.gregorian_date DESC, l.volume, l.letter_number",
    "number": "l.volume, l.letter_number, l.id",
}


def text_search(q, filters, sort="relevance", offset=0, limit=20, prefixes=True, match="all",
                with_facets=True):
    fts = build_fts_query(q, prefixes=prefixes, mode=match)
    fsql, fparams = filter_sql(filters)
    con = db()
    if not fts and q.strip():
        return {"total": 0, "results": [], "facets": None,
                "error": "אין בשאילתה מילה לחיפוש. יש להוסיף לפחות מילה אחת (מילה עם מינוס רק מחריגה)."}
    if not fts:  # browse mode: filters only
        where = f"WHERE {fsql}" if fsql else ""
        order = SORTS.get(sort, SORTS["number"]).replace("score", "l.volume, l.letter_number")
        total = con.execute(f"SELECT COUNT(*) FROM letters l {where}", fparams).fetchone()[0]
        rows = con.execute(f"SELECT {LIST_COLS}, substr(l.body, 1, 260) AS snip FROM letters l {where} "
                           f"ORDER BY {order} LIMIT ? OFFSET ?", fparams + [limit, offset]).fetchall()
        res = [row_to_result(r, html.escape(r["snip"] or "") + "…") for r in rows]
        return {"total": total, "results": res, "facets": facets(None, fsql, fparams) if with_facets else None}

    where = "letters_fts MATCH ?" + (f" AND {fsql}" if fsql else "")
    params = [fts] + fparams
    # bm25 weights: text, recipient, notes
    base = (f"FROM letters_fts JOIN letters l ON l.id = letters_fts.rowid WHERE {where}")
    try:
        total = con.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
        order = SORTS.get(sort, "score")
        rows = con.execute(
            f"SELECT {LIST_COLS}, bm25(letters_fts, 1.0, 1.5, 0.4) AS score, "
            f"snippet(letters_fts, -1, char(1), char(2), '…', 40) AS snip "
            f"{base} ORDER BY {order} LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    except sqlite3.OperationalError as e:
        return {"total": 0, "results": [], "error": f"שאילתה לא תקינה: {e}", "fts": fts}
    res = [row_to_result(r, _escape_snippet(r["snip"]), -r["score"]) for r in rows]
    if total < RELAX_BELOW and match == "all" and len(query_terms(q)) > 1:
        relaxed = relaxed_search(q, filters, offset, limit, prefixes, exclude=[r["id"] for r in res])
        if relaxed["results"] or total == 0:
            # strict matches first, then letters with most of the words
            relaxed["results"] = (res + relaxed["results"])[:limit] if offset == 0 else relaxed["results"]
            relaxed["total"] += total
            relaxed["strict_total"] = total
            if with_facets:
                relaxed["facets"] = None
            return relaxed
    out = {"total": total, "results": res, "fts": fts}
    if with_facets:
        out["facets"] = facets(fts, fsql, fparams)
    return out


RELAX_BELOW = 3  # strict (all words) results below this -> also show letters with most of the words


def query_terms(q):
    """Top-level query items (quoted phrases count as one), without exclusions."""
    import re as _re
    return [t for t in _re.findall(r'"[^"]+"|\S+', q or "") if not t.startswith("-")]


def relaxed_search(q, filters, offset, limit, prefixes=True, exclude=(), n=400):
    """Rank letters by how many of the query terms they contain (then bm25 of the OR query)."""
    terms = [t for t in query_terms(q) if t.strip('"') not in STOPWORDS] or query_terms(q)
    negs = " ".join(t for t in re.findall(r'"[^"]+"|\S+', q) if t.startswith("-"))
    fsql, fparams = filter_sql(filters)
    con = db()
    cover = {}
    for t in terms:
        e = build_fts_query(f"{t} {negs}".strip(), prefixes=prefixes)
        if not e:
            continue
        where = "letters_fts MATCH ?" + (f" AND {fsql}" if fsql else "")
        try:
            for (lid,) in con.execute(f"SELECT l.id FROM letters_fts JOIN letters l ON l.id = letters_fts.rowid "
                                      f"WHERE {where}", [e] + fparams):
                cover[lid] = cover.get(lid, 0) + 1
        except sqlite3.OperationalError:
            continue
    excl = set(exclude)
    # need at least half of the terms (and at least 2 when there are 2+ terms)
    need = max(2, (len(terms) + 1) // 2) if len(terms) >= 2 else 1
    cands = [lid for lid, c in cover.items() if c >= need and lid not in excl]
    if not cands:
        return {"total": 0, "results": [], "relaxed": True, "terms": len(terms)}
    any_q = " ".join(terms) + (" " + negs if negs else "")
    order = {lid: i for i, lid in enumerate(text_ranked_ids(any_q, filters, n=5000, prefixes=prefixes, match="any"))}
    cands.sort(key=lambda lid: (-cover[lid], order.get(lid, 10 ** 6)))
    page = cands[offset: offset + limit]
    rows = fetch_rows(page)
    res = []
    for lid in page:
        if lid in rows:
            res.append(row_to_result(rows[lid], _text_snippet(lid, any_q, prefixes), cover[lid],
                                     {"matched_terms": cover[lid], "n_terms": len(terms)}))
    return {"total": len(cands), "results": res, "relaxed": True, "terms": len(terms)}


def text_ranked_ids(q, filters, n=300, prefixes=True, match="all"):
    fts = build_fts_query(q, prefixes=prefixes, mode=match)
    if not fts:
        return []
    fsql, fparams = filter_sql(filters)
    where = "letters_fts MATCH ?" + (f" AND {fsql}" if fsql else "")
    try:
        return [r[0] for r in db().execute(
            f"SELECT l.id FROM letters_fts JOIN letters l ON l.id = letters_fts.rowid WHERE {where} "
            f"ORDER BY bm25(letters_fts, 1.0, 1.5, 0.4) LIMIT ?", [fts] + fparams + [n])]
    except sqlite3.OperationalError:
        return []


def facets(fts, fsql, fparams):
    con = db()
    if fts:
        base = "FROM letters_fts JOIN letters l ON l.id = letters_fts.rowid WHERE letters_fts MATCH ?"
        params = [fts] + fparams
        if fsql:
            base += f" AND {fsql}"
    else:
        base = "FROM letters l" + (f" WHERE {fsql}" if fsql else "")
        params = fparams
    out = {}
    for name, col in (("volume", "l.volume"), ("year", "l.hebrew_year"), ("language", "l.language")):
        rows = con.execute(f"SELECT {col} AS k, COUNT(*) AS n {base} GROUP BY {col} ORDER BY {col}", params)
        out[name] = [{"key": r["k"], "n": r["n"],
                      **({"label": heb_year(r["k"])} if name == "year" and r["k"] else {})} for r in rows]
    return out


# ------------------------------------------------------------------ semantic search
class SemanticIndex:
    def __init__(self, folder):
        folder = Path(folder)
        self.folder = folder
        self.info = json.loads((folder / "embeddings.json").read_text(encoding="utf-8"))
        self.emb = np.load(folder / "embeddings.npy").astype(np.float32)
        self.chunk_letter = []
        self.chunk_text = []
        with open(folder / "chunks.jsonl", encoding="utf-8") as fh:
            for line in fh:
                c = json.loads(line)
                self.chunk_letter.append(c["letter_id"])
                self.chunk_text.append(c["text"])
        self.chunk_letter = np.array(self.chunk_letter)
        self._model = None
        self._lock = threading.Lock()

    @property
    def model(self):
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer
                m = SentenceTransformer(self.info["model"], device="cpu")
                m.max_seq_length = self.info.get("max_seq", 512)
                self._model = m
        return self._model

    def encode(self, q):
        qv = self.model.encode([self.info.get("query_prefix", "") + q], normalize_embeddings=True)
        return qv[0].astype(np.float32)

    def search(self, q, allowed_ids=None, n=300):
        """-> list of (letter_id, score, best_chunk_index) sorted by score."""
        scores = self.emb @ self.encode(q)
        if allowed_ids is not None:
            mask = np.isin(self.chunk_letter, np.fromiter(allowed_ids, dtype=self.chunk_letter.dtype))
            scores = np.where(mask, scores, -1.0)
        k = min(len(scores), n * 6)
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        best = {}
        for i in top:
            if scores[i] <= -1.0:
                break
            lid = int(self.chunk_letter[i])
            if lid not in best:
                best[lid] = (float(scores[i]), int(i))
            if len(best) >= n:
                break
        return sorted(((lid, s, ci) for lid, (s, ci) in best.items()), key=lambda x: -x[1])


_sem = {"index": None, "error": None}


def semantic_index():
    if _sem["index"] is None and _sem["error"] is None:
        folder = os.environ.get("IGROT_EMB")
        if not folder:
            # prefer the strongest finished model
            for slug in ("BAAI__bge-m3", "intfloat__multilingual-e5-base", "intfloat__multilingual-e5-small"):
                if (DATA / "emb" / slug / "embeddings.npy").exists():
                    folder = DATA / "emb" / slug
                    break
        if not folder:
            _sem["error"] = "האינדקס הסמנטי עדיין נבנה"
            return None
        try:
            _sem["index"] = SemanticIndex(folder)
        except Exception as e:  # noqa: BLE001
            _sem["error"] = f"שגיאה בטעינת האינדקס הסמנטי: {e}"
    return _sem["index"]


def allowed_letter_ids(filters):
    fsql, fparams = filter_sql(filters)
    if not fsql:
        return None
    return {r[0] for r in db().execute(f"SELECT l.id FROM letters l WHERE {fsql}", fparams)}


def _chunk_snippet(text, terms, size=320):
    t = text
    pos = -1
    for term in terms:
        pos = t.find(term)
        if pos >= 0:
            break
    start = max(0, pos - size // 3) if pos > 0 else 0
    s = ("…" if start else "") + t[start:start + size] + ("…" if start + size < len(t) else "")
    s = html.escape(s, quote=False)
    for term in sorted(set(terms), key=len, reverse=True):
        if len(term) > 1:
            s = re.sub(rf"(?<![א-ת])({re.escape(html.escape(term))})", r"<mark>\1</mark>", s)
    return s.replace("\n", " ")


def fetch_rows(ids):
    if not ids:
        return {}
    rows = db().execute(f"SELECT {LIST_COLS}, substr(l.body,1,260) AS snip FROM letters l "
                        f"WHERE l.id IN ({','.join('?' * len(ids))})", list(ids)).fetchall()
    return {r["id"]: r for r in rows}


def semantic_search(q, filters, offset=0, limit=20, n=300):
    idx = semantic_index()
    if idx is None:
        return {"total": 0, "results": [], "error": _sem["error"]}
    hits = idx.search(q, allowed_letter_ids(filters), n=n)
    page = hits[offset: offset + limit]
    rows = fetch_rows([h[0] for h in page])
    terms = [t for t in highlight_terms(q) if t not in STOPWORDS]
    res = [row_to_result(rows[lid], _chunk_snippet(idx.chunk_text[ci], terms), s)
           for lid, s, ci in page if lid in rows]
    return {"total": len(hits), "results": res, "model": idx.info["model"], "capped": len(hits) >= n}


def hybrid_search(q, filters, offset=0, limit=20, n=300, prefixes=True, k=60, w_text=1.0, w_sem=1.0):
    idx = semantic_index()
    # text part: all words if that finds enough, otherwise any (non-stopword) word
    t_ids = text_ranked_ids(q, filters, n, prefixes, "all")
    if len(t_ids) < 20:
        q_any = " ".join(w for w in q.split() if w.strip('"') not in STOPWORDS) or q
        more = text_ranked_ids(q_any, filters, n, prefixes, "any")
        t_ids = t_ids + [i for i in more if i not in set(t_ids)]
    s_hits = idx.search(q, allowed_letter_ids(filters), n=n) if idx else []
    score, best_chunk = {}, {}
    for rank, lid in enumerate(t_ids):
        score[lid] = score.get(lid, 0) + w_text / (k + rank + 1)
    for rank, (lid, s, ci) in enumerate(s_hits):
        score[lid] = score.get(lid, 0) + w_sem / (k + rank + 1)
        best_chunk[lid] = ci
    ranked = sorted(score.items(), key=lambda x: -x[1])
    page = ranked[offset: offset + limit]
    rows = fetch_rows([lid for lid, _ in page])
    terms = [t for t in highlight_terms(q) if t not in STOPWORDS]
    res = []
    for lid, s in page:
        if lid not in rows:
            continue
        if idx is not None and lid in best_chunk:
            snip = _chunk_snippet(idx.chunk_text[best_chunk[lid]], terms)
        else:
            snip = _text_snippet(lid, q, prefixes)
        res.append(row_to_result(rows[lid], snip, s * 100,
                                 {"in_text": lid in set(t_ids[:n]), "in_semantic": lid in best_chunk}))
    return {"total": len(ranked), "results": res, "model": idx.info["model"] if idx else None,
            "warning": None if idx else _sem["error"]}


def _text_snippet(lid, q, prefixes=True):
    fts = build_fts_query(" ".join(w for w in q.split() if w not in STOPWORDS) or q, prefixes, "any")
    try:
        r = db().execute("SELECT snippet(letters_fts, -1, char(1), char(2), '…', 28) FROM letters_fts "
                         "WHERE letters_fts MATCH ? AND rowid = ?", (fts, lid)).fetchone()
        if r:
            return _escape_snippet(r[0])
    except sqlite3.OperationalError:
        pass
    r = db().execute("SELECT substr(body,1,260) FROM letters WHERE id=?", (lid,)).fetchone()
    return html.escape(r[0] if r else "") + "…"


# ------------------------------------------------------------------ letter + meta
def get_letter(lid):
    con = db()
    r = con.execute("SELECT * FROM letters WHERE id = ?", (lid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["notes"] = [dict(n) for n in con.execute(
        "SELECT term, text FROM notes WHERE letter_id = ? ORDER BY seq", (lid,))]
    return d


def find_letter(volume=None, number=None):
    con = db()
    if number:
        r = con.execute("SELECT id FROM letters WHERE letter_number = ? ORDER BY id LIMIT 1", (number,)).fetchone()
        return r[0] if r else None
    return None


def meta():
    con = db()
    vols = [dict(r) for r in con.execute(
        "SELECT volume, volume_heb, COUNT(*) AS letters, MIN(letter_number) AS first, MAX(letter_number) AS last "
        "FROM letters GROUP BY volume ORDER BY volume")]
    years = [{"year": r[0], "label": heb_year(r[0]), "n": r[1]} for r in con.execute(
        "SELECT hebrew_year, COUNT(*) FROM letters WHERE hebrew_year IS NOT NULL GROUP BY hebrew_year ORDER BY 1")]
    langs = [{"key": r[0], "n": r[1]} for r in con.execute(
        "SELECT language, COUNT(*) FROM letters GROUP BY language ORDER BY 2 DESC")]
    months = [r[0] for r in con.execute(
        "SELECT hebrew_month FROM letters WHERE hebrew_month IS NOT NULL GROUP BY hebrew_month ORDER BY COUNT(*) DESC")]
    total = con.execute("SELECT COUNT(*) FROM letters").fetchone()[0]
    idx = _sem["index"]
    return {"total": total, "volumes": vols, "years": years, "languages": langs, "months": months,
            "semantic_model": idx.info["model"] if idx else None, "semantic_status": _sem["error"]}
