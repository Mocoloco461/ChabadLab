#!/usr/bin/env python3
"""
Run the search test cases against the running server and score them.

    python3 search-site/eval/run_eval.py [--cases search-site/eval/cases/*.jsonl]
                                         [--server http://localhost:8765] [--label "what changed"]

Case format (one JSON object per line):
    {"id": "b1-007-phrase", "type": "phrase|keywords|paraphrase|question|filter|number|crosslang",
     "query": "...", "mode": "text|semantic|hybrid", "filters": {"year_from": 5716, ...},
     "expected_ids": [1200880205], "k": 10, "note": "why this should find it"}

A case passes when any expected id is in the top k results (default k=10).
A case with "modes": ["semantic", "hybrid"] is run once per mode (ids get a suffix).

Outputs:
    search-site/eval/results/<timestamp>.json   every case with rank + top ids
    search-site/eval/history.csv                one line per run: overall and per-type pass rate
"""
import argparse
import csv
import glob
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

EVAL = Path(__file__).resolve().parent


def call(server, q, mode, filters, size):
    params = {"q": q, "mode": mode, "size": size}
    params.update({k: v for k, v in (filters or {}).items() if v not in (None, "")})
    url = f"{server}/api/search?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r)


def expand(cases):
    for c in cases:
        modes = c.get("modes") or [c.get("mode", "text")]
        for m in modes:
            d = dict(c)
            d["mode"] = m
            if len(modes) > 1:
                d["id"] = f"{c['id']}@{m}"
            yield d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="*", default=sorted(glob.glob(str(EVAL / "cases" / "*.jsonl"))))
    ap.add_argument("--server", default="http://localhost:8765")
    ap.add_argument("--label", default="")
    ap.add_argument("--only-type")
    args = ap.parse_args()

    cases = []
    for f in args.cases:
        for line in open(f, encoding="utf-8"):
            if line.strip():
                cases.append(json.loads(line))
    cases = list(expand(cases))
    if args.only_type:
        cases = [c for c in cases if c["type"] == args.only_type]

    results = []
    by_type = defaultdict(lambda: [0, 0])
    by_mode = defaultdict(lambda: [0, 0])
    t0 = time.time()
    for c in cases:
        k = c.get("k", 10)
        try:
            d = call(args.server, c["query"], c["mode"], c.get("filters"), max(k, 20))
            ids = [r["id"] for r in d["results"]]
            err = d.get("error")
        except Exception as e:  # noqa: BLE001
            ids, err, d = [], str(e), {}
        exp = set(c["expected_ids"])
        rank = next((i + 1 for i, x in enumerate(ids) if x in exp), None)
        ok = rank is not None and rank <= k
        by_type[c["type"]][0] += ok
        by_type[c["type"]][1] += 1
        by_mode[c["mode"]][0] += ok
        by_mode[c["mode"]][1] += 1
        results.append({**c, "pass": ok, "rank": rank, "total": d.get("total"), "top": ids[:k], "error": err})

    n_ok = sum(r["pass"] for r in results)
    overall = 100 * n_ok / len(results) if results else 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (EVAL / "results").mkdir(exist_ok=True)
    out = EVAL / "results" / f"{stamp}.json"
    summary = {
        "stamp": stamp, "label": args.label, "cases": len(results), "passed": n_ok,
        "overall_pct": round(overall, 1),
        "by_type": {t: {"passed": a, "n": n, "pct": round(100 * a / n, 1)} for t, (a, n) in sorted(by_type.items())},
        "by_mode": {m: {"passed": a, "n": n, "pct": round(100 * a / n, 1)} for m, (a, n) in sorted(by_mode.items())},
        "seconds": round(time.time() - t0, 1),
    }
    out.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    hist = EVAL / "history.csv"
    new = not hist.exists()
    with open(hist, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["stamp", "label", "cases", "overall_pct", "by_type", "by_mode", "results_file"])
        w.writerow([stamp, args.label, len(results), summary["overall_pct"],
                    json.dumps({t: v["pct"] for t, v in summary["by_type"].items()}, ensure_ascii=False),
                    json.dumps({m: v["pct"] for m, v in summary["by_mode"].items()}),
                    out.name])

    print(json.dumps(summary, ensure_ascii=False, indent=1))
    fails = [r for r in results if not r["pass"]]
    print(f"\n{len(fails)} failures (first 15):")
    for r in fails[:15]:
        print(f"  {r['id']:<28} [{r['mode']}] rank={r['rank']} total={r['total']} q={r['query'][:70]!r}"
              + (f" ERR {r['error']}" if r["error"] else ""))
    print(f"\nfull report: {out}")


if __name__ == "__main__":
    main()
