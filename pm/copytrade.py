"""Copy-trading backtest with real delays and real prices.

Signal: the first fill of wallet W in condition C (a 'new position') at time t0 with event-direction D
(+1 = W is long the event, -1 = short). Execution: at t0 + delay, we take the *next real taker fill in C
with the same event direction D* and assume we could have bought at that price (p_exec on the event axis
for D=+1, or 1-p_exec for D=-1, i.e. we buy the token W effectively bought). Stake $100 per position,
pay that fill's observed fee rate, hold to resolution. Return = 100*(won/p_token - 1) - fee.
Controls: random wallets through the same pipeline. CIs: bootstrap over conditions (markets).
"""
from __future__ import annotations
import numpy as np, pandas as pd

def first_positions(con, wallets_table: str, src="fills", name="sig", max_markets_per_wallet: int | None = 1000):
    """First fill per (wallet, condition) for wallets in wallets_table(wallet). Uses taker+maker legs.
    max_markets_per_wallet: keep a pseudo-random subset of markets per wallet (hash-based) to bound the join size."""
    cap = ""
    if max_markets_per_wallet:
        cap = f"QUALIFY row_number() OVER (PARTITION BY wallet ORDER BY hash(wallet || condition_id)) <= {max_markets_per_wallet}"
    con.execute(f"""
    CREATE OR REPLACE TABLE {name} AS
    WITH legs AS (
      SELECT taker AS wallet, condition_id, ts, D AS d, p_event, usdc_amount FROM {src}
      UNION ALL
      SELECT maker, condition_id, ts, -D, p_event, usdc_amount FROM {src}
    )
    SELECT l.wallet, l.condition_id, min_by(l.d, l.ts) AS d, min(l.ts) AS t0, min_by(l.p_event, l.ts) AS p0
    FROM legs l JOIN {wallets_table} w USING(wallet)
    GROUP BY 1,2 {cap}
    """)

def copy_backtest(con, sig="sig", src="fills", delays=(10, 60, 600, 3600), stake=100.0, name="cp", n_buckets=8):
    """For each signal and delay, find the next taker fill in the same condition with the same direction at ts >= t0+delay.
    Memory-bounded: candidate fills are spilled to parquet and the ASOF join runs per (hash bucket of markets, delay)."""
    from pm.db import CACHE
    import glob, os
    tkp = CACHE / "tk_tmp.parquet"
    con.execute(f"""
    COPY (
    SELECT condition_id, ts, D AS d, p_event, fee_usdc/usdc_amount AS fee_rate, pay_event
    FROM {src} WHERE usdc_amount>0 AND pay IS NOT NULL
      AND condition_id IN (SELECT DISTINCT condition_id FROM {sig})
    ) TO '{tkp}' (FORMAT PARQUET)""")
    for f in glob.glob(str(CACHE / "cp_part_*.parquet")): os.remove(f)
    for b in range(n_buckets):
        con.execute(f"CREATE OR REPLACE TABLE tk_b AS SELECT * FROM read_parquet('{tkp}') WHERE hash(condition_id) % {n_buckets} = {b}")
        con.execute(f"CREATE OR REPLACE TABLE sig_b AS SELECT * FROM {sig} WHERE hash(condition_id) % {n_buckets} = {b}")
        for dl in delays:
            con.execute(f"""
            COPY (
            SELECT s.wallet, s.condition_id, s.d, s.t0, s.p0, {dl} AS delay,
                   e.ts AS t_exec, e.p_event AS p_exec_event, e.fee_rate, e.pay_event,
                   CASE WHEN s.d=1 THEN e.p_event ELSE 1-e.p_event END AS p_token,
                   CASE WHEN s.d=1 THEN e.pay_event ELSE 1-e.pay_event END AS won
            FROM sig_b s
            ASOF JOIN tk_b e ON e.condition_id=s.condition_id AND e.d=s.d AND e.ts >= s.t0 + {dl}
            ) TO '{CACHE}/cp_part_{b}_{dl}.parquet' (FORMAT PARQUET)""")
    con.execute("DROP TABLE IF EXISTS tk_b; DROP TABLE IF EXISTS sig_b")
    con.execute(f"""
    CREATE OR REPLACE TABLE {name} AS
    SELECT *, {stake} * (won / p_token - 1) - {stake}*coalesce(fee_rate,0) AS pnl, {stake} AS stake
    FROM read_parquet('{CACHE}/cp_part_*.parquet')
    WHERE p_token > 0 AND p_token < 1
    """)

def summarize(con, name="cp", price_band=(0.0, 1.0), n_boot=500, seed=0) -> pd.DataFrame:
    df = con.sql(f"SELECT wallet, condition_id, delay, p_token, pnl, stake FROM {name} WHERE p_token>={price_band[0]} AND p_token<={price_band[1]}").df()
    rng = np.random.default_rng(seed)
    rows = []
    for dl, g in df.groupby("delay"):
        mk = g.groupby("condition_id").agg(pnl=("pnl", "sum"), stake=("stake", "sum"))
        roi = mk.pnl.sum() / mk.stake.sum()
        boots = []
        idx = np.arange(len(mk))
        for _ in range(n_boot):
            s = rng.choice(idx, len(idx), replace=True)
            boots.append(mk.pnl.values[s].sum() / mk.stake.values[s].sum())
        lo, hi = np.percentile(boots, [2.5, 97.5])
        rows.append(dict(delay_s=dl, n_positions=len(g), n_markets=len(mk), roi=roi, ci_lo=lo, ci_hi=hi))
    return pd.DataFrame(rows)
