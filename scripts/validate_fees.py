"""Which fee model matches Polymarket's own per-position entry_fees_usdc and realized pnl? (April 2026, fee markets)"""
import requests, time, pandas as pd, numpy as np
from pm.db import connect, register_fills
pd.set_option('display.width', 220)
con = connect(mem_gb=2, threads=2); register_fills(con, '2026_04_01', '2026_04_28')
con.execute("""create table legs as
 select taker wallet, condition_id, pay, price, taker_direction, usdc_amount, fee_usdc, true is_taker,
        case when taker_direction='BUY' then usdc_amount/price else -usdc_amount/price end d_shares,
        case when taker_direction='BUY' then -usdc_amount else usdc_amount end d_cash
 from fills where pay is not null and taker_base_fee>0
 union all
 select maker, condition_id, pay, price, taker_direction, usdc_amount, fee_usdc, false,
        case when taker_direction='BUY' then -usdc_amount/price else usdc_amount/price end,
        case when taker_direction='BUY' then usdc_amount else -usdc_amount end
 from fills where pay is not null and taker_base_fee>0""")
con.execute("""create table wmf as select wallet, condition_id,
  sum(d_cash)+sum(d_shares*pay) pnl_nofee,
  sum(case when is_taker then fee_usdc end) fee_taker_raw,
  sum(case when is_taker then fee_usdc*(case when taker_direction='SELL' then price else 1 end) end) fee_taker_conv,
  sum(case when not is_taker then fee_usdc end) fee_maker_raw,
  sum(case when not is_taker then fee_usdc*(case when taker_direction='SELL' then price else 1 end) end) fee_maker_conv,
  sum(case when is_taker then usdc_amount end) taker_notional, sum(usdc_amount) notional, count(*) n
 from legs group by 1,2""")
ws = con.sql("""select wallet, count(*) n, sum(taker_notional)/sum(notional) ts from wmf group by 1 having count(*) between 30 and 400 and sum(notional)>20000 order by random() limit 10""").df()
rows = []
for w, n, ts in ws.itertuples(index=False):
    ours = con.sql(f"select * from wmf where wallet='{w}'").df().set_index('condition_id')
    data = []; cursor = None
    for _ in range(40):
        q = {'user': w, 'status': 'CLOSED', 'limit': 500}
        if cursor: q['cursor'] = cursor
        r = requests.get("https://data-api.polymarket.com/v2/positions", params=q, timeout=60)
        if r.status_code != 200: break
        j = r.json(); data += j.get('data', []); cursor = (j.get('pagination') or {}).get('next_cursor')
        if not cursor or not j.get('data'): break
        time.sleep(0.15)
    api = {}
    for pz in data:
        c = pz['condition_id']
        if c in ours.index:
            a = api.setdefault(c, dict(pnl=0.0, fee=0.0)); a['pnl'] += float(pz.get('realized_pnl') or 0) + float(pz.get('unrealized_pnl') or 0); a['fee'] += float(pz.get('entry_fees_usdc') or 0)
    for c, a in api.items():
        o = ours.loc[c]; rows.append(dict(wallet=w[:8], taker_share=ts, api_pnl=a['pnl'], api_fee=a['fee'], pnl_nofee=o.pnl_nofee,
                                        ftr=o.fee_taker_raw or 0, ftc=o.fee_taker_conv or 0, fmr=o.fee_maker_raw or 0, fmc=o.fee_maker_conv or 0))
df = pd.DataFrame(rows); print(len(df), "wallet-markets;", df.wallet.nunique(), "wallets")
print("API fee vs candidates (sum):", df[['api_fee', 'ftr', 'ftc', 'fmr', 'fmc']].sum().round(0).to_dict())
for name, fee in [('nofee', 0), ('taker_raw', df.ftr), ('taker_conv', df.ftc), ('maker_raw', df.fmr), ('maker_conv', df.fmc), ('both_conv', df.ftc + df.fmc)]:
    est = df.pnl_nofee - fee; d = (est - df.api_pnl).abs()
    print(f"{name:12s} within$1={np.mean(d < 1):.3f} median_abs={d.median():8.2f} sum_ours={est.sum():12.0f} sum_api={df.api_pnl.sum():12.0f}")
print(df.groupby('wallet').agg(n=('api_pnl', 'size'), api_fee=('api_fee', 'sum'), ftc=('ftc', 'sum'), fmc=('fmc', 'sum'), ftr=('ftr', 'sum'), ts=('taker_share', 'first')).round(1).to_string())
