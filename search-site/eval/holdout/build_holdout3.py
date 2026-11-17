#!/usr/bin/env python3
"""Builds eval/holdout/holdout3.jsonl from the agent-network output.

Pipeline (see WORKLOG 2026-11-17 "holdout3"):
  1. sample_letters.py --exclude-used (seeds 505/506) -> eval/samples/holdout3*.jsonl
  2. 5 Sonnet writer agents with no context (personas: yeshiva student, layperson,
     researcher, Yiddish letters -> Hebrew/English, new types) -> holdout3_src/writer_*.jsonl
  3. 5 separate Sonnet checker agents, also with no context -> holdout3_src/check_*.jsonl
     (keep / fix / drop)
  4. this script: keeps "keep", applies "fix", drops "drop", plus a mechanical
     filter: a meaning-type query that copies 4+ consecutive words of its letter is dropped.

    python search-site/eval/holdout/build_holdout3.py
"""
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "holdout3_src"
DB = HERE.parent.parent / "data" / "search.db"

# how each type is run (modes) and its k
MODES = {
    "question": ["semantic", "hybrid"],
    "paraphrase": ["semantic", "hybrid"],
    "crosslang": ["semantic", "hybrid"],
    "english": ["semantic", "hybrid"],
    "topic": ["semantic", "hybrid"],
    "typo": ["text", "hybrid"],
    "name": ["text", "hybrid"],
}
MEANING = {"question", "paraphrase", "crosslang", "english"}

_strip = re.compile(r'[֑-ׇ"״\'׳.,;:!?()\[\]\-–]')


def words(t):
    return [w for w in _strip.sub(" ", t or "").split() if w]


def copied_run(query, text, n=4):
    """longest run of consecutive query words that also appears consecutively in the letter (>= n)"""
    qw, tw = words(query), " " + " ".join(words(text)) + " "
    best = 0
    for i in range(len(qw)):
        for j in range(i + n, len(qw) + 1):
            if " " + " ".join(qw[i:j]) + " " in tw:
                best = max(best, j - i)
            else:
                break
    return best


def main():
    con = sqlite3.connect(DB)
    text_of = dict(con.execute("SELECT id, full_text FROM letters"))
    letter_no = dict(con.execute("SELECT id, letter_number FROM letters"))
    out, stats, seen = [], Counter(), set()
    for w in sorted(SRC.glob("writer_*.jsonl")):
        batch = w.stem.split("_")[1]
        writes = [json.loads(l) for l in open(w, encoding="utf-8") if l.strip()]
        chk_path = SRC / f"check_{batch}.jsonl"
        checks = [json.loads(l) for l in open(chk_path, encoding="utf-8") if l.strip()] if chk_path.exists() else []
        by_q = {(c["letter_id"], c["query"]): c for c in checks}
        for i, q in enumerate(writes):
            c = by_q.get((q["letter_id"], q["query"])) or (checks[i] if i < len(checks) else None)
            verdict = (c or {}).get("verdict", "unchecked")
            stats[f"{batch}:{verdict}"] += 1
            if verdict not in ("keep", "fix"):
                continue
            query = c.get("fixed_query") or q["query"] if verdict == "fix" else q["query"]
            typ = q["type"]
            if typ in MEANING and copied_run(query, text_of.get(q["letter_id"], "")) >= 4:
                stats["dropped:copied4"] += 1
                continue
            base = f"h3{batch}-{letter_no[q['letter_id']]}-{typ}"
            cid, n = base, 2
            while cid in seen:
                cid, n = f"{base}{n}", n + 1
            seen.add(cid)
            case = {"id": cid, "type": typ, "modes": MODES[typ], "query": query, "k": 10,
                    "expected_ids": [q["letter_id"]],
                    "note": f"writer {batch}; {q.get('why', '')}".strip()}
            if typ == "typo":
                case["correct"] = (c.get("fixed_correct") if verdict == "fix" else None) or q.get("correct")
            if q.get("weak"):
                case["weak"] = True
            out.append(case)
    with open(HERE / "holdout3.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for c in out:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    runs = sum(len(c["modes"]) for c in out)
    print(f"{len(out)} cases, {runs} runs, {len({c['expected_ids'][0] for c in out})} letters")
    print("by type:", dict(Counter(c["type"] for c in out)))
    print("verdicts:", dict(sorted(stats.items())))


if __name__ == "__main__":
    main()
