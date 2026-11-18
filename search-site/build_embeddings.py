#!/usr/bin/env python3
"""
Semantic index for the Igrot Kodesh search site.

Splits every letter into passages (~900 chars, on paragraph boundaries), embeds
them with a multilingual sentence-embedding model (default BAAI/bge-m3, which
handles Hebrew and Yiddish), and stores:

    chunks.jsonl     one passage per line: {i, letter_id, part, text}
    embeddings.npy   float16 matrix [n_chunks, dim], L2-normalized
    embeddings.json  model name, dims, query/passage prefixes, chunking params

Each model gets its own folder: search-site/data/emb/<model-slug>/
(chunks.jsonl, embeddings.npy, embeddings.json). Resumable: progress is saved
every --save-every passages to embeddings.partial.npy.

    python3 search-site/build_embeddings.py [--model intfloat/multilingual-e5-small] [--limit N]

Models tried (CPU, 10 threads): e5-small ~15 passages/s, e5-base ~5/s, bge-m3 ~1-2/s.
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
from hebrew import clean  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def chunk_letter(text, target=900, max_len=1400, overlap_paras=1):
    """Split on blank-line paragraphs; pack paragraphs up to ~target chars.
    Very long paragraphs are cut on sentence ends. Consecutive chunks share the
    last paragraph of the previous chunk (context overlap) when it is short."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    pieces = []
    for p in paras:
        if len(p) <= max_len:
            pieces.append(p)
            continue
        cur = ""
        for sent in p.replace(". ", ".\u0001").split("\u0001"):
            if len(cur) + len(sent) > target and cur:
                pieces.append(cur.strip())
                cur = ""
            cur += sent + " "
        if cur.strip():
            pieces.append(cur.strip())
    chunks, cur = [], []
    for p in pieces:
        if cur and sum(len(x) for x in cur) + len(p) > target:
            chunks.append(cur)
            cur = cur[-overlap_paras:] if overlap_paras and len(cur[-1]) < 300 else []
        cur.append(p)
    if cur:
        chunks.append(cur)
    return ["\n".join(c) for c in chunks] or [text[:max_len]]


def letter_header(row):
    """Short context prefix added to every passage of a letter (helps retrieval)."""
    bits = [f"אגרות קודש כרך {row['volume_heb']} אגרת {row['letter_number_heb']}"]
    if row["hebrew_year_heb"]:
        bits.append(f"שנת {row['hebrew_year_heb']}")
    return ", ".join(bits)


# models that expect an instruction prefix on queries / passages
INSTRUCT = "Instruct: Given a Hebrew search query, retrieve passages from rabbinic letters that answer it"
PREFIXES = {
    "intfloat/multilingual-e5-small": ("query: ", "passage: "),
    "intfloat/multilingual-e5-base": ("query: ", "passage: "),
    "intfloat/multilingual-e5-large": ("query: ", "passage: "),
    "intfloat/multilingual-e5-large-instruct": (INSTRUCT + "\nQuery: ", ""),
    "Qwen/Qwen3-Embedding-0.6B": (INSTRUCT + "\nQuery:", ""),
    "Qwen/Qwen3-Embedding-4B": (INSTRUCT + "\nQuery:", ""),
}


def model_slug(name):
    return name.replace("/", "__")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DATA / "search.db"))
    ap.add_argument("--model", default="intfloat/multilingual-e5-small")
    ap.add_argument("--threads", type=int, default=0, help="torch threads (0 = all)")
    ap.add_argument("--device", default="auto", help="auto | cpu | cuda | cuda:1 ...")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--max-seq", type=int, default=512)
    ap.add_argument("--limit", type=int, help="only the first N letters (speed test)")
    ap.add_argument("--save-every", type=int, default=2000)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id, volume_heb, letter_number_heb, hebrew_year_heb, full_text "
                       "FROM letters ORDER BY volume, letter_number, id").fetchall()
    if args.limit:
        rows = rows[: args.limit]

    chunks = []
    for r in rows:
        head = letter_header(r)
        for j, c in enumerate(chunk_letter(clean(r["full_text"] or ""))):
            chunks.append({"i": len(chunks), "letter_id": r["id"], "part": j, "text": c, "head": head})
    print(f"{len(rows)} letters -> {len(chunks)} passages", flush=True)

    suffix = f".limit{args.limit}" if args.limit else ""
    outdir = DATA / "emb" / model_slug(args.model)
    outdir.mkdir(parents=True, exist_ok=True)
    q_prefix, p_prefix = PREFIXES.get(args.model, ("", ""))
    with open(outdir / f"chunks{suffix}.jsonl", "w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps({k: c[k] for k in ("i", "letter_id", "part", "text")}, ensure_ascii=False) + "\n")

    import torch
    if args.threads:
        torch.set_num_threads(args.threads)
    from sentence_transformers import SentenceTransformer
    device = ("cuda" if torch.cuda.is_available() else "cpu") if args.device == "auto" else args.device
    model = SentenceTransformer(args.model, device=device)
    if device.startswith("cuda"):
        model.half()  # fp16 on GPU: ~2x faster, same retrieval quality
    model.max_seq_length = args.max_seq
    dim = model.get_sentence_embedding_dimension()

    partial = outdir / f"embeddings{suffix}.partial.npy"
    done = np.load(partial) if partial.exists() else np.zeros((0, dim), dtype=np.float16)
    print(f"model {args.model} dim={dim} device={device}; resuming at {len(done)}", flush=True)
    out = [done]
    t0, n0 = time.time(), len(done)
    texts = [p_prefix + c["head"] + "\n" + c["text"] for c in chunks]
    for s in range(len(done), len(texts), args.save_every):
        block = texts[s: s + args.save_every]
        emb = model.encode(block, batch_size=args.batch, normalize_embeddings=True,
                           show_progress_bar=False, convert_to_numpy=True).astype(np.float16)
        out.append(emb)
        cur = np.concatenate(out)
        np.save(partial, cur)
        rate = (len(cur) - n0) / (time.time() - t0)
        eta = (len(texts) - len(cur)) / rate if rate else 0
        print(f"  {len(cur)}/{len(texts)} passages  {rate:.1f}/s  eta {eta/60:.0f} min", flush=True)

    emb = np.concatenate(out)
    np.save(outdir / f"embeddings{suffix}.npy", emb)
    (outdir / f"embeddings{suffix}.json").write_text(json.dumps({
        "model": args.model, "dim": dim, "n": len(emb), "max_seq": args.max_seq,
        "query_prefix": q_prefix, "passage_prefix": p_prefix,
        "chunking": "paragraph pack ~900 chars, 1 short-paragraph overlap, header prefix"},
        ensure_ascii=False, indent=1))
    partial.unlink(missing_ok=True)
    print(f"done: {emb.shape} in {(time.time() - t0)/60:.1f} min")


if __name__ == "__main__":
    main()
