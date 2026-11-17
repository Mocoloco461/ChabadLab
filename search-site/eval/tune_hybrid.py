#!/usr/bin/env python3
"""Offline tuning of semantic / hybrid ranking on the eval cases (no HTTP).

    /root/venv-igrot/bin/python search-site/eval/tune_hybrid.py [--emb <folder>]

Runs every semantic-type case (paraphrase, question, crosslang) through several
ranking variants and prints pass@10 per variant and type.
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

ap = argparse.ArgumentParser()
ap.add_argument("--emb")
ap.add_argument("--k", type=int, default=10)
args = ap.parse_args()
if args.emb:
    os.environ["IGROT_EMB"] = args.emb

import search  # noqa: E402
from hebrew import parse_query  # noqa: E402

cases = []
for f in sorted(glob.glob(str(ROOT / "eval/cases/*.jsonl"))):
    for line in open(f, encoding="utf-8"):
        c = json.loads(line)
        if c["type"] in ("paraphrase", "question", "crosslang"):
            cases.append(c)

idx = search.semantic_index()
print("model:", idx.info["model"], "cases:", len(cases))

# cache query embeddings and semantic hits
sem_cache = {}
for c in cases:
    sem_cache[c["id"]] = idx.search(c["query"], None, n=300)


def text_variant(q, kind):
    if kind == "none":
        return []
    terms = [it for it in parse_query(q) if not it["neg"]
             and not (it["kind"] == "word" and it["words"][0] in search.STOPWORDS)]
    if kind == "any":
        e = search.build_fts_query("", True, "any", items=terms)
        return search._match_ids(e, "", [], order_bm25=True, limit=300) if e else []
    if kind == "cover":  # number of matched terms, then bm25(any)
        cover = defaultdict(int)
        for it in terms:
            e = search.build_fts_query("", True, items=[it])
            if e:
                for lid in search._match_ids(e, "", []):
                    cover[lid] += 1
        e = search.build_fts_query("", True, "any", items=terms)
        order = {lid: i for i, lid in enumerate(search._match_ids(e, "", [], order_bm25=True, limit=3000))} if e else {}
        ids = sorted(cover, key=lambda lid: (-cover[lid], order.get(lid, 10 ** 6)))
        return ids[:300]
    if kind == "all_then_any":
        return search.text_ranked_ids(q, {}, 300, True, "all") + [
            i for i in text_variant(q, "any") if i not in set(search.text_ranked_ids(q, {}, 300, True, "all"))]
    raise ValueError(kind)


EXTRA_STOP = set("""מה מי איך למה מדוע האם כיצד היכן איפה מתי כתב כותב הרבי הרב רבי ממליץ יעץ מציע
לגבי בעניין כדי צריך שצריך צריכים נכון כאשר כש רוצה שרוצה שרוצים עומד לעשות להיות יכול אפשר
מכתב אגרת תשובה מענה ברכה מברך מבקש כותב שלו שלה שלהם עם על אל את""".split())
import re as _re
from hebrew import heb_year as _hy
YEARS = {_hy(y).replace('"', ''): y for y in range(5680, 5740)}


def query_years(q):
    out = set()
    for tok in _re.findall(r"(?:ה')?[א-ת]{1,4}\"[א-ת]|[א-ת]{2,4}", q):
        t = tok.replace('"', "").replace("ה'", "")
        if t in YEARS and ('"' in tok or len(t) >= 3) and t.startswith("ת"):
            out.add(YEARS[t])
    return out


def text_cover(q, stop, star):
    terms = [it for it in parse_query(q) if not it["neg"]
             and not (it["kind"] == "word" and (it["words"][0] in search.STOPWORDS or it["words"][0] in stop))]
    cover = defaultdict(int)
    for it in terms:
        it2 = dict(it)
        if star and it["kind"] == "word" and len(it["words"][0]) >= 4:
            it2["star"] = True
        e = search.build_fts_query("", True, items=[it2])
        if e:
            for lid in search._match_ids(e, "", []):
                cover[lid] += 1
    e = search.build_fts_query("", True, "any", items=terms)
    order = {lid: i for i, lid in enumerate(search._match_ids(e, "", [], order_bm25=True, limit=3000))} if e else {}
    return sorted(cover, key=lambda lid: (-cover[lid], order.get(lid, 10 ** 6)))[:300]


year_of = {r[0]: r[1] for r in search.db().execute("SELECT id, hebrew_year FROM letters")}

text_cache = {}


def fuse(c, tkind, k=60, wt=1.0, ws=1.0, ybonus=0.0):
    key = (c["id"], tkind)
    if key not in text_cache:
        if tkind.startswith("cov+"):
            text_cache[key] = text_cover(c["query"], EXTRA_STOP if "stop" in tkind else set(), "star" in tkind)
        else:
            text_cache[key] = text_variant(c["query"], tkind)
    t_ids = text_cache[key]
    s = defaultdict(float)
    for r, lid in enumerate(t_ids):
        s[lid] += wt / (k + r + 1)
    for r, (lid, _, _) in enumerate(sem_cache[c["id"]]):
        s[lid] += ws / (k + r + 1)
    if ybonus:
        ys = query_years(c["query"])
        if ys:
            for lid in list(s):
                if year_of.get(lid) in ys:
                    s[lid] += ybonus
    return [lid for lid, _ in sorted(s.items(), key=lambda x: -x[1])]


variants = {"semantic only": ("none", 60, 0, 1, 0), "cover k=10 (prev best)": ("cover", 10, 1, 1, 0)}
for tk in ("cov+stop", "cov+star", "cov+stop+star"):
    for k in (5, 10, 20):
        for wt, ws in ((1, 1), (1.5, 1), (1, 1.5)):
            variants[f"{tk} k={k} wt={wt} ws={ws}"] = (tk, k, wt, ws, 0)
variants["cov+stop+star k=10 +year"] = ("cov+stop+star", 10, 1, 1, 0.05)
variants["cov+stop k=10 +year"] = ("cov+stop", 10, 1, 1, 0.05)

rows = []
for name, (tk, k, wt, ws, yb) in variants.items():
    by = defaultdict(lambda: [0, 0])
    for c in cases:
        ranked = fuse(c, tk, k, wt, ws, yb)[: args.k]
        ok = c["expected_ids"][0] in ranked
        by[c["type"]][0] += ok
        by[c["type"]][1] += 1
        by["ALL"][0] += ok
        by["ALL"][1] += 1
    rows.append((by["ALL"][0] / by["ALL"][1], name, {t: f"{100 * a / n:.0f}" for t, (a, n) in by.items()}))
for pct, name, d in sorted(rows, reverse=True):
    print(f"{100 * pct:5.1f}  {name:<32} {d}")
