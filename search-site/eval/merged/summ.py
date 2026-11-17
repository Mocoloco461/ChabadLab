#!/usr/bin/env python3
"""Summaries of run_eval.py results: strict / MRR / any_rel@10 / unjudged per set (main = gen1-3, dev =
dev_newtypes) and per type; and of run_synth.py runs (strict + judged-sample graded metrics).

    python summ.py <results.json | synth runs .jsonl> ...
"""
import glob
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

EVAL = Path(__file__).resolve().parent.parent
QRELS = defaultdict(dict)  # current judgments (they grow over time): metrics are recomputed from them
for f in glob.glob(str(EVAL / "qrels" / "*.jsonl")):
    for line in open(f, encoding="utf-8"):
        if line.strip():
            q = json.loads(line)
            QRELS[q["case"]][int(q["letter_id"])] = max(int(q["rel"]), QRELS[q["case"]].get(int(q["letter_id"]), 0))


def remetric(r):
    base = r["id"].split("@")[0]
    grades = dict(QRELS.get(base, {}))
    for x in r["expected_ids"]:
        grades[x] = 2
    top10 = r["top"][:10]
    dcg = sum((2 ** grades.get(x, 0) - 1) / math.log2(i + 2) for i, x in enumerate(top10))
    ideal = sorted(grades.values(), reverse=True)[:10]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    r["metrics"] = {"rr": 1 / r["rank"] if r["rank"] else 0.0,
                    "any_rel10": any(grades.get(x, 0) >= 1 for x in top10),
                    "ndcg10": dcg / idcg if idcg else 0.0,
                    "unjudged10": sum(1 for x in top10 if x not in grades) / max(len(top10), 1)}


DEV = {json.loads(l)["id"] for l in open(EVAL / "cases" / "dev_newtypes.jsonl", encoding="utf-8") if l.strip()}


def agg(rs):
    n = len(rs)
    if not n:
        return {}
    errs = sum(1 for r in rs if r.get("error"))
    return {"n": n, "strict": round(100 * sum(r["pass"] for r in rs) / n, 1),
            "mrr": round(sum(r["metrics"]["rr"] for r in rs) / n, 3),
            "any_rel": round(100 * sum(r["metrics"]["any_rel10"] for r in rs) / n, 1),
            "ndcg": round(sum(r["metrics"]["ndcg10"] for r in rs) / n, 3),
            "unjudged": round(sum(r["metrics"]["unjudged10"] for r in rs) / n, 2), "errors": errs}


def evalfile(p, by_type=False):
    d = json.load(open(p, encoding="utf-8"))
    rs = d["results"]
    for r in rs:
        remetric(r)
    sets = defaultdict(list)
    for r in rs:
        base = r["id"].split("@")[0]
        sets["dev" if base in DEV else "main"].append(r)
    out = {"label": d["summary"]["label"], "all": agg(rs)}
    if len(sets) > 1:
        out.update({k: agg(v) for k, v in sorted(sets.items())})
    if by_type:
        bt = defaultdict(list)
        for r in rs:
            bt[r["type"]].append(r)
        out["by_type"] = {t: agg(v)["strict"] for t, v in sorted(bt.items())}
        bm = defaultdict(list)
        for r in rs:
            bm[r["mode"]].append(r)
        out["by_mode"] = {t: agg(v)["strict"] for t, v in sorted(bm.items())}
    return out


if __name__ == "__main__":
    bt = "--types" in sys.argv
    for p in [a for a in sys.argv[1:] if not a.startswith("--")]:
        print(json.dumps(evalfile(p, bt), ensure_ascii=False))
