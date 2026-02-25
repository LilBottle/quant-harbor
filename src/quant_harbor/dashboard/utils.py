from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class RunSummary:
    run_id: str
    path: Path
    summary: Dict[str, Any]


@dataclass
class ScorecardRun:
    run_id: str
    path: Path
    scorecard: Dict[str, Any]
    meta: Dict[str, Any]
    sources: Dict[str, Any]


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def discover_runs(results_dir: Path) -> List[RunSummary]:
    """Discover legacy per-run summary.json outputs."""
    runs: List[RunSummary] = []
    if not results_dir.exists():
        return runs

    for p in sorted(results_dir.iterdir()):
        if not p.is_dir():
            continue
        s = p / "summary.json"
        if not s.exists():
            continue
        summary = _read_json(s)
        if not summary:
            continue
        runs.append(RunSummary(run_id=p.name, path=p, summary=summary))

    runs.sort(key=lambda r: r.run_id, reverse=True)
    return runs


def discover_scorecards(results_dir: Path) -> List[ScorecardRun]:
    """Discover scorecard.json outputs (Dashboard v2 primary input)."""
    out: List[ScorecardRun] = []
    if not results_dir.exists():
        return out

    for p in sorted(results_dir.iterdir()):
        if not p.is_dir():
            continue
        sc_path = p / "scorecard.json"
        if not sc_path.exists():
            continue
        blob = _read_json(sc_path)
        if not blob:
            continue
        meta = blob.get("meta") or {}
        scorecard = blob.get("scorecard") or {}
        sources = blob.get("sources") or {}
        out.append(ScorecardRun(run_id=p.name, path=p, scorecard=scorecard, meta=meta, sources=sources))

    out.sort(key=lambda r: r.run_id, reverse=True)
    return out


def runs_to_dataframe(runs: List[RunSummary]) -> pd.DataFrame:
    rows = []
    for r in runs:
        s = r.summary
        rows.append(
            {
                "run_id": r.run_id,
                "strategy": s.get("strategy"),
                "symbol": s.get("symbol"),
                "net_pnl": s.get("net_pnl"),
                "net_return_pct": s.get("net_return_pct"),
                "max_dd_close_pct": s.get("max_drawdown_close_pct"),
                "max_dd_intrabar_pct": s.get("max_drawdown_intrabar_pct"),
                "profit_factor": s.get("profit_factor"),
                "expectancy": s.get("expectancy"),
                "win_rate_pct": s.get("win_rate_pct"),
                "total_trades": s.get("total_trades"),
                "sharpe": s.get("sharpe"),
                "slippage_bps_side": s.get("slippage_bps_side"),
                "generated_utc": s.get("generated_utc"),
                "path": str(r.path),
            }
        )
    return pd.DataFrame(rows)


def scorecards_to_dataframe(runs: List[ScorecardRun]) -> pd.DataFrame:
    rows = []
    for r in runs:
        sc = r.scorecard
        meta = r.meta
        inputs = sc.get("inputs") or {}
        subs = sc.get("subscores01") or {}
        rows.append(
            {
                "run_id": r.run_id,
                "symbol": meta.get("symbol"),
                "strategy": meta.get("strategy"),
                "run_kind": (meta.get("run_kind") or ""),
                "total_score": sc.get("total_score"),
                "robustness01": subs.get("robustness"),
                "risk01": subs.get("risk"),
                "return_quality01": subs.get("return_quality"),
                "impl01": subs.get("implementability"),
                "pos_window_rate": inputs.get("pos_window_rate"),
                "basin_pass_rate": inputs.get("basin_pass_rate"),
                "maxdd_intra": inputs.get("max_drawdown_intrabar_pct"),
                "pf": inputs.get("profit_factor"),
                "sharpe": inputs.get("sharpe"),
                "net_return_pct": inputs.get("net_return_pct"),
                "path": str(r.path),
            }
        )
    return pd.DataFrame(rows)


def load_equity(run_dir: Path) -> Optional[pd.DataFrame]:
    p = run_dir / "equity.parquet"
    if not p.exists():
        return None
    return pd.read_parquet(p)


def load_trades(run_dir: Path) -> Optional[pd.DataFrame]:
    p = run_dir / "trades.parquet"
    if not p.exists():
        return None
    return pd.read_parquet(p)


def load_parquet(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    return pd.read_parquet(path)


def load_snapshot_bars(snapshot_dir: Path, symbol: str) -> Optional[pd.DataFrame]:
    """Load bars for a symbol from a snapshot directory.

    Supports both layouts:
    - bars.parquet (single symbol)
    - bars_<SYMBOL>.parquet (multi symbol)
    """
    p_multi = snapshot_dir / f"bars_{symbol}.parquet"
    p_single = snapshot_dir / "bars.parquet"

    p = p_multi if p_multi.exists() else p_single
    if not p.exists():
        return None

    df = pd.read_parquet(p)
    if df.index.tz is None:
        df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()
