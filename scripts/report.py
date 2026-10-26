"""Build charts (PNG) and REPORT.md from reports/*.csv|json."""
from __future__ import annotations
import json, numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from pm.db import ROOT
REP = ROOT / "reports"; FIG = REP / "fig"; FIG.mkdir(exist_ok=True)
# reference palette (dataviz skill): fixed categorical order
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"
plt.rcParams.update({"figure.facecolor": SURF, "axes.facecolor": SURF, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK2, "text.color": INK, "axes.grid": True, "grid.color": GRID,
                     "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
                     "legend.frameon": False, "figure.dpi": 130})
def pct(x): return f"{100*x:+.1f}%"
md = ["# Polymarket first-principles research report", "", "Window: 2025-07-01 to 2026-04-28 (v1 exchange tape). Period A = markets last traded before 2026-01-01, period B = 2026.", ""]

def fig_calibration():
    df = pd.read_csv(REP / "calibration_5c.csv"); fit = json.load(open(REP / "calibration_fit.json"))
    fig, ax = plt.subplots(1, 2, figsize=(10, 4.2))
    ax[0].plot([0, 1], [0, 1], color=GRID, lw=1.5, ls="--", label="perfect calibration")
    ax[0].plot(df.p_avg, df.win_rate_sharewt, "o-", color=C[0], lw=2, ms=5, label="realised win rate (share-weighted)")
    p = np.linspace(0.01, 0.99, 200); a, b = fit["share"]["a"], fit["share"]["b"]
    q = 1 / (1 + np.exp(-(a + b * np.log(p / (1 - p)))))
    ax[0].plot(p, q, color=C[1], lw=2, label=f"logit fit  b={b:.2f}")
    ax[0].set_xlabel("price paid by taker buyer"); ax[0].set_ylabel("P(token pays $1)"); ax[0].legend(loc="upper left"); ax[0].set_title("Calibration of taker buys")
    ax[1].axhline(0, color=GRID, lw=1.5)
    ax[1].bar(df.p_lo + 0.025, df.roi_sharewt, width=0.045, color=[C[2] if v > 0 else C[7] for v in df.roi_sharewt])
    ax[1].set_ylim(max(-1.0, df.roi_sharewt.min() - 0.05), min(0.3, df.roi_sharewt.max() + 0.05))
    ax[1].set_xlabel("price bucket"); ax[1].set_ylabel("ROI of buy-and-hold to resolution (after fees)"); ax[1].set_title("Favourite-longshot reversal")
    fig.tight_layout(); fig.savefig(FIG / "calibration.png"); plt.close(fig)
    md.append("## 1. Calibration and the favourite-longshot reversal\n")
    md.append("![](fig/calibration.png)\n")
    t = df[["p_lo", "n_fills", "n_markets", "p_avg", "win_rate_sharewt", "roi_sharewt", "fee_rate"]].copy()
    t.columns = ["price bucket", "fills", "markets", "avg price", "win rate (share-wt)", "ROI after fees", "fee rate"]
    md.append(t.round(4).to_markdown(index=False)); md.append("")
    md.append(f"Logit fit (share-weighted): a={a:.3f}, b={b:.3f}. b>1 means the market is under-confident: favourites are underpriced, longshots overpriced.\n")
    cm = pd.read_csv(REP / "calibration_model_table.csv"); md.append("Model-implied edge and Kelly fraction for buying at a given price (no fee):\n"); md.append(cm.round(4).to_markdown(index=False)); md.append("")

