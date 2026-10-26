"""Information content of trades and its decay: signed markouts.

For a fill at time t with event-direction D (+1 = buys event probability) at price p_event(t),
markout_h = D * (p_event(t+h) - p_event(t))  where p_event(t+h) is the next traded price at or
after t+h in the same condition (last fill before t+h if none after, i.e. ASOF backward).
Averaged over fills, markout_h is the gross edge available to someone who executes h after the
informed trader; the market maker on the other side suffers -markout. For a copier who buys at
the *next taker price after delay d*, expected edge  = E[payout] - p_event(t+d) - fee
                                                    = markout_inf - markout_d - fee.
"""
from __future__ import annotations

HORIZONS = {"10s": 10, "1m": 60, "10m": 600, "1h": 3600, "1d": 86400}

def build_price_path(con, src="fills", name="px"):
    """Per condition, time-ordered event-price path (one row per fill, dedup to last price per second)."""
    con.execute(f"""
    CREATE OR REPLACE TABLE {name} AS
    SELECT condition_id, ts, arg_max(p_event, rn) AS p
    FROM (SELECT condition_id, ts, p_event, row_number() OVER () AS rn FROM {src})
    GROUP BY 1,2 ORDER BY 1,2
    """)

def markouts(con, fills_subset_sql: str, px="px", horizons=HORIZONS, name="mo"):
    """fills_subset_sql must select: condition_id, ts, D, p_event, plus any id columns (wallet, etc.)."""
    cols = ", ".join(f"({h}.p - f.p_event)*f.D AS mo_{k}" for k, h in horizons.items())
    joins = " ".join(
        f"ASOF LEFT JOIN {px} {h} ON {h}.condition_id=f.condition_id AND {h}.ts >= f.ts + {s}"
        for (k, s), h in zip(horizons.items(), [f"h{k}" for k in horizons]))
    # ASOF with >= picks the *first* price at or after t+h (forward asof). DuckDB ASOF supports >= with ordering on ts.
    cols = ", ".join(f"(h{k}.p - f.p_event)*f.D AS mo_{k}" for k in horizons)
    con.execute(f"""
    CREATE OR REPLACE TABLE {name} AS
    SELECT f.*, {cols},
           f.pay AS won_token,
           (f.pay_event - f.p_event)*f.D AS mo_inf
    FROM ({fills_subset_sql}) f {joins}
    """)
