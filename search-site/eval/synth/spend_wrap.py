#!/usr/bin/env python3
"""Run a command and log its OpenRouter spend (key usage before/after) to spend.csv.

    python spend_wrap.py <task> <model> -- <command...>
Refuses to start when key usage is already at the stop line.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orclient  # noqa: E402

task, model = sys.argv[1], sys.argv[2]
cmd = sys.argv[sys.argv.index("--") + 1:]
before = orclient.key_usage()[0]
if before >= orclient.STOP_USD:
    raise SystemExit(f"key usage ${before:.3f} >= stop ${orclient.STOP_USD}")
rc = subprocess.call(cmd)
time.sleep(5)  # usage on /key lags a little
after = orclient.log_spend(task, model, 0, before, note=f"rc={rc}")
# cost column = measured delta (other runs in parallel would be included)
import csv  # noqa: E402
rows = list(csv.reader(open(orclient.SPEND, encoding="utf-8")))
rows[-1][8] = f"{after - before:.4f}"
with open(orclient.SPEND, "w", newline="", encoding="utf-8") as fh:
    csv.writer(fh).writerows(rows)
print(f"[{task}] spend ${after - before:.4f} (key usage ${after:.4f})")
sys.exit(rc)
