"""Exact reconciliation: our wallet x condition pnl vs Data-API closed positions aggregated per condition."""
import requests, time, statistics, sys
from pm.db import connect, register_fills
from pm.pnl import wallet_market_pnl
con=connect(); register_fills(con,'2025_07_01','2025_07_31'); wallet_market_pnl(con)
con.execute("create or replace table mk as select condition_id, any_value(opens_ts) o, any_value(close_ts) c, any_value(resolution_status) rs from fills group by 1")
inwin="o>=epoch(timestamp '2025-07-01') and c<=epoch(timestamp '2025-07-30') and rs='resolved'"
ws=con.sql(f"select wm.wallet, count(*) n, sum(abs(wm.pnl)) p from wm join mk using(condition_id) where {inwin} group by 1 having count(*) between 20 and 300 order by p desc limit 8").fetchall()
allrows=[]
for w,n,p in ws:
    ours={cid:pnl for cid,pnl in con.sql(f"select wm.condition_id, wm.pnl from wm join mk using(condition_id) where wallet='{w}' and {inwin}").fetchall()}
    data=[]; cursor=None
    for _ in range(60):
        q={'user':w,'status':'CLOSED','limit':500}
        if cursor: q['cursor']=cursor
        r=requests.get("https://data-api.polymarket.com/v2/positions",params=q,timeout=60)
        if r.status_code!=200: print('HTTP',r.status_code); break
        j=r.json(); data+=j.get('data',[]); cursor=(j.get('pagination') or {}).get('next_cursor')
        if not cursor or not j.get('data'): break
        time.sleep(0.15)
    api={}
    for pz in data:
        cid=pz['condition_id']
        if cid in ours: api[cid]=api.get(cid,0.0)+float(pz.get('realized_pnl') or 0)+float(pz.get('unrealized_pnl') or 0)
    pairs=[(ours[c],api[c]) for c in api]
    if not pairs: print(w[:10],'no overlap'); continue
    d=[abs(a-b) for a,b in pairs]; rel=[abs(a-b)/max(1,abs(b)) for a,b in pairs]
    exact=sum(1 for x in d if x<1.0)
    print(f"{w[:12]} markets={len(pairs)} ours={sum(a for a,_ in pairs):12.2f} api={sum(b for _,b in pairs):12.2f} within$1={exact}/{len(pairs)} median_rel={statistics.median(rel):.2e} max_abs={max(d):.2f}")
    worst=sorted(((abs(a-b),a,b,c) for c,(a,b) in zip(api,pairs)), reverse=True)[:2]
    for x in worst: print('    worst', x[1:],)
    allrows+=pairs
print(f"TOTAL markets={len(allrows)} ours={sum(a for a,_ in allrows):.2f} api={sum(b for _,b in allrows):.2f} within$1={sum(1 for a,b in allrows if abs(a-b)<1)/len(allrows):.3f}")
