#!/usr/bin/env python3
"""Decision table: main+dev (run_eval results, metrics recomputed from current qrels) + synth runs."""
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

H = Path(__file__).resolve().parent
sys.path.insert(0, str(H))
import summ  # noqa: E402

sys.path.insert(0, str(H.parent / "synth"))
import contextlib  # noqa: E402
import io  # noqa: E402

import run_synth  # noqa: E402

JUDG = run_synth.load_judgments()


def synth_metrics(p):
    rows = [json.loads(l) for l in open(p, encoding="utf-8")]
    with contextlib.redirect_stdout(io.StringIO()):
        return run_synth.report(rows, JUDG)

PY = sys.executable
rows = []
for log in sorted(glob.glob(str(H / "logs" / "*.main.log"))):
    name = Path(log).name[:-len(".main.log")].rstrip("_")
    m = re.findall(r"results.(\d{8}-\d{6}\.json)", open(log, encoding="utf-8").read())
    if not m:
        continue
    d = summ.evalfile(H.parent / "results" / m[-1], True)
    syn = {}
    for kind, pre in (("synth", "M_"), ("synthJ", "MJ_")):
        slug = re.sub(r"[^\w.-]+", "_", name).strip("_")
        p = H.parent / "synth" / "runs" / f"{pre}{slug}.jsonl"
        if p.exists():
            syn[kind] = synth_metrics(p)
    rows.append((name, d, syn))
print(f"{'config':<38}{'m+d':>6}{'main':>6}{'dev':>6}{'MRR':>7}{'d.MRR':>7}{'d.any':>7}{'d.ndcg':>7}{'d.unj':>6}"
      f"{'S.h10':>7}{'J.h10':>7}{'J.gh1':>7}{'J.gh10':>7}{'J.ndcg':>7}  name topic eng typo")
for name, d, syn in rows:
    a, m, v, t = d["all"], d["main"], d["dev"], d["by_type"]
    s = syn.get("synth", {}).get("all", {})
    j = (syn.get("synth") or syn.get("synthJ") or {}).get("J:all", {})
    f = lambda x, k, p=True: (f"{100*x[k]:.1f}" if p else f"{x[k]:.3f}") if k in x else "-"
    print(f"{name[:37]:<38}{a['strict']:>6}{m['strict']:>6}{v['strict']:>6}{a['mrr']:>7}{v['mrr']:>7}{v['any_rel']:>7}"
          f"{v['ndcg']:>7}{v['unjudged']:>6}{f(s,'hit10'):>7}{f(j,'hit10'):>7}{f(j,'ghit1'):>7}{f(j,'ghit10'):>7}"
          f"{f(j,'ndcg',False):>7}  {t.get('name')} {t.get('topic')} {t.get('english')} {t.get('typo')}")