def fig_by_month():
    bym = pd.read_csv(REP / "calibration_by_month.csv"); bym["p_lo"] = bym.p_lo.round(1)
    piv = bym.pivot(index="month", columns="p_lo", values="roi_sw"); fee = bym.groupby("month").apply(lambda g: (g.fee_rate * g.notional).sum() / g.notional.sum())
    fig, ax = plt.subplots(figsize=(10, 4.2)); ax.axhline(0, color=GRID, lw=1.5)
    for i, col in enumerate([0.0, 0.2, 0.5, 0.7, 0.9]):
        if col in piv: ax.plot(piv.index, piv[col], "o-", color=C[i], lw=2, ms=4, label=f"buy at {int(col*100)}-{int(col*100)+9}c")
    ax.set_ylim(-1.0, 0.4); ax.set_ylabel("ROI to resolution, after fees"); ax.set_title("ROI of taker buys by price bucket and month"); ax.legend(ncol=5, loc="lower left"); ax.tick_params(axis="x", rotation=45)
    fig.tight_layout(); fig.savefig(FIG / "roi_by_month.png"); plt.close(fig)
    md.append("## 2. Stability over time and the arrival of fees\n"); md.append("![](fig/roi_by_month.png)\n")
    md.append("Notional-weighted taker fee rate by month:\n"); md.append(fee.rename("fee rate").round(5).to_frame().T.to_markdown()); md.append("")

def fig_markout():
    mo = pd.read_csv(REP / "markout.csv"); fits = json.load(open(REP / "info_decay_fits.json"))
    hs = [10, 60, 600, 3600, 86400]; cols = ["w10s", "w1m", "w10m", "w1h", "w1d"]
    fig, ax = plt.subplots(1, 2, figsize=(10, 4.2))
    for i, g in enumerate(["a<10", "b<100", "c<1k", "d<10k", "e>=10k"]):
        r = mo[mo.grp == g]
        if len(r): r = r.iloc[0]; ax[0].plot(hs, [r[c] * 100 for c in cols], "o-", color=C[i], lw=2, ms=4, label=f"{g.lstrip('abcde')}  ->  {r.winf*100:+.2f}c at resolution")
    ax[0].axhline(0, color=GRID, lw=1.5); ax[0].set_xscale("log"); ax[0].set_xlabel("horizon after the trade (s)"); ax[0].set_ylabel("signed markout (cents of probability)"); ax[0].set_title("Information in taker trades, by trade size"); ax[0].legend(fontsize=8)
    cats = [g for g in mo.grp if g not in ("all",) and not g[:1] in "abcde" or g == "e>=10k"]
    cats = [g for g in mo.grp if g in ("Sports", "Crypto", "Politics", "Price Action", "Culture", "Finance", "Sci-Tech", "Other")]
    for i, g in enumerate(cats[:8]):
        r = mo[mo.grp == g].iloc[0]; ax[1].plot(hs + [1e6], [r[c] * 100 for c in cols] + [r.winf * 100], "o-", color=C[i], lw=2, ms=4, label=g)
    ax[1].axhline(0, color=GRID, lw=1.5); ax[1].set_xscale("log"); ax[1].set_xlabel("horizon (s; 1e6 = resolution)"); ax[1].set_title("By category"); ax[1].legend(fontsize=8, ncol=2)
    fig.tight_layout(); fig.savefig(FIG / "markout.png"); plt.close(fig)
    md.append("## 3. Information decay and market making (Glosten-Milgrom)\n"); md.append("![](fig/markout.png)\n")
    t = mo[["grp", "n", "notional", "w10s", "w1m", "w10m", "w1h", "w1d", "winf", "fee_rate"]].copy(); t.columns = ["group", "fills", "notional $", "10s", "1m", "10m", "1h", "1d", "resolution", "fee"]
    md.append("Share-weighted signed markouts of taker fills (probability units; positive = the taker was right):\n"); md.append(t.round(4).to_markdown(index=False)); md.append("")
    mm = pd.read_csv(REP / "market_maker.csv"); md.append("Maker economics per share: half-spread (bounce) minus adverse selection minus fee:\n")
    md.append(mm[["grp", "half_spread_est", "adverse_selection", "maker_edge_per_share"]].round(4).to_markdown(index=False)); md.append("")
    fr = mo[["grp", "winf"]].copy()
    for c, h in zip(cols, ["10s", "1m", "10m", "1h", "1d"]): fr[f"realised by {h}"] = mo[c] / mo.winf
    fr = fr[fr.winf.abs() > 0.003]
    md.append("Fraction of the terminal markout already in the price by horizon h (M(h)/M_inf), groups with |M_inf| > 0.3c. Values near 0 mean the information is realised only at resolution, not traded into the price:\n")
    md.append(fr.round(3).to_markdown(index=False)); md.append("")
    md.append("The exponential-decay model M(h) = M_inf - (M_inf - M_0) exp(-h/tau) is rejected for most groups (fitted tau diverges, see info_decay_fits.json): markouts are flat or negative for a day and then jump at resolution. The information in ordinary taker trades is not incorporated by the market before the outcome; only the largest trades and top wallets move the price within seconds.\n")
    if (REP / "markout_top_vs_rest.csv").exists():
        tv = pd.read_csv(REP / "markout_top_vs_rest.csv"); md.append("Top-100 (period-A pnl) wallets as takers vs everyone else:\n"); md.append(tv.round(4).to_markdown(index=False)); md.append("")

