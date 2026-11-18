#!/usr/bin/env python3
"""Automatic relevance labels for "name" queries.

A name query ("שמטוב לונדון", "גרשון סגל כפר חב"ד") is looking for letters to that person or
institution; every letter to them is a correct answer, not only the one the case was written from.
Rule: take the query words that occur in the source letter's recipient line (the name part;
context words such as "ישיבה" are ignored); every letter whose recipient line contains all of
them gets grade 2. Letters whose text mentions them all get nothing (left to the judges).

    python search-site/eval/name_qrels.py   # -> eval/qrels/names_auto.jsonl (rebuilt from scratch)
"""
import glob
import json
import re
import sqlite3
from pathlib import Path

EVAL = Path(__file__).resolve().parent
DB = EVAL.parent / "data" / "search.db"
_strip = re.compile(r'[֑-ׇ"״\'׳.,;:()\[\]\-–]')


def norm(t):
    return " ".join(_strip.sub(" ", t or "").split())


def main():
    con = sqlite3.connect(DB)
    recips = {lid: norm(r) for lid, r in con.execute("SELECT id, recipient_info FROM letters")}
    out, n_cases = [], 0
    files = sorted(glob.glob(str(EVAL / "cases" / "*.jsonl")) + glob.glob(str(EVAL / "holdout" / "*.jsonl")))
    for f in files:
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            c = json.loads(line)
            if c["type"] != "name":
                continue
            src = recips.get(c["expected_ids"][0], "")
            words = [w for w in norm(c["query"]).split() if len(w) > 2 and w in src]
            if not words:
                continue
            n_cases += 1
            for lid, r in recips.items():
                if lid not in c["expected_ids"] and all(w in r for w in words):
                    out.append({"case": c["id"], "letter_id": lid, "rel": 2, "judge": "auto:recipient",
                                "reason": "נמען: " + " ".join(words)})
    p = EVAL / "qrels" / "names_auto.jsonl"
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        for q in out:
            fh.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"{n_cases} name cases, {len(out)} extra relevant letters -> {p}")


if __name__ == "__main__":
    main()
