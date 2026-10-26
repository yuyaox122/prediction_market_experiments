"""Calibration / favourite-longshot analysis from first principles.

For every taker BUY of a token at price p (notional-weighted or count-weighted), the realised
payoff is 1 if that token won else 0. Calibration curve: E[won | p]. Edge of buying at p is
E[won|p] - p - fee. Buckets by price; also by category and by time-to-close.
"""
from __future__ import annotations
import numpy as np, pandas as pd

def calibration_sql(src="fills", bucket=0.05, extra_group=""):
    g = f", {extra_group}" if extra_group else ""
    return f"""
    SELECT floor(price/{bucket})*{bucket} AS p_lo {g},
           count(*) AS n_fills, sum(usdc_amount) AS notional,
           avg(price) AS p_avg,
           avg(pay) AS win_rate,
           sum(usdc_amount*pay/price)/sum(usdc_amount/price) AS win_rate_sharewt,
           sum(fee_rate_model*usdc_amount)/sum(usdc_amount) AS fee_rate,
           count(distinct condition_id) AS n_markets
    FROM {src}
    WHERE taker_direction='BUY' AND pay IS NOT NULL
      AND price>0 AND price<1
    GROUP BY ALL ORDER BY ALL
    """

def calibration(con, src="fills", bucket=0.05, extra_group="") -> pd.DataFrame:
    df = con.sql(calibration_sql(src, bucket, extra_group)).df()
    df["edge_count"] = df.win_rate - df.p_avg
    df["edge_sharewt"] = df.win_rate_sharewt - df.p_avg
    df["roi_sharewt"] = df.win_rate_sharewt / df.p_avg - 1 - df.fee_rate
    # Wilson-ish SE on market-clustered counts is complex; report naive SE and n_markets for context
    df["se_naive"] = np.sqrt(df.win_rate * (1 - df.win_rate) / df.n_fills.clip(lower=1))
    return df

def fit_logit_shrink(p: np.ndarray, y: np.ndarray, w: np.ndarray | None = None):
    """Fit true-prob model  logit(q) = a + b*logit(p)  by weighted MLE (Bernoulli).
    b<1 => market prices are over-confident at extremes (classic longshot bias);
    b>1 => under-confident (favourite-longshot *reversal*, as reported for Polymarket).
    """
    from scipy.optimize import minimize
    p = np.clip(p, 1e-4, 1 - 1e-4); x = np.log(p / (1 - p)); w = np.ones_like(p) if w is None else w
    def nll(th):
        a, b = th; z = a + b * x; q = 1 / (1 + np.exp(-z))
        q = np.clip(q, 1e-9, 1 - 1e-9)
        return -np.sum(w * (y * np.log(q) + (1 - y) * np.log(1 - q)))
    r = minimize(nll, x0=[0.0, 1.0], method="L-BFGS-B")
    return dict(a=r.x[0], b=r.x[1], nll=r.fun)
