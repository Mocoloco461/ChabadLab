#!/usr/bin/env python3
"""Markdown tables for the report: every configuration x every set (strict / MRR / any_rel@10), metrics
recomputed from the current qrels and synth judgments."""
import glob
import re
import sys
from pathlib import Path

H = Path(__file__).resolve().parent
sys.path.insert(0, str(H))
import summ  # noqa: E402
import table  # noqa: E402  (prints the decision table on import; harmless)


def res(log):
    m = re.findall(r"results.(\d{8}-\d{6}\.json)", open(log, encoding="utf-8").read())
    return summ.evalfile(H.parent / "results" / m[-1]) if m else None


def cell(a, k="strict"):
    return f"{a['strict']} / {a['mrr']:.3f} / {a['any_rel']}" if a else "–"


print("\n| תצורה | ראשי | פיתוח | ראשי+פיתוח | סינתטי (מדגם נשפט) | holdout1 | holdout2 | holdout3 |")
print("|---|---|---|---|---|---|---|---|")
for name, d, syn in table.rows:
    j = (syn.get("synth") or syn.get("synthJ") or {}).get("J:all", {})
    sj = f"{100*j['hit10']:.1f} / {j['mrr']:.3f} / {100*j['ghit10']:.1f}" if j else "–"
    hs = []
    for h in ("h1", "h2", "h3"):
        logs = glob.glob(str(H / "logs" / f"H_{name.split('_')[0]}_*.{h}.log")) + \
            glob.glob(str(H / "logs" / f"LB_{name.split('_')[0]}_*.{h}.log"))
        r = res(logs[0]) if logs and not name.startswith("LB") else None
        hs.append(cell(r["all"]) if r else "–")
    print(f"| {name} | {cell(d['main'])} | {cell(d['dev'])} | {cell(d['all'])} | {sj} | " + " | ".join(hs) + " |")
print("\nholdout runs:")
for log in sorted(glob.glob(str(H / "logs" / "*.h[123].log")) + glob.glob(str(H / "logs" / "LB_*.main.log"))):
    r = res(log)
    a = r["all"]
    print(f"{Path(log).name:<60} strict {a['strict']:>5} MRR {a['mrr']:.3f} any {a['any_rel']:>5} ndcg {a['ndcg']:.3f} "
          f"unjudged {a['unjudged']}")
