"""Full-window pipeline (Jul 2025 - Apr 2026), staged with parquet caching. Usage:
   PYTHONPATH=. .venv/bin/python scripts/run_full.py [stage ...]
Stages: pnl concentration persistence calibration markout copy report   (default: all)
"""
from __future__ import annotations
import sys, time, json, numpy as np, pandas as pd
from pathlib import Path
from pm.db import connect, register_fills, files, CACHE, ROOT
from pm.pnl import wallet_market_pnl, LEGS_SQL
from pm.persistence import wallet_period, persistence_table, rank_correlation
from pm.calibration import calibration
from pm.markout import build_price_path, markouts
from pm.copytrade import first_positions, copy_backtest, summarize
from pm.models import CalibrationModel, InfoDecayModel, market_maker_table, skill_shrinkage

START, END = "2025_07_01", "2026_04_28"
SPLIT = int(pd.Timestamp("2026-01-01", tz="UTC").timestamp())   # period A: resolved markets last traded before 2026
REP = ROOT / "reports"; REP.mkdir(exist_ok=True)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 50)
LOG = open(REP / "run_full.log", "a")
def log(*a):
    s = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a); print(s, flush=True); LOG.write(s + "\n"); LOG.flush()

def months():
    ms = sorted({Path(f).stem[:7] for f in files("daily_aligned", START, END)} | {Path(f).stem[:7] for f in files("daily_aligned_multi", START, END)})
    return ms

def stage_pnl(con):
    """Month-by-month partial aggregates (wallet, condition, asset) -> wm.parquet. Additive, so memory-safe."""
    out = CACHE / "wm_parts"; out.mkdir(exist_ok=True)
    for m in months():
        f = out / f"{m}.parquet"
        if f.exists() and f.stat().st_size > 0: continue
        tmp = out / f"{m}.tmp.parquet"
        t = time.time()
        register_fills(con, f"{m}_01", f"{m}_31")
        con.execute(f"""
        COPY (
          {LEGS_SQL.format(src="fills")}
          SELECT wallet, condition_id, neg_risk_market_id, asset_id, category, any_value(pay) AS pay,
                 sum(d_shares) AS net_shares, sum(d_cash) AS cash,
                 sum(CASE WHEN d_shares>0 THEN d_shares*price ELSE -d_shares*(1-price) END) AS risk_usdc,
                 sum(notional) AS notional, sum(CASE WHEN is_taker THEN notional ELSE 0 END) AS taker_notional, sum(fee_raw) AS fee_raw,
                 count(*) AS n_fills, min(ts) AS first_ts, max(ts) AS last_ts
          FROM legs GROUP BY 1,2,3,4,5
        ) TO '{tmp}' (FORMAT PARQUET)""")
        tmp.rename(f)
        log("pnl part", m, round(time.time() - t), "s")
    t = time.time()
    con.execute(f"""
    COPY (
      SELECT wallet, condition_id, any_value(neg_risk_market_id) AS neg_risk_market_id, any_value(category) AS category,
             any_value(pay) IS NOT NULL AS resolved,
             sum(cash) AS cash, sum(net_shares*coalesce(pay,0)) AS payout, sum(cash)+sum(net_shares*coalesce(pay,0)) AS pnl,
             sum(risk_usdc) AS risk_usdc, sum(notional) AS notional, sum(taker_notional) AS taker_notional, sum(fee_raw) AS fee_raw,
             sum(n_fills) AS n_fills, min(first_ts) AS first_ts, max(last_ts) AS last_ts
      FROM read_parquet('{out}/2*.parquet') GROUP BY 1,2
    ) TO '{CACHE}/wm.parquet' (FORMAT PARQUET)""")
    log("wm.parquet written", round(time.time() - t), "s")

def stage_markets(con):
    register_fills(con, START, END)
    con.execute(f"""COPY (SELECT condition_id, any_value(category) AS category, any_value(market_slug) AS slug, any_value(neg_risk) AS neg_risk,
        max(taker_base_fee)>0 AS fee_enabled, any_value(opens_ts) AS opens_ts, any_value(close_ts) AS close_ts, any_value(pay_event) AS pay_event,
        sum(usdc_amount) AS notional, count(*) AS n_fills, min(ts) AS first_ts, max(ts) AS last_ts
        FROM fills GROUP BY 1) TO '{CACHE}/markets.parquet' (FORMAT PARQUET)""")
    log("markets.parquet", con.sql(f"SELECT count(*), sum(fee_enabled::int) FROM read_parquet('{CACHE}/markets.parquet')").fetchone())

