from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

import pandas as pd


@dataclass
class GateConfig:
    """Hard gates (机构式硬门槛 v1).

    Notes:
    - These are *hard* gates: fail any -> reject.
    - Trade-count should be compared on an *annualized* basis when evaluating
      short OOS windows (e.g., 3 months).
    - Temporal consistency and parameter basin belong to later steps, but
      we still provide WFA aggregation helpers to evaluate gates over OOS windows.
    """

    maxdd_intrabar_pct: float = 10.0
    min_trades_annualized: int = 200
    # Strategy realism gates (optional; None disables):
    min_avg_hold_bars: int | None = None
    max_trades_annualized: float | None = None

    require_net_positive: bool = True


@dataclass
class WfaGateAggregateConfig:
    """How to aggregate hard-gate results over WFA OOS windows."""

    min_pass_rate: float = 0.70  # institutions typically want most windows to pass


def apply_gates(summary: Dict[str, Any], cfg: GateConfig) -> Dict[str, Any]:
    """Evaluate hard gates on a single segment summary.

    Returns a dict with:
    - gate_ok: bool
    - gate_reasons: list[str]
    - gate_cfg: echoed cfg
    """
    reasons: List[str] = []

    maxdd = summary.get("max_drawdown_intrabar_pct")
    trades = summary.get("total_trades")
    net = summary.get("net_pnl")

    dt_min = summary.get("data_dt_min_utc")
    dt_max = summary.get("data_dt_max_utc")

    ok = True

    if maxdd is None:
        ok = False
        reasons.append("missing_maxdd_intrabar")
    else:
        if float(maxdd) > cfg.maxdd_intrabar_pct:
            ok = False
            reasons.append(f"maxdd_intrabar>{cfg.maxdd_intrabar_pct}")

    # Trade count gate: annualize for window-length fairness.
    # We use bar timestamps range as proxy for coverage.
    if trades is None:
        ok = False
        reasons.append("missing_trades")
    else:
        # Prefer precomputed annualized trades from metrics if present.
        ann_trades = summary.get("trades_annualized")
        try:
            ann_trades = float(ann_trades) if ann_trades is not None else None
        except Exception:
            ann_trades = None

        if ann_trades is None:
            try:
                t = int(trades)
                if dt_min is not None and dt_max is not None:
                    t0 = pd.to_datetime(dt_min, utc=True)
                    t1 = pd.to_datetime(dt_max, utc=True)
                    days = max((t1 - t0).total_seconds() / 86400.0, 1.0)
                    ann_trades = t * (365.25 / days)
                else:
                    ann_trades = float(t)
            except Exception:
                ann_trades = None

        if ann_trades is None or float(ann_trades) < cfg.min_trades_annualized:
            ok = False
            reasons.append(f"trades_annualized<{cfg.min_trades_annualized}")

    # Optional realism gates
    avg_hold_bars = summary.get('avg_hold_bars')
    if cfg.min_avg_hold_bars is not None:
        if avg_hold_bars is None or float(avg_hold_bars) < float(cfg.min_avg_hold_bars):
            ok = False
            reasons.append(f"avg_hold_bars<{cfg.min_avg_hold_bars}")

    tr_ann = summary.get('trades_annualized')
    if cfg.max_trades_annualized is not None:
        if tr_ann is None or float(tr_ann) > float(cfg.max_trades_annualized):
            ok = False
            reasons.append(f"trades_annualized>{cfg.max_trades_annualized}")

    if cfg.require_net_positive:
        if net is None or float(net) <= 0:
            ok = False
            reasons.append("net_pnl<=0")

    return {
        "gate_ok": ok,
        "gate_reasons": reasons,
        "gate_cfg": {
            "maxdd_intrabar_pct": cfg.maxdd_intrabar_pct,
            "min_trades_annualized": cfg.min_trades_annualized,
            "min_avg_hold_bars": cfg.min_avg_hold_bars,
            "max_trades_annualized": cfg.max_trades_annualized,
            "require_net_positive": cfg.require_net_positive,
        },
    }


def aggregate_wfa_oos_gate_results(
    per_window: List[Dict[str, Any]],
    hard_cfg: GateConfig,
    agg_cfg: Optional[WfaGateAggregateConfig] = None,
) -> Dict[str, Any]:
    """Aggregate per-window hard-gate results into an institutional-style decision.

    per_window entries should include: gate_ok (bool) + metrics.
    """
    if agg_cfg is None:
        agg_cfg = WfaGateAggregateConfig()

    n = len(per_window)
    pass_rate = (sum(1 for r in per_window if r.get("gate_ok")) / n) if n else None

    ok = True
    reasons: List[str] = []

    if pass_rate is None:
        ok = False
        reasons.append("missing_windows")
    else:
        if pass_rate < agg_cfg.min_pass_rate:
            ok = False
            reasons.append(f"pass_rate<{agg_cfg.min_pass_rate}")

    return {
        "wfa_gate_ok": ok,
        "wfa_gate_reasons": reasons,
        "wfa_windows": n,
        "wfa_pass_rate": pass_rate,
        "hard_gate_cfg": {
            "maxdd_intrabar_pct": hard_cfg.maxdd_intrabar_pct,
            "min_trades_annualized": hard_cfg.min_trades_annualized,
            "require_net_positive": hard_cfg.require_net_positive,
        },
        "wfa_agg_cfg": {"min_pass_rate": agg_cfg.min_pass_rate},
    }
