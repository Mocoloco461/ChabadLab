#!/usr/bin/env python3
"""Side-by-side comparison of eval runs from history.csv.

    python search-site/eval/compare.py "MAIN+dev:"            # every run whose label contains the text
    python search-site/eval/compare.py "HOLDOUT3" --last 3
"""
import argparse
import csv
import json
from pathlib import Path

EVAL = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("--last", type=int, default=6)
    args = ap.parse_args()
    rows = [r for r in csv.reader(open(EVAL / "history.csv", encoding="utf-8")) if len(r) > 3 and args.pattern in r[1]]
    rows = rows[-args.last:]
    if not rows:
        print("no runs match")
        return
    types = sorted({t for r in rows for t in json.loads(r[4])})
    head = f"{'run':<44}{'total':>7}" + "".join(f"{t[:9]:>10}" for t in types) + f"{'anyrel':>8}{'mrr':>7}"
    print(head)
    for r in rows:
        bt = json.loads(r[4])
        m = json.loads(r[7]) if len(r) > 7 and r[7] else {}
        print(f"{r[1][:43]:<44}{r[3]:>7}" + "".join(f"{bt.get(t, ''):>10}" for t in types)
              + f"{m.get('any_rel10_pct', ''):>8}{m.get('mrr', ''):>7}")


if __name__ == "__main__":
    main()
