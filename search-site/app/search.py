"""Search logic: full-text (FTS5), semantic (embeddings) and hybrid (RRF)."""
import html
import json
import os
import re
import sqlite3
import threading
from pathlib import Path

import numpy as np

from hebrew import (build_fts_query, clean, heb_year, highlight_terms, parse_query, phrase_query,
                    term_regex)

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("IGROT_DATA", ROOT / "data"))
DB_PATH = DATA / "search.db"

LIST_COLS = ("l.id, l.volume, l.volume_heb, l.letter_number, l.letter_number_heb, l.date_raw, "
             "l.date_editorial, l.hebrew_year, l.hebrew_year_heb, l.gregorian_date, l.recipient, "
             "l.recipient_note, l.language, l.n_chars, l.url")

# very common function words: dropped from "any word" (OR) queries so they don't dominate
STOPWORDS = set("""של את על עם הוא היא הם הן זה זו זאת מה כי אם לא גם או אשר כל יש אין אל מן
ב ל מ ש ה ו כ עד אף רק כן לו לי לה להם בו בה בזה כמו אבל וכו וכו' ע"ד שלו שלה איך למה מדוע
האם ביותר הרבי הרב מה ומה אודות בנוגע בהנוגע שאלה תשובה כתב כותב אמר""".split())

# question / meta words that say nothing about a letter's content (dropped from the
# lexical part of hybrid search; tuned on eval cases, see WORKLOG)
QUERY_STOP = set("""מה מי איך למה מדוע האם כיצד היכן איפה מתי כתב כותב הרבי הרב רבי ממליץ יעץ מציע
לגבי בעניין כדי צריך שצריך צריכים נכון כאשר כש רוצה שרוצה שרוצים עומד לעשות להיות יכול אפשר
מכתב אגרת תשובה מענה ברכה מברך מבקש שלו שלה שלהם עם על אל את""".split())

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
    """Filters dict -> (sql fragment on alias l, params).
    Indexed columns are written as +col so SQLite never drives an FTS query from
    a metadata index (that made lang=he take seconds)."""
    where, params = [], []

    def num(key):
        v = f.get(key)
        try:
            v = int(str(v).strip()) if v not in (None, "") else None
        except ValueError:
            return None
        return None if v is None else max(-10 ** 9, min(10 ** 9, v))

    for key, col, op in (("vol_from", "+l.volume", ">="), ("vol_to", "+l.volume", "<="),
                         ("year_from", "+l.hebrew_year", ">="), ("year_to", "+l.hebrew_year", "<="),
                         ("greg_from", "+l.gregorian_year", ">="), ("greg_to", "+l.gregorian_year", "<="),
                         ("num_from", "+l.letter_number", ">="), ("num_to", "+l.letter_number", "<=")):
        v = num(key)
        if v is not None:
            where.append(f"{col} {op} ?")
            params.append(v)
    if f.get("lang"):
        langs = [x for x in str(f["lang"]).split(",") if x]
        where.append(f"+l.language IN ({','.join('?' * len(langs))})")
        params += langs
    if f.get("month"):
        m = str(f["month"]).strip()
        months = ["אדר", "אדר א", "אדר ב"] if m == "אדר" else [m]
        where.append(f"l.hebrew_month IN ({','.join('?' * len(months))})")
        params += months
    if f.get("recipient"):
        # every word must appear in the address / addressee note; gershayim ignored (שז"ר == שזר)
        for w in clean(f["recipient"]).replace('"', "").replace("'", "").split():
            where.append("REPLACE(REPLACE(l.recipient_info, '\"', ''), \"'\", '') LIKE ?")
            params.append(f"%{w}%")
    if f.get("dated") in ("1", 1, True, "true"):
        where.append("l.hebrew_year IS NOT NULL")
    return (" AND ".join(where), params)


def row_to_result(r, snippet=None, score=None, extra=None):
    d = {k: r[k] for k in r.keys() if k not in ("snip", "full_text")}
    if snippet is not None:
        d["snippet"] = snippet
    if score is not None:
        d["score"] = round(float(score), 4)
    if extra:
        d.update(extra)
    return d


