#!/usr/bin/env python3
"""Builds eval/holdout/holdout2.jsonl from the hand-written case list in holdout2_src.py.

The cases were written by reading only the sampled letters (eval/samples/holdout2*.jsonl,
seeds 303/304, letters never used in earlier sets) - the engine was not queried while writing.

    python search-site/eval/holdout/build_holdout2.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from holdout2_src import CASES  # noqa: E402

SAMPLES = {"he": HERE.parent / "samples/holdout2.jsonl", "yi": HERE.parent / "samples/holdout2_yi.jsonl"}
letters = {k: [json.loads(l) for l in open(p, encoding="utf-8")] for k, p in SAMPLES.items()}

SEM = {"paraphrase", "question", "crosslang"}
out, seen = [], set()
for src, i, typ, query, *rest in CASES:
    r = letters[src][i]
    base = f"h2-{r['letter_number']}-{typ}"
    cid, n = base, 2
    while cid in seen:
        cid, n = f"{base}{n}", n + 1
    seen.add(cid)
    c = {"id": cid, "type": typ}
    if typ in SEM:
        c["modes"] = ["semantic", "hybrid"]
    else:
        c["mode"] = "text"
    c["query"] = query
    k = 10
    if typ == "filter":
        recip = rest[0]
        y = r["hebrew_year"]
        c["filters"] = {"recipient": recip, "year_from": y, "year_to": y}
        k = 20
    c["k"] = k
    c["expected_ids"] = [r["id"]]
    note = rest[1] if typ == "filter" and len(rest) > 1 else (rest[0] if rest and typ != "filter" else "")
    if src == "yi":
        note = ("אידיש; " + note).strip("; ")
    c["note"] = note
    out.append(c)

with open(HERE / "holdout2.jsonl", "w", encoding="utf-8", newline="\n") as fh:
    for c in out:
        fh.write(json.dumps(c, ensure_ascii=False) + "\n")
runs = sum(2 if c["type"] in SEM else 1 for c in out)
print(f"{len(out)} cases, {runs} runs, {len({c['expected_ids'][0] for c in out})} letters")
