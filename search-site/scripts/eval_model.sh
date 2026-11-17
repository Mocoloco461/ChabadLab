#!/bin/bash
# Evaluate one embedding index on both the main set and the holdout set.
# Starts a temporary server on port 8766 with IGROT_EMB=<folder>, runs run_eval.py twice, stops it.
#
#   search-site/scripts/eval_model.sh <emb-slug> "<label>"
#   e.g. search-site/scripts/eval_model.sh BAAI__bge-m3 "bge-m3 GPU"
#        search-site/scripts/eval_model.sh BAAI__bge-m3,intfloat__multilingual-e5-large "ensemble"
#
# Env: PY (python, default .venv), PORT (default 8766).
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SLUG="$1"; LABEL="$2"
PORT="${PORT:-8766}"
if [ -z "${PY:-}" ]; then
  if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PY="$ROOT/.venv/Scripts/python.exe"; else PY="$ROOT/.venv/bin/python"; fi
fi
export PYTHONUTF8=1
cd "$ROOT/search-site/app"
# several slugs (comma-separated) = ensemble of models on the same chunks
EMBROOT="$ROOT/search-site/data/emb"
command -v cygpath >/dev/null && EMBROOT=$(cygpath -m "$EMBROOT")  # Git Bash on Windows: native paths
EMB=$(echo "$SLUG" | tr ',' '\n' | sed "s#^#$EMBROOT/#" | paste -sd, -)
IGROT_EMB="$EMB" "$PY" -m uvicorn server:app --host 127.0.0.1 --port "$PORT" \
  --log-level warning > "$ROOT/search-site/data/eval_server_$PORT.log" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
for i in $(seq 1 120); do
  curl -s -o /dev/null "http://127.0.0.1:$PORT/api/search?q=test&mode=semantic" && break; sleep 1
  kill -0 $SRV 2>/dev/null || break
done
if ! curl -s -o /dev/null "http://127.0.0.1:$PORT/api/meta"; then
  echo "server did not start:"; tail -20 "$ROOT/search-site/data/eval_server_$PORT.log"; exit 1
fi
cd "$ROOT"
H=search-site/eval/history.csv
"$PY" search-site/eval/run_eval.py --server "http://127.0.0.1:$PORT" --label "$LABEL" > /dev/null
tail -1 "$H"
"$PY" search-site/eval/run_eval.py --server "http://127.0.0.1:$PORT" \
  --cases search-site/eval/holdout/holdout1.jsonl --label "HOLDOUT $LABEL" > /dev/null
tail -1 "$H"
if [ -f search-site/eval/holdout/holdout2.jsonl ]; then
  "$PY" search-site/eval/run_eval.py --server "http://127.0.0.1:$PORT" \
    --cases search-site/eval/holdout/holdout2.jsonl --label "HOLDOUT2 $LABEL" > /dev/null
  tail -1 "$H"
fi
