#!/bin/bash
# Start a temporary server from THIS checkout on port 8767 (data read-only via IGROT_DATA), run the synth
# TEST set (and optionally the holdouts), stop the server.
#
#   search-site/eval/synth/eval_synth.sh "<label>" [--holdouts]
# Env: PY, PORT (8767), IGROT_DATA (default: main checkout data), any IGROT_* engine settings.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
MAIN="C:/Users/USER/Documents/Moshe/ChabadLab"
PY="${PY:-$MAIN/.venv/Scripts/python.exe}"
PORT="${PORT:-8767}"
export IGROT_DATA="${IGROT_DATA:-$MAIN/search-site/data}"
export PYTHONUTF8=1
LABEL="$1"
LOG="$HERE/runs/server_$PORT.log"; mkdir -p "$HERE/runs"
cd "$ROOT/search-site/app"
"$PY" -m uvicorn server:app --host 127.0.0.1 --port "$PORT" --log-level warning > "$LOG" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
for i in $(seq 1 180); do
  curl -s -o /dev/null "http://127.0.0.1:$PORT/api/search?q=test&mode=semantic" && break; sleep 1
  kill -0 $SRV 2>/dev/null || break
done
curl -s -o /dev/null "http://127.0.0.1:$PORT/api/meta" || { echo "server did not start:"; tail -20 "$LOG"; exit 1; }
cd "$ROOT"
"$PY" "$HERE/run_synth.py" --server "http://127.0.0.1:$PORT" --label "$LABEL"
if [ "${2:-}" = "--holdouts" ]; then
  for f in search-site/eval/holdout/holdout1.jsonl search-site/eval/holdout/holdout2.jsonl; do
    "$PY" search-site/eval/run_eval.py --server "http://127.0.0.1:$PORT" --cases "$f" \
      --label "SYNTH-BRANCH $(basename $f .jsonl) $LABEL" | head -4
  done
fi
