#!/bin/bash
# One configuration end to end: start a server with the current IGROT_* env on $PORT, run sets, stop it.
#   PORT=8768 IGROT_EMB=... config.sh "<label>" main synth ...
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HERE/logs"; SLUG=$(echo "$1" | tr -c 'A-Za-z0-9._-' '_')
PID=$(bash "$HERE/serve.sh" "$HERE/logs/$SLUG.server.log") || { echo "[$1] SERVER FAILED"; exit 1; }
bash "$HERE/run_sets.sh" "$@"
kill $PID 2>/dev/null; bash "$HERE/stop_port.sh" "${PORT:-8767}"
grep -ci 'error\|traceback' "$HERE/logs/$SLUG.server.log" | sed "s/^/[$1] server log error lines: /"
