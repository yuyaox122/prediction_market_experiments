"""Reconcile our July-2025 fills against Polymarket's own numbers (Gamma volume, Data-API closed positions)."""
import requests, json, time
from pm.db import connect, register_fills
from pm.pnl import wallet_market_pnl
con=connect(); register_fills(con,'2025_07_01','2025_07_31'); wallet_market_pnl(con)
con.execute("create or replace table mk as select condition_id, any_value(market_slug) slug, any_value(opens_ts) o, any_value(close_ts) c, any_value(resolution_status) rs, sum(usdc_amount) vol, count(*) n from fills group by 1")
inwin = "o>=epoch(timestamp '2025-07-01') and c<=epoch(timestamp '2025-07-30') and rs='resolved'"
rows=con.sql(f"select condition_id, slug, vol from mk where {inwin} order by vol desc limit 12").fetchall()
print("== Gamma volume reconciliation (markets opened+closed inside July) ==")
for cid, slug, vol in rows:
    r=requests.get(f"https://gamma-api.polymarket.com/markets/slug/{slug}",timeout=30)
    if r.status_code!=200: print(slug, 'HTTP', r.status_code); continue
    m=r.json(); gv=float(m.get('volumeNum') or m.get('volume') or 0)
    print(f"{slug[:50]:50s} ours={vol:14.2f} gamma={gv:14.2f} ratio={vol/gv if gv else float('nan'):.4f}")
    time.sleep(0.2)
print("\n== Data-API closed positions vs our wallet-market pnl ==")
# wallets with the most resolved in-window markets
ws=con.sql(f"""select wm.wallet, count(*) n, sum(wm.pnl) p from wm join mk using(condition_id) where {inwin} group by 1 having count(*)>=20 order by abs(p) desc limit 6""").fetchall()
for w,n,p in ws:
    ours={cid:(pnl,bu) for cid,pnl,bu in con.sql(f"select wm.condition_id, wm.pnl, wm.bought_usdc from wm join mk using(condition_id) where wallet='{w}' and {inwin}").fetchall()}
    data=[]; cursor=None
    for _ in range(40):
        q={'user':w,'status':'CLOSED','limit':500}
        if cursor: q['cursor']=cursor
        r=requests.get("https://data-api.polymarket.com/v2/positions",params=q,timeout=60)
        if r.status_code!=200: print('HTTP',r.status_code,r.text[:200]); break
        j=r.json(); data+=j.get('data',[]); cursor=(j.get('pagination') or {}).get('next_cursor') or (j.get('pagination') or {}).get('cursor')
        if not cursor or not j.get('data'): break
        time.sleep(0.2)
    if data and _==0: print('sample keys', list(data[0].keys()))
    hits=[]
    for pz in data:
        cid=pz.get('condition_id') or pz.get('conditionId')
        if cid in ours:
            theirs=pz.get('realized_pnl', pz.get('realizedPnl')); 
            hits.append((cid, ours[cid][0], theirs))
    if hits:
        import statistics
        diffs=[abs(a-float(b)) for _,a,b in hits if b is not None]
        print(f"wallet {w[:10]} our_mkts={len(ours)} api_closed={len(data)} matched={len(hits)} our_sum={sum(a for _,a,_ in hits):.2f} api_sum={sum(float(b) for _,_,b in hits if b is not None):.2f} median_absdiff={statistics.median(diffs) if diffs else None}")
        for h in hits[:3]: print('   ', h)
    else:
        print(f"wallet {w[:10]} our_mkts={len(ours)} api_closed={len(data)} matched=0", (data[0] if data else None))
