#!/usr/bin/env python3
"""Copy search.db and add d2q_fts: one FTS5 row per letter (rowid = letter id) with all its doc2query text.

    python build_d2q_fts.py <src search.db> <dst search.db> <d2q.jsonl> [<d2q.jsonl> ...]
The source db is only read (sqlite backup API). Text mode never uses the table; hybrid mode adds it as one
more lexical RRF list (search.py D2Q_LEX_W).
"""
import json
import sqlite3
import sys
from collections import defaultdict

src, dst, files = sys.argv[1], sys.argv[2], sys.argv[3:]
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
d = sqlite3.connect(dst)
s.backup(d)
s.close()
texts = defaultdict(list)
for f in files:
    for line in open(f, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            texts[int(r["letter_id"])].append(r["text"])
d.execute("DROP TABLE IF EXISTS d2q_fts")
d.execute("CREATE VIRTUAL TABLE d2q_fts USING fts5(text, tokenize = 'unicode61 remove_diacritics 2')")
d.executemany("INSERT INTO d2q_fts(rowid, text) VALUES (?, ?)", [(k, "\n".join(v)) for k, v in texts.items()])
d.execute("INSERT INTO d2q_fts(d2q_fts) VALUES ('optimize')")
d.commit()
d.execute("VACUUM")
print(f"{len(texts)} letters -> d2q_fts in {dst}")
