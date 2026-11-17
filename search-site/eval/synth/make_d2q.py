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
    ap.add_argument("--leaky", action="store_true",
                    help="also TEST letters (questions_test_trainprompt.jsonl): upper bound only, never for decisions")
    a = ap.parse_args()
    test = set(json.loads((SYN / "split.json").read_text(encoding="utf-8"))["test"])
    n = 0
    name = f"d2q_{a.variant}" + ("_leaky" if a.leaky else "")
    srcs = ["questions_train.jsonl"] + (["questions_test_trainprompt.jsonl"] if a.leaky else [])
    with open(SYN / f"{name}.jsonl", "w", encoding="utf-8") as out:
        for line in (l for f in srcs for l in open(SYN / f, encoding="utf-8")):
            d = json.loads(line)
            assert a.leaky or d["id"] not in test, f"TEST letter {d['id']} in the train questions"
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
    print(f"{n} passages -> {name}.jsonl")


if __name__ == "__main__":
    main()
