"""Usage: PYTHONPATH=. .venv/bin/python scripts/track.py [--once] [--min 1000] [--max-price 1.0] [wallet ...]
Default watch-list: top wallets from reports/top200_wallets.csv if present, else the all-time leaderboard top 10."""
import sys, requests, pandas as pd
from pm.tracker import poll, run
from pm.db import ROOT
args = sys.argv[1:]; once = "--once" in args
mn = float(args[args.index("--min") + 1]) if "--min" in args else 1000.0
mp = float(args[args.index("--max-price") + 1]) if "--max-price" in args else 1.0
ws = [a for a in args if a.startswith("0x")]
if not ws:
    f = ROOT / "reports" / "top200_wallets.csv"
    if f.exists(): ws = pd.read_csv(f).wallet.head(50).tolist()
    else: ws = [x["proxyWallet"] for x in requests.get("https://data-api.polymarket.com/v1/leaderboard?timePeriod=all&orderBy=PNL&limit=10").json()]
if once:
    for a in poll(ws, min_notional=mn, max_price=mp, state={}): print(a)
else: run(ws, min_notional=mn, max_price=mp)