def fetch_rows(ids, with_text=False):
    if not ids:
        return {}
    extra = ", l.full_text" if with_text else ""
    rows = db().execute(f"SELECT {LIST_COLS}, substr(l.body,1,260) AS snip{extra} FROM letters l "
                        f"WHERE l.id IN ({','.join('?' * len(ids))})", list(ids)).fetchall()
    return {r["id"]: r for r in rows}


# ------------------------------------------------------------------ snippets
def make_snippet(text, q="", size=300, terms=None):
    """Snippet cut from the display text around the densest cluster of query
    terms, with <mark> highlights. Works on the original text (gershayim kept)."""
    text = clean(text or "").replace("\n", " ")
    terms = terms if terms is not None else highlight_terms(q)
    terms = [t for t in terms if len(re.sub(r"[^\w]", "", t)) > 1 and t not in STOPWORDS]
    pats = [p for p in (term_regex(t) for t in sorted(set(terms), key=len, reverse=True)) if p]
    if not pats:
        return html.escape(text[:size]) + ("…" if len(text) > size else "")
    rx = re.compile("|".join(f"(?:{p})" for p in pats))
    hits = [(m.start(), m.group(0)) for m in rx.finditer(text)]
    if not hits:
        return html.escape(text[:size]) + ("…" if len(text) > size else "")
    best, best_score = hits[0][0], -1
    for i, (st, _) in enumerate(hits):  # window with the most distinct matched words
        words = {w for p, w in hits[i:] if p < st + size * 0.8}
        if len(words) > best_score:
            best, best_score = st, len(words)
    start = max(0, best - size // 4)
    if start:
        sp = text.rfind(" ", 0, start)
        start = sp + 1 if sp > start - 30 else start
    end = min(len(text), start + size)
    if end < len(text):
        sp = text.find(" ", end)
        end = sp if 0 < sp < end + 30 else end
    frag = text[start:end]
    out, last = [], 0
    for m in rx.finditer(frag):
        out.append(html.escape(frag[last:m.start()]))
        out.append("<mark>" + html.escape(m.group(0)) + "</mark>")
        last = m.end()
    out.append(html.escape(frag[last:]))
    return ("…" if start else "") + "".join(out) + ("…" if end < len(text) else "")


# ------------------------------------------------------------------ text search
SORTS = {
    "relevance": None,
    "date": "l.hebrew_year IS NULL, l.hebrew_year, l.gregorian_date, l.volume, l.letter_number",
    "date_desc": "l.hebrew_year IS NULL, l.hebrew_year DESC, l.gregorian_date DESC, l.volume DESC, l.letter_number DESC",
    "number": "l.volume, l.letter_number, l.id",
}
BM25 = "bm25(letters_fts, 1.0, 3.0, 0.3)"  # weights: text, recipient, notes
MAX_RANKED = 5000
RELAX_BELOW = 3  # strict (all words) results below this -> also show letters with most of the words


def _match_ids(fts, fsql, fparams, order_bm25=False, limit=None):
    where = "letters_fts MATCH ?" + (f" AND {fsql}" if fsql else "")
    sql = f"SELECT l.id FROM letters_fts JOIN letters l ON l.id = letters_fts.rowid WHERE {where}"
    if order_bm25:
        sql += f" ORDER BY {BM25}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r[0] for r in db().execute(sql, [fts] + fparams)]


def _count(fts, fsql, fparams):
    where = "letters_fts MATCH ?" + (f" AND {fsql}" if fsql else "")
    return db().execute(f"SELECT COUNT(*) FROM letters_fts JOIN letters l ON l.id = letters_fts.rowid "
                        f"WHERE {where}", [fts] + fparams).fetchone()[0]


def ranked_ids(q, filters, prefixes=True, match="all", items=None):
    """Relevance order in tiers: (1) the whole query as an exact phrase,
    (2) every word in its exact form (no added prefix), (3) the rest - each
    tier ordered by bm25. Returns (ids, total)."""
    items = parse_query(q) if items is None else items
    fts = build_fts_query(q, prefixes, match, items=items)
    if not fts:
        return [], 0
    fsql, fparams = filter_sql(filters)
    ids = _match_ids(fts, fsql, fparams, order_bm25=True, limit=MAX_RANKED)
    total = len(ids) if len(ids) < MAX_RANKED else _count(fts, fsql, fparams)
    if len(ids) < 2:
        return ids, total
    tiers = []
    pq = phrase_query(q, prefixes) if match == "all" else None
    if pq:
        tiers.append(set(_match_ids(f"({pq}) AND ({fts})", fsql, fparams)))
    if prefixes:
        ex = build_fts_query(q, prefixes, match, exact=True, items=items)
        if ex and ex != fts:
            tiers.append(set(_match_ids(ex, fsql, fparams)))
    if tiers:
        pos = {lid: i for i, lid in enumerate(ids)}
        ids.sort(key=lambda lid: (*[0 if lid in t else 1 for t in tiers], pos[lid]))
    return ids, total


def text_search(q, filters, sort="relevance", offset=0, limit=20, prefixes=True, match="all",
                with_facets=True):
    items = parse_query(q)
    fts = build_fts_query(q, prefixes=prefixes, mode=match, items=items)
    fsql, fparams = filter_sql(filters)
    con = db()
    if not fts and q.strip():
        return {"total": 0, "results": [], "facets": None,
                "error": "אין בשאילתה מילה לחיפוש. יש להוסיף לפחות מילה אחת (מילה עם מינוס רק מחריגה)."}
    if not fts:  # browse mode: filters only
        where = f"WHERE {fsql}" if fsql else ""
        order = SORTS.get(sort) or SORTS["number"]
        total = con.execute(f"SELECT COUNT(*) FROM letters l {where}", fparams).fetchone()[0]
        rows = con.execute(f"SELECT {LIST_COLS}, substr(l.body, 1, 260) AS snip FROM letters l {where} "
                           f"ORDER BY {order} LIMIT ? OFFSET ?", fparams + [limit, offset]).fetchall()
        res = [row_to_result(r, html.escape(r["snip"] or "") + "…") for r in rows]
        return {"total": total, "results": res, "facets": facets(None, fsql, fparams) if with_facets else None}

    try:
        if SORTS.get(sort):
            where = "letters_fts MATCH ?" + (f" AND {fsql}" if fsql else "")
            base = f"FROM letters_fts JOIN letters l ON l.id = letters_fts.rowid WHERE {where}"
            total = con.execute(f"SELECT COUNT(*) {base}", [fts] + fparams).fetchone()[0]
            page_ids = [r[0] for r in con.execute(f"SELECT l.id {base} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
                                                  [fts] + fparams + [limit, offset])]
        else:
            ids, total = ranked_ids(q, filters, prefixes, match, items)
            page_ids = ids[offset: offset + limit]
    except sqlite3.OperationalError as e:
        return {"total": 0, "results": [], "error": f"שאילתה לא תקינה: {e}", "fts": fts}

    terms = highlight_terms(q)
    rows = fetch_rows(page_ids, with_text=True)
    res = [row_to_result(rows[i], make_snippet(rows[i]["full_text"], terms=terms)) for i in page_ids if i in rows]

    if total < RELAX_BELOW and match == "all" and len([it for it in items if not it["neg"]]) > 1:
        relaxed = relaxed_search(q, filters, offset, limit, prefixes, exclude=[r["id"] for r in res], items=items)
        if relaxed["results"] or total == 0:
            relaxed["results"] = (res + relaxed["results"])[:limit] if offset == 0 else relaxed["results"]
            relaxed["total"] += total
            relaxed["strict_total"] = total
            relaxed["facets"] = None
            return relaxed
    out = {"total": total, "results": res, "fts": fts}
    if with_facets:
        out["facets"] = facets(fts, fsql, fparams)
    return out


def relaxed_search(q, filters, offset, limit, prefixes=True, exclude=(), items=None):
    """Rank letters by how many of the query items they contain (then bm25 of the OR query)."""
    items = parse_query(q) if items is None else items
    pos_items = [it for it in items if not it["neg"]]
    terms = [it for it in pos_items if not (it["kind"] == "word" and it["words"][0] in STOPWORDS)] or pos_items
    negs = [it for it in items if it["neg"]]
    fsql, fparams = filter_sql(filters)
    cover = {}
    for it in terms:
        e = build_fts_query("", prefixes, items=[it] + negs)
        if not e:
            continue
        try:
            for lid in _match_ids(e, fsql, fparams):
                cover[lid] = cover.get(lid, 0) + 1
        except sqlite3.OperationalError:
            continue
    excl = set(exclude)
    need = max(1, (len(terms) + 1) // 2)
    cands = [lid for lid, c in cover.items() if c >= need and lid not in excl]
    if not cands:
        return {"total": 0, "results": [], "relaxed": True, "terms": len(terms)}
    any_fts = build_fts_query("", prefixes, "any", items=terms + negs)
    try:
        order = {lid: i for i, lid in enumerate(_match_ids(any_fts, fsql, fparams, order_bm25=True, limit=MAX_RANKED))}
    except sqlite3.OperationalError:
        order = {}
    cands.sort(key=lambda lid: (-cover[lid], order.get(lid, 10 ** 6)))
    page = cands[offset: offset + limit]
    rows = fetch_rows(page, with_text=True)
    hl = highlight_terms(q)
    res = [row_to_result(rows[lid], make_snippet(rows[lid]["full_text"], terms=hl), cover[lid],
                         {"matched_terms": cover[lid], "n_terms": len(terms)}) for lid in page if lid in rows]
    return {"total": len(cands), "results": res, "relaxed": True, "terms": len(terms)}


def text_ranked_ids(q, filters, n=300, prefixes=True, match="all"):
    try:
        ids, _ = ranked_ids(q, filters, prefixes, match)
    except sqlite3.OperationalError:
        return []
    return ids[:n]


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
                import torch
                from sentence_transformers import SentenceTransformer
                dev = os.environ.get("IGROT_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
                m = SentenceTransformer(self.info["model"], device=dev)
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
            return None  # still building: try again on the next request
        try:
            _sem["index"] = SemanticIndex(folder)
        except Exception as e:  # noqa: BLE001
            _sem["error"] = f"שגיאה בטעינת האינדקס הסמנטי: {e}"
    return _sem["index"]


def semantic_status():
    return _sem["error"] or ("האינדקס הסמנטי עדיין נבנה – נסו שוב מאוחר יותר, או השתמשו בחיפוש טקסט"
                             if _sem["index"] is None else None)


def allowed_letter_ids(filters):
    fsql, fparams = filter_sql(filters)
    if not fsql:
        return None
    return {r[0] for r in db().execute(f"SELECT l.id FROM letters l WHERE {fsql}", fparams)}


# semantic mode = meaning first; lexical coverage only as a light tie-break
# (RRF weight 0.5 vs 1.0, k=10; +10 points on the eval set, see WORKLOG)
SEM_LEX_W, SEM_K = 0.5, 10


def semantic_search(q, filters, offset=0, limit=20, n=300):
    idx = semantic_index()
    if idx is None:
        return {"total": 0, "results": [], "error": semantic_status()}
    hits = idx.search(q, allowed_letter_ids(filters), n=n)
    best_chunk = {lid: ci for lid, _, ci in hits}
    score = {}
    for r, (lid, _, _) in enumerate(hits):
        score[lid] = 1.0 / (SEM_K + r + 1)
    if SEM_LEX_W:
        for r, lid in enumerate(coverage_ids(q, filters, True, n)):
            if lid in score:  # tie-break only: never adds letters the model did not retrieve
                score[lid] += SEM_LEX_W / (SEM_K + r + 1)
    ranked = sorted(score.items(), key=lambda x: -x[1])
    page = ranked[offset: offset + limit]
    rows = fetch_rows([lid for lid, _ in page])
    terms = [t for t in highlight_terms(q) if t not in QUERY_STOP]
    res = [row_to_result(rows[lid], make_snippet(idx.chunk_text[best_chunk[lid]], terms=terms), s * 100)
           for lid, s in page if lid in rows]
    return {"total": len(hits), "results": res, "model": idx.info["model"], "capped": len(hits) >= n}


def coverage_ids(q, filters, prefixes=True, n=300):
    """Lexical ranking for natural-language queries: letters that contain more of
    the query's content words first, then bm25 of the OR query."""
    items = [it for it in parse_query(q) if not it["neg"] and not (
        it["kind"] == "word" and (it["words"][0] in STOPWORDS or it["words"][0] in QUERY_STOP))]
    if not items:
        return []
    fsql, fparams = filter_sql(filters)
    cover = {}
    for it in items:
        e = build_fts_query("", prefixes, items=[it])
        if not e:
            continue
        try:
            for lid in _match_ids(e, fsql, fparams):
                cover[lid] = cover.get(lid, 0) + 1
        except sqlite3.OperationalError:
            continue
    e = build_fts_query("", prefixes, "any", items=items)
    try:
        order = {lid: i for i, lid in enumerate(_match_ids(e, fsql, fparams, order_bm25=True, limit=3000))} if e else {}
    except sqlite3.OperationalError:
        order = {}
    return sorted(cover, key=lambda lid: (-cover[lid], order.get(lid, 10 ** 6)))[:n]


# RRF parameters tuned on the eval set (eval/tune_hybrid.py): k=10, text weight 1.5
HYBRID_K, HYBRID_W_TEXT, HYBRID_W_SEM = 10, 1.5, 1.0


def hybrid_search(q, filters, offset=0, limit=20, n=300, prefixes=True,
                  k=HYBRID_K, w_text=HYBRID_W_TEXT, w_sem=HYBRID_W_SEM):
    idx = semantic_index()
    items = [it for it in parse_query(q) if not it["neg"]]
    # exact-syntax queries (phrases, *, -) keep strict text ranking; natural language uses coverage
    if any(it["kind"] == "phrase" or it.get("star") for it in items) or "-" in q:
        t_ids = text_ranked_ids(q, filters, n, prefixes, "all")
    else:
        t_ids = coverage_ids(q, filters, prefixes, n)
    s_hits = idx.search(q, allowed_letter_ids(filters), n=n) if idx else []
    score, best_chunk = {}, {}
    for rank, lid in enumerate(t_ids):
        score[lid] = score.get(lid, 0) + w_text / (k + rank + 1)
    for rank, (lid, s, ci) in enumerate(s_hits):
        score[lid] = score.get(lid, 0) + w_sem / (k + rank + 1)
        best_chunk[lid] = ci
    ranked = sorted(score.items(), key=lambda x: -x[1])
    page = ranked[offset: offset + limit]
    rows = fetch_rows([lid for lid, _ in page], with_text=True)
    terms = [t for t in highlight_terms(q) if t not in QUERY_STOP]
    in_text = set(t_ids)
    res = []
    for lid, s in page:
        if lid not in rows:
            continue
        if lid in in_text or idx is None or lid not in best_chunk:
            snip = make_snippet(rows[lid]["full_text"], terms=terms)
        else:
            snip = make_snippet(idx.chunk_text[best_chunk[lid]], terms=terms)
        res.append(row_to_result(rows[lid], snip, s * 100,
                                 {"in_text": lid in in_text, "in_semantic": lid in best_chunk}))
    return {"total": len(ranked), "results": res, "model": idx.info["model"] if idx else None,
            "warning": None if idx else semantic_status()}


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
    idx = semantic_index()
    return {"total": total, "volumes": vols, "years": years, "languages": langs, "months": months,
            "semantic_model": idx.info["model"] if idx else None, "semantic_status": semantic_status()}
