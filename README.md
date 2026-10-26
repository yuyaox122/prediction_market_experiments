# Polymarket first-principles research pipeline

Reproduces and extends the analysis in Algovibes' video *"Polymarket's Best Traders Keep Winning. So I Copied Them."*
(2026-10-15) on the public on-chain trade tape, and fits first-principles models of where profit comes from.

## Data

* **Source:** Hugging Face `TimeSeventeen/Polymarket-v1` (CC-BY-4.0): every `OrderFilled` event of the v1 CTF Exchange
  on Polygon, cleaned (router/relayer legs removed, market metadata joined), partitioned by UTC day.
  `daily_aligned/` = standard binary markets, `daily_aligned_multi/` = neg-risk multi-outcome events.
  Coverage 2022-11-21 to 2026-04-28 (the v2 exchange cut-over day, so the v1 tape is complete).
* **Window used here:** 2025-07-01 to 2026-04-28 (~15 GB parquet) in `data/hf/Polymarket-v1/`.
* **Resolution truth:** `CTF/resolutions.parquet` (`payout_numerators` from `ConditionResolution` events).
  The dataset's `winning_outcome_label` has whitespace mismatches (e.g. `"Sinner "` vs `"Sinner"`), so it is **not** used.
* One row = one maker/taker fill. `taker` is the aggressor, `taker_direction` is BUY/SELL of `asset_id`,
  `usdc_amount` is notional, `shares = usdc_amount / price`.
* **`fee_usdc` is not what traders paid.** It is the raw on-chain fee field of the maker order (USDC units when the maker
  sold, token units when it bought, at the 10% cap rate). Polymarket's own PnL accounting ignores it (validated against the
  Data API) and its sum is ~10x real fee revenue. Profit is reported gross of it; copier/calibration fees use the published
  schedule `fee = feeRate * p * (1-p)` per share on fee-enabled markets (`pm/db.py: fee_rate_model`).
  `p_event` / `D` put every fill on the outcome-1 probability axis (buying NO == selling YES).

## Results

See `reports/REPORT.md` (summary, methods, charts in `reports/fig/`, tables as csv/json). Live alert tool: `scripts/track.py`.

## Validation (July 2025 and April 2026)

* Wallet x market profit vs Polymarket Data API `/v2/positions?status=CLOSED` (realized + unrealized pnl, summed per condition):
  median relative error ~1e-6, 92% of rows within $1, aggregate within 0.2% over 1,167 wallet-markets.
* Profit is zero-sum across wallets per market up to fees (checked to 1e-8).
* April 2026 (fee markets): median difference $0.26 per wallet-market with no fee deducted; every fee model fits worse.
* Gamma `volumeNum` is ~2x our one-sided notional: Gamma counts both legs of a fill.

## Modules (`pm/`)

| module | what it does |
|---|---|
| `db.py` | DuckDB views over the daily parquet files, joined to on-chain payouts (`pay`, `pay_event`) |
| `pnl.py` | wallet x market ledger: cash from fills + payout on net shares (exact, including mint/merge footprints) |
| `calibration.py` | realized win-rate vs price bucket; logit-linear calibration fit |
| `markout.py` | signed markouts of taker fills at 10s/1m/10m/1h/1d/resolution (forward ASOF joins) |
| `persistence.py` | rank wallets on period A, measure on period B; top-N tables, rank correlation |
| `copytrade.py` | copy backtest: first position per wallet-market, executed at the next real same-side taker price after a delay, fees included, bootstrap CIs by market |
| `models.py` | first-principles models: `CalibrationModel`, `InfoDecayModel`, Glosten-Milgrom maker table, skill shrinkage |

## Running

```bash
python3 -m venv .venv && .venv/bin/pip install pandas numpy scipy pyarrow duckdb polars requests matplotlib huggingface_hub
# download the window (see scripts/run_full.py START/END) with huggingface_hub.snapshot_download allow_patterns
PYTHONPATH=. .venv/bin/python scripts/run_full.py            # stages: pnl markets concentration persistence calibration markout copy copy_summary
PYTHONPATH=. .venv/bin/python scripts/top_style.py           # maker/taker style of the top-100 wallets
PYTHONPATH=. .venv/bin/python scripts/run_full.py copy       # one stage
PYTHONPATH=. .venv/bin/python scripts/validate_wallets.py    # API reconciliation
PYTHONPATH=. .venv/bin/python scripts/report.py              # charts + REPORT.md from reports/
```

## Models (first principles)

1. **Calibration.** `q(p) = sigma(a + b*logit p)` fitted by weighted Bernoulli MLE on taker buys. `b > 1` means prices are
   under-confident (favourite-longshot *reversal*): buying longshots loses, buying favourites earns. Edge = `q - p - fee`,
   Kelly `f* = (q - c)/(1 - c)` with `c` the fee-inclusive cost.
2. **Information decay.** Signed markout `M(h) = D*(p(t+h) - p(t))`, fitted as `M(h) = M_inf - (M_inf - M_0) e^{-h/tau}`.
   A copier executing `d` seconds late captures `M_inf - M(d) - fee`; `tau` is the half-life of the edge.
3. **Market making (Glosten-Milgrom).** Maker edge per share = half-spread (bounce, `-M(10s)`) - adverse selection
   (`M_inf` of the takers it trades against) - fee. Shows where providing liquidity pays.
4. **Skill shrinkage.** `ROI_A = skill + noise`; reliability `rho = Cov(ROI_A, ROI_B)/Var(ROI_A)` is how much of a wallet's
   past ROI to believe. Predicted `ROI_B = rho*ROI_A + (1-rho)*mean`.

Outputs land in `reports/` (csv/json + `REPORT.md` with charts).
