#!/usr/bin/env python3
"""LLM relevance judge for letters the engine ranked ABOVE the source letter (top 10).

    python search-site/eval/synth/judge.py --runs runs/baseline.jsonl [--batch] [--limit-cases 50]

Grades (per question, letter): 2 = answers the question directly, 1 = same subject / partly helpful,
0 = not relevant. One call per question with all its unjudged candidate letters (both modes pooled).
Appends to judgments.jsonl, so later engine variants only pay for new pairs.
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orclient  # noqa: E402

SYN = Path(__file__).resolve().parent
DATA = Path(os.environ.get("IGROT_DATA", SYN.parent.parent / "data"))
LETTER_CHARS = 1400

PROMPT = """השאלה של המשתמש:
"{q}"

להלן {n} מכתבים מתוך "אגרות קודש" של הרבי מליובאוויטש (מכתבים ארוכים קוצרו).

{letters}

דרג כל מכתב לפי מידת התשובה שלו לשאלה:
2 = עונה ישירות על השאלה (או על עיקרה) – מי ששאל היה שמח למצוא אותו.
1 = עוסק באותו נושא ותורם משהו, אבל לא באמת עונה על השאלה.
0 = לא רלוונטי לשאלה.

החזר JSON בלבד: מערך של בדיוק {n} פריטים, אחד לכל מכתב, n מ-1 עד {n}:
[{{"n": 1, "grade": 0}}, {{"n": 2, "grade": 1}}, ...]"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--model", default="anthropic/claude-haiku-5.5")
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--limit-cases", type=int)
    ap.add_argument("--sample", type=int, help="first run only: fix a random sample of N questions to judge "
                                              "(judge_cases.json); later runs judge only those")
    ap.add_argument("--seed", type=int, default=31)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    out = SYN / "judgments.jsonl"
    done = set()
    if out.exists():
        done = {(d["case"], d["letter"]) for d in map(json.loads, open(out, encoding="utf-8"))}
    need = defaultdict(list)
    query = {}
    for r in map(json.loads, open(a.runs, encoding="utf-8")):
        query[r["case"]] = r["query"]
        above = r["top"][: min(r["rank"] - 1, 10)] if r["rank"] else r["top"][:10]
        for lid in above:
            if (r["case"], lid) not in done and lid not in need[r["case"]]:
                need[r["case"]].append(lid)
    jc = SYN / "judge_cases.json"
    if a.sample and not jc.exists():  # fixed once: the questions whose results get judged
        import random
        allc = sorted({json.loads(l)["case"] for l in open(a.runs, encoding="utf-8")})
        jc.write_text(json.dumps(sorted(random.Random(a.seed).sample(allc, a.sample))), encoding="utf-8")
    if jc.exists():
        keep = set(json.loads(jc.read_text(encoding="utf-8")))
        need = {c: v for c, v in need.items() if c in keep}
    cases = [c for c in need if need[c]]
    if a.limit_cases:
        cases = cases[: a.limit_cases]
    con = sqlite3.connect(DATA / "search.db")
    text = {}
    for c in cases:
        for lid in need[c]:
            if lid not in text:
                t = con.execute("SELECT full_text FROM letters WHERE id=?", (lid,)).fetchone()[0] or ""
                text[lid] = t[:LETTER_CHARS] + (" [...]" if len(t) > LETTER_CHARS else "")
    jobs = [{"case": c, "q": query[c], "letters": need[c]} for c in cases]
    print(f"{len(jobs)} questions, {sum(len(j['letters']) for j in jobs)} pairs to judge")
    if not jobs:
        return
    model = a.model + (":batch" if a.batch and not a.model.endswith(":batch") else "")

    def fn(job):
        block = "\n\n".join(f"<מכתב {i}>\n{text[lid]}\n</מכתב {i}>" for i, lid in enumerate(job["letters"], 1))
        infos = []
        for _ in range(2):
            r = orclient.chat(model, [{"role": "user", "content": PROMPT.format(
                q=job["q"], n=len(job["letters"]), letters=block)}], max_tokens=40 + 25 * len(job["letters"]),
                temperature=0)
            infos.append(r)
            try:
                arr = orclient.parse_json(r["text"])
                g = {int(x["n"]): int(x["grade"]) for x in arr}
                if set(g) >= set(range(1, len(job["letters"]) + 1)) and all(v in (0, 1, 2) for v in g.values()):
                    return [{"case": job["case"], "letter": lid, "grade": g[i], "model": r["model"]}
                            for i, lid in enumerate(job["letters"], 1)], infos
            except (ValueError, KeyError, TypeError):
                pass
        e = RuntimeError("bad judge reply: " + r["text"][:200])
        e.infos = infos
        raise e

    errs = 0
    with open(out, "a", encoding="utf-8") as fh:
        try:
            for job, res, err in orclient.run_jobs("judge", model, jobs, fn, workers=a.workers):
                if res:
                    for d in res:
                        fh.write(json.dumps(d, ensure_ascii=False) + "\n")
                    fh.flush()
                else:
                    errs += 1
                    print(f"  {job['case']}: {err}")
        except orclient.BudgetStop as e:
            print("BUDGET STOP:", e)
            sys.exit(3)
    print(f"done, {errs} errors")


if __name__ == "__main__":
    main()
