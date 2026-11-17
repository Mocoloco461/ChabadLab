#!/bin/bash
# Run eval sets against a server that is already up.
#   PORT=8767 run_sets.sh "<label>" main synth synthJ holdouts
# main = gen1-3 + dev_newtypes (run_eval.py, qrels-aware); synth = full synth TEST set; synthJ = judged
# sample only (for paid rerankers); holdouts = holdout1/2/3 (measurement only, never tuned on).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
PY="${PY:-C:/Users/USER/Documents/Moshe/ChabadLab/.venv/Scripts/python.exe}"; PORT="${PORT:-8767}"
export PYTHONUTF8=1
S="http://127.0.0.1:$PORT"; LABEL="$1"; shift
cd "$ROOT"
OUT="$HERE/logs"; mkdir -p "$OUT"; SLUG=$(echo "$LABEL" | tr -c 'A-Za-z0-9._-' '_')
for set in "$@"; do
  case $set in
    main) "$PY" eval/run_eval.py --server "$S" --label "MERGED main+dev $LABEL" > "$OUT/$SLUG.main.log" 2>&1 ;;
    synth) "$PY" eval/synth/run_synth.py --server "$S" --label "M_$LABEL" --workers "${WORKERS:-6}" > "$OUT/$SLUG.synth.log" 2>&1 ;;
    synthJ) "$PY" eval/synth/run_synth.py --server "$S" --label "MJ_$LABEL" --workers "${WORKERS:-6}" --only-judged > "$OUT/$SLUG.synthJ.log" 2>&1 ;;
    holdouts) for h in 1 2 3; do
        "$PY" eval/run_eval.py --server "$S" --cases eval/holdout/holdout$h.jsonl --label "MERGED HOLDOUT$h $LABEL" > "$OUT/$SLUG.h$h.log" 2>&1
      done ;;
  esac
  echo "[$LABEL] $set done"
done
