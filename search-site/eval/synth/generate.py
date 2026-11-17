#!/usr/bin/env python3
"""Generate questions for letters, from the letter text only (the model is not told what they are for).

    python search-site/eval/synth/generate.py --pool test  [--n 20] [--model anthropic/claude-haiku-5.5]
    python search-site/eval/synth/generate.py --pool train [--n 500] --batch

--pool test  : "real people" persona, 4 items (topic / 2 questions / vague paraphrase) -> questions_test.jsonl
--pool train : "content card" persona, 5 concise questions                          -> questions_train.jsonl
Resumable: letters already in the output file are skipped. --out overrides the file (e.g. model comparison).
"""
import argparse
import json
import os
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orclient  # noqa: E402

SYN = Path(__file__).resolve().parent
DATA = Path(os.environ.get("IGROT_DATA", SYN.parent.parent / "data"))
MAX_CHARS = 6000

PROMPT_TEST = """להלן מכתב של הרבי מליובאוויטש (מתוך "אגרות קודש").

<מכתב>
{text}
</מכתב>

חשוב על אנשים אמיתיים שהמכתב הזה לא נמצא מולם, אבל הם מתלבטים בדיוק בעניין שהוא עוסק בו: חסיד, בחור ישיבה, הורה, אשה, מחנך, איש עסקים, מי שמתעניין בתורת חב"ד. מה הם היו כותבים, במילים שלהם, כשהם מחפשים מה הרבי אמר על זה?

כתוב עד 4 פריטים, בעברית (גם אם המכתב ביידיש):
1. "topic" – נושא קצר של 2–5 מילים, כמו שמקלידים בשורת חיפוש. נושא מסוים של המכתב, לא צירוף כללי שמופיע במכתבים רבים (כמו "הפצת המעיינות" או "י"ט כסלו"). אם אין למכתב נושא מסוים כזה – דלג על הפריט.
2. "question" – שאלה טבעית ומלאה, בלשון יומיומית, על העניין העיקרי של המכתב, כמו שאדם עם צורך אמיתי היה שואל.
3. "question" – עוד שאלה טבעית, על עניין אחר במכתב או מזווית אחרת.
4. "paraphrase" – נקודה מסוימת שהרבי אומר במכתב, כמו שאדם זוכר אותה במעורפל ומתאר אותה במילים שלו (למשל "הרבי כתב פעם שאם...").

כללים:
- המכתב הזה צריך באמת לענות על כל פריט, ובדיוק על מה שנשאל (לא על עניין קרוב או הפוך).
- השואל לא ראה את המכתב: אל תעתיק ביטויים ייחודיים ממנו, ואל תזכיר את שם הנמען, תאריכים, מוסדות, מקורות, סיפורים או פרטים צדדיים שרק מי שקרא אותו יודע. שאל על העיקר – העצה, ההוראה או הרעיון – לא על פרט שולי.
- כתוב בעברית פשוטה של היום, לא בלשון המכתב. גם ב-"paraphrase": לתאר את הרעיון במילים אחרות, לא לצטט.
- נסח את הצורך ישירות ("מה עושים כש...", "האם מותר...", "איך להתמודד עם..."), ולא "מה הרבי כתב על כך ש..." או "האם הרבי בירך...".
- לא כללי מדי: "מה הרבי אומר על אמונה?" לא טוב. צריך לכוון לעניין המסוים של המכתב.
- אל תשתמש במושגים שלא היו בזמן המכתב (כמו "מייל").
- אם המכתב קצר מאוד, או שהוא רק ברכה או אישור קבלה, כתוב פחות פריטים (אפילו אחד).

בנוסף: "summary" – משפט אחד במה המכתב עוסק, ו-"keywords" – ‏3–8 מילות מפתח.

החזר JSON בלבד, בצורה:
{{"summary": "...", "keywords": ["..."], "items": [{{"kind": "topic", "text": "..."}}, {{"kind": "question", "text": "..."}}]}}"""

PROMPT_TRAIN = """קרא את המכתב הבא מתוך "אגרות קודש" של הרבי מליובאוויטש, והכן לו כרטיס תוכן.

<מכתב>
{text}
</מכתב>

הכרטיס כולל:
- "summary": סיכום של 1–2 משפטים.
- "keywords": ‏4–10 מושגים ונושאים מרכזיים.
- "questions": עד 5 שאלות שהמכתב משיב עליהן. ניסוח ענייני ותמציתי, בעברית (גם אם המכתב ביידיש). מותר להשתמש במונחים של המכתב. כל שאלה על עניין אחר, ושהמכתב באמת עונה עליה. במכתב קצר או במכתב ברכה בלבד – פחות שאלות.

החזר JSON בלבד:
{{"summary": "...", "keywords": ["..."], "questions": ["...", "..."]}}"""


