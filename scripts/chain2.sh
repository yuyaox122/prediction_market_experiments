#!/bin/zsh
cd /Users/yuyao/Projects/interesting_prediction_markets
export PYTHONPATH=.
.venv/bin/python -W ignore scripts/run_full.py markout || echo "CHAIN: markout failed"
COPY_GROUPS=random1000 .venv/bin/python -W ignore scripts/run_full.py copy || echo "CHAIN: copy failed"
.venv/bin/python -W ignore scripts/run_full.py copy_summary || echo "CHAIN: summary failed"
echo "CHAIN: finished"
