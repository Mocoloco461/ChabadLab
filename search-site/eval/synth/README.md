# Synthetic queries (OpenRouter)

Questions written by an LLM from a letter's text only, used two ways:
* **TEST set** (`synth_test.jsonl`) – measure the engine on many natural questions (2,318 cases on 600 letters).
* **doc2query** (`questions_train.jsonl` → `d2q_*.jsonl`) – extra indexed passages for every non-TEST letter.

The numbers and costs are in `WORKLOG.md` (repo root); spend per run is in `spend.csv`.

## Files

| file | what |
|---|---|
| `split.json` | fixed split: 600 TEST letters, 10,239 TRAIN letters, 223 excluded (used by the hand-written sets) |
| `questions_test.jsonl` | TEST generation v2 (Haiku 5.5, "real people" prompt). `_v1` = first prompt, before QC |
| `synth_test.jsonl` | the eval set, `run_eval.py` format (`modes: semantic, hybrid`, one source letter) |
| `questions_train.jsonl` | TRAIN generation ("content card" prompt, different wording) for TRAIN + the 223 used letters |
| `d2q_q.jsonl`, `d2q_sqk.jsonl` | doc2query passages: questions only / summary + questions + keywords |
| `judge_cases.json` | the fixed 600-question sample whose results get LLM relevance grades |
| `judgments.jsonl` | grades 0/1/2 for (question, letter) pairs ranked above the source letter |
| `runs/*.summary.json` | metrics of each engine run (the full `runs/*.jsonl` dumps are not committed) |
| `spend.csv` | every paid run: tokens, cost, key usage before/after |
| `pilot/` | 20-letter model comparison (Haiku vs DeepSeek) |

## Scripts (run from the repo root, `PYTHONUTF8=1`)

```bash
# key: env OPENROUTER_API_KEY or %USERPROFILE%/.openrouter_key; every paid script stops at $8.50 key usage
export IGROT_DATA=C:/Users/USER/Documents/Moshe/ChabadLab/search-site/data   # read-only data of the main checkout
python search-site/eval/synth/orclient.py                       # current key usage
python search-site/eval/synth/make_split.py                     # once; refuses to overwrite
python search-site/eval/synth/generate.py --pool test           # resumable
python search-site/eval/synth/build_set.py                      # -> synth_test.jsonl
python search-site/eval/synth/generate.py --pool train --include-used
python search-site/eval/synth/make_d2q.py --variant q

# engine run (temporary server on 8767, no GPU: query vectors + reranker through the API)
IGROT_EMBED_API=1 IGROT_DEVICE=cpu IGROT_RERANK=off IGROT_EMB=<folder>,<folder> \
  bash search-site/eval/synth/eval_synth.sh "<label>" --holdouts      # synth + main + holdout1/2
SYNTH_ARGS=--only-judged IGROT_RERANK=api:cohere/rerank-5-fast ...   # judged sample only (paid reranker)
python search-site/eval/synth/judge.py --runs search-site/eval/synth/runs/<label>.jsonl   # grade new pairs
python search-site/eval/synth/run_synth.py --report search-site/eval/synth/runs/<label>.jsonl

# prefetch query vectors once (64 per API call instead of one per query)
cd search-site/app && python remote.py <folder>,<folder> ../eval/synth/synth_test.jsonl ../eval/holdout/*.jsonl ../eval/cases/*.jsonl
```

## Metrics (`run_synth.py`)

* **strict**: hit@1, hit@10, MRR@10 of the source letter.
* **graded** (rows `J:` = the judged sample only): letters ranked above the source got 0/1/2 from the judge
  (2 = answers the question), the source counts as 2, unjudged = 0.
  `ghit@10` = a grade-2 letter in the top 10; `nDCG@10` with gains 2^g−1.
* Generic questions have many right answers in 11k letters, so strict hit@10 is low (~50–60%) while
  ghit@10 is ~90–98%. Compare engines on both.

## Rules

* TEST letters never get doc2query passages; holdouts are measured, never tuned on.
* The generator is never told the questions are for search or evaluation.
