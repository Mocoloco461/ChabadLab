#!/usr/bin/env python3
"""
Verify the Igrot Kodesh dataset against the source site (chabadlibrary.org).

For every letter in igrot_kodesh.jsonl.gz the letter is downloaded again from
the source API and compared with what is stored in the repository:

  body_pct   letter text in the repo (full_text: date, city, address, body,
             signature, postscript) vs. the same parts of the letter on the site
  notes_pct  footnotes in the repo (notes) vs. footnotes on the site
  text_pct   the two above combined, weighted by length  <-- the main score
  word_pct   order-insensitive word match (Dice over word counts) of all the
             text: tells "text is missing/different" apart from "same text,
             different order" (e.g. several addressees merged into one block)
  html_pct   the HTML stored in the repo vs. the HTML on the site today
             (shows whether the source changed since the scrape)

Percentages are character-level similarity (normalized Levenshtein) after
collapsing whitespace and unifying Hebrew quote marks. Two empty texts = 100%.

It also checks coverage: every letter's "next" link on the site must point to
a letter that is in the repo (finds letters missing from the dataset).

Downloads are cached in <data>/raw/verify/ (git-ignored) so re-runs are fast.

Usage:
    python3 scripts/igrot_kodesh/verify.py [--data data/igtotkodsh] [--workers 6] [--threshold 90]

Outputs (in <data>/verify/):
    verify_all.csv        one row per letter with all scores
    below_threshold.csv   letters whose text_pct < threshold (default 90)
    missing.csv           letters on the site that are not in the repo
    summary.json          totals and score distribution
"""
import argparse
import csv
import gzip
import html as htmlmod
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rapidfuzz.distance import Levenshtein

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse import blocks, html_to_text, letter_blocks  # noqa: E402
from scrape import fetch, save, strip_sections  # noqa: E402


def norm(t):
    t = htmlmod.unescape(t or "")
    t = t.replace("״", '"').replace("׳", "'").replace("‏", "").replace("‎", "")
    t = t.replace("[", "").replace("]", "")
    return re.sub(r"\s+", " ", t).strip()


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a and not b:
        return 100.0
    return round(Levenshtein.normalized_similarity(a, b) * 100, 2)


def source_parts(node):
    """(letter_text, notes_text) from a source API node, same split as parse.py."""
    d = node["content"]["data"]
    text, notes = [], []
    for role, inner in letter_blocks(d.get("text") or ""):
        if role in ("number", "skip"):
            continue
        t = html_to_text(inner)
        if t:
            (notes if role == "note" else text).append(t)
    if not text and notes:  # page with editorial blocks only: parse.py uses them as the body
        text, notes = notes, []
    for _cls, inner in blocks(d.get("haoros") or ""):
        if "oysbehaoro" in inner:
            continue
        t = html_to_text(inner)
        if t:
            notes.append(t)
    return "\n".join(text), "\n".join(notes)


def repo_parts(rec):
    notes = []
    for n in rec.get("notes") or []:
        notes.append(f'{n["term"]}: {n["text"]}' if n.get("term") else n["text"])
    text = rec.get("full_text") or ""
    return text, "\n".join(notes)


def word_sim(a, b):
    wa = Counter(re.sub(r"[^\w\s]", " ", norm(a)).split())
    wb = Counter(re.sub(r"[^\w\s]", " ", norm(b)).split())
    n = sum(wa.values()) + sum(wb.values())
    return round(200 * sum((wa & wb).values()) / n, 2) if n else 100.0


def notes_sim(repo_notes, src_notes):
    # parse.py splits "term: text" / "term = text"; ignore which separator was used
    strip = lambda s: re.sub(r"\s*[:=]\s*", " ", norm(s))  # noqa: E731
    a, b = strip(repo_notes), strip(src_notes)
    if not a and not b:
        return 100.0
    return round(Levenshtein.normalized_similarity(a, b) * 100, 2)


