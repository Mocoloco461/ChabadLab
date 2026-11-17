You are continuing the Igrot Kodesh search engine work (Hebrew search site for the Lubavitcher Rebbe's letters, 11,062 letters). Your job: merge two lines of work into one engine, run the next round of experiments (in parallel where possible), choose the best configuration, and report. Report to the user in simple Hebrew, in organized tables, so no number gets lost.

## Read first
- Repo: `C:\Users\USER\Documents\Moshe\ChabadLab` (git). Two branches matter, both LOCAL ONLY:
  - `igrot-kodesh`: the main session's line. It has holdout3 (the most objective test set, with judged relevance in `eval/qrels/`), spelling correction (`app/spell.py`), a recipient-name boost, sorting in meaning modes, a Gregorian year range, the dev set `eval/cases/dev_newtypes.jsonl`, `eval/judge.py prep|merge`, and qrels-aware `eval/run_eval.py`.
  - `claude/synthetic-queries-37c934` (worktree `C:\Users\USER\Documents\Moshe\ChabadLab\.claude\worktrees\synthetic-queries-37c934`): an engine with no GPU needed. It has OpenRouter embeddings and reranking (`app/remote.py`, with a cache), a Qwen3-Embedding-8B index built through the API, doc2query passages for every non-TEST letter, a synthetic TEST set, and an LLM judge (`search-site/eval/synth/`, see its `README.md`).
- Read the top 4 entries of `WORKLOG.md` on BOTH branches, `search-site/eval/synth/README.md`, and the hand-over note `synthetic-queries-handover.md` in `C:\Users\USER\AppData\Local\Claude-Data\claude_pzs8sxrjxfjjc\side-session-notes\local_d09b7952-5ddd-440a-b7a0-b7033b244e93\`.
- **Where things stand** (strict = the source letter in the top 10):

| config | main | holdout1 | holdout2 | holdout3 |
|---|---|---|---|---|
| igrot-kodesh engine (Qwen-4B + e5 + local bge rerank + spell + names) | 95.1 (89.7 on main+dev) | 90.8 | 87.4 | 76.9 |
| synth branch: Qwen-8B + e5, no rerank | 93.8 | 94.5 | 91.2 | 78.3 |
| synth branch: Qwen-8B + e5 + doc2query (summary + questions + keywords) | 97.6 | 97.7 | 92.3 | 86.9 |
| synth branch: the same + Cohere rerank-5-fast | 97.6 | 96.3 | 94.5 | 88.1 |

- **Known facts:**
  - doc2query MUST cover every letter. When the eval letters have no passage, scores drop to 88 / 90 / 88 / 72.
  - On holdout3, 50–60% of the synth branch's top-10 letters are unjudged, so its any_rel / ndcg numbers are not yet comparable.
  - The holdout3 weak types are topic (53–56%), name (77%), typo (82%), english (91%).

## Environment (Windows 11, Git Bash + PowerShell, RTX 4070 12GB shared with the main session)
- **Python:** `C:/Users/USER/Documents/Moshe/ChabadLab/.venv/Scripts/python.exe`. Always run with `PYTHONUTF8=1`.
- **Networking:** use `127.0.0.1`, never `localhost`. Your servers go on ports **8767 / 8768** (8765 / 8766 belong to the main session).
- **Paths:** pass Windows paths to Python. In Git Bash use `cygpath -m "$PWD"`. A `/c/Users/...` path makes the semantic index fail silently: semantic mode scores 0%. Check that semantic results exist before every eval.
- **Data:** the main data in `C:/Users/USER/Documents/Moshe/ChabadLab/search-site/data` is READ-ONLY for you; use it through `IGROT_DATA`. Things you build (indexes, a copy of `search.db` if you change it) go in your own worktree's `search-site/data/`.
- **Existing synth-branch indexes** (gitignored), under `...\worktrees\synthetic-queries-37c934\search-site\data\`:
  - `emb/qwen__qwen3-embedding-8b-api`
  - `emb/d2q_sqk/{q8b,e5l}`
  - `emb/d2q_sqk_noeval/{q8b,e5l}`
  - `api_cache.sqlite`, the query-vector and rerank cache. Copy it so you don't pay twice.
- **Engine switches:**
  - `IGROT_EMBED_API=1`: query vectors come from OpenRouter.
  - `IGROT_EMB=<folder>,<folder>`: an ensemble of index folders.
  - `IGROT_RERANK=off | api:cohere/rerank-5-fast | auto` (`auto` = local bge on the GPU).
  - `IGROT_DEVICE=cpu`: keeps the server off the GPU.
- **GPU:** before any GPU use, check `nvidia-smi` and ask the main session (SendMessage to `local_fed5408d-6152-44fc-97e6-5fcaa93f9a80`). Prefer the API path.
- **Prefetching:** prefetch query vectors once per new set: `cd search-site/app && python remote.py <folders> <cases.jsonl...>`. This takes 64 queries per call; otherwise each query costs about 5 s.

## OpenRouter key and budget (the user approved; about $20 of new credit was added)
- **Reading the key:** read it at runtime only, from env `OPENROUTER_API_KEY` or `%USERPROFILE%\.openrouter_key`. Never print it or write it to the repo, logs, WORKLOG or messages.
- **Before starting:** run `python search-site/eval/synth/orclient.py` (it prints key usage and limit). If the key limit is still $10, stop and ask the user to raise the key's limit in OpenRouter settings (about $30).
- **Budget:** about $20 for this round. Usage now is about $6.70. Set `SYNTH_STOP_USD` to (limit − 1.5); the scripts stop by themselves at that line.
- **Logging:** log every paid run to `search-site/eval/synth/spend.csv` (`orclient.run_jobs`, or wrap other commands in `spend_wrap.py`).
- **At the stop line:** stop and send the user a PushNotification in Hebrew: what is done, what is left, how much more it needs.
- **Prices:** Haiku 5.5 $0.10 / $0.50 per M (always `reasoning: {"enabled": false}`; this is already the default in `orclient.chat`). Cohere rerank-5-fast about $0.0004 per query, rerank-5-pro about $0.0009. Qwen3-8B embeddings $0.01 per M.

## Rules
- **Git:** create your own branch from `igrot-kodesh` (for example `claude/merged-engine`) in a new worktree. Do NOT commit to `igrot-kodesh` or the synth branch. NEVER `git push`. Commit locally after each stage, with a WORKLOG entry in the existing Hebrew style (what, commit, numbers, cost in $).
- **Decisions:** make them ONLY on the main set + the dev set (`eval/cases/*.jsonl`) + the synth TEST set (`search-site/eval/synth/synth_test.jsonl`, judged sample `judge_cases.json`).
- **Holdouts:** holdout1/2/3 are measured once per final candidate, never tuned on.
- **Generation prompts:** they never mention search, retrieval or evaluation. TEST-pool letters (`split.json`) never get doc2query passages, except in a clearly labeled "leaky upper bound" run.
- **Stale numbers:** do not report a number from a run whose server failed (check errors and per-mode results).
- **Feedback from real users:** out of scope for now (the user will plan it later).

## Plan (run independent steps in parallel: background processes, subagents, or side sessions for whole independent steps)

**Stage 1: merge (serial, first)**
1. New worktree + branch from `igrot-kodesh`; `git merge claude/synthetic-queries-37c934`. The only expected conflicts are `WORKLOG.md` and `search-site/eval/history.csv`; keep both sides. Run the app once to check that spell + names + remote embeddings all work together.
2. Copy the synth-branch indexes and `api_cache.sqlite` into your worktree's `search-site/data/`.

**Stage 2: run these in parallel**
- **A. Measure the combined engine:** spell + names + Qwen-8B + e5 + doc2query(sqk), without rerank and with Cohere-fast. Sets: main + dev, synth (full set without rerank; the judged sample with rerank), and then holdout1/2/3 for measurement only. Also measure the same without doc2query, so the gain is visible.
- **B. Judge holdout3:** judge the new holdout3 pairs with the SAME protocol the main session used (Sonnet subagents with no context, `eval/judge.py prep|merge`; see the holdout3 WORKLOG entry), so any_rel@10 and ndcg@10 become comparable. This costs $0 in OpenRouter.
- **C. doc2query v2 (API, about $5):** a new generation for all non-TEST letters + the 343 eval letters. Keep Hebrew questions and add:
  - 2–3 English questions
  - the recipient (give the model `recipient_note` / `recipient_info` together with the letter)
  - 2–4 very short topic phrases (1–3 words)
  - Keep the "content card" framing.
  - First pilot it on 20 letters and have a Sonnet subagent spot-check the quality. Then generate everything (resumable `generate.py`, about 24 workers) and embed it with `build_embeddings_api.py --base ... --extra ...` for q8b and e5l.
  - Try both one passage and several passages per letter if cheap.
- **D. doc2query in the lexical side:** add the doc2query text to the lexical side of hybrid (a copy of `search.db` in your data dir, with an extra FTS column or table; never write to the main `search.db`). Measure on main + dev + synth.
- **E. A second embedding model, about $2:** gemini-embedding-2 (or voyage-4-large if gemini fails in Hebrew), with the same chunks + doc2query extras. Compare it alone and in an ensemble with q8b / e5l.
- **F. Reranker:** Cohere rerank-5-pro vs rerank-5-fast, on the judged synth sample + main + dev. Use local bge only if the GPU is free and the main session agrees.

**Stage 3: decide and finish (serial)**
3. Judge any new above-source synth pairs (`search-site/eval/synth/judge.py`, Haiku, about $1).
4. Choose the final configuration on main + dev + synth only. Then measure it once on holdout1/2/3, plus a "doc2query without eval letters" lower bound, for honesty.
5. **Wire it into the server:** an automatic preference for the new index folders when they exist; the API path when a key is present; fall back to the local configuration when it is not. Do not change what the user's live site runs without the user's approval.
6. Write the WORKLOG entry and a hand-over note in the shared notes folder, and tell the main session (SendMessage) what changed.
7. **Final report** to the user in simple Hebrew:
   - one table of every configuration × every set (strict, MRR, any_rel@10)
   - what was chosen and why
   - cost per step and total
   - ongoing cost per 1,000 queries / day
   - what is still open
