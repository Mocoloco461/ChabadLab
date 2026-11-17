#!/bin/bash
# restart the search server (kills by PID file / exact uvicorn binary path)
PIDF=/root/ChabadLab/search-site/data/server.pid
[ -f $PIDF ] && kill $(cat $PIDF) 2>/dev/null
for p in $(pgrep -f "^/root/venv-igrot/bin/python3? /root/venv-igrot/bin/uvicorn"); do kill $p 2>/dev/null; done
sleep 1
cd /root/ChabadLab/search-site/app
setsid nohup /root/venv-igrot/bin/uvicorn server:app --host 0.0.0.0 --port 8765 >> /root/ChabadLab/search-site/data/server.log 2>&1 < /dev/null &
echo $! > $PIDF
for i in $(seq 1 30); do curl -s -o /dev/null localhost:8765/api/meta && echo "server up" && exit 0; sleep 1; done
echo "server failed"; tail -20 /root/ChabadLab/search-site/data/server.log
