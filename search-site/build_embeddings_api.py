#!/usr/bin/env python3
"""Build a semantic index through the OpenRouter embeddings API (no GPU).

Same passages as build_embeddings.py (paragraph packs ~900 chars + letter header), same folder format
(chunks.jsonl, embeddings.npy, embeddings.json). embeddings.json gets "api_model", so the server embeds
queries through the API too. Optional extra passages per letter (doc2query): --extra <jsonl> with
{"letter_id", "text"} lines are appended as additional chunks of that letter.

    python search-site/build_embeddings_api.py --model qwen/qwen3-embedding-8b [--out <folder>] [--limit N]

Env: IGROT_DATA (search.db source), output default: search-site/data/emb/<slug>-api of this checkout.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))
import remote  # noqa: E402
from build_embeddings import INSTRUCT, chunk_letter, letter_header  # noqa: E402
from hebrew import clean  # noqa: E402

DATA = Path(os.environ.get("IGROT_DATA", ROOT / "data"))
QWEN_Q = INSTRUCT + "\nQuery:"
PREFIXES = {"qwen/qwen3-embedding-8b": (QWEN_Q, ""), "qwen/qwen3-embedding-4b": (QWEN_Q, ""),
            "intfloat/multilingual-e5-large": ("query: ", "passage: "),
            # Gemini embedding task prompts (asymmetric retrieval)
            "google/gemini-embedding-2": ("task: search result | query: ", "title: none | text: ")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen/qwen3-embedding-8b")
    ap.add_argument("--out")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--extra", help="jsonl of extra passages {letter_id, text} (doc2query)")
    ap.add_argument("--base", help="existing folder of the same model: copy its letter passages + vectors, "
                                   "embed only the --extra passages")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    out = Path(a.out) if a.out else ROOT / "data" / "emb" / (a.model.replace("/", "__") + "-api")
    out.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATA / "search.db")
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id, volume_heb, letter_number_heb, hebrew_year_heb, full_text "
                       "FROM letters ORDER BY volume, letter_number, id").fetchall()
    if a.limit:
        rows = rows[: a.limit]
    extra = {}
    if a.extra:
        for line in open(a.extra, encoding="utf-8"):
            d = json.loads(line)
            extra.setdefault(d["letter_id"], []).append(d["text"])
    q_prefix, p_prefix = PREFIXES.get(a.model, ("", ""))
    chunks, texts = [], []
    heads = {}
    base_emb = None
    if a.base:  # reuse the letter passages + vectors of an existing folder (local or API built, same model)
        base = Path(a.base)
        binfo = json.loads((base / "embeddings.json").read_text(encoding="utf-8"))
        q_prefix, p_prefix = binfo.get("query_prefix", q_prefix), binfo.get("passage_prefix", p_prefix)
        for line in open(base / "chunks.jsonl", encoding="utf-8"):
            c = json.loads(line)
            if not c.get("extra"):
                chunks.append({k: c[k] for k in ("i", "letter_id", "part", "text")})
        base_emb = np.load(base / "embeddings.npy")[: len(chunks)]
        for r in rows:
            heads[r["id"]] = letter_header(r)
    else:
        for r in rows:
            head = heads[r["id"]] = letter_header(r)
            for j, c in enumerate(chunk_letter(clean(r["full_text"] or ""))):
                chunks.append({"i": len(chunks), "letter_id": r["id"], "part": j, "text": c})
                texts.append(p_prefix + head + "\n" + c)
    # doc2query passages go at the END, in letter order, so every model's folder lines up
    n_letter = len(chunks)
    for r in rows:
        for j, c in enumerate(extra.get(r["id"], [])):
            chunks.append({"i": len(chunks), "letter_id": r["id"], "part": 1000 + j, "text": c, "extra": True})
            texts.append(p_prefix + heads[r["id"]] + "\n" + c)
    print(f"{len(rows)} letters -> {n_letter} passages + {len(chunks) - n_letter} extra; "
          f"{len(texts)} to embed", flush=True)
    with open(out / "chunks.jsonl", "w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    partial = out / "embeddings.partial.npy"
    have = np.load(partial) if partial.exists() else None
    start = 0 if have is None else len(have)
    batches = [(s, texts[s:s + a.batch]) for s in range(start, len(texts), a.batch)]
    t0 = time.time()

    def run(b):
        s, tx = b
        d = remote.post("/embeddings", {"model": a.model, "input": tx})
        v = np.asarray([x["embedding"] for x in sorted(d["data"], key=lambda x: x["index"])], dtype=np.float32)
        assert len(v) == len(tx)
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        return s, v.astype(np.float16)

    blocks = [] if have is None else [have]
    with ThreadPoolExecutor(a.workers) as ex:
        for n, (s, v) in enumerate(ex.map(run, batches), 1):  # map keeps order
            blocks.append(v)
            if n % 50 == 0 or n == len(batches):
                cur = np.concatenate(blocks)
                np.save(partial, cur)
                blocks = [cur]
                print(f"  {len(cur)}/{len(texts)}  ${remote.spent['usd']:.4f}  {time.time() - t0:.0f}s", flush=True)
    emb = np.concatenate(blocks) if blocks else have
    assert len(emb) == len(texts)
    if base_emb is not None:
        emb = np.concatenate([base_emb.astype(np.float16), emb])
        assert len(emb) == len(chunks)
    np.save(out / "embeddings.npy", emb)
    (out / "embeddings.json").write_text(json.dumps({
        "model": a.model, "api_model": a.model, "dim": int(emb.shape[1]), "n": len(emb),
        "query_prefix": q_prefix, "passage_prefix": p_prefix, "extra": a.extra,
        "chunking": "paragraph pack ~900 chars, 1 short-paragraph overlap, header prefix"},
        ensure_ascii=False, indent=1), encoding="utf-8")
    partial.unlink(missing_ok=True)
    print(f"done: {emb.shape}, ${remote.spent['usd']:.4f}, {time.time() - t0:.0f}s -> {out}")


if __name__ == "__main__":
    main()
