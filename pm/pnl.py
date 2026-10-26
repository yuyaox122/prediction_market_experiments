"""Wallet x market profit engine.

A fill transfers shares = usdc/price of an outcome token between maker and taker, and usdc the other way.
Taker BUY  -> taker +shares, -usdc ; maker -shares, +usdc.   Taker SELL -> the reverse.
The dataset's fee_usdc is the raw on-chain fee field of the maker order (mixed units) which Polymarket's
own PnL accounting does not deduct (validated against the Data API), so profit is gross of it; it is kept as fee_raw. At resolution each share pays `pay` (1 / 0, or 0.5 on a split resolution).
Wallet profit in a market = net cash from fills + sum(net_shares * pay).  A negative net position is the
on-chain footprint of a split (mint) + sell, and the formula remains exact (short YES at p == long NO at 1-p).
"""
from __future__ import annotations

LEGS_SQL = """
WITH legs AS (
  SELECT taker AS wallet, condition_id, neg_risk_market_id, asset_id, outcome_seq, pay, category, ts, price,
         CASE WHEN taker_direction='BUY' THEN usdc_amount/price ELSE -usdc_amount/price END AS d_shares,
         CASE WHEN taker_direction='BUY' THEN -usdc_amount ELSE usdc_amount END AS d_cash,
         fee_usdc AS fee_raw, usdc_amount AS notional, TRUE AS is_taker
  FROM {src}
  UNION ALL
  SELECT maker, condition_id, neg_risk_market_id, asset_id, outcome_seq, pay, category, ts, price,
         CASE WHEN taker_direction='BUY' THEN -usdc_amount/price ELSE usdc_amount/price END,
         CASE WHEN taker_direction='BUY' THEN usdc_amount ELSE -usdc_amount END,
         0.0, usdc_amount, FALSE
  FROM {src}
)
"""

def wallet_market_pnl(con, src="fills", resolved_only=True, name="wm"):
    """Materialise `wm`: one row per (wallet, condition) with cash, payout, pnl, risk (cost basis) etc."""
    where = "WHERE pay IS NOT NULL" if resolved_only else ""
    con.execute(f"""
    CREATE OR REPLACE TABLE {name} AS
    {LEGS_SQL.format(src=src)}
    , wa AS (
      SELECT wallet, condition_id, neg_risk_market_id, asset_id, category,
             any_value(pay) AS pay, sum(d_shares) AS net_shares, sum(d_cash) AS cash,
             -- economic cost basis: buying a token at p costs p/share; selling it short costs (1-p)/share of collateral
             sum(CASE WHEN d_shares>0 THEN d_shares*price ELSE -d_shares*(1-price) END) AS risk_usdc,
             sum(notional) AS notional, sum(CASE WHEN is_taker THEN notional ELSE 0 END) AS taker_notional,
             sum(fee_raw) AS fee_raw,
             count(*) AS n_fills, min(ts) AS first_ts, max(ts) AS last_ts
      FROM legs {where} GROUP BY 1,2,3,4,5)
    SELECT wallet, condition_id, any_value(neg_risk_market_id) AS neg_risk_market_id, any_value(category) AS category,
           sum(cash) AS cash, sum(net_shares*pay) AS payout, sum(cash) + sum(net_shares*pay) AS pnl,
           sum(risk_usdc) AS risk_usdc, sum(notional) AS notional, sum(taker_notional) AS taker_notional, sum(fee_raw) AS fee_raw,
           sum(n_fills) AS n_fills, min(first_ts) AS first_ts, max(last_ts) AS last_ts
    FROM wa GROUP BY 1,2
    """)
    return con