def fig_persistence():
    pt = pd.read_csv(REP / "persistence.csv"); pm_ = json.load(open(REP / "persistence_models.json")); conc = json.load(open(REP / "concentration.json"))
    fig, ax = plt.subplots(figsize=(8, 4)); sub = pt[pt.group.isin(["all comparable", "top100_by_pnl", "top500_by_pnl", "top2000_by_pnl", "bottom500_by_pnl", "top100_by_roi", "top500_by_roi"])]
    ax.barh(sub.group, sub.frac_profitable_b, color=[C[0] if "pnl" in g else C[1] if "roi" in g else INK2 for g in sub.group], height=0.6)
    ax.axvline(sub[sub.group == "all comparable"].frac_profitable_b.iloc[0], color=GRID, lw=1.5, ls="--"); ax.set_xlabel("fraction profitable in period B (2026)"); ax.set_title("Do period-A winners keep winning?"); ax.invert_yaxis()
    fig.tight_layout(); fig.savefig(FIG / "persistence.png"); plt.close(fig)
    md.append("## 4. Concentration and skill persistence\n")
    md.append(f"- Wallets: {conc['n_wallets']:,}; losing: {conc['frac_losing']:.1%}; top 1% of wallets took {conc['top1pct_share_of_profits']:.1%} of all profits; top 100 took {conc['top100_share']:.1%}. Markets: {conc['n_markets']:,}.\n")
    md.append("![](fig/persistence.png)\n"); md.append(pt.round(3).to_markdown(index=False)); md.append("")
    md.append("Skill-shrinkage model (how much of period-A ROI predicts period-B ROI):\n"); md.append(pd.DataFrame(pm_).round(4).to_markdown()); md.append("")
    if (REP / "top100_style_summary.csv").exists():
        st = pd.read_csv(REP / "top100_style_summary.csv")
        md.append("Trading style of the top-100 wallets (by period-A profit) and what happened to each style in period B. Taker share = fraction of notional where the wallet was the aggressor; both-sides = fraction of markets where it traded both outcome tokens:\n")
        md.append(st.round(3).to_markdown(index=False)); md.append("")

