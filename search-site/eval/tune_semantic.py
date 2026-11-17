#!/usr/bin/env python3
"""Offline tuning of the semantic ranking (letter aggregation, query feedback,
light lexical tie-break) on the eval cases.

    /root/venv-igrot/bin/python search-site/eval/tune_semantic.py [--emb <folder>]
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
ap = argparse.ArgumentParser()
ap.add_argument("--emb")
args = ap.parse_args()
if args.emb:
    os.environ["IGROT_EMB"] = args.emb
import search  # noqa: E402

cases = [json.loads(line) for f in sorted(glob.glob(os.environ.get("CASES", str(ROOT / "eval/cases/*.jsonl"))))
         for line in open(f, encoding="utf-8")]
cases = [c for c in cases if c["type"] in ("paraphrase", "question", "crosslang")]
idx = search.semantic_index()
E, CL = idx.emb, idx.chunk_letter
print("model", idx.info["model"], "cases", len(cases))
qv = {c["id"]: idx.encode(c["query"]) for c in cases}
lex = {c["id"]: search.coverage_ids(c["query"], {}, True, 300) for c in cases}


def letters_from_scores(sc, agg="max", n=300):
    order = np.argsort(-sc)[: n * 8]
    best = defaultdict(list)
    for i in order:
        lid = int(CL[i])
        if len(best[lid]) < 2:
            best[lid].append(float(sc[i]))
    if agg == "max":
        val = {lid: v[0] for lid, v in best.items()}
    else:  # max + a * second
        a = float(agg.split("=")[1])
        val = {lid: v[0] + a * (v[1] if len(v) > 1 else 0) for lid, v in best.items()}
    return [lid for lid, _ in sorted(val.items(), key=lambda x: -x[1])][:n]


def run(c, agg="max", prf=0.0, prf_k=3, lex_w=0.0, k=10):
    q = qv[c["id"]]
    sc = E @ q
    if prf:
        top = np.argsort(-sc)[:prf_k]
        q2 = q + prf * E[top].mean(axis=0)
        q2 /= np.linalg.norm(q2)
        sc = E @ q2
    ids = letters_from_scores(sc, agg)
    if not lex_w:
        return ids
    s = defaultdict(float)
    for r, lid in enumerate(ids):
        s[lid] += 1 / (k + r + 1)
    for r, lid in enumerate(lex[c["id"]]):
        s[lid] += lex_w / (k + r + 1)
    return [lid for lid, _ in sorted(s.items(), key=lambda x: -x[1])]


variants = {"max (current)": {}}
for a in (0.2, 0.5):
    variants[f"max+{a}*2nd"] = {"agg": f"a={a}"}
for p in (0.3, 0.7):
    for kk in (3, 5):
        variants[f"prf {p} top{kk}"] = {"prf": p, "prf_k": kk}
for lw in (0.25, 0.5):
    variants[f"lex tie-break {lw}"] = {"lex_w": lw}
    variants[f"lex {lw} + max+0.2*2nd"] = {"lex_w": lw, "agg": "a=0.2"}
rows = []
for name, kw in variants.items():
    by = defaultdict(lambda: [0, 0])
    for c in cases:
        ok = c["expected_ids"][0] in run(c, **kw)[:10]
        for t in (c["type"], "ALL"):
            by[t][0] += ok
            by[t][1] += 1
    rows.append((by["ALL"][0] / by["ALL"][1], name, {t: f"{100 * a / n:.0f}" for t, (a, n) in by.items()}))
for pct, name, d in sorted(rows, reverse=True):
    print(f"{100 * pct:5.1f}  {name:<28} {d}")
