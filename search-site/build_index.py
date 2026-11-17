#!/usr/bin/env python3
"""
Build the search database for the Igrot Kodesh search site.

    python3 search-site/build_index.py [--src data/igtotkodsh/igrot_kodesh.jsonl.gz]
                                       [--out search-site/data/search.db]

Tables
    letters      one row per letter: metadata + display text
    notes        editors' footnotes
    letters_fts  FTS5 (unicode61) over: text (full letter), recipient (address +
                 the editors' note identifying the addressee), notes
"""
import argparse
import gzip
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
from hebrew import clean, heb_year  # noqa: E402

ROOT = Path(__file__).resolve().parent

SCHEMA = """
CREATE TABLE letters (
    id                INTEGER PRIMARY KEY,
    volume            INTEGER NOT NULL,
    volume_heb        TEXT,
    letter_number     INTEGER,
    letter_number_heb TEXT,
    date_raw          TEXT,
    date_editorial    TEXT,
    hebrew_day        INTEGER,
    hebrew_month      TEXT,
    hebrew_year       INTEGER,
    hebrew_year_heb   TEXT,
    gregorian_date    TEXT,
    gregorian_year    INTEGER,
    city              TEXT,
    recipient         TEXT,
    recipient_info    TEXT,   -- address + the footnote that names the addressee
    recipient_note    TEXT,   -- just the identifying footnote part ("סרברנסקי, מלבורן")
    address           TEXT,
    language          TEXT,
    body              TEXT,
    postscript        TEXT,
    signature         TEXT,
    source_note       TEXT,
    full_text         TEXT,
    n_notes           INTEGER,
    n_chars           INTEGER,
    prev_id           INTEGER,
    next_id           INTEGER,
    url               TEXT
);
CREATE INDEX idx_vol_num ON letters(volume, letter_number);
CREATE INDEX idx_num ON letters(letter_number);
CREATE INDEX idx_year ON letters(hebrew_year);
CREATE INDEX idx_greg ON letters(gregorian_year);
CREATE INDEX idx_lang ON letters(language);

CREATE TABLE notes (
    letter_id INTEGER NOT NULL,
    seq       INTEGER NOT NULL,
    term      TEXT,
    text      TEXT
);
CREATE INDEX idx_notes_letter ON notes(letter_id);

CREATE VIRTUAL TABLE letters_fts USING fts5(
    text, recipient, notes,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def recipient_note(rec):
    addr = rec.get("address") or ""
    out = []
    for n in rec.get("notes") or []:
        term = (n.get("term") or "").strip()
        if term and term in addr:
            txt = re.split(r"אגרות? נוספות|אגרת נוספת|לעיל|לקמן", n["text"])[0].strip(" .,")
            if txt and txt not in out:
                out.append(txt)
    return clean("; ".join(out)) or None


def recipient_info(rec):
    """The address lines plus footnotes attached to them: the editors usually
    identify the addressee in a note whose term is part of the address
    (term 'מו"ה חיים שי'' -> 'סרברנסקי, מלבורן. אגרות נוספות אליו ...')."""
    parts = [rec.get("address") or ""]
    addr = rec.get("address") or ""
    for n in rec.get("notes") or []:
        term = (n.get("term") or "").strip()
        if term and term in addr:
            # keep the identifying part, drop "אגרות נוספות אליו ..." cross references
            txt = re.split(r"אגרות? נוספות|אגרת נוספת|לעיל|לקמן", n["text"])[0]
            parts.append(txt.strip(" .,"))
    return clean("\n".join(p for p in parts if p))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT.parent / "data/igtotkodsh/igrot_kodesh.jsonl.gz"))
    ap.add_argument("--out", default=str(ROOT / "data/search.db"))
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)

    cols = ["id", "volume", "volume_heb", "letter_number", "letter_number_heb", "date_raw",
            "date_editorial", "hebrew_day", "hebrew_month", "hebrew_year", "hebrew_year_heb",
            "gregorian_date", "gregorian_year", "city", "recipient", "recipient_info", "recipient_note", "address",
            "language", "body", "postscript", "signature", "source_note", "full_text",
            "n_notes", "n_chars", "prev_id", "next_id", "url"]
    ins = f"INSERT INTO letters ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    n = 0
    with gzip.open(args.src, "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            r["hebrew_year_heb"] = heb_year(r.get("hebrew_year"))
            g = r.get("gregorian_date")
            r["gregorian_year"] = int(g[:4]) if g else None
            r["recipient_info"] = recipient_info(r)
            r["recipient_note"] = recipient_note(r)
            r["n_notes"] = len(r["notes"])
            r["n_chars"] = len(r["full_text"] or "")
            con.execute(ins, [r.get(c) for c in cols])
            con.executemany("INSERT INTO notes VALUES (?,?,?,?)",
                            [(r["id"], i, nt["term"], nt["text"]) for i, nt in enumerate(r["notes"])])
            notes_txt = "\n".join(f"{nt['term'] or ''}: {nt['text']}" for nt in r["notes"])
            con.execute("INSERT INTO letters_fts (rowid, text, recipient, notes) VALUES (?,?,?,?)",
                        (r["id"], clean(r["full_text"]), r["recipient_info"], clean(notes_txt)))
            n += 1
    con.execute("INSERT INTO letters_fts(letters_fts) VALUES('optimize')")
    con.execute("INSERT INTO meta VALUES ('letters', ?)", (str(n),))
    con.commit()
    con.execute("VACUUM")
    con.close()
    tmp.replace(out)
    print(f"{n} letters -> {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
