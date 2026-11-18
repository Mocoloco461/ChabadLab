#!/usr/bin/env python3
"""Write random letters (seeded) for test-case generation.

    python3 search-site/eval/sample_letters.py --n 40 --seed 1 --out search-site/eval/samples/batch1.jsonl
"""
import argparse
import json
import random
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--min-chars", type=int, default=400)
    ap.add_argument("--lang", help="only this language (he/yi)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--exclude-used", action="store_true",
                    help="skip letters already used in eval/cases, eval/holdout or eval/samples")
    args = ap.parse_args()
    con = sqlite3.connect(ROOT / "data/search.db")
    con.row_factory = sqlite3.Row
    where = "n_chars >= ?"
    params = [args.min_chars]
    if args.lang:
        where += " AND language = ?"
        params.append(args.lang)
    ids = [r[0] for r in con.execute(f"SELECT id FROM letters WHERE {where} ORDER BY id", params)]
    if args.exclude_used:
        import glob
        used = set()
        ev = ROOT / "eval"
        for f in glob.glob(str(ev / "cases/*.jsonl")) + glob.glob(str(ev / "holdout/*.jsonl")):
            for line in open(f, encoding="utf-8"):
                if line.strip():
                    used.update(json.loads(line)["expected_ids"])
        for f in glob.glob(str(ev / "samples/*.jsonl")):
            if Path(f).resolve() == Path(args.out).resolve():
                continue
            for line in open(f, encoding="utf-8"):
                if line.strip():
                    used.add(json.loads(line)["id"])
        ids = [i for i in ids if i not in used]
    random.Random(args.seed).shuffle(ids)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        for lid in ids[: args.n]:
            r = con.execute("SELECT id, volume, volume_heb, letter_number, letter_number_heb, date_raw, "
                            "hebrew_year, hebrew_year_heb, gregorian_date, recipient_info, language, full_text "
                            "FROM letters WHERE id=?", (lid,)).fetchone()
            d = dict(r)
            d["full_text"] = d["full_text"][:3000]
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"wrote {args.n} letters -> {args.out}")


if __name__ == "__main__":
    main()
