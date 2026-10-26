"""First-principles models fitted on Polymarket fills.

1. CalibrationModel   : q(p) = sigma(a + b*logit(p)).  Edge of buying token at p: q(p) - p - fee.
                        Kelly fraction (binary payoff): f* = (q - p) / (1 - p)  for q > p.
2. InfoDecayModel     : signed markout curve  M(h) = M_inf - (M_inf - M_0) * exp(-h/tau)  (h in seconds).
                        A copier executing at delay d captures  M_inf - M(d) - fee  on the event axis.
3. MarketMakerModel   : Glosten-Milgrom accounting. Maker edge per $ notional = half_spread - adverse_sel - maker_fee,
                        with half_spread estimated from short-horizon bounce (-M(10s)) and adverse selection = M_inf
                        of the takers the maker trades against.
4. SkillShrinkage     : ROI_A = skill + noise; reliability rho = Var(skill)/Var(ROI_A) estimated by method of moments
                        using per-wallet sampling variance; predicted ROI_B = rho * ROI_A + (1-rho)*mean  (James-Stein).
"""
from __future__ import annotations
import numpy as np, pandas as pd
from dataclasses import dataclass
from scipy.optimize import minimize, curve_fit

def logit(p): p = np.clip(p, 1e-6, 1 - 1e-6); return np.log(p / (1 - p))
def sigmoid(z): return 1 / (1 + np.exp(-z))

@dataclass
class CalibrationModel:
    a: float = 0.0
    b: float = 1.0
    def fit(self, p, y, w=None):
        p = np.asarray(p, float); y = np.asarray(y, float); w = np.ones_like(p) if w is None else np.asarray(w, float)
        x = logit(p)
        def nll(th):
            q = np.clip(sigmoid(th[0] + th[1] * x), 1e-9, 1 - 1e-9)
            return -np.sum(w * (y * np.log(q) + (1 - y) * np.log(1 - q)))
        r = minimize(nll, x0=[0.0, 1.0], method="L-BFGS-B"); self.a, self.b = r.x; return self
    def q(self, p): return sigmoid(self.a + self.b * logit(np.asarray(p, float)))
    def edge(self, p, fee_rate=0.0): return self.q(p) - np.asarray(p, float) - fee_rate * np.asarray(p, float)
    def roi(self, p, fee_rate=0.0): p = np.asarray(p, float); return self.q(p) / p - 1 - fee_rate
    def kelly(self, p, fee_rate=0.0):
        p = np.asarray(p, float); q = self.q(p); c = p * (1 + fee_rate)  # cost per share incl. fee
        return np.clip((q - c) / (1 - c), 0, None)
    def table(self, prices=(0.02, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98), fee_rate=0.0):
        p = np.array(prices)
        return pd.DataFrame(dict(price=p, q_true=self.q(p), roi=self.roi(p, fee_rate), kelly=self.kelly(p, fee_rate)))

@dataclass
class InfoDecayModel:
    m0: float = 0.0; m_inf: float = 0.0; tau: float = 60.0
    @staticmethod
    def f(h, m0, m_inf, tau): return m_inf - (m_inf - m0) * np.exp(-h / tau)
    def fit(self, horizons_s, markouts, m_inf=None):
        h = np.asarray(horizons_s, float); m = np.asarray(markouts, float)
        if m_inf is None:
            (self.m0, self.m_inf, self.tau), _ = curve_fit(self.f, h, m, p0=[m[0], m[-1], 300], maxfev=20000)
        else:
            self.m_inf = m_inf
            g = lambda h, m0, tau: self.f(h, m0, m_inf, tau)
            (self.m0, self.tau), _ = curve_fit(g, h, m, p0=[m[0], 300], maxfev=20000)
        return self
    def copier_edge(self, delay_s, fee=0.0): return self.m_inf - self.f(np.asarray(delay_s, float), self.m0, self.m_inf, self.tau) - fee
    def half_life(self): return self.tau * np.log(2)

def market_maker_table(mo_by_group: pd.DataFrame, maker_fee_rate=0.0) -> pd.DataFrame:
    """mo_by_group: columns group, w10s, winf (share-weighted signed taker markouts, event-prob units), notional.
    Maker edge per $1 notional ~ bounce (= -w10s) - adverse selection (= winf) - fee. Units: probability points ~ $ per share."""
    t = mo_by_group.copy()
    t["half_spread_est"] = -t["w10s"]
    t["adverse_selection"] = t["winf"]
    t["maker_edge_per_share"] = t.half_spread_est - t.adverse_selection - maker_fee_rate
    return t

def skill_shrinkage(df: pd.DataFrame, roi_a="roi", roi_b="roi_b", n_a="n_markets", per_market_sd: float | None = None):
    """Method-of-moments reliability of period-A ROI as a predictor of period-B ROI.
    If per_market_sd is None it is estimated from the residual (B - A) dispersion assuming independent noise."""
    d = df.dropna(subset=[roi_a, roi_b]).copy()
    d = d[np.isfinite(d[roi_a]) & np.isfinite(d[roi_b])]
    var_a = d[roi_a].var(); cov = np.cov(d[roi_a], d[roi_b])[0, 1]
    slope = cov / var_a  # OLS slope of ROI_B on ROI_A = reliability under classical measurement error
    # winsorised version (ROI can be heavy tailed)
    lo, hi = d[roi_a].quantile([0.01, 0.99]); dw = d[(d[roi_a] > lo) & (d[roi_a] < hi)]
    lo2, hi2 = dw[roi_b].quantile([0.01, 0.99]); dw = dw[(dw[roi_b] > lo2) & (dw[roi_b] < hi2)]
    slope_w = np.cov(dw[roi_a], dw[roi_b])[0, 1] / dw[roi_a].var()
    from scipy.stats import spearmanr
    return dict(n=len(d), reliability_ols=slope, reliability_winsor=slope_w, spearman=spearmanr(d[roi_a], d[roi_b]).correlation,
                mean_roi_a=d[roi_a].mean(), mean_roi_b=d[roi_b].mean(), var_a=var_a, cov_ab=cov)
