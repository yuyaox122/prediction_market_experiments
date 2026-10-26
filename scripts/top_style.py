"""Trading style of the top-100 (period-A pnl) wallets: maker share, both-sides trading, fills per market, and period-B outcome."""
import duckdb, pandas as pd
from pm.db import CACHE, ROOT
pd.set_option('display.width', 250)
con = duckdb.connect(); con.execute("SET memory_limit='3GB'; SET threads=3")
wp = pd.read_parquet(CACHE / 'wallet_periods.parquet')
top = wp.sort_values('pnl', ascending=False).head(100)[['wallet', 'pnl', 'roi', 'n_markets', 'pnl_b', 'roi_b']]
con.register('top', top)
df = con.sql(f"""
with p as (select wallet, condition_id, asset_id, sum(notional) notional, sum(taker_notional) tn, sum(n_fills) nf
           from read_parquet('{CACHE}/wm_parts/2*.parquet') where wallet in (select wallet from top) group by 1,2,3),
 m as (select wallet, condition_id, count(distinct asset_id)=2 both_sides, sum(notional) notional, sum(tn) tn, sum(nf) nf from p group by 1,2)
select wallet, count(*) n_mkts, avg(both_sides::int) both_sides_frac, sum(tn)/sum(notional) taker_share, sum(nf) fills, sum(notional)/2 notional, sum(nf)/count(*) fills_per_mkt
from m group by 1""").df()
t = top.merge(df, on='wallet')
t['style'] = pd.cut(t.taker_share, [-0.01, 0.3, 0.7, 1.01], labels=['maker (<30% taker)', 'mixed', 'taker (>70% taker)'])
g = t.groupby('style', observed=True).agg(n=('wallet', 'size'), pnl_A=('pnl', 'sum'), pnl_B=('pnl_b', 'sum'), still_active=('pnl_b', lambda s: s.notna().sum()),
                                        profitable_B=('pnl_b', lambda s: (s.dropna() > 0).mean()), both_sides_frac=('both_sides_frac', 'mean'),
                                        median_fills_per_mkt=('fills_per_mkt', 'median'), median_markets=('n_markets', 'median'))
print(g.round(3).to_string()); g.to_csv(ROOT / 'reports' / 'top100_style_summary.csv'); t.to_csv(ROOT / 'reports' / 'top100_style.csv', index=False)
print(t.sort_values('pnl', ascending=False).head(10)[['wallet', 'pnl', 'pnl_b', 'n_mkts', 'both_sides_frac', 'taker_share', 'fills_per_mkt', 'style']].round(3).to_string())