def fig_copy():
    cp = pd.read_csv(REP / "copy_backtest.csv")
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), sharey=True)
    for ax, mk in zip(axes, ["all", "vol>=250k"]):
        for i, (g, band) in enumerate([("top100_pnl", "0-1"), ("random1000", "0-1"), ("top100_pnl", "0.05-0.95"), ("random1000", "0.05-0.95")]):
            r = cp[(cp.group == g) & (cp.band == band) & (cp.markets == mk)].sort_values("delay_s")
            if len(r): ax.errorbar(r.delay_s, r.roi * 100, yerr=[(r.roi - r.ci_lo) * 100, (r.ci_hi - r.roi) * 100], fmt="o-", color=C[i], lw=2, ms=5, capsize=3, label=f"{g} ({'5-95c' if band != '0-1' else 'all prices'})")
        ax.axhline(0, color=GRID, lw=1.5); ax.set_xscale("log"); ax.set_xlabel("copy delay (s)"); ax.set_title(f"markets: {mk}"); ax.set_ylim(-20, 10)
    axes[0].set_ylabel("ROI per $100 position, %  (95% CI by market)"); axes[0].legend(fontsize=8, loc="lower left")
    fig.suptitle("Copy-trading at the next real same-side taker price after the delay, fees included, held to resolution", fontsize=10)
    fig.tight_layout(); fig.savefig(FIG / "copy_backtest.png"); plt.close(fig)
    md.append("## 5. Copy-trading backtest\n"); md.append("![](fig/copy_backtest.png)\n"); md.append("The top100_roi group (ranked by period-A ROI) is omitted from the chart: it has only a few hundred positions and its CIs span tens of percent; see the table.\n"); md.append(cp.round(4).to_markdown(index=False)); md.append("")

def summary():
    conc = json.load(open(REP / "concentration.json")); pt = pd.read_csv(REP / "persistence.csv").set_index("group")
    cp = pd.read_csv(REP / "copy_backtest.csv"); fit = json.load(open(REP / "calibration_fit.json")); st = pd.read_csv(REP / "top100_style_summary.csv").set_index("style")
    def cpv(g, band, mk, d): r = cp[(cp.group == g) & (cp.band == band) & (cp.markets == mk) & (cp.delay_s == d)].iloc[0]; return f"{r.roi:+.1%} [{r.ci_lo:+.1%}, {r.ci_hi:+.1%}]"
    md.append("## Summary\n")
    md.append(f"- **Concentration.** {conc['n_wallets']:,} wallets traded {conc['n_markets']:,} resolved markets (${conc['notional_one_sided']/1e9:.1f}B one-sided notional). {conc['frac_losing']:.0%} lost money. Profit is exactly zero-sum (${conc['total_profit_of_winners']/1e6:.0f}M won = ${-conc['total_loss_of_losers']/1e6:.0f}M lost); the top 1% of wallets took {conc['top1pct_share_of_profits']:.0%} of it.")
    md.append(f"- **Persistence.** Of the top-100 wallets by pre-2026 profit, {pt.loc['top100_by_pnl','frac_profitable_b']:.0%} were profitable again in Jan-Apr 2026 vs {pt.loc['all comparable','frac_profitable_b']:.0%} of all comparable wallets (top-500: {pt.loc['top500_by_pnl','frac_profitable_b']:.0%}). Ranking by past ROI instead selects longshot luck: {pt.loc['top100_by_roi','frac_profitable_b']:.0%} profitable. Rank correlation of ROI across periods is ~0.12-0.23; the shrinkage model says to believe only ~10-30% of a wallet's past ROI.")
    md.append(f"- **Who persists: makers.** Within the top 100, maker-style wallets (<30% of notional as aggressor) were profitable again {st.loc['maker (<30% taker)','profitable_B']:.0%} of the time (${st.loc['maker (<30% taker)','pnl_B']/1e6:.1f}M), taker-style wallets {st.loc['taker (>70% taker)','profitable_B']:.0%} (${st.loc['taker (>70% taker)','pnl_B']/1e6:.1f}M). Most top wallets trade both sides of the same market.")
    md.append(f"- **Copying does not work.** $100 per new position, next real same-side taker price, fees per Polymarket's schedule, 10s delay, markets >=$250k and prices 5-95c: top-100 {cpv('top100_pnl','0.05-0.95','vol>=250k',10)}, 1,000 random wallets {cpv('random1000','0.05-0.95','vol>=250k',10)}. Over all markets and prices: top-100 {cpv('top100_pnl','0-1','all',10)}, random {cpv('random1000','0-1','all',10)}. The edge of the winners does not survive the trip to a copier.")
    md.append(f"- **Calibration.** Prices are slightly under-confident overall (logit slope b={fit['share']['b']:.2f}, share-weighted): buying below 5c loses ~84% and 5-10c ~32% to resolution, while 65-95c favourites earn +1-4%. By category the slope is ~1 for sports and politics and >1 for crypto, so the reversal is mostly a crypto/price-action phenomenon.")
    md.append("- **Information.** Ordinary taker trades carry almost no information that the market prices in before resolution: markouts are flat for a day and jump only at settlement. The exceptions are the largest trades (>=$10k: +1.7c at resolution) and the top-100 wallets, whose trades move the price in their favour within 10 seconds (+0.35c) and are worth +4c at resolution. That is a speed edge, not a copyable one.")
    md.append("- **Market making.** Glosten-Milgrom accounting (half-spread from bounce minus adverse selection from taker markouts): providing liquidity is positive-edge in politics (+2.2c/share) and finance, roughly flat in price-action markets, and negative in sports and crypto where takers are informed in-game (-1.1c and -0.7c per share before rebates).")
    md.append("")

