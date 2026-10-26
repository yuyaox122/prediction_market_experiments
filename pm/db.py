from __future__ import annotations
import duckdb, os, glob
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HF = ROOT / "data" / "hf" / "Polymarket-v1"
CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

def connect(mem_gb: int = 10, threads: int = 8) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{mem_gb}GB'; SET threads={threads}; SET TimeZone='UTC';")
    con.execute(f"SET temp_directory='{CACHE / 'tmp'}';")
    return con

def files(layer: str, start: str | None = None, end: str | None = None) -> list[str]:
    """Daily parquet files for layer in ('daily_aligned','daily_aligned_multi'), inclusive date bounds 'YYYY_MM_DD'."""
    fs = sorted(glob.glob(str(HF / layer / "*.parquet")))
    out = []
    for f in fs:
        d = Path(f).stem
        if start and d < start: continue
        if end and d > end: continue
        if os.path.getsize(f) > 0: out.append(f)
    return out

def register_resolutions(con, name="res"):
    """On-chain ConditionResolution payouts -> per-condition payout per share of outcome 1 and 2 (fractions)."""
    con.execute(f"""
    CREATE OR REPLACE TABLE {name} AS
    SELECT condition_id,
           payout_numerators[1]::DOUBLE / (payout_numerators[1]::DOUBLE + payout_numerators[2]::DOUBLE) AS pay1,
           payout_numerators[2]::DOUBLE / (payout_numerators[1]::DOUBLE + payout_numerators[2]::DOUBLE) AS pay2
    FROM read_parquet('{HF}/CTF/resolutions.parquet')
    WHERE outcome_slot_count=2 AND (payout_numerators[1]::DOUBLE + payout_numerators[2]::DOUBLE) > 0
    """)

FILL_COLS = """
  f.asset_id, f.block_timestamp AS ts, f.price, f.maker, f.taker, f.taker_direction,
  f.usdc_amount, f.fee_usdc, f.condition_id, f.outcome_seq, f.neg_risk,
  f.category_refined AS category, f.outcome_label, f.winning_outcome_label,
  f.resolution_status, f.taker_base_fee, f.maker_base_fee,
  epoch(f.opens_at) AS opens_ts, epoch(f.close_at) AS close_ts, epoch(f.resolved_at) AS resolved_ts,
  f.market_slug, f.p_event, f.D,
  CASE WHEN f.outcome_seq=1 THEN r.pay1 ELSE r.pay2 END AS pay,
  r.pay1 AS pay_event,
  CASE WHEN f.taker_base_fee>0 THEN (CASE f.category_refined WHEN 'Crypto' THEN 0.07 WHEN 'Price Action' THEN 0.07 WHEN 'Sports' THEN 0.03
       WHEN 'Politics' THEN 0.04 WHEN 'Finance' THEN 0.04 WHEN 'Sci-Tech' THEN 0.04 ELSE 0.05 END) * f.price * (1 - f.price) ELSE 0 END AS fee_rate_model
"""

def register_fills(con, start=None, end=None, layers=("daily_aligned", "daily_aligned_multi"), name="fills"):
    """View `fills` over daily files, joined to on-chain resolutions. `pay` = $ paid per share of the traded
    token at resolution (NULL if unresolved); `pay_event` = payout of outcome 1 (the p_event axis)."""
    if not con.sql("SELECT count(*) FROM information_schema.tables WHERE table_name='res'").fetchone()[0]:
        register_resolutions(con)
    parts = []
    for layer in layers:
        fs = files(layer, start, end)
        if not fs: continue
        lst = "[" + ",".join(f"'{f}'" for f in fs) + "]"
        nrm = "f.neg_risk_market_id" if layer.endswith("multi") else "NULL::VARCHAR AS neg_risk_market_id"
        parts.append(f"SELECT {FILL_COLS}, {nrm} FROM read_parquet({lst}) f LEFT JOIN res r USING(condition_id)")
    con.execute(f"CREATE OR REPLACE VIEW {name} AS " + " UNION ALL ".join(parts))
    return con
