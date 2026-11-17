#!/usr/bin/env python3
"""Small check of a new embedding model before building a full index: 120 synth TEST questions (Hebrew +
English-free), pool = the chunks of their source letters + 250 other letters. Rank of the source letter
(max over its chunks) for the new model vs the stored Qwen3-8B vectors.
    python emb_minitest.py <openrouter model> <query prefix> <passage prefix>
"""
import json
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
import remote  # noqa: E402

model, qp, pp = sys.argv[1], sys.argv[2], sys.argv[3]
q8 = ROOT / "data" / "emb" / "qwen__qwen3-embedding-8b-api"
chunks = [json.loads(l) for l in open(q8 / "chunks.jsonl", encoding="utf-8")]
info = json.loads((q8 / "embeddings.json").read_text(encoding="utf-8"))
E8 = np.load(q8 / "embeddings.npy").astype(np.float32)
cases = [json.loads(l) for l in open(ROOT / "eval" / "synth" / "synth_test.jsonl", encoding="utf-8")]
rnd = random.Random(5)
rnd.shuffle(cases)
cases = cases[:120]
src = {c["expected_ids"][0] for c in cases}
others = sorted({c["letter_id"] for c in chunks} - src)
pool = src | set(rnd.sample(others, 250))
idx = [c["i"] for c in chunks if c["letter_id"] in pool and not c.get("extra")]
lids = np.array([chunks[i]["letter_id"] for i in idx])
P = np.asarray(remote.embed(model, [pp + chunks[i]["text"] for i in idx], batch=32), dtype=np.float32)
Q = np.asarray(remote.embed(model, [qp + c["query"] for c in cases], batch=32), dtype=np.float32)
Q8 = np.asarray(remote.embed(info["api_model"], [info["query_prefix"] + c["query"] for c in cases]), dtype=np.float32)


def ranks(Qm, Pm):
    out = []
    for qv, c in zip(Qm, cases):
        s = Pm @ qv
        best = {}
        for lid, v in zip(lids, s):
            best[lid] = max(best.get(lid, -9), v)
        order = sorted(best, key=lambda x: -best[x])
        out.append(order.index(c["expected_ids"][0]) + 1)
    return np.array(out)


def norm(m):
    return m / np.linalg.norm(m, axis=1, keepdims=True)


for name, r in (("qwen8b", ranks(norm(Q8), norm(E8[idx]))), (model, ranks(norm(Q), norm(P)))):
    print(f"{name:<32} hit@1 {np.mean(r == 1):.3f}  hit@10 {np.mean(r <= 10):.3f}  MRR {np.mean(1 / r):.3f}  "
          f"({len(idx)} chunks, {len(pool)} letters)")
print(f"spent ${remote.spent['usd']:.4f}")
