#!/bin/bash
# stop whatever listens on $1 (the venv python.exe is a launcher: killing it can leave the real server alive)
for i in 1 2 3 4 5 6 7 8 9 10; do
  pids=$(powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort $1 -State Listen -ErrorAction SilentlyContinue).OwningProcess" | tr -d '\r')
  [ -z "$pids" ] && exit 0
  for p in $pids; do powershell -NoProfile -Command "Stop-Process -Id $p -Force -ErrorAction SilentlyContinue"; done
  sleep 1
done
echo "port $1 still busy" >&2; exit 1
