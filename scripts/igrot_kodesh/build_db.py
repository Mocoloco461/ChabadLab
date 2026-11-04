#!/usr/bin/env python3
"""
Build a SQLite database with full-text search (FTS5) from igrot_kodesh.jsonl.gz.

Usage:
    python3 scripts/igrot_kodesh/build_db.py [--out data/igtotkodsh] [--trigram]

Tables:
    letters       one row per letter (metadata + text)
    notes         editor's footnotes, many per letter (letter_id -> letters.id)
    letters_fts   FTS5 index over recipient / address / body / notes

Hebrew abbreviations (ב"ה, שליט"א, אדה"ז ...) are indexed without the
gershayim, so a query must be normalized the same way - see normalize().
Example:
    SELECT l.volume, l.letter_number_heb, l.date_raw, l.recipient
    FROM letters_fts f JOIN letters l ON l.id = f.rowid
    WHERE letters_fts MATCH 'מחנה ישראל' ORDER BY rank LIMIT 20;

--trigram builds the index with the trigram tokenizer instead, which allows
substring matches (useful for Hebrew prefixes like ו/ה/ב/ל/ש) at the cost of a
much larger file.
"""
import argparse
import gzip
import json
import re
import sqlite3
from pathlib import Path

GERSH = re.compile(r"(?<=[א-ת])[\"'״׳](?=[א-ת])")


def normalize(text):
    """Remove gershayim/geresh inside words: שליט"א -> שליטא. Apply to queries too."""
    return GERSH.sub("", text or "")


SCHEMA = """
CREATE TABLE letters (
    id                INTEGER PRIMARY KEY,   -- node id on chabadlibrary.org
    volume            INTEGER NOT NULL,
    volume_heb        TEXT,
    letter_number     INTEGER,
    letter_number_heb TEXT,
    date_raw          TEXT,
    date_editorial    TEXT,
    hebrew_day        INTEGER,
    hebrew_month      TEXT,
    hebrew_year       INTEGER,
    gregorian_date    TEXT,
    city              TEXT,
    recipient         TEXT,
    address           TEXT,
    language          TEXT,
    body              TEXT,
    postscript        TEXT,
    signature         TEXT,
    source_note       TEXT,
    full_text         TEXT,
    html              TEXT,
    prev_id           INTEGER,
    next_id           INTEGER,
    url               TEXT
);
CREATE INDEX idx_letters_vol_num ON letters(volume, letter_number);
CREATE INDEX idx_letters_num ON letters(letter_number);
CREATE INDEX idx_letters_year ON letters(hebrew_year);
CREATE INDEX idx_letters_greg ON letters(gregorian_date);
CREATE INDEX idx_letters_recipient ON letters(recipient);

CREATE TABLE notes (
    letter_id INTEGER NOT NULL REFERENCES letters(id),
    seq       INTEGER NOT NULL,
    term      TEXT,
    text      TEXT
);
CREATE INDEX idx_notes_letter ON notes(letter_id);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/igtotkodsh")
    ap.add_argument("--trigram", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    db_path = out / "igrot_kodesh.db"
    if db_path.exists():
        db_path.unlink()

    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    tok = "trigram" if args.trigram else "unicode61 remove_diacritics 2"
    con.execute(
        f"CREATE VIRTUAL TABLE letters_fts USING fts5("
        f"recipient, address, body, notes, tokenize='{tok}')")

    cols = ["id", "volume", "volume_heb", "letter_number", "letter_number_heb", "date_raw",
            "date_editorial", "hebrew_day", "hebrew_month", "hebrew_year", "gregorian_date",
            "city", "recipient", "address", "language", "body", "postscript", "signature",
            "source_note", "full_text", "html", "prev_id", "next_id", "url"]
    ins = f"INSERT INTO letters ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    n = 0
    with gzip.open(out / "igrot_kodesh.jsonl.gz", "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            con.execute(ins, [r.get(c) for c in cols])
            con.executemany(
                "INSERT INTO notes (letter_id, seq, term, text) VALUES (?,?,?,?)",
                [(r["id"], i, nt["term"], nt["text"]) for i, nt in enumerate(r["notes"])])
            notes_txt = "\n".join(f"{nt['term'] or ''} {nt['text']}" for nt in r["notes"])
            body = "\n\n".join(x for x in (r["body"], r["postscript"]) if x)
            con.execute(
                "INSERT INTO letters_fts (rowid, recipient, address, body, notes) VALUES (?,?,?,?,?)",
                (r["id"], normalize(r["recipient"]), normalize(r["address"]),
                 normalize(body), normalize(notes_txt)))
            n += 1
    con.execute("INSERT INTO letters_fts(letters_fts) VALUES('optimize')")
    con.commit()
    con.execute("VACUUM")
    con.close()
    print(f"{n} letters -> {db_path} ({db_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
