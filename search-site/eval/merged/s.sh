#!/bin/bash
# summarize the run_eval results named in the given logs
for l in "$@"; do f=$(grep -o 'results.[0-9-]*\.json' "$l" | tail -1); C:/Users/USER/Documents/Moshe/ChabadLab/.venv/Scripts/python.exe "$(dirname "$0")/summ.py" --types "$(dirname "$0")/../$f"; done
