#!/usr/bin/env python3
"""questions_test.jsonl -> synth_test.jsonl, an eval set in run_eval.py format.

    python search-site/eval/synth/build_set.py [--exclude-flagged]

One case per generated item; source letter = the expected id; run in semantic and hybrid.
Items listed in qc_flags.json (bad questions found in the quality check) are dropped with --exclude-flagged.
"""
import argparse
import json
from pathlib import Path

SYN = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exclude-flagged", action="store_true")
    a = ap.parse_args()
    flagged = set()
    if a.exclude_flagged and (SYN / "qc_flags.json").exists():
        flagged = set(json.loads((SYN / "qc_flags.json").read_text(encoding="utf-8")))
    split = json.loads((SYN / "split.json").read_text(encoding="utf-8"))
    test = set(split["test"])
    n = skipped = 0
    with open(SYN / "synth_test.jsonl", "w", encoding="utf-8") as out:
        for line in open(SYN / "questions_test.jsonl", encoding="utf-8"):
            d = json.loads(line)
            assert d["id"] in test, f"letter {d['id']} is not in the TEST pool"
            for i, it in enumerate(d["items"], 1):
                cid = f"syn-{d['id']}-{i}"
                if cid in flagged:
                    skipped += 1
                    continue
                kind = it["kind"] if it["kind"] in ("topic", "question", "paraphrase") else "question"
                case = {"id": cid, "type": f"synth-{kind}", "query": it["text"], "modes": ["semantic", "hybrid"],
                        "expected_ids": [d["id"]], "k": 10, "lang": d["language"],
                        "note": "synthetic (LLM, letter text only): " + d.get("summary", "")[:120]}
                out.write(json.dumps(case, ensure_ascii=False) + "\n")
                n += 1
    print(f"wrote {n} cases ({skipped} flagged dropped) -> synth_test.jsonl")


if __name__ == "__main__":
    main()