def methods():
    md.append("## Methods and validation\n")
    md.append("- **Data.** Hugging Face `TimeSeventeen/Polymarket-v1` (CC-BY-4.0): cleaned on-chain `OrderFilled` tape of the v1 CTF Exchange on Polygon, 2025-07-01 to 2026-04-28 (the v2 cut-over day). One row per maker/taker fill with the taker's direction; shares = notional/price. Resolution from `ConditionResolution` payout numerators (the dataset's `winning_outcome_label` has whitespace mismatches and is not used).")
    md.append("- **Ledger.** Wallet x market profit = cash from fills + net shares x payout. Short positions are the on-chain footprint of split-and-sell and the formula stays exact (short YES at p == long NO at 1-p). Reconciled against Polymarket's Data API closed positions: July 2025, 92% of 1,167 wallet-markets within $1, aggregate within 0.2%; April 2026 (fee markets), median difference $0.26 with no fee deducted.")
    md.append("- **Fees.** The dataset's `fee_usdc` is the raw on-chain fee field of the maker order (USDC units when the maker sold, token units when it bought, computed at the 10% cap rate). Polymarket's own PnL accounting does not deduct it and third-party estimates of real Q1-2026 net fees (~$24M) are an order of magnitude below its sum ($335M), so wallet profit is reported gross of it. Copier and calibration fees use the published schedule, fee = feeRate x p x (1-p) per share (crypto 0.07, sports 0.03, politics/finance/tech 0.04, others 0.05, geopolitics 0), on markets flagged fee-enabled.")
    md.append("- **Out of sample.** Wallets are ranked on markets last traded before 2026-01-01 (>=20 markets, >=$10k cost basis) and evaluated on 2026. Copy signals are the first fill of a wallet in a market (capped at 1,000 random markets per wallet); execution is the next real taker fill on the same side at or after t0+delay; CIs bootstrap over markets.")
    md.append("- **Markouts.** 2% random sample of resolved markets (~13M fills); signed markout = D x (p(t+h) - p(t)) with p(t+h) the first trade at or after t+h; share-weighted.")
    md.append("- **Caveats.** v1 contracts only (v2 since 2026-04-28 has pUSD collateral and a new fee structure); maker rebates not modelled; no order-book data, so half-spreads are inferred from bounce; period B is four months and includes the fee roll-out; the ROI-ranked wallets are few and noisy.")
    md.append("")

if __name__ == "__main__":
    summary(); methods()
    for f in (fig_calibration, fig_by_month, fig_markout, fig_persistence, fig_copy):
        try: f()
        except Exception as e: md.append(f"\n_({f.__name__} skipped: {e})_\n"); print(f.__name__, "skipped:", e)
    (REP / "REPORT.md").write_text("\n".join(md)); print("wrote", REP / "REPORT.md")