def stage_copy_summary(con):
    """Re-price cached copy positions with the documented taker-fee schedule and summarise."""
    con.execute(f"CREATE OR REPLACE VIEW mkt AS SELECT * FROM read_parquet('{CACHE}/markets.parquet')")
    res = []
    for name in ("top100_pnl", "top100_roi", "random1000"):
        f = CACHE / f"cp_{name}.parquet"
        if not f.exists(): continue
        con.execute(f"""CREATE OR REPLACE TABLE cp AS
            SELECT c.wallet, c.condition_id, c.d, c.t0, c.delay, c.t_exec, c.p_token, c.won, 100.0 AS stake,
                   CASE WHEN m.fee_enabled THEN (CASE m.category WHEN 'Crypto' THEN 0.07 WHEN 'Price Action' THEN 0.07 WHEN 'Sports' THEN 0.03
                        WHEN 'Politics' THEN 0.04 WHEN 'Finance' THEN 0.04 WHEN 'Sci-Tech' THEN 0.04 ELSE 0.05 END) * c.p_token * (1-c.p_token) ELSE 0 END AS fee_rate,
                   100.0 * (c.won / c.p_token - 1) - 100.0 * (CASE WHEN m.fee_enabled THEN (CASE m.category WHEN 'Crypto' THEN 0.07 WHEN 'Price Action' THEN 0.07 WHEN 'Sports' THEN 0.03
                        WHEN 'Politics' THEN 0.04 WHEN 'Finance' THEN 0.04 WHEN 'Sci-Tech' THEN 0.04 ELSE 0.05 END) * c.p_token * (1-c.p_token) ELSE 0 END) AS pnl,
                   m.notional/2 AS vol
            FROM read_parquet('{f}') c JOIN mkt m USING(condition_id) WHERE c.p_token>0 AND c.p_token<1""")
        con.execute("CREATE OR REPLACE TABLE cp_big AS SELECT * FROM cp WHERE vol>=250000")
        for tbl, band in [("cp", (0, 1)), ("cp", (0.05, 0.95)), ("cp_big", (0, 1)), ("cp_big", (0.05, 0.95))]:
            s = summarize(con, tbl, band, n_boot=300); s["group"] = name; s["band"] = f"{band[0]}-{band[1]}"; s["markets"] = "vol>=250k" if tbl == "cp_big" else "all"; res.append(s)
        log("copy_summary", name, "\n" + pd.concat(res[-4:]).round(4).to_string())
    pd.concat(res).to_csv(REP / "copy_backtest.csv", index=False)

def load_wm(con, resolved_only=True):
    con.execute(f"CREATE OR REPLACE VIEW wm AS SELECT * FROM read_parquet('{CACHE}/wm.parquet')" + (" WHERE resolved" if resolved_only else ""))

