"""Minimal OpenRouter client with a hard spend guard.

Key: env OPENROUTER_API_KEY, else %USERPROFILE%/.openrouter_key (one line). Never printed or logged.
Spend: every paid run checks GET /api/v1/key first, stops at STOP_USD of total key usage, and appends
one line per run to eval/synth/spend.csv (task, model, calls, tokens, cost, key usage before/after).
"""
import csv
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

SYN = Path(__file__).resolve().parent
API = "https://openrouter.ai/api/v1"
STOP_USD = float(os.environ.get("SYNTH_STOP_USD", "8.50"))
SPEND = SYN / "spend.csv"


class BudgetStop(Exception):
    pass


def get_key():
    k = os.environ.get("OPENROUTER_API_KEY")
    if not k:
        p = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".openrouter_key"
        if p.exists():
            k = p.read_text(encoding="utf-8-sig").strip()
    if not k:
        raise SystemExit("no OpenRouter key: set OPENROUTER_API_KEY or save it to %USERPROFILE%/.openrouter_key")
    return k


def _req(method, path, body=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": "Bearer " + get_key(), "Content-Type": "application/json",
        "X-Title": "synthetic-queries"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.load(resp)


def key_usage():
    d = _req("GET", "/key")["data"]
    return float(d.get("usage") or 0), d.get("limit")


def chat(model, messages, max_tokens=800, temperature=0.7, retries=4, reasoning=False):
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature, "usage": {"include": True}}
    if not reasoning:  # Haiku 5.5 / DeepSeek think by default: ~700 extra output tokens per call
        body["reasoning"] = {"enabled": False}
    for i in range(retries):
        try:
            d = _req("POST", "/chat/completions", body)
            if "choices" not in d:
                raise RuntimeError(str(d.get("error"))[:300])
            u = d.get("usage") or {}
            if d["choices"][0].get("finish_reason") == "length":
                print(f"  warning: reply cut at max_tokens ({u.get('completion_tokens')} tokens)", flush=True)
            return {"text": d["choices"][0]["message"].get("content") or "",
                    "in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
                    "cost": float(u.get("cost") or 0), "model": d.get("model", model)}
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if e.code in (401, 402, 403):
                raise SystemExit(f"OpenRouter HTTP {e.code}: {msg}")
            err = f"HTTP {e.code}: {msg}"
        except Exception as e:  # noqa: BLE001
            err = str(e)[:300]
        time.sleep(2 * (i + 1) ** 2)
    raise RuntimeError(err)


def parse_json(text):
    """First JSON object or array in the reply (models sometimes wrap it in ``` fences)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    for opener, closer in (("{", "}"), ("[", "]")):
        s, e = t.find(opener), t.rfind(closer)
        if s != -1 and e > s:
            try:
                return json.loads(t[s:e + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON in reply")


def run_jobs(task, model, jobs, fn, workers=8, est_cost_per_job=None):
    """Run fn(job) -> (result, call_info) for each job in parallel, with the spend guard.

    Yields (job, result_or_None, error_or_None). Stops (BudgetStop) when key usage before the run
    plus the cost seen so far reaches STOP_USD. Writes one spend.csv line at the end, even on stop."""
    before, limit = key_usage()
    if before >= STOP_USD:
        raise BudgetStop(f"key usage ${before:.3f} >= stop ${STOP_USD}")
    if est_cost_per_job and before + est_cost_per_job * len(jobs) >= STOP_USD:
        print(f"warning: estimate ${est_cost_per_job * len(jobs):.2f} would cross the stop; "
              f"the run will stop early")
    tot = {"calls": 0, "in": 0, "out": 0, "cost": 0.0, "errors": 0}
    lock = threading.Lock()
    stop = threading.Event()
    t0 = time.time()

    def wrapped(job):
        if stop.is_set():
            return job, None, "skipped (budget stop)"
        try:
            res, infos = fn(job)
            err = None
        except Exception as e:  # noqa: BLE001
            res, infos, err = None, getattr(e, "infos", []), str(e)[:300]
        with lock:
            for inf in infos:
                tot["calls"] += 1
                tot["in"] += inf["in"]
                tot["out"] += inf["out"]
                tot["cost"] += inf["cost"]
            tot["errors"] += err is not None
            if before + tot["cost"] >= STOP_USD:
                stop.set()
        return job, res, err

    stopped = False
    try:
        with ThreadPoolExecutor(workers) as ex:
            futs = [ex.submit(wrapped, j) for j in jobs]
            for n, f in enumerate(as_completed(futs), 1):
                yield f.result()
                if n % 50 == 0:
                    print(f"  {task}: {n}/{len(jobs)} jobs, ${tot['cost']:.3f}, {tot['errors']} errors, "
                          f"{time.time() - t0:.0f}s", flush=True)
        stopped = stop.is_set()
    finally:
        try:
            after = key_usage()[0]
        except Exception:  # noqa: BLE001
            after = ""
        new = not SPEND.exists()
        with open(SPEND, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["time", "task", "model", "jobs", "calls", "errors", "tokens_in", "tokens_out",
                            "cost_usd", "key_usage_before", "key_usage_after", "stopped"])
            w.writerow([datetime.now().isoformat(timespec="seconds"), task, model, len(jobs), tot["calls"],
                        tot["errors"], tot["in"], tot["out"], round(tot["cost"], 4), round(before, 4),
                        round(after, 4) if after != "" else "", stopped])
        print(f"{task}: {tot['calls']} calls, {tot['in']} in / {tot['out']} out tokens, ${tot['cost']:.4f}; "
              f"key usage ${before:.3f} -> {after if after == '' else f'${after:.3f}'}"
              + (" — STOPPED at budget" if stopped else ""))
    if stopped:
        raise BudgetStop(f"stopped at ${STOP_USD}")


def log_spend(task, model, cost, before, jobs="", calls="", note=""):
    """one spend.csv line for work done outside run_jobs (embedding builds, engine eval runs)"""
    after = key_usage()[0]
    new = not SPEND.exists()
    with open(SPEND, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["time", "task", "model", "jobs", "calls", "errors", "tokens_in", "tokens_out",
                        "cost_usd", "key_usage_before", "key_usage_after", "stopped"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), task, model, jobs, calls, "", "", "",
                    round(cost, 4), round(before, 4), round(after, 4), note])
    return after


if __name__ == "__main__":  # python orclient.py  -> current key usage
    u, lim = key_usage()
    print(f"key usage ${u:.4f} of limit {lim}; stop at ${STOP_USD}")
