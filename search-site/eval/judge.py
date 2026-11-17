#!/usr/bin/env python3
"""Relevance judging for meaning-type eval cases (pooled, LLM judges).

The eval scores a case only by its one source letter. For questions and topics other
letters in the top 10 are often just as relevant. Judges (Sonnet agents with no context,
no engine access) grade each pooled candidate 0/1/2; run_eval.py then reports any_rel@10
and nDCG@10 next to the strict pass rate.

    # 1. pool: top --depth of every judged run in one or more results files
    python search-site/eval/judge.py prep --results eval/results/A.json [B.json] --set holdout3 --out DIR --parts 6
    # 2. judges write DIR/judged_<n>.jsonl  ({"case", "letter_id", "rel", "reason"})
    # 3. merge into eval/qrels/<set>.jsonl (adds to existing judgments, never overwrites a case/letter pair)
    python search-site/eval/judge.py merge --dir DIR --set holdout3

Grades: 2 = answers the query / is what the searcher wants; 1 = partly relevant (same specific
subject, partial answer); 0 = not relevant. The source letter is not shown (always 2).
"""
import argparse
import glob
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

EVAL = Path(__file__).resolve().parent
DB = Path(os.environ.get("IGROT_DATA", EVAL.parent / "data")) / "search.db"
JUDGED_TYPES = {"question", "paraphrase", "crosslang", "english", "topic"}


def prep(args):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    have = load_qrels(args.set)
    if args.cases:  # only the cases of one cases file (results files of run_eval.py hold every set it ran)
        args.cases = {json.loads(l)["id"] for l in open(args.cases, encoding="utf-8") if l.strip()}
    pool = defaultdict(set)
    query, typ, expected = {}, {}, {}
    for f in args.results:
        for r in json.load(open(f, encoding="utf-8"))["results"]:
            if r["type"] not in JUDGED_TYPES or (args.cases and r["id"].split("@")[0] not in args.cases):
                continue
            base = r["id"].split("@")[0]
            query[base], typ[base], expected[base] = r["query"], r["type"], set(r["expected_ids"])
            for lid in r["top"][: args.depth]:
                if lid not in expected[base] and lid not in have.get(base, {}):
                    pool[base].add(lid)
    items = [(b, sorted(pool[b])) for b in sorted(pool) if pool[b]]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n_pairs = sum(len(c) for _, c in items)
    per = max(1, -(-n_pairs // args.parts))
    part, cur, size = 1, [], 0
    for base, cands in items:
        block = [f"=== CASE {base}\nסוג: {typ[base]}\nשאילתה: {query[base]}\n"]
        for lid in cands:
            r = con.execute("SELECT volume_heb, letter_number_heb, hebrew_year_heb, recipient_info, full_text "
                            "FROM letters WHERE id=?", (lid,)).fetchone()
            txt = (r["full_text"] or "")[: args.chars].replace("\n\n", "\n")
            block.append(f"--- LETTER {lid} (כרך {r['volume_heb']}, אגרת {r['letter_number_heb']}, "
                         f"{r['hebrew_year_heb'] or ''}; נמען: {(r['recipient_info'] or '')[:80]})\n{txt}\n")
        cur.append("\n".join(block))
        size += len(cands)
        if size >= per:
            (out / f"to_judge_{part}.txt").write_text("\n\n".join(cur), encoding="utf-8")
            part, cur, size = part + 1, [], 0
    if cur:
        (out / f"to_judge_{part}.txt").write_text("\n\n".join(cur), encoding="utf-8")
    else:
        part -= 1
    print(f"{len(items)} cases, {n_pairs} (case, letter) pairs -> {part} files in {out}")


def load_qrels(name):
    p = EVAL / "qrels" / f"{name}.jsonl"
    have = defaultdict(dict)
    if p.exists():
        for line in open(p, encoding="utf-8"):
            if line.strip():
                q = json.loads(line)
                have[q["case"]][int(q["letter_id"])] = int(q["rel"])
    return have


def merge(args):
    have = load_qrels(args.set)
    p = EVAL / "qrels" / f"{args.set}.jsonl"
    p.parent.mkdir(exist_ok=True)
    added = 0
    from collections import Counter
    grades = Counter()
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        for f in sorted(glob.glob(str(Path(args.dir) / "judged_*.jsonl"))):
            for line in open(f, encoding="utf-8"):
                if not line.strip():
                    continue
                j = json.loads(line)
                case, lid, rel = j["case"], int(j["letter_id"]), int(j["rel"])
                if lid in have.get(case, {}):
                    continue
                have[case][lid] = rel
                grades[rel] += 1
                fh.write(json.dumps({"case": case, "letter_id": lid, "rel": rel, "judge": args.judge,
                                     "reason": j.get("reason", "")}, ensure_ascii=False) + "\n")
                added += 1
    print(f"added {added} judgments to {p}; grades {dict(grades)}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("prep")
    a.add_argument("--results", nargs="+", required=True)
    a.add_argument("--set", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--depth", type=int, default=10)
    a.add_argument("--chars", type=int, default=900)
    a.add_argument("--parts", type=int, default=6)
    a.add_argument("--cases", help="cases .jsonl: pool only its cases")
    m = sub.add_parser("merge")
    m.add_argument("--dir", required=True)
    m.add_argument("--set", required=True)
    m.add_argument("--judge", default="sonnet")
    args = ap.parse_args()
    prep(args) if args.cmd == "prep" else merge(args)


if __name__ == "__main__":
    main()
