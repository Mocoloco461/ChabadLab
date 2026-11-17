#!/bin/bash
# Judge (Haiku, eval/synth/judge.py) the above-source pairs of the given synth runs, then print metrics.
cd "$(dirname "$0")/../.."
export PYTHONUTF8=1 SYNTH_STOP_USD=${SYNTH_STOP_USD:-28.5} IGROT_DATA=${IGROT_DATA_JUDGE:-C:/Users/USER/Documents/Moshe/ChabadLab/search-site/data}
PY=C:/Users/USER/Documents/Moshe/ChabadLab/.venv/Scripts/python.exe
for r in "$@"; do $PY eval/synth/judge.py --runs "$r" --workers 16 2>&1 | grep -i 'judge:\|error\|budget' | tail -n 2; done
for r in "$@"; do echo "== $r"; $PY eval/synth/run_synth.py --report "$r" | grep '^all\|J:all\|unjudged'; done
