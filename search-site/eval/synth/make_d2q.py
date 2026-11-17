#!/usr/bin/env python3
"""questions_train.jsonl -> doc2query passages for build_embeddings_api.py --extra.

    python search-site/eval/synth/make_d2q.py --variant q    -> d2q_q.jsonl    (the questions only)
    python search-site/eval/synth/make_d2q.py --variant sqk  -> d2q_sqk.jsonl  (summary + questions + keywords)

One passage per letter. TEST-pool letters never get one (they are what the synth set measures).
"""
import argparse
import json
from pathlib import Path

SYN = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["q", "sqk"], default="q")
    a = ap.parse_args()
    test = set(json.loads((SYN / "split.json").read_text(encoding="utf-8"))["test"])
    n = 0
    with open(SYN / f"d2q_{a.variant}.jsonl", "w", encoding="utf-8") as out:
        for line in open(SYN / "questions_train.jsonl", encoding="utf-8"):
            d = json.loads(line)
            assert d["id"] not in test, f"TEST letter {d['id']} in the train questions"
            qs = [it["text"] for it in d["items"]]
            if not qs:
                continue
            if a.variant == "q":
                text = "\n".join(qs)
            else:
                kw = ", ".join(str(k) for k in d.get("keywords", []))
                text = f"{d.get('summary', '')}\n" + "\n".join(qs) + (f"\n{kw}" if kw else "")
            out.write(json.dumps({"letter_id": d["id"], "text": text}, ensure_ascii=False) + "\n")
            n += 1
    print(f"{n} passages -> d2q_{a.variant}.jsonl")


if __name__ == "__main__":
    main()
