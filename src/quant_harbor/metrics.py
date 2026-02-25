from __future__ import annotations

from typing import Dict, Any, List

import numpy as np


def compute_trade_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute trade-level metrics.

    Notes:
    - turnover is approximated by annualized trade count (since we don't yet track notional).
    - average holding is measured in bars via backtrader's trade.barlen.
    """
    if not trades:
        return {
            'total_trades': 0,
            'gross_profit': 0.0,
            'gross_loss': 0.0,
            'profit_factor': None,
            'expectancy': None,
            'avg_win': None,
            'avg_loss': None,
            'win_rate_pct': None,
            'best_trade': None,
            'worst_trade': None,
            'avg_hold_bars': None,
            'avg_hold_minutes': None,
            'trades_annualized': None,
        }

    pnls = np.array([t['pnl_comm'] for t in trades], dtype=float)
    hold_bars = np.array([t.get('bar_len', 0) for t in trades], dtype=float)

    # Annualize trade count using time span covered by trades (best-effort).
    trades_annualized = None
    try:
        import pandas as pd

        entry = pd.to_datetime([t['entry_dt'] for t in trades], utc=True)
        exit = pd.to_datetime([t['exit_dt'] for t in trades], utc=True)
        t0 = min(entry.min(), exit.min())
        t1 = max(entry.max(), exit.max())
        days = max((t1 - t0).total_seconds() / 86400.0, 1.0)
        trades_annualized = float(len(trades) * (365.25 / days))
    except Exception:
        trades_annualized = None
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    gross_profit = float(wins.sum())
    gross_loss = float(losses.sum())  # negative

    pf = None
    if gross_loss != 0:
        pf = gross_profit / abs(gross_loss)

    expectancy = float(pnls.mean())
    win_rate = float((pnls > 0).mean() * 100.0)

    avg_hold_bars = float(hold_bars.mean()) if len(hold_bars) else None

    return {
        'total_trades': int(len(pnls)),
        'gross_profit': gross_profit,
        'gross_loss': float(abs(gross_loss)),
        'profit_factor': pf,
        'expectancy': expectancy,
        'avg_win': float(wins.mean()) if len(wins) else None,
        'avg_loss': float(abs(losses.mean())) if len(losses) else None,
        'win_rate_pct': win_rate,
        'best_trade': float(pnls.max()),
        'worst_trade': float(pnls.min()),
        'pnl_p05': float(np.percentile(pnls, 5)),
        'pnl_p50': float(np.percentile(pnls, 50)),
        'pnl_p95': float(np.percentile(pnls, 95)),
        'avg_hold_bars': avg_hold_bars,
        'avg_hold_minutes': (avg_hold_bars * 15.0) if avg_hold_bars is not None else None,
        'trades_annualized': trades_annualized,
    }


def compute_drawdown_from_equity(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(equity)
    dd = (peaks - equity) / peaks
    return float(dd.max() * 100.0)
