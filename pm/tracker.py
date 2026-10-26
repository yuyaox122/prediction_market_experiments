"""Live wallet tracker: poll the Data API for new fills from a watch-list of wallets and flag big bets.

The backtests show copying is not profitable after the delay, so this is a *research alert*, not a signal.
Flags: notional >= min_notional, optionally only long-shot prices (<= max_price) or late bets (near market end).
State (last seen tx per wallet) is kept in data/cache/tracker_state.json.
"""
from __future__ import annotations
import json, time, requests
from pathlib import Path
from pm.db import CACHE
API = "https://data-api.polymarket.com"
STATE = CACHE / "tracker_state.json"

def fetch_trades(wallet: str, limit=100):
    r = requests.get(f"{API}/trades", params=dict(user=wallet, limit=limit, takerOnly="false"), timeout=30)
    r.raise_for_status(); return r.json()

def poll(wallets: list[str], min_notional=1000.0, max_price=1.0, state: dict | None = None):
    state = state if state is not None else (json.load(open(STATE)) if STATE.exists() else {})
    alerts = []
    for w in wallets:
        try: trades = fetch_trades(w)
        except Exception as e: print("fetch failed", w[:10], e); continue
        seen = set(state.get(w, []))
        new = [t for t in trades if t["transactionHash"] + str(t["timestamp"]) + t["asset"] not in seen]
        for t in new:
            notional = float(t["size"]) * float(t["price"])
            if notional >= min_notional and float(t["price"]) <= max_price:
                alerts.append(dict(wallet=w, name=t.get("name"), ts=t["timestamp"], side=t["side"], outcome=t["outcome"],
                                   price=float(t["price"]), notional=round(notional, 2), title=t["title"], slug=t["slug"]))
        state[w] = [t["transactionHash"] + str(t["timestamp"]) + t["asset"] for t in trades][:500]
        time.sleep(0.2)
    STATE.write_text(json.dumps(state))
    return alerts

def run(wallets, interval_s=60, **kw):
    first = True
    while True:
        al = poll(wallets, **kw)
        if first: print(f"baseline set for {len(wallets)} wallets; {len(al)} existing big bets:"); first = False
        for a in al: print(time.strftime("%H:%M:%S", time.gmtime(a["ts"])), a["name"] or a["wallet"][:8], a["side"], f"${a['notional']:,.0f}", a["outcome"], f"@{a['price']:.2f}", "|", a["title"])
        time.sleep(interval_s)
