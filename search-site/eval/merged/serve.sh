#!/bin/bash
# Start a temporary server from THIS checkout (data read-only via IGROT_DATA), wait until semantic search
# answers, print its PID. Usage: PORT=8767 IGROT_EMB=... serve.sh <logfile>
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
MAIN="C:/Users/USER/Documents/Moshe/ChabadLab"
PY="${PY:-$MAIN/.venv/Scripts/python.exe}"; PORT="${PORT:-8767}"
export IGROT_DATA="${IGROT_DATA:-$MAIN/search-site/data}" PYTHONUTF8=1
export IGROT_API_CACHE="${IGROT_API_CACHE:-$(cygpath -m "$ROOT")/data/api_cache.sqlite}"
cd "$ROOT/app"
# a server already on this port would answer the readiness check and the run would measure the wrong config
if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/meta"; then echo "port $PORT is busy" >&2; exit 1; fi
"$PY" -m uvicorn server:app --host 127.0.0.1 --port "$PORT" --log-level warning > "$1" 2>&1 &
SRV=$!
for i in $(seq 1 300); do
  n=$(curl -s "http://127.0.0.1:$PORT/api/search?q=%D7%97%D7%99%D7%A0%D7%95%D7%9A&mode=semantic" | "$PY" -c "import json,sys;d=json.load(sys.stdin);print(len(d.get('results',[])))" 2>/dev/null)
  [ "${n:-0}" -gt 0 ] && { echo $SRV; exit 0; }
  kill -0 $SRV 2>/dev/null || break; sleep 1
done
echo "server failed:" >&2; tail -20 "$1" >&2; kill $SRV 2>/dev/null; exit 1
