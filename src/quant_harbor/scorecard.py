from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np

from .stats import deflated_sharpe_ratio


@dataclass
class ScoreWeights:
    """Scorecard v1 weights (sum=100).

    Per plan:
    - Robustness 55
    - Risk/Tail 25
    - Return Quality 15
    - Implementability 5
    """

    robustness: float = 55.0
    risk: float = 25.0
    retq: float = 15.0
    impl: float = 5.0


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _linear(x: float, x0: float, x1: float) -> float:
    """Map x in [x0,x1] to [0,1] (clipped)."""
    if x1 == x0:
        return 0.0
    return _clip01((x - x0) / (x1 - x0))


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def scorecard_v1(
    *,
    wfa_summary: Optional[Dict[str, Any]] = None,
    wfa_gate_report: Optional[Dict[str, Any]] = None,
    basin_report: Optional[Dict[str, Any]] = None,
    basin_wfa_report: Optional[Dict[str, Any]] = None,
    val_summary: Optional[Dict[str, Any]] = None,
    test_summary: Optional[Dict[str, Any]] = None,
    weights: ScoreWeights = ScoreWeights(),
) -> Dict[str, Any]:
    """Compute a pragmatic scorecard v1.

    This is an infrastructure-first implementation so the research loop can run end-to-end.
    It produces auditable sub-scores and a total score in [0,100].

    Inputs are optional; missing components degrade gracefully and are reported.

    Conventions:
    - Robustness is driven by WFA OOS temporal consistency + parameter basin stability.
    - Risk is driven by (intrabar) max drawdown and drawdown length when available.
    - Return Quality uses PF/expectancy/sharpe/net_return when available.
    """

    missing = []

    # ---------- Robustness (0..1) ----------
    # Temporal consistency proxy: pos_window_rate from wfa_summary, or wfa_pass_rate from wfa_gate_report
    pos_window_rate = None
    if wfa_summary:
        pos_window_rate = _safe_float(wfa_summary.get("pos_window_rate"))
    if pos_window_rate is None and wfa_gate_report:
        # gate report may be v2 structure: {wfa:{wfa_pass_rate:...}}
        wfa = wfa_gate_report.get("wfa") if isinstance(wfa_gate_report, dict) else None
        if isinstance(wfa, dict):
            pos_window_rate = _safe_float(wfa.get("wfa_pass_rate"))

    if pos_window_rate is None:
        missing.append("wfa_pos_window_rate")
        pos_window_rate = 0.0

    # Basin stability proxy: basin_pass_rate from basin_report (val) or median from basin_wfa_report
    basin_pass = None
    if basin_wfa_report:
        basin_pass = _safe_float(basin_wfa_report.get("basin_pass_rate_median"))
    if basin_pass is None and basin_report:
        basin_pass = _safe_float(basin_report.get("basin_pass_rate"))

    if basin_pass is None:
        missing.append("basin_pass_rate")
        basin_pass = 0.0

    # Map to 0..1. Institutions want pos_window_rate ~>= 0.7.
    temporal_score = _linear(pos_window_rate, 0.0, 0.7)
    basin_score = _linear(basin_pass, 0.0, 0.3)  # 30% passing in neighborhood is already decent

    robustness01 = 0.6 * temporal_score + 0.4 * basin_score

    # Multiple-comparison penalty / confidence (optional): Deflated Sharpe Ratio
    # If a run provides n_trials + sharpe in sources/meta, we can compute DSR and expose it.
    n_trials = None
    if wfa_summary:
        n_trials = wfa_summary.get("n_trials")
    dsr = None
    if n_trials is not None:
        # Use test Sharpe if available, else val Sharpe.
        sr_in = None
        if test_summary:
            sr_in = _safe_float(test_summary.get("sharpe"))
        if sr_in is None and val_summary:
            sr_in = _safe_float(val_summary.get("sharpe"))
        if sr_in is not None:
            dsr = deflated_sharpe_ratio(sr_in, int(n_trials))

    # ---------- Risk / Tail (0..1) ----------
    # Use max_drawdown_intrabar_pct. Lower is better.
    dd = None
    if val_summary:
        dd = _safe_float(val_summary.get("max_drawdown_intrabar_pct"))
    if dd is None and test_summary:
        dd = _safe_float(test_summary.get("max_drawdown_intrabar_pct"))

    if dd is None:
        missing.append("max_drawdown_intrabar_pct")
        dd = 100.0

    # Score: dd <= 5% is great (1), dd >= 20% is bad (0)
    risk_dd01 = 1.0 - _linear(dd, 5.0, 20.0)

    # Drawdown length (optional): lower better.
    ddl = None
    if val_summary:
        ddl = _safe_float(val_summary.get("max_drawdown_close_len"))
    if ddl is None and test_summary:
        ddl = _safe_float(test_summary.get("max_drawdown_close_len"))
    if ddl is None:
        risk_len01 = 0.5
        missing.append("max_drawdown_close_len")
    else:
        # Heuristic: <= 500 is good, >= 5000 is poor
        risk_len01 = 1.0 - _linear(ddl, 500.0, 5000.0)

    risk01 = 0.7 * risk_dd01 + 0.3 * risk_len01

    # ---------- Return Quality (0..1) ----------
    # Prefer Val/OOS metrics; fall back to Test.
    pf = None
    exp = None
    sharpe = None
    netr = None

    s0 = val_summary or test_summary or {}
    pf = _safe_float(s0.get("profit_factor"))
    # Prefer normalized expectancy if available
    exp = _safe_float(s0.get("expectancy_pct_of_start"))
    if exp is None:
        exp = _safe_float(s0.get("expectancy"))
    sharpe = _safe_float(s0.get("sharpe"))
    netr = _safe_float(s0.get("net_return_pct"))

    if pf is None:
        missing.append("profit_factor")
        pf = 0.0
    if exp is None:
        missing.append("expectancy")
        exp = 0.0
    if sharpe is None:
        missing.append("sharpe")
        sharpe = 0.0
    if netr is None:
        missing.append("net_return_pct")
        netr = 0.0

    # Map PF: 1.0 -> 0.5, 1.3 -> 1.0
    pf01 = _clip01((pf - 0.7) / (1.3 - 0.7))
    # Expectancy (normalized, pct-of-start). Heuristic mapping:
    # -0.05% -> 0, 0% -> 0.5, +0.05% -> 1.0
    # (This keeps a reasonable dynamic range for typical per-trade expectancy values.)
    exp01 = _clip01((exp + 0.05) / 0.10)
    # Sharpe: 0 -> 0.5, 1.0 -> 1.0
    sharpe01 = _clip01((sharpe + 1.0) / 2.0)
    # Net return: -5% -> 0, +5% -> 1
    netr01 = _linear(netr, -5.0, 5.0)

    retq01 = 0.35 * pf01 + 0.25 * exp01 + 0.25 * sharpe01 + 0.15 * netr01

    # ---------- Implementability (0..1) ----------
    # Placeholder v1: penalize if strategy params missing.
    impl01 = 1.0
    if not s0.get("strategy_params"):
        impl01 = 0.5
        missing.append("strategy_params")

    # ---------- Weighted total ----------
    total = (
        weights.robustness * robustness01
        + weights.risk * risk01
        + weights.retq * retq01
        + weights.impl * impl01
    )

    return {
        "score_version": "v1",
        "weights": {
            "robustness": weights.robustness,
            "risk": weights.risk,
            "return_quality": weights.retq,
            "implementability": weights.impl,
        },
        "inputs": {
            "pos_window_rate": pos_window_rate,
            "basin_pass_rate": basin_pass,
            "max_drawdown_intrabar_pct": dd,
            "profit_factor": pf,
            "expectancy": exp,
            "sharpe": sharpe,
            "net_return_pct": netr,
            "n_trials": n_trials,
            "dsr": dsr,
        },
        "subscores01": {
            "robustness": robustness01,
            "risk": risk01,
            "return_quality": retq01,
            "implementability": impl01,
            "temporal_consistency": temporal_score,
            "basin": basin_score,
        },
        "total_score": float(np.round(total, 6)),
        "missing": sorted(list(set(missing))),
    }
