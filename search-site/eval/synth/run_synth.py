#!/usr/bin/env python3
"""Run synth_test.jsonl against a server; record the rank of the source letter; print metrics.

    python search-site/eval/synth/run_synth.py --server http://127.0.0.1:8767 --label "baseline"
    python search-site/eval/synth/run_synth.py --report runs/baseline.jsonl     # metrics only

Writes eval/synth/runs/<label>.jsonl: one line per (case, mode) with rank (1-based, up to 50) and top-50 ids.
Metrics: strict = source letter only (hit@1/@10, MRR@10). Graded = with judgments.jsonl from judge.py:
letters ranked above the source got a 0/1/2 relevance grade; the source counts as 2; unjudged = 0.
  ghit@10  = a grade-2 letter (source or other) in the top 10
  nDCG@10  = gains 2^g-1, ideal = best ordering of all known grades for that question
"""
import argparse
import json
import math
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SYN = Path(__file__).resolve().parent
DEPTH = 50


def call(server, q, mode):
    url = f"{server}/api/search?" + urllib.parse.urlencode({"q": q, "mode": mode, "size": DEPTH})
    with urllib.request.urlopen(url, timeout=300) as r:
        return [x["id"] for x in json.load(r)["results"]]


def load_judgments():
    j = {}
    p = SYN / "judgments.jsonl"
    if p.exists():
        for line in open(p, encoding="utf-8"):
            d = json.loads(line)
            j[(d["case"], d["letter"])] = d["grade"]
    return j


def dcg(gains):
    return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(gains))


def report(rows, judg):
    # pool of known grades per case (both modes share the question)
    pool = defaultdict(dict)
    for r in rows:
        pool[r["case"]][r["source"]] = 2
        for lid in r["top"][:10]:
            if (r["case"], lid) in judg:
                pool[r["case"]][lid] = judg[(r["case"], lid)]
    groups = defaultdict(list)
    for r in rows:
        rk = r["rank"]
        grades = [2 if lid == r["source"] else judg.get((r["case"], lid), 0) for lid in r["top"][:10]]
        ideal = sorted(pool[r["case"]].values(), reverse=True)[:10]
        m = {"hit1": rk == 1, "hit10": rk is not None and rk <= 10, "mrr": 1 / rk if rk and rk <= 10 else 0,
             "ghit10": 2 in grades, "ghit1": bool(grades) and grades[0] == 2,
             "ndcg": dcg(grades) / dcg(ideal) if ideal else 0}
        for g in ("all", f"mode={r['mode']}", f"type={r['type']}", f"lang={r['lang']}"):
            groups[g].append(m)
    print(f"{'group':<24}{'n':>6}{'hit@1':>8}{'hit@10':>8}{'MRR@10':>8}{'ghit@1':>8}{'ghit@10':>9}{'nDCG@10':>9}")
    out = {}
    for g in sorted(groups, key=lambda x: (x != "all", x)):
        ms = groups[g]
        n = len(ms)
        avg = {k: sum(m[k] for m in ms) / n for k in ms[0]}
        out[g] = {"n": n, **{k: round(v, 4) for k, v in avg.items()}}
        print(f"{g:<24}{n:>6}{100*avg['hit1']:>7.1f}%{100*avg['hit10']:>7.1f}%{avg['mrr']:>8.3f}"
              f"{100*avg['ghit1']:>7.1f}%{100*avg['ghit10']:>8.1f}%{avg['ndcg']:>9.3f}")
    n_need = sum(1 for r in rows for lid in (r["top"][:min(r["rank"] - 1, 10)] if r["rank"] else r["top"][:10])
                 if (r["case"], lid) not in judg)
    print(f"(judgments: {len(judg)} pairs; {n_need} above-source pairs still unjudged)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server")
    ap.add_argument("--label")
    ap.add_argument("--cases", default=str(SYN / "synth_test.jsonl"))
    ap.add_argument("--report", help="existing runs file: print metrics only")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    if a.report:
        rows = [json.loads(l) for l in open(a.report, encoding="utf-8")]
    else:
        cases = [json.loads(l) for l in open(a.cases, encoding="utf-8") if l.strip()]
        jobs = [(c, m) for c in cases for m in c["modes"]]
        t0 = time.time()

        def run(job):
            c, m = job
            try:
                top = call(a.server, c["query"], m)
                err = None
            except Exception as e:  # noqa: BLE001
                top, err = [], str(e)[:200]
            src = c["expected_ids"][0]
            rank = top.index(src) + 1 if src in top else None
            return {"case": c["id"], "mode": m, "type": c["type"], "lang": c.get("lang"), "query": c["query"],
                    "source": src, "rank": rank, "top": top, "error": err}

        with ThreadPoolExecutor(a.workers) as ex:
            rows = list(ex.map(run, jobs))
        errs = sum(1 for r in rows if r["error"])
        (SYN / "runs").mkdir(exist_ok=True)
        slug = re.sub(r"[^\w.-]+", "_", a.label).strip("_")
        p = SYN / "runs" / f"{slug}.jsonl"
        with open(p, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{len(rows)} runs in {time.time() - t0:.0f}s, {errs} errors -> {p}")
    res = report(rows, load_judgments())
    if not a.report:
        (SYN / "runs" / f"{slug}.summary.json").write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
