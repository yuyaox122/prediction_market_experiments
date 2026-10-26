"""Skill persistence: rank wallets on period A, measure on period B (out of sample).

Wallet-period aggregates come from the `wm` table (wallet x condition pnl). A market is assigned to
the period of its resolution-close (close_ts) so that A and B do not share markets.
Metrics: pnl, ROI = pnl / risk (cost basis), n_markets. Rank on ROI with minimum activity filters,
then measure fraction profitable and mean ROI in B; compare to the base rate of all comparable wallets.
"""
from __future__ import annotations
import pandas as pd, numpy as np

def wallet_period(con, wm="wm", split_ts: int = 0, min_markets=20, min_risk=10_000.0):
    con.execute(f"""
    CREATE OR REPLACE TABLE wp AS
    SELECT wallet, (last_ts >= {split_ts}) AS period_b,
           sum(pnl) AS pnl, sum(risk_usdc) AS risk, count(*) AS n_markets, sum(notional) AS notional,
           sum(taker_notional)/sum(notional) AS taker_share
    FROM {wm} GROUP BY 1,2
    """)
    a = con.sql(f"SELECT wallet, pnl, risk, n_markets, taker_share FROM wp WHERE NOT period_b AND n_markets>={min_markets} AND risk>={min_risk}").df()
    b = con.sql("SELECT wallet, pnl AS pnl_b, risk AS risk_b, n_markets AS n_b FROM wp WHERE period_b").df()
    a["roi"] = a.pnl / a.risk
    m = a.merge(b, on="wallet", how="left")
    m["roi_b"] = m.pnl_b / m.risk_b
    return m

def persistence_table(m: pd.DataFrame, top_ns=(100, 500, 2000)) -> pd.DataFrame:
    rows = []
    act = m.dropna(subset=["pnl_b"])
    base = dict(group="all comparable", n=len(m), still_active=len(act), frac_profitable_b=(act.pnl_b > 0).mean(),
                mean_roi_b=act.roi_b.mean(), median_roi_b=act.roi_b.median(), sum_pnl_b=act.pnl_b.sum())
    rows.append(base)
    for key in ("pnl", "roi"):
        s = m.sort_values(key, ascending=False)
        for n in top_ns:
            t = s.head(n); ta = t.dropna(subset=["pnl_b"])
            rows.append(dict(group=f"top{n}_by_{key}", n=len(t), still_active=len(ta), frac_profitable_b=(ta.pnl_b > 0).mean(),
                             mean_roi_b=ta.roi_b.mean(), median_roi_b=ta.roi_b.median(), sum_pnl_b=ta.pnl_b.sum()))
        t = s.tail(500); ta = t.dropna(subset=["pnl_b"])
        rows.append(dict(group=f"bottom500_by_{key}", n=len(t), still_active=len(ta), frac_profitable_b=(ta.pnl_b > 0).mean(),
                         mean_roi_b=ta.roi_b.mean(), median_roi_b=ta.roi_b.median(), sum_pnl_b=ta.pnl_b.sum()))
    return pd.DataFrame(rows)

def rank_correlation(m: pd.DataFrame):
    from scipy.stats import spearmanr
    act = m.dropna(subset=["roi_b"])
    return dict(n=len(act), spearman_roi=spearmanr(act.roi, act.roi_b).correlation,
                spearman_pnl=spearmanr(act.pnl, act.pnl_b).correlation)
