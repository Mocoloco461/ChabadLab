"""Embeddings and reranking through the OpenRouter API (no local model, no GPU).

Used when IGROT_EMBED_API=1 (query vectors for the stored indexes come from the same model served by
OpenRouter; checked: cosine 1.000 with the locally built vectors), for folders built by
build_embeddings_api.py (embeddings.json has "api_model"), and for IGROT_RERANK=api:<model>.

Key: env OPENROUTER_API_KEY, else %USERPROFILE%/.openrouter_key. Every result is cached on disk
(IGROT_API_CACHE, default search-site/data/api_cache.sqlite of this checkout), so repeated eval runs
cost nothing and run fast.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

API = "https://openrouter.ai/api/v1"
CACHE = Path(os.environ.get("IGROT_API_CACHE", Path(__file__).resolve().parent.parent / "data" / "api_cache.sqlite"))

# Hugging Face name of a locally built index -> the same model on OpenRouter
API_EMBED_IDS = {
    "Qwen/Qwen3-Embedding-4B": "qwen/qwen3-embedding-4b",
    "Qwen/Qwen3-Embedding-8B": "qwen/qwen3-embedding-8b",
    "intfloat/multilingual-e5-large": "intfloat/multilingual-e5-large",
    "BAAI/bge-m3": "baai/bge-m3",
}

_lock = threading.Lock()
_local = threading.local()
_key = None
spent = {"usd": 0.0, "calls": 0}


def key():
    global _key
    if _key is None:
        k = os.environ.get("OPENROUTER_API_KEY")
        if not k:
            p = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".openrouter_key"
            k = p.read_text(encoding="utf-8-sig").strip() if p.exists() else ""
        if not k:
            raise RuntimeError("no OpenRouter key (OPENROUTER_API_KEY or ~/.openrouter_key)")
        _key = k
    return _key


def has_key():
    """True when an OpenRouter key is configured (env or ~/.openrouter_key); never raises."""
    try:
        return bool(key())
    except Exception:  # noqa: BLE001
        return False


def post(path, body, retries=5, timeout=120):
    data = json.dumps(body).encode()
    err = None
    for i in range(retries):
        req = urllib.request.Request(API + path, data=data, method="POST", headers={
            "Authorization": "Bearer " + key(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            if "error" in d and not d.get("data") and not d.get("results"):
                raise RuntimeError(str(d["error"])[:300])
            u = d.get("usage") or {}
            with _lock:
                spent["usd"] += float(u.get("cost") or 0)
                spent["calls"] += 1
            return d
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
            if e.code in (400, 401, 402, 403):
                break
        except Exception as e:  # noqa: BLE001
            err = str(e)[:300]
        time.sleep(1.5 * (i + 1) ** 2)
    raise RuntimeError(f"OpenRouter {path}: {err}")


def _db():
    con = getattr(_local, "con", None)
    if con is None:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(CACHE, timeout=60, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS emb (k TEXT PRIMARY KEY, v BLOB)")
        con.execute("CREATE TABLE IF NOT EXISTS rr (k TEXT PRIMARY KEY, s REAL)")
        _local.con = con
    return con


def _h(*parts):
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()


def embed(model, texts, batch=64):
    """L2-normalized float32 vectors [len(texts), dim]; cached per (model, text)."""
    con = _db()
    keys = [_h(model, t) for t in texts]
    got = {}
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        for k, v in con.execute(f"SELECT k, v FROM emb WHERE k IN ({','.join('?' * len(part))})", part):
            got[k] = np.frombuffer(v, dtype=np.float32)
    todo = [i for i, k in enumerate(keys) if k not in got]
    for s in range(0, len(todo), batch):
        idx = todo[s:s + batch]
        d = post("/embeddings", {"model": model, "input": [texts[i] for i in idx]})
        rows = sorted(d["data"], key=lambda x: x["index"])
        assert len(rows) == len(idx), "embedding count mismatch"
        with con:
            for i, r in zip(idx, rows):
                v = np.asarray(r["embedding"], dtype=np.float32)
                v /= np.linalg.norm(v) or 1.0
                got[keys[i]] = v
                con.execute("INSERT OR REPLACE INTO emb VALUES (?, ?)", (keys[i], v.tobytes()))
    return np.stack([got[k] for k in keys])


def prefetch(folders, queries):
    """Fill the cache with the query vectors an index will ask for (one API call per 64 queries
    instead of one per query): python remote.py <folder>[,<folder>...] <cases.jsonl>..."""
    for f in folders:
        info = json.loads((Path(f) / "embeddings.json").read_text(encoding="utf-8"))
        api = info.get("api_model") or API_EMBED_IDS.get(info["model"])
        if not api:
            print(f"skip {f}: no API model")
            continue
        texts = [info.get("query_prefix", "") + q for q in queries]
        t = time.time()
        embed(api, texts)
        print(f"{api}: {len(texts)} queries cached in {time.time() - t:.0f}s, ${spent['usd']:.4f} so far")


def rerank(model, query, docs):
    """Relevance scores for docs (same order); cached per (model, query, doc)."""
    con = _db()
    keys = [_h(model, query, d) for d in docs]
    got = dict(con.execute(f"SELECT k, s FROM rr WHERE k IN ({','.join('?' * len(keys))})", keys)) if keys else {}
    todo = [i for i, k in enumerate(keys) if k not in got]
    if todo:
        d = post("/rerank", {"model": model, "query": query, "documents": [docs[i] for i in todo]})
        with con:
            for r in d["results"]:
                i = todo[r["index"]]
                got[keys[i]] = float(r["relevance_score"])
                con.execute("INSERT OR REPLACE INTO rr VALUES (?, ?)", (keys[i], got[keys[i]]))
    return [got[k] for k in keys]


class RemoteReranker:
    """Same predict() interface as sentence_transformers.CrossEncoder, for search.rerank_scores."""

    def __init__(self, model):
        self.model = model

    def predict(self, pairs, **_):
        if not pairs:
            return []
        q = pairs[0][0]
        assert all(p[0] == q for p in pairs)
        return rerank(self.model, q, [p[1] for p in pairs])


if __name__ == "__main__":
    import sys
    qs = []
    for f in sys.argv[2:]:
        for line in open(f, encoding="utf-8"):
            if line.strip():
                qs.append(json.loads(line)["query"])
    qs = list(dict.fromkeys(qs))
    prefetch([x for x in sys.argv[1].split(",") if x], qs)
