#!/usr/bin/env python3
"""Copy a doc2query index folder without the extra passages of some letters (no API calls).

    python filter_d2q.py <src folder> <dst folder> <ids.json> [<ids.json> ...]
ids files: JSON list of letter ids, or split.json (its "excluded_used" list is used).
Used for the lower-bound measurement: eval letters keep only their own text, all others keep doc2query.
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
drop = set()
for f in sys.argv[3:]:
    d = json.loads(Path(f).read_text(encoding="utf-8"))
    drop |= set(d["excluded_used"] if isinstance(d, dict) else d)
emb = np.load(src / "embeddings.npy")
keep, chunks = [], []
for line in open(src / "chunks.jsonl", encoding="utf-8"):
    c = json.loads(line)
    if c.get("extra") and c["letter_id"] in drop:
        continue
    keep.append(c["i"])
    c["i"] = len(chunks)
    chunks.append(c)
dst.mkdir(parents=True, exist_ok=True)
np.save(dst / "embeddings.npy", emb[keep])
with open(dst / "chunks.jsonl", "w", encoding="utf-8") as fh:
    for c in chunks:
        fh.write(json.dumps(c, ensure_ascii=False) + "\n")
info = json.loads((src / "embeddings.json").read_text(encoding="utf-8"))
info.update(n=len(chunks), filtered_out_letters=len(drop))
(dst / "embeddings.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{len(emb)} -> {len(chunks)} passages ({len(emb) - len(chunks)} doc2query passages dropped) -> {dst}")
