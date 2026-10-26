#!/bin/zsh
cd /Users/yuyao/Projects/interesting_prediction_markets
export PYTHONPATH=.
COPY_GROUPS=random1000 .venv/bin/python -W ignore scripts/run_full.py copy || echo "CHAIN: copy failed"
.venv/bin/python -W ignore scripts/run_full.py pnl markets concentration persistence calibration markout copy_summary || echo "CHAIN: main failed"
echo "CHAIN: finished"