def stage_concentration(con):
    load_wm(con)
    w = con.sql("SELECT wallet, sum(pnl) pnl, sum(risk_usdc) risk, count(*) n, sum(notional) notional FROM wm GROUP BY 1").df()
    w = w.sort_values("pnl", ascending=False)
    pos = w[w.pnl > 0]; neg = w[w.pnl < 0]
    top1 = w.head(max(1, len(w) // 100))
    res = dict(n_wallets=len(w), frac_losing=float((w.pnl < 0).mean()), frac_winning=float((w.pnl > 0).mean()),
               total_profit_of_winners=float(pos.pnl.sum()), total_loss_of_losers=float(neg.pnl.sum()),
               top1pct_share_of_profits=float(top1.pnl.sum() / pos.pnl.sum()), top100_share=float(w.head(100).pnl.sum() / pos.pnl.sum()),
               n_markets=int(con.sql("SELECT count(distinct condition_id) FROM wm").fetchone()[0]),
               notional_one_sided=float(w.notional.sum() / 2))
    json.dump(res, open(REP / "concentration.json", "w"), indent=1); log("concentration", res)
    w.head(200).to_csv(REP / "top200_wallets.csv", index=False)

def stage_persistence(con):
    load_wm(con)
    m = wallet_period(con, split_ts=SPLIT, min_markets=20, min_risk=10_000)
    tab = persistence_table(m, top_ns=(100, 500, 2000)); tab.to_csv(REP / "persistence.csv", index=False)
    log("persistence\n" + tab.round(3).to_string())
    rc = rank_correlation(m); sh = skill_shrinkage(m)
    # same with a stricter filter (>=50 markets) and ROI-ranked
    m2 = wallet_period(con, split_ts=SPLIT, min_markets=50, min_risk=50_000); sh2 = skill_shrinkage(m2)
    json.dump(dict(rank_corr=rc, shrinkage_20mkts_10k=sh, shrinkage_50mkts_50k=sh2), open(REP / "persistence_models.json", "w"), indent=1, default=float)
    log("rank corr", rc, "\nshrinkage", sh, "\nshrinkage strict", sh2)
    m.to_parquet(CACHE / "wallet_periods.parquet")

def stage_calibration(con):
    register_fills(con, START, END)
    df = calibration(con, bucket=0.05); df.to_csv(REP / "calibration_5c.csv", index=False); log("calibration\n" + df.round(4).to_string())
    bym = con.sql("""SELECT strftime(to_timestamp(ts),'%Y-%m') AS month, floor(price/0.1)*0.1 AS p_lo, count(*) n, sum(usdc_amount) notional,
                     avg(price) p_avg, sum(usdc_amount*pay/price)/sum(usdc_amount/price) win_sw, sum(fee_rate_model*usdc_amount)/sum(usdc_amount) fee_rate
                     FROM fills WHERE taker_direction='BUY' AND pay IS NOT NULL AND price>0 AND price<1 GROUP BY ALL ORDER BY ALL""").df()
    bym["roi_sw"] = bym.win_sw / bym.p_avg - 1 - bym.fee_rate; bym.to_csv(REP / "calibration_by_month.csv", index=False)
    byc = calibration(con, bucket=0.1, extra_group="category"); byc.to_csv(REP / "calibration_by_category.csv", index=False)
    # fit logit model on a 2% sample of taker buys (share-weighted and count-weighted)
    raw = con.sql("SELECT price p, pay y, usdc_amount/price sh, category FROM fills WHERE taker_direction='BUY' AND pay IS NOT NULL AND price>0 AND price<1 USING SAMPLE 2%").df()
    fits = {"count": CalibrationModel().fit(raw.p, raw.y).__dict__, "share": CalibrationModel().fit(raw.p, raw.y, raw.sh).__dict__}
    for c, g in raw.groupby("category"):
        if len(g) > 20000: fits[f"share_{c}"] = CalibrationModel().fit(g.p, g.y, g.sh).__dict__
    json.dump(fits, open(REP / "calibration_fit.json", "w"), indent=1, default=float); log("calibration fits", fits)
    cm = CalibrationModel(**fits["share"]); cm.table(fee_rate=0.0).round(4).to_csv(REP / "calibration_model_table.csv", index=False)

def stage_markout(con):
    """Markouts on taker fills of a 1-in-20 sample of markets (all fills of those markets), plus all fills of top/random wallets."""
    register_fills(con, START, END)
    fsp = CACHE / "fs_sample.parquet"
    if not fsp.exists():
        con.execute("CREATE OR REPLACE TABLE mk_sample AS SELECT condition_id FROM (SELECT DISTINCT condition_id FROM fills WHERE pay IS NOT NULL) USING SAMPLE 2% (bernoulli, 42)")
        con.execute(f"COPY (SELECT condition_id, ts, D, p_event, outcome_seq, pay, pay_event, usdc_amount, taker, maker, category, fee_rate_model FROM fills f JOIN mk_sample USING(condition_id)) TO '{fsp}' (FORMAT PARQUET)")
    con.execute(f"CREATE OR REPLACE VIEW fs AS SELECT * FROM read_parquet('{fsp}')")
    log("markout sample fills", con.sql("SELECT count(*), count(distinct condition_id) FROM fs").fetchone())
    build_price_path(con, "fs")
    sub = "SELECT condition_id, ts, D, p_event, outcome_seq, pay, pay_event, usdc_amount, taker AS wallet, maker, category, fee_rate_model*usdc_amount AS fee_usdc FROM fs WHERE usdc_amount>=2"
    markouts(con, sub)
    con.execute(f"COPY mo TO '{CACHE}/mo.parquet' (FORMAT PARQUET)")
    q = """SELECT {g} count(*) n, sum(usdc_amount) notional,
           sum(mo_10s*usdc_amount)/sum(usdc_amount) w10s, sum(mo_1m*usdc_amount)/sum(usdc_amount) w1m, sum(mo_10m*usdc_amount)/sum(usdc_amount) w10m,
           sum(mo_1h*usdc_amount)/sum(usdc_amount) w1h, sum(mo_1d*usdc_amount)/sum(usdc_amount) w1d, sum(mo_inf*usdc_amount)/sum(usdc_amount) winf,
           avg(mo_10s) m10s, avg(mo_1m) m1m, avg(mo_10m) m10m, avg(mo_1h) m1h, avg(mo_1d) m1d, avg(mo_inf) minf,
           sum(fee_usdc)/sum(usdc_amount) fee_rate
           FROM mo {w} GROUP BY ALL ORDER BY ALL"""
    allm = con.sql(q.format(g="'all' AS grp,", w="")).df()
    bysz = con.sql(q.format(g="CASE WHEN usdc_amount<10 THEN 'a<10' WHEN usdc_amount<100 THEN 'b<100' WHEN usdc_amount<1000 THEN 'c<1k' WHEN usdc_amount<10000 THEN 'd<10k' ELSE 'e>=10k' END AS grp,", w="")).df()
    bycat = con.sql(q.format(g="category AS grp,", w="WHERE category IN ('Sports','Crypto','Politics','Price Action','Culture','Finance','Sci-Tech','Other')")).df()
    # by wallet group: top-100 (period A pnl) takers vs everyone else
    wp = pd.read_parquet(CACHE / "wallet_periods.parquet") if (CACHE / "wallet_periods.parquet").exists() else None
    if wp is not None:
        top = wp.sort_values("pnl", ascending=False).head(100)[["wallet"]]; con.register("top_df", top)
        con.execute("CREATE OR REPLACE TABLE topw AS SELECT wallet FROM top_df")
        bytop = con.sql(q.format(g="(wallet IN (SELECT wallet FROM topw)) AS is_top, (ts>=%d) AS period_b," % SPLIT, w="")).df()
        bytop.to_csv(REP / "markout_top_vs_rest.csv", index=False); log("markout top vs rest\n" + bytop.round(4).to_string())
    out = pd.concat([allm, bysz, bycat]); out.to_csv(REP / "markout.csv", index=False); log("markouts\n" + out.round(4).to_string())
    # info-decay fits (share-weighted) per group
    fits = {}
    for _, r in out.iterrows():
        try:
            md = InfoDecayModel().fit([10, 60, 600, 3600, 86400], [r.w10s, r.w1m, r.w10m, r.w1h, r.w1d], m_inf=r.winf)
            fits[r.grp] = dict(m0=md.m0, m_inf=md.m_inf, tau_s=md.tau, half_life_s=md.half_life(), copier_edge_1m=md.copier_edge(60), copier_edge_1h=md.copier_edge(3600))
        except Exception as e: fits[r.grp] = str(e)
    json.dump(fits, open(REP / "info_decay_fits.json", "w"), indent=1, default=float)
    mm = market_maker_table(out[["grp", "n", "notional", "w10s", "winf", "fee_rate"]]); mm.to_csv(REP / "market_maker.csv", index=False)
    log("market maker table\n" + mm.round(4).to_string())

def stage_copy(con):
    register_fills(con, START, END)
    wp = pd.read_parquet(CACHE / "wallet_periods.parquet")
    groups = {"top100_pnl": wp.sort_values("pnl", ascending=False).head(100), "top100_roi": wp.sort_values("roi", ascending=False).head(100)}
    load_wm(con)
    con.execute(f"CREATE OR REPLACE TABLE rnd AS SELECT wallet FROM (SELECT wallet FROM wm WHERE last_ts>={SPLIT} GROUP BY 1 HAVING count(*)>=5) USING SAMPLE 1000 (reservoir, 7)")
    con.execute(f"CREATE OR REPLACE VIEW fills_b AS SELECT * FROM fills WHERE ts>={SPLIT}")
    res = []
    import os
    only = os.environ.get("COPY_GROUPS", "").split(",") if os.environ.get("COPY_GROUPS") else None
    for name, g in list(groups.items()) + [("random1000", None)]:
        if only and name not in only: continue
        if g is not None:
            con.register("g_df", g[["wallet"]]); con.execute("CREATE OR REPLACE TABLE gw AS SELECT wallet FROM g_df")
        else:
            con.execute("CREATE OR REPLACE TABLE gw AS SELECT wallet FROM rnd")
        t = time.time()
        first_positions(con, "gw", "fills_b", "sig")
        copy_backtest(con, "sig", "fills_b", name="cp")
        con.execute(f"COPY cp TO '{CACHE}/cp_{name}.parquet' (FORMAT PARQUET)")
        con.execute("CREATE OR REPLACE TABLE cp_big AS SELECT cp.* FROM cp JOIN (SELECT condition_id, sum(notional)/2 AS vol FROM wm GROUP BY 1) v USING(condition_id) WHERE v.vol>=250000")
        for tbl, band in [("cp", (0, 1)), ("cp", (0.05, 0.95)), ("cp_big", (0, 1)), ("cp_big", (0.05, 0.95))]:
            s = summarize(con, tbl, band, n_boot=300); s["group"] = name; s["band"] = f"{band[0]}-{band[1]}"; s["markets"] = "vol>=250k" if tbl == "cp_big" else "all"; res.append(s)
        log("copy", name, round(time.time() - t), "s\n" + pd.concat(res[-4:]).round(4).to_string())
    pd.concat(res).to_csv(REP / "copy_backtest.csv", index=False)

STAGES = dict(pnl=stage_pnl, markets=stage_markets, copy_summary=stage_copy_summary, concentration=stage_concentration, persistence=stage_persistence, calibration=stage_calibration, markout=stage_markout, copy=stage_copy)
if __name__ == "__main__":
    con = connect(mem_gb=9, threads=4)
    for s in (sys.argv[1:] or list(STAGES)):
        log("=== stage", s); t = time.time(); STAGES[s](con); log("=== done", s, round(time.time() - t), "s")
