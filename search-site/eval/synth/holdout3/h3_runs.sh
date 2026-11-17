#!/bin/bash
# holdout3 across configs: temporary server on 8767 per config, igrot-kodesh's run_eval.py (qrels metrics)
set -u
ROOT=C:/Users/USER/Documents/Moshe/ChabadLab/.claude/worktrees/synthetic-queries-37c934
S=C:/Users/USER/AppData/Local/Temp/claude/c4d977f846a31699/14af95fa-d365-4cad-89d5-9795efcc0aaf/scratchpad/h3
PY=C:/Users/USER/Documents/Moshe/ChabadLab/.venv/Scripts/python.exe
MD=C:/Users/USER/Documents/Moshe/ChabadLab/search-site/data/emb; W=$ROOT/search-site/data/emb
export IGROT_DATA=C:/Users/USER/Documents/Moshe/ChabadLab/search-site/data IGROT_EMBED_API=1 IGROT_DEVICE=cpu PYTHONUTF8=1
run() {  # label rerank emb
  cd $ROOT/search-site/app
  IGROT_RERANK=$2 IGROT_EMB=$3 $PY -m uvicorn server:app --host 127.0.0.1 --port 8767 --log-level warning > $S/server.log 2>&1 &
  SRV=$!
  for i in $(seq 1 180); do curl -s -o /dev/null "http://127.0.0.1:8767/api/search?q=test&mode=semantic" && break; sleep 1; done
  $PY $S/eval/run_eval.py --server http://127.0.0.1:8767 --cases $S/eval/holdout/holdout3.jsonl --qrels $S/eval/qrels/holdout3.jsonl --label "H3 $1" > $S/out_$(echo $1 | tr ' +' '__').txt
  kill $SRV; sleep 3
  $PY -c "
import json,sys; t=open(sys.argv[1],encoding='utf-8').read(); d=json.loads(t[:t.index('\n}\n')+3])
print(f\"{sys.argv[2]:<40} strict {d['overall_pct']:5}%  \", {k:v for k,v in d.items() if k in ('mrr','any_rel10_pct','any_rel2_10_pct','ndcg10','unjudged10_pct','mrr10')}, d.get('by_mode'))" $S/out_$(echo $1 | tr ' +' '__').txt "$1"
}
run "base q4b+e5l norerank" off "$MD/Qwen__Qwen3-Embedding-4B,$MD/intfloat__multilingual-e5-large"
run "q8b+e5l norerank" off "$W/qwen__qwen3-embedding-8b-api,$MD/intfloat__multilingual-e5-large"
run "q8b+e5l d2q-all norerank" off "$W/d2q_sqk/q8b,$W/d2q_sqk/e5l"
run "q8b+e5l d2q-noeval norerank" off "$W/d2q_sqk_noeval/q8b,$W/d2q_sqk_noeval/e5l"
run "q8b+e5l cohere5fast" api:cohere/rerank-5-fast "$W/qwen__qwen3-embedding-8b-api,$MD/intfloat__multilingual-e5-large"
run "q8b+e5l d2q-all cohere5fast" api:cohere/rerank-5-fast "$W/d2q_sqk/q8b,$W/d2q_sqk/e5l"
run "q8b+e5l d2q-noeval cohere5fast" api:cohere/rerank-5-fast "$W/d2q_sqk_noeval/q8b,$W/d2q_sqk_noeval/e5l"
