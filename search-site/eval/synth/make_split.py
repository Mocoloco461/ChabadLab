#!/usr/bin/env python3
"""Fixed TEST / TRAIN split for the synthetic-query pipeline (written once, then never changed).

    python search-site/eval/synth/make_split.py [--test 600] [--seed 7001]

* Excluded: every letter already used in eval/cases, eval/holdout/*, eval/samples (same rule as
  sample_letters.py --exclude-used).
* TEST: random letters with n_chars >= 400 (very short letters rarely answer a real question).
* TRAIN: every other letter (any length) – used only for doc2query, never for measuring.
Writes eval/synth/split.json.
"""
import argparse
import glob
import json
import os
import random
import sqlite3
from pathlib import Path

SYN = Path(__file__).resolve().parent
EV = SYN.parent
DATA = Path(os.environ.get("IGROT_DATA", EV.parent / "data"))


def used_ids():
    used = set()
    for f in glob.glob(str(EV / "cases/*.jsonl")) + glob.glob(str(EV / "holdout/*.jsonl")):
        for line in open(f, encoding="utf-8"):
            if line.strip():
                used.update(json.loads(line)["expected_ids"])
    for f in glob.glob(str(EV / "samples/*.jsonl")):
        for line in open(f, encoding="utf-8"):
            if line.strip():
                used.add(json.loads(line)["id"])
    return used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", type=int, default=600)
    ap.add_argument("--seed", type=int, default=7001)
    ap.add_argument("--min-chars", type=int, default=400)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    out = SYN / "split.json"
    if out.exists() and not a.force:
        raise SystemExit(f"{out} exists – the split is fixed; pass --force only if you mean it")
    con = sqlite3.connect(DATA / "search.db")
    rows = con.execute("SELECT id, n_chars, language FROM letters ORDER BY id").fetchall()
    used = used_ids()
    free = [r for r in rows if r[0] not in used]
    elig = [r[0] for r in free if r[1] >= a.min_chars]
    rnd = random.Random(a.seed)
    rnd.shuffle(elig)
    test = sorted(elig[: a.test])
    ts = set(test)
    train = sorted(r[0] for r in free if r[0] not in ts)
    lang = dict((r[0], r[2]) for r in rows)
    meta = {
        "seed": a.seed, "min_chars_test": a.min_chars, "n_letters": len(rows),
        "n_excluded_used": len(rows) - len(free), "n_test": len(test), "n_train": len(train),
        "test_by_lang": {l: sum(lang[i] == l for i in test) for l in sorted(set(lang.values()))},
    }
    out.write_text(json.dumps({"meta": meta, "excluded_used": sorted(used), "test": test, "train": train}),
                   encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
