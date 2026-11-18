#!/usr/bin/env python3
"""
Download all letters of Igrot Kodesh (the Lubavitcher Rebbe, 28 volumes)
from the Lubavitch Library website (https://chabadlibrary.org/books).

The site is an Angular SPA backed by a JSON API:
    GET https://chabadlibrary.org/books/api/main?path=<node id>
A node is either "children" (a list of child nodes) or "page" (one letter,
with HTML text). The root of Igrot Kodesh is node 1200000000.

Every API response is stored as-is under <out>/raw/<volume_id>/<node_id>.json,
so the parsing step (parse.py) can be re-run any time without re-downloading.
Re-running this script skips nodes already on disk (resumable).

Usage:
    python3 scripts/igrot_kodesh/scrape.py [--out data/igtotkodsh] [--workers 4]
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import brotli  # noqa: F401  (server replies with br encoding; urllib3 needs this to decode)
import requests

API = "https://chabadlibrary.org/books/api/main"
ROOT_ID = 1200000000  # ספרי כ"ק אדמו"ר > אגרות קודש
CA_BUNDLE = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or True

session = requests.Session()
session.headers["User-Agent"] = "ChabadLab-IgrotKodesh-archiver/1.0"


def fetch(node_id, retries=6):
    delay = 2
    for attempt in range(retries):
        try:
            r = session.get(API, params={"path": node_id}, timeout=60, verify=CA_BUNDLE)
            if r.status_code == 200:
                data = r.json()
                if "content" in data:
                    return data
                raise ValueError(f"no 'content' in response for {node_id}")
            if r.status_code == 404:
                raise ValueError(f"404 for {node_id}")
            raise IOError(f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            print(f"  retry {node_id} ({e}); sleeping {delay}s", file=sys.stderr)
            time.sleep(delay)
            delay *= 2


def save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_or_fetch(path: Path, node_id):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = fetch(node_id)
    save(path, data)
    return data


def strip_sections(data):
    # the API repeats the whole site's table of contents in "sections"; not needed
    data.pop("sections", None)
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/igtotkodsh")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--volumes", help="comma separated volume indexes (1-based) to fetch, default all")
    args = ap.parse_args()

    raw = Path(args.out) / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    root = strip_sections(fetch(ROOT_ID))
    save(raw / "index.json", root)
    volumes = root["content"]["data"]
    print(f"{len(volumes)} volumes")
    if args.volumes:
        wanted = {int(x) for x in args.volumes.split(",")}
        volumes = [v for i, v in enumerate(volumes, 1) if i in wanted]

    errors = []
    for vol in volumes:
        vid = vol["id"]
        vdir = raw / str(vid)
        vol_data = strip_sections(load_or_fetch(vdir / "_volume.json", vid))
        save(vdir / "_volume.json", vol_data)

        # walk the tree breadth-first: children nodes may contain further children
        pending = [c["id"] for c in vol_data["content"]["data"]]
        done = 0
        while pending:
            todo = [n for n in pending if not (vdir / f"{n}.json").exists()]
            cached = [n for n in pending if (vdir / f"{n}.json").exists()]
            next_level = []
            for n in cached:
                d = json.loads((vdir / f"{n}.json").read_text(encoding="utf-8"))
                if d["content"]["type"] == "children":
                    next_level += [c["id"] for c in d["content"]["data"]]
            with ThreadPoolExecutor(args.workers) as ex:
                futs = {ex.submit(fetch, n): n for n in todo}
                for f in as_completed(futs):
                    n = futs[f]
                    try:
                        d = strip_sections(f.result())
                    except Exception as e:  # noqa: BLE001
                        errors.append((n, str(e)))
                        print(f"  ERROR {n}: {e}", file=sys.stderr)
                        continue
                    save(vdir / f"{n}.json", d)
                    if d["content"]["type"] == "children":
                        next_level += [c["id"] for c in d["content"]["data"]]
                    done += 1
            pending = next_level
        n_files = len(list(vdir.glob("[0-9]*.json")))
        print(f"{vol['heading']} ({vid}): {n_files} nodes (+{done} new)", flush=True)

    if errors:
        (Path(args.out) / "scrape_errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=1))
        print(f"{len(errors)} errors; re-run to retry", file=sys.stderr)
        sys.exit(1)
    print("done")


if __name__ == "__main__":
    main()
