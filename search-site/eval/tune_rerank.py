#!/usr/bin/env python3
"""Offline tuning of the cross-encoder reranker (no HTTP).

    python search-site/eval/tune_rerank.py --model BAAI/bge-reranker-v2-m3 [--emb <folder[,folder]>]
    CASES='search-site/eval/holdout/*.jsonl' python ...   # measure only, never tune on it

For every semantic-type case (paraphrase / question / crosslang) in both modes
(semantic, hybrid) it takes the first-stage ranking of the app, scores the top
--depth letters once with the cross-encoder, and then evaluates merge variants
(how many letters to rerank, RRF weights, passages per letter) offline.
"""
import argparse
import glob
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="BAAI/bge-reranker-v2-m3")
ap.add_argument("--emb")
ap.add_argument("--depth", type=int, default=100)
ap.add_argument("--chunks", default="1,2", help="passages per letter to try")
args = ap.parse_args()
if args.emb:
    os.environ["IGROT_EMB"] = args.emb
os.environ["IGROT_RERANK"] = args.model

import search  # noqa: E402

cases = []
for f in sorted(glob.glob(os.environ.get("CASES", str(ROOT / "eval/cases/*.jsonl")))):
    for line in open(f, encoding="utf-8"):
        c = json.loads(line)
        if c["type"] in ("paraphrase", "question", "crosslang"):
            for m in c.get("modes") or [c.get("mode", "semantic")]:
                cases.append(dict(c, mode=m))

idx = search.semantic_index()
search.reranker()  # load once
print("emb:", idx.info["model"], "| reranker:", args.model, "| runs:", len(cases), flush=True)

first, ce = {}, {}
t0 = time.time()
n_pairs = 0
chunk_opts = [int(x) for x in args.chunks.split(",")]
for i, c in enumerate(cases):
    key = (c["id"], c["mode"])
    filters = c.get("filters") or {}
    if c["mode"] == "semantic":
        ranked, _, qv = search.semantic_ranked(c["query"], filters)
    else:
        ranked, _, _, qv = search.hybrid_ranked(c["query"], filters)
    first[key] = ranked
    lids = [lid for lid, _ in ranked[: args.depth]]
    for nc in chunk_opts:
        ce[key, nc] = search.rerank_scores(c["query"], lids, idx, qv, n_chunks=nc)
        n_pairs += len(lids)
dt = time.time() - t0
print(f"scored {n_pairs} pairs in {dt:.0f}s ({n_pairs / dt:.0f} pairs/s)", flush=True)


def evaluate(n, k, wf, wc, nc):
    by = defaultdict(lambda: [0, 0])
    for c in cases:
        key = (c["id"], c["mode"])
        ranked = first[key]
        if n:
            ranked = search.rerank_merge(ranked, ce[key, nc][:n], n=n, k=k, w_first=wf, w_ce=wc)
        ok = c["expected_ids"][0] in [lid for lid, _ in ranked[:10]]
        for t in (c["type"], c["mode"], "ALL"):
            by[t][0] += ok
            by[t][1] += 1
    return by


rows = []
variants = {"no rerank": (0, 10, 1, 1, 1)}
for nc in chunk_opts:
    for n in (20, 30, 50, 100):
        if n > args.depth:
            continue
        variants[f"pure CE n={n} ch={nc}"] = (n, 10, 0, 1, nc)
        for k in (5, 10, 30):
            for wf, wc in ((1, 1), (1, 2), (2, 1)):
                variants[f"rrf n={n} k={k} first={wf} ce={wc} ch={nc}"] = (n, k, wf, wc, nc)
for name, v in variants.items():
    by = evaluate(*v)
    rows.append((by["ALL"][0] / by["ALL"][1], name,
                 {t: f"{100 * a / m:.0f}" for t, (a, m) in sorted(by.items()) if t != "ALL"}))
for pct, name, d in sorted(rows, reverse=True)[:25]:
    print(f"{100 * pct:5.1f}  {name:<36} {d}")
print("...")
for pct, name, d in rows:
    if name == "no rerank" or name == "rrf n=50 k=10 first=1 ce=1 ch=1":
        print(f"{100 * pct:5.1f}  {name:<36} {d}")