def letters(ids):
    con = sqlite3.connect(DATA / "search.db")
    for lid in ids:
        r = con.execute("SELECT full_text, language FROM letters WHERE id=?", (lid,)).fetchone()
        yield {"id": lid, "text": (r[0] or "")[:MAX_CHARS], "language": r[1]}


def make_fn(pool, model, style=None):
    style = style or pool  # which prompt: "test" (real people) or "train" (content card)
    prompt = PROMPT_TEST if style == "test" else PROMPT_TRAIN
    temp = 0.8 if style == "test" else 0.5

    def fn(job):
        infos = []
        last = None
        for _ in range(2):  # one retry on unparsable JSON
            r = orclient.chat(model, [{"role": "user", "content": prompt.format(text=job["text"])}],
                              max_tokens=1200, temperature=temp)
            infos.append(r)
            try:
                d = orclient.parse_json(r["text"])
                if not isinstance(d, dict):
                    raise ValueError("reply is not a JSON object")
                if style == "test":
                    items = [{"kind": str(i.get("kind", "question")), "text": str(i["text"]).strip()}
                             for i in d.get("items", []) if isinstance(i, dict) and str(i.get("text", "")).strip()]
                else:
                    items = [{"kind": "question", "text": str(q).strip()} for q in d.get("questions", []) if str(q).strip()]
                out = {"id": job["id"], "language": job["language"], "pool": pool, "model": r["model"],
                       "summary": d.get("summary", ""), "keywords": d.get("keywords", []), "items": items}
                return out, infos
            except (ValueError, KeyError, AttributeError, TypeError) as e:
                last = e
        e = RuntimeError(f"bad JSON: {last}")
        e.infos = infos
        raise e
    return fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", choices=["test", "train"], required=True)
    ap.add_argument("--n", type=int, help="only the first n letters of the pool (in a fixed shuffled order)")
    ap.add_argument("--ids", help="comma-separated letter ids (subset of the pool)")
    ap.add_argument("--model", default="anthropic/claude-haiku-5.5")
    ap.add_argument("--batch", action="store_true", help="use the :batch variant (half price, slower)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out")
    ap.add_argument("--prompt", choices=["test", "train"],
                    help="prompt style (default: the pool's own); --pool test --prompt train = doc2query passages "
                         "for TEST letters, only for the leaky upper-bound measurement")
    ap.add_argument("--include-used", action="store_true",
                    help="train pool + the letters already used by eval/cases, holdouts, samples")
    a = ap.parse_args()
    split = json.loads((SYN / "split.json").read_text(encoding="utf-8"))
    pool = list(split[a.pool])
    if a.include_used:  # letters of the hand-written eval sets: doc2query must cover them too (letter text only)
        assert a.pool == "train"
        pool += split["excluded_used"]
    random.Random(11).shuffle(pool)  # fixed order, so --n 20 then --n 600 extends the same prefix
    if a.ids:
        want = {int(x) for x in a.ids.split(",")}
        assert want <= set(pool), "ids outside the pool"
        pool = [i for i in pool if i in want]
    if a.n:
        pool = pool[: a.n]
    out = Path(a.out) if a.out else SYN / f"questions_{a.pool}.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(l)["id"] for l in open(out, encoding="utf-8") if l.strip()}
    jobs = [j for j in letters([i for i in pool if i not in done])]
    model = a.model + (":batch" if a.batch and not a.model.endswith(":batch") else "")
    print(f"{len(jobs)} letters to do ({len(done)} already in {out.name}), model {model}")
    if not jobs:
        return
    errs = 0
    with open(out, "a", encoding="utf-8") as fh:
        try:
            for job, res, err in orclient.run_jobs(f"gen-{a.pool}", model, jobs, make_fn(a.pool, model, a.prompt),
                                                   workers=a.workers):
                if res:
                    fh.write(json.dumps(res, ensure_ascii=False) + "\n")
                    fh.flush()
                else:
                    errs += 1
                    print(f"  letter {job['id']}: {err}")
        except orclient.BudgetStop as e:
            print("BUDGET STOP:", e)
            sys.exit(3)
    print(f"done, {errs} errors")


if __name__ == "__main__":
    main()
