"""Search logic: full-text (FTS5), semantic (embeddings) and hybrid (RRF)."""
import html
import json
import os
import re
import sqlite3
import threading
from pathlib import Path

import numpy as np

import spell
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
_greg_range = {}


def has_greg_range():
    """index built with greg_year_min/max (Gregorian range for letters dated only by Hebrew year)?"""
    if "v" not in _greg_range:
        cols = {r[1] for r in db().execute("PRAGMA table_info(letters)")}
        _greg_range["v"] = "greg_year_min" in cols
    return _greg_range["v"]


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

    gmin, gmax = ("+l.greg_year_min", "+l.greg_year_max") if has_greg_range() else ("+l.gregorian_year",) * 2
    for key, col, op in (("vol_from", "+l.volume", ">="), ("vol_to", "+l.volume", "<="),
                         ("year_from", "+l.hebrew_year", ">="), ("year_to", "+l.hebrew_year", "<="),
                         ("greg_from", gmax, ">="), ("greg_to", gmin, "<="),
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


def _match_ids(fts, fsql, fparams, order_bm25=False, limit=None, table="letters_fts"):
    where = f"{table} MATCH ?" + (f" AND {fsql}" if fsql else "")
    sql = f"SELECT l.id FROM {table} JOIN letters l ON l.id = {table}.rowid WHERE {where}"
    if order_bm25:
        sql += f" ORDER BY {BM25 if table == 'letters_fts' else f'bm25({table})'}"
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


def spell_fix(q):
    """-> (corrected query, [(wrong, right), ...]). Only words that occur nowhere in the corpus are
    corrected (see spell.py). IGROT_SPELL=0 disables it."""
    if os.environ.get("IGROT_SPELL", "1") == "0" or not q.strip():
        return q, []
    try:
        return spell.correct_query(db(), q)
    except sqlite3.Error:
        return q, []


def corrected_info(q, q2, fixes):
    return {"from": q, "to": q2, "fixes": [{"from": a, "to": b} for a, b in fixes]} if fixes else None


def text_search(q, filters, sort="relevance", offset=0, limit=20, prefixes=True, match="all",
                with_facets=True, correct=True):
    if correct:
        q2, fixes = spell_fix(q)
        if fixes:
            out = text_search(q2, filters, sort, offset, limit, prefixes, match, with_facets, correct=False)
            out["corrected"] = corrected_info(q, q2, fixes)
            return out
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
    """One embedding folder, or several (comma-separated) built on the same chunks.
    With several models the passage score is the sum of their cosine similarities
    (implemented as concatenated, per-model-normalized vectors)."""

    def __init__(self, folders):
        if isinstance(folders, (str, Path)):
            folders = [f for f in str(folders).split(",") if f.strip()]
        folders = [Path(str(f).strip()) for f in folders]
        self.folder = folders[0]
        self.infos = [json.loads((f / "embeddings.json").read_text(encoding="utf-8")) for f in folders]
        self.info = dict(self.infos[0])
        self.info["model"] = " + ".join(i["model"] for i in self.infos)
        self.emb = np.hstack([np.load(f / "embeddings.npy").astype(np.float32) for f in folders])
        self.chunk_letter = []
        self.chunk_text = []
        self.chunk_extra = set()
        with open(self.folder / "chunks.jsonl", encoding="utf-8") as fh:
            for line in fh:
                c = json.loads(line)
                self.chunk_letter.append(c["letter_id"])
                self.chunk_text.append(c["text"])
                if c.get("extra"):  # doc2query passage (generated questions), not letter text
                    self.chunk_extra.add(c["i"])
        self.chunk_letter = np.array(self.chunk_letter)
        for f in folders[1:]:  # ensembles only make sense on identical chunking
            with open(f / "chunks.jsonl", encoding="utf-8") as fh:
                if sum(1 for _ in fh) != len(self.chunk_letter):
                    raise ValueError(f"chunks of {f} differ from {self.folder}")
        self._models = None
        self._letter_chunks = None
        self._lock = threading.Lock()

    def api_model(self, info):
        """OpenRouter id when this part's query vectors come from the API, else None.
        Folders built through the API always do; local ones only with IGROT_EMBED_API=1."""
        if info.get("api_model"):
            return info["api_model"]
        if os.environ.get("IGROT_EMBED_API", "").lower() in ("1", "on", "true"):
            import remote
            return remote.API_EMBED_IDS.get(info["model"])
        return None

    @property
    def models(self):
        """local SentenceTransformer per part (None for parts served by the API)"""
        with self._lock:
            if self._models is None:
                ms = []
                for info in self.infos:
                    if self.api_model(info):
                        ms.append(None)
                        continue
                    import torch
                    from sentence_transformers import SentenceTransformer
                    dev = os.environ.get("IGROT_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
                    # fp16 on GPU, loaded directly in fp16 (same precision as build_embeddings.py;
                    # Qwen3-4B in fp32 is 16GB and does not fit a 12GB card even transiently)
                    kw = {"model_kwargs": {"torch_dtype": torch.float16}} if dev.startswith("cuda") else {}
                    m = SentenceTransformer(info["model"], device=dev, **kw)
                    m.max_seq_length = info.get("max_seq", 512)
                    ms.append(m)
                self._models = ms
        return self._models

    def encode(self, q):
        parts = []
        for m, info in zip(self.models, self.infos):
            text = info.get("query_prefix", "") + q
            api = self.api_model(info)
            if api:
                import remote
                parts.append(remote.embed(api, [text])[0])
            else:
                parts.append(m.encode([text], normalize_embeddings=True)[0])
        return np.concatenate(parts).astype(np.float32)

    def letter_chunks(self, lid):
        if self._letter_chunks is None:
            d = {}
            for i, l in enumerate(self.chunk_letter.tolist()):
                d.setdefault(l, []).append(i)
            self._letter_chunks = d
        return self._letter_chunks.get(lid, [])

    def display_text(self, ci):
        """snippet source: the passage, or the letter's first real passage for a doc2query one"""
        if ci in self.chunk_extra:
            real = [i for i in self.letter_chunks(int(self.chunk_letter[ci])) if i not in self.chunk_extra]
            ci = real[0] if real else ci
        return self.chunk_text[ci]

    def best_chunks(self, qv, lid, top=1, letter_only=False):
        """chunk indices of one letter, most similar to the query vector first"""
        idx = self.letter_chunks(lid)
        if letter_only and self.chunk_extra:
            idx = [i for i in idx if i not in self.chunk_extra] or idx
        if not idx:
            return []
        sc = self.emb[idx] @ qv
        return [idx[i] for i in np.argsort(-sc)[:top]]

    def search(self, q, allowed_ids=None, n=300, qv=None):
        """-> list of (letter_id, score, best_chunk_index) sorted by score."""
        scores = self.emb @ (self.encode(q) if qv is None else qv)
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


_sem_lock = threading.Lock()
# Preferred index when its folder exists AND an OpenRouter key is present (no GPU needed): gemini-embedding-2
# over the letter passages + doc2query v2 cards (merged-engine WORKLOG). IGROT_API_INDEX="" disables it;
# without a key the server falls back to the local models below.
API_INDEX = os.environ.get("IGROT_API_INDEX", "d2q_v2/gem2")


def semantic_index():
    if _sem["index"] is not None or _sem["error"] is not None:
        return _sem["index"]
    with _sem_lock:  # the startup warm-up thread and the first request must not both load it
        return _load_semantic_index()


def _load_semantic_index():
    if _sem["index"] is None and _sem["error"] is None:
        folder = os.environ.get("IGROT_EMB")
        if not folder and API_INDEX and (DATA / "emb" / API_INDEX / "embeddings.npy").exists():
            import remote
            if remote.has_key():  # API index: query vectors (and the reranker) through OpenRouter
                folder = [DATA / "emb" / API_INDEX]
        if not folder:
            # local models: the strongest finished option (see WORKLOG for the eval numbers)
            for slugs in (("Qwen__Qwen3-Embedding-4B", "intfloat__multilingual-e5-large"),
                          ("BAAI__bge-m3", "intfloat__multilingual-e5-large"),
                          ("Qwen__Qwen3-Embedding-4B",),
                          ("BAAI__bge-m3",), ("intfloat__multilingual-e5-large",),
                          ("intfloat__multilingual-e5-base",), ("intfloat__multilingual-e5-small",)):
                if all((DATA / "emb" / s / "embeddings.npy").exists() for s in slugs):
                    folder = [DATA / "emb" / s for s in slugs]
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
# (RRF weight 0.75 vs 1.0, k=10; retuned for bge-m3 / e5-large on the main eval set, see WORKLOG)
SEM_LEX_W, SEM_K = 0.75, 10


def sort_ranked(ranked, sort):
    """date / number order for the letters a meaning search found (relevance order otherwise)"""
    if not SORTS.get(sort) or not ranked:
        return ranked
    score = dict(ranked)
    order = [r[0] for r in db().execute(
        f"SELECT l.id FROM letters l WHERE l.id IN (SELECT value FROM json_each(?)) ORDER BY {SORTS[sort]}",
        [json.dumps(list(score))])]
    return [(lid, score[lid]) for lid in order]


def semantic_ranked(q, filters, n=300):
    """-> (ranked [(lid, score)], best_chunk {lid: chunk}, qv) or None if no index."""
    idx = semantic_index()
    if idx is None:
        return None
    qv = idx.encode(q)
    hits = idx.search(q, allowed_letter_ids(filters), n=n, qv=qv)
    best_chunk = {lid: ci for lid, _, ci in hits}
    score = {}
    for r, (lid, _, _) in enumerate(hits):
        score[lid] = 1.0 / (SEM_K + r + 1)
    if SEM_LEX_W:
        for r, lid in enumerate(coverage_ids(spell_fix(q)[0], filters, True, n)):
            if lid in score:  # tie-break only: never adds letters the model did not retrieve
                score[lid] += SEM_LEX_W / (SEM_K + r + 1)
    return sorted(score.items(), key=lambda x: -x[1]), best_chunk, qv


def semantic_search(q, filters, offset=0, limit=20, n=300, rerank=None, sort="relevance"):
    out = semantic_ranked(q, filters, n)
    if out is None:
        return {"total": 0, "results": [], "error": semantic_status()}
    ranked, best_chunk, qv = out
    idx = semantic_index()
    reranked = False
    if rerank is not False and reranker() is not None and not SORTS.get(sort):
        ranked, reranked = rerank_ranked(q, ranked, idx, qv), True
    ranked = sort_ranked(ranked, sort)
    page = ranked[offset: offset + limit]
    rows = fetch_rows([lid for lid, _ in page])
    terms = [t for t in highlight_terms(q) if t not in QUERY_STOP]
    res = [row_to_result(rows[lid], make_snippet(idx.display_text(best_chunk[lid]), terms=terms), s * 100)
           for lid, s in page if lid in rows]
    return {"total": len(ranked), "results": res, "model": idx.info["model"], "capped": len(ranked) >= n,
            "reranked": reranked}


def coverage_ids(q, filters, prefixes=True, n=300, table="letters_fts"):
    """Lexical ranking for natural-language queries: letters that contain more of
    the query's content words first, then bm25 of the OR query.
    table="d2q_fts": the same over the letters' doc2query cards (summary, questions, topics)."""
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
            for lid in _match_ids(e, fsql, fparams, table=table):
                cover[lid] = cover.get(lid, 0) + 1
        except sqlite3.OperationalError:
            continue
    e = build_fts_query("", prefixes, "any", items=items)
    try:
        order = {lid: i for i, lid in enumerate(_match_ids(e, fsql, fparams, order_bm25=True, limit=3000,
                                                           table=table))} if e else {}
    except sqlite3.OperationalError:
        order = {}
    return sorted(cover, key=lambda lid: (-cover[lid], order.get(lid, 10 ** 6)))[:n]


# RRF parameters tuned on the main eval set (eval/tune_hybrid.py): k=10, text 1.0 vs meaning 1.5
# (e5-small needed text 1.5 vs 1.0; stronger models need less lexical help)
HYBRID_K, HYBRID_W_TEXT, HYBRID_W_SEM = 10, 1.0, 1.5


def exact_syntax(q):
    items = [it for it in parse_query(q) if not it["neg"]]
    return any(it["kind"] == "phrase" or it.get("star") for it in items) or "-" in q


# Name queries ("שמטוב לונדון", "הרב זוין"): meaning models know nothing about names, so the letters
# addressed to that person sink in hybrid mode. When most of a short query's words occur in recipient
# lines, letters whose recipient line has all of those words get a third RRF list.
REC_W = float(os.environ.get("IGROT_REC_W", "1.5"))  # 0 disables
REC_MAX_ITEMS = 5


def recipient_ids(q, filters, n=300):
    items = [it for it in parse_query(q) if not it["neg"] and it["kind"] in ("word", "alts") and not (
        it["words"][0] in STOPWORDS or it["words"][0] in QUERY_STOP)]
    if not items or len(items) > REC_MAX_ITEMS:
        return []
    fsql, fparams = filter_sql(filters)
    rec = []
    for it in items:
        e = build_fts_query("", False, items=[it])
        try:
            if e and _count("{recipient} : " + e, "", []) > 0:
                rec.append(e)
        except sqlite3.OperationalError:
            continue
    if not rec or 2 * len(rec) < len(items):
        return []
    expr = " AND ".join("{recipient} : " + e for e in rec)
    try:
        return _match_ids(expr, fsql, fparams, order_bm25=True, limit=n)
    except sqlite3.OperationalError:
        return []


# doc2query cards on the word side: a separate FTS table d2q_fts (rowid = letter id; built by
# eval/merged/build_d2q_fts.py into a copy of search.db). Text mode never uses it (it is not letter text);
# in hybrid mode it is one more RRF list. IGROT_D2Q_LEX_W=0 disables it.
D2Q_LEX_W = float(os.environ.get("IGROT_D2Q_LEX_W", "1.0"))
_d2q = {}


def has_d2q_fts():
    if "ok" not in _d2q:
        _d2q["ok"] = db().execute("SELECT 1 FROM sqlite_master WHERE name='d2q_fts'").fetchone() is not None
    return _d2q["ok"]


def hybrid_ranked(q, filters, n=300, prefixes=True, k=HYBRID_K, w_text=HYBRID_W_TEXT, w_sem=HYBRID_W_SEM):
    """-> (ranked [(lid, score)], best_chunk {lid: chunk}, text ids, qv or None)"""
    idx = semantic_index()
    tq = spell_fix(q)[0]  # misspelled words fixed for the word side only; the meaning side sees the original
    # exact-syntax queries (phrases, *, -) keep strict text ranking; natural language uses coverage
    if exact_syntax(q):
        t_ids = text_ranked_ids(tq, filters, n, prefixes, "all")
    else:
        t_ids = coverage_ids(tq, filters, prefixes, n)
    try:
        qv = idx.encode(q) if idx else None
    except Exception as e:  # noqa: BLE001  API down / no network: the word side still answers
        print(f"[hybrid] ERROR meaning side unavailable: {e}", flush=True)
        idx, qv = None, None
    s_hits = idx.search(q, allowed_letter_ids(filters), n=n, qv=qv) if idx else []
    score, best_chunk = {}, {}
    for rank, lid in enumerate(t_ids):
        score[lid] = score.get(lid, 0) + w_text / (k + rank + 1)
    for rank, (lid, s, ci) in enumerate(s_hits):
        score[lid] = score.get(lid, 0) + w_sem / (k + rank + 1)
        best_chunk[lid] = ci
    if REC_W and not exact_syntax(q):
        for rank, lid in enumerate(recipient_ids(tq, filters, n)):
            score[lid] = score.get(lid, 0) + REC_W / (k + rank + 1)
    if D2Q_LEX_W and not exact_syntax(q) and has_d2q_fts():
        for rank, lid in enumerate(coverage_ids(tq, filters, prefixes, n, table="d2q_fts")):
            score[lid] = score.get(lid, 0) + D2Q_LEX_W / (k + rank + 1)
    return sorted(score.items(), key=lambda x: -x[1]), best_chunk, t_ids, qv


def hybrid_search(q, filters, offset=0, limit=20, n=300, prefixes=True,
                  k=HYBRID_K, w_text=HYBRID_W_TEXT, w_sem=HYBRID_W_SEM, rerank=None, sort="relevance"):
    idx = semantic_index()
    ranked, best_chunk, t_ids, qv = hybrid_ranked(q, filters, n, prefixes, k, w_text, w_sem)
    reranked = False
    # exact-syntax queries are about the words, not the meaning: no reranking
    if rerank is not False and idx is not None and qv is not None and not exact_syntax(q) \
            and reranker() is not None and not SORTS.get(sort):
        ranked, reranked = rerank_ranked(q, ranked, idx, qv), True
    ranked = sort_ranked(ranked, sort)
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
            snip = make_snippet(idx.display_text(best_chunk[lid]), terms=terms)
        res.append(row_to_result(rows[lid], snip, s * 100,
                                 {"in_text": lid in in_text, "in_semantic": lid in best_chunk}))
    q2, fixes = spell_fix(q)
    return {"total": len(ranked), "results": res, "model": idx.info["model"] if idx else None,
            "warning": None if idx else semantic_status(), "reranked": reranked,
            "corrected": corrected_info(q, q2, fixes)}


# ------------------------------------------------------------------ reranker (cross-encoder)
# IGROT_RERANK=<model> | auto (default: BAAI/bge-reranker-v2-m3 when a GPU is present, else off) | off.
# On CPU it costs seconds per query, so "auto" leaves it off there.
# The top RERANK_N letters of semantic / hybrid ranking are re-scored by the cross-encoder on
# (query, best passage of the letter) and merged with the first-stage rank by weighted RRF.
RERANK_N = int(os.environ.get("IGROT_RERANK_N", "50"))
RERANK_K = 10
RERANK_W_FIRST, RERANK_W_CE = 1.0, 1.0
RERANK_CHUNKS = 1   # passages per letter given to the cross-encoder
DEFAULT_RERANKER = "BAAI/bge-reranker-v2-m3"
DEFAULT_API_RERANKER = "api:cohere/rerank-5-pro"  # "auto" with an API index (merged-engine eval)
_rr = {"model": None, "name": None, "error": None}
_rr_lock = threading.Lock()


def reranker():
    name = os.environ.get("IGROT_RERANK", "auto")
    if not name or name.lower() in ("0", "off", "none", "false"):
        return None
    if name == "auto":
        if _rr["name"] is None:
            try:
                import torch
                cuda = torch.cuda.is_available() and not os.environ.get("IGROT_DEVICE", "").startswith("cpu")
            except Exception:  # noqa: BLE001
                cuda = False
            _rr["auto"] = DEFAULT_RERANKER if cuda else None
        name = _rr.get("auto")
        idx = _sem["index"]
        if idx is not None and any(idx.api_model(i) for i in idx.infos):
            # an API index is loaded (so a key is present): rerank through the API too, no GPU needed
            name = os.environ.get("IGROT_RERANK_API", DEFAULT_API_RERANKER)
        if not name:
            return None
    if name.startswith("api:"):  # e.g. IGROT_RERANK=api:cohere/rerank-5-pro (OpenRouter, no GPU)
        if _rr["name"] != name:
            import remote
            _rr.update(model=remote.RemoteReranker(name[4:]), name=name, error=None)
        return _rr["model"]
    with _rr_lock:
        if _rr["name"] != name:
            try:
                import torch
                from sentence_transformers import CrossEncoder
                dev = os.environ.get("IGROT_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
                kw = {"model_kwargs": {"torch_dtype": torch.float16}} if dev.startswith("cuda") else {}
                m = CrossEncoder(name, device=dev, max_length=512, **kw)
                _rr.update(model=m, name=name, error=None)
            except Exception as e:  # noqa: BLE001
                _rr.update(model=None, name=name, error=str(e))
    return _rr["model"]


def rerank_passages(idx, qv, lid, n_chunks=RERANK_CHUNKS):
    # the cross-encoder sees letter text only (IGROT_RERANK_EXTRA=1: doc2query passages too)
    letter_only = os.environ.get("IGROT_RERANK_EXTRA", "") not in ("1", "on", "true")
    return "\n".join(idx.chunk_text[i] for i in idx.best_chunks(qv, lid, n_chunks, letter_only=letter_only))


def rerank_scores(q, lids, idx, qv, n_chunks=RERANK_CHUNKS):
    m = reranker()
    pairs = [(q, rerank_passages(idx, qv, lid, n_chunks)) for lid in lids]
    return [float(x) for x in m.predict(pairs, batch_size=32, show_progress_bar=False)]


def rerank_merge(ranked, ce, n=None, k=RERANK_K, w_first=RERANK_W_FIRST, w_ce=RERANK_W_CE):
    """ranked: first-stage [(lid, score)]; ce: cross-encoder scores for ranked[:len(ce)].
    Top n re-ordered by w_first/(k+first_rank) + w_ce/(k+ce_rank); the rest keep their order below."""
    n = len(ce) if n is None else min(n, len(ce))
    head = ranked[:n]
    ce = ce[:n]
    ce_rank = {lid: r for r, lid in enumerate(
        lid for _, lid in sorted(zip(ce, (lid for lid, _ in head)), key=lambda x: -x[0]))}
    new = {lid: w_first / (k + r + 1) + w_ce / (k + ce_rank[lid] + 1) for r, (lid, _) in enumerate(head)}
    top = sorted(new.items(), key=lambda x: -x[1])
    floor = min(v for _, v in top) if top else 0
    # keep the tail strictly below the head, preserving first-stage order
    tail = [(lid, floor * 0.5 * (1 - i / (len(ranked) + 1))) for i, (lid, _) in enumerate(ranked[n:])]
    return top + tail


def rerank_ranked(q, ranked, idx, qv):
    lids = [lid for lid, _ in ranked[:RERANK_N]]
    if not lids or qv is None:
        return ranked
    try:
        return rerank_merge(ranked, rerank_scores(q, lids, idx, qv))
    except Exception as e:  # noqa: BLE001  API reranker down: keep the first-stage order
        print(f"[rerank] ERROR first-stage order kept: {e}", flush=True)
        return ranked


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