def get_node(cache: Path, node_id):
    p = cache / f"{node_id}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    d = strip_sections(fetch(node_id))
    save(p, d)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/igtotkodsh")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--threshold", type=float, default=90.0)
    ap.add_argument("--limit", type=int, help="check only the first N letters (testing)")
    args = ap.parse_args()

    data = Path(args.data)
    cache = data / "raw" / "verify"
    out = data / "verify"
    out.mkdir(parents=True, exist_ok=True)

    recs = [json.loads(l) for l in gzip.open(data / "igrot_kodesh.jsonl.gz", "rt", encoding="utf-8")]
    if args.limit:
        recs = recs[: args.limit]
    repo_ids = {r["id"] for r in recs}
    print(f"{len(recs)} letters to verify", flush=True)

    rows, errors, next_ids = [], [], {}
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(get_node, cache, r["id"]): r for r in recs}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            base = {"id": r["id"], "volume": r["volume"], "letter_number": r["letter_number"],
                    "letter_number_heb": r["letter_number_heb"], "url": r["url"]}
            try:
                node = f.result()
            except Exception as e:  # noqa: BLE001
                errors.append({**base, "error": str(e)})
                rows.append({**base, "status": "fetch_error", "text_pct": 0.0})
                continue
            next_ids[r["id"]] = node["content"].get("nav", {}).get("next")
            s_text, s_notes = source_parts(node)
            r_text, r_notes = repo_parts(r)
            body = sim(r_text, s_text)
            notes = notes_sim(r_notes, s_notes)
            w_body = max(len(norm(s_text)), len(norm(r_text)))
            w_notes = max(len(norm(s_notes)), len(norm(r_notes)))
            total = round((body * w_body + notes * w_notes) / (w_body + w_notes), 2) if (w_body + w_notes) else 100.0
            src_html = node["content"]["data"].get("text") or ""
            rows.append({
                **base, "status": "ok",
                "text_pct": total, "body_pct": body, "notes_pct": notes,
                "word_pct": word_sim(r_text + "\n" + r_notes, s_text + "\n" + s_notes),
                "html_pct": sim(r.get("html"), src_html),
                "repo_chars": len(norm(r_text)) + len(norm(r_notes)),
                "source_chars": len(norm(s_text)) + len(norm(s_notes)),
                "repo_body_empty": not (r.get("body") or "").strip(),
            })
            if i % 500 == 0:
                print(f"  {i}/{len(recs)}", flush=True)

    rows.sort(key=lambda x: (x["volume"], x["letter_number"] or 0, x["id"]))
    fields = ["id", "volume", "letter_number", "letter_number_heb", "status", "text_pct", "body_pct",
              "notes_pct", "word_pct", "html_pct", "repo_chars", "source_chars", "repo_body_empty", "url"]

    def write(path, items):
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(items)

    below = [x for x in rows if x["text_pct"] < args.threshold]
    write(out / "verify_all.csv", rows)
    write(out / "below_threshold.csv", sorted(below, key=lambda x: x["text_pct"]))

    # coverage: letters the site links to (via "next") that are not in the repo
    missing = sorted({n for n in next_ids.values() if n and n not in repo_ids}) if not args.limit else []
    with open(out / "missing.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "url"])
        for n in missing:
            w.writerow([n, f"https://chabadlibrary.org/books/{n}"])

    ok = [x for x in rows if x["status"] == "ok"]
    bands = {"100": 0, "99-100": 0, "95-99": 0, "90-95": 0, "<90": 0}
    for x in ok:
        p = x["text_pct"]
        k = "100" if p == 100 else "99-100" if p >= 99 else "95-99" if p >= 95 else "90-95" if p >= 90 else "<90"
        bands[k] += 1
    summary = {
        "letters_checked": len(rows),
        "fetch_errors": len(errors),
        "threshold": args.threshold,
        "below_threshold": len(below),
        "avg_text_pct": round(sum(x["text_pct"] for x in ok) / len(ok), 3) if ok else None,
        "avg_word_pct": round(sum(x["word_pct"] for x in ok) / len(ok), 3) if ok else None,
        "word_below_threshold": sum(1 for x in ok if x["word_pct"] < args.threshold),
        "avg_html_pct": round(sum(x["html_pct"] for x in ok) / len(ok), 3) if ok else None,
        "html_identical": sum(1 for x in ok if x["html_pct"] == 100),
        "text_pct_distribution": bands,
        "repo_body_empty": [x["letter_number_heb"] for x in ok if x["repo_body_empty"]],
        "missing_from_repo": len(missing),
        "errors": errors,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "errors"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
