from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import json

import pandas as pd

from quant_harbor.alpaca_data import make_snapshot_multi
from quant_harbor.backtest_runner import BacktestConfig, run_backtest_df
from quant_harbor.strategies.registry import get_strategy_spec

ET = ZoneInfo("America/New_York")


def _parse_symbols(symbol: str | None, symbols_csv: str | None) -> list[str]:
    if symbols_csv:
        return [s.strip().upper() for s in symbols_csv.split(",") if s.strip()]
    if symbol:
        return [symbol.strip().upper()]
    raise SystemExit("Provide --symbol or --symbols")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, help="Strategy id (see registry). e.g. rsi2, bollinger_mr, zscore_mr, vwap_mr, pairs_mr")
    ap.add_argument("--symbol", default="QQQ")
    ap.add_argument("--symbols", default="", help="Comma-separated symbols for multi-leg strategies (e.g. QQQ,SPY)")
    ap.add_argument("--years", type=int, default=5)

    ap.add_argument("--params", default="", help="JSON string of strategy params to override defaults")

    args = ap.parse_args()

    spec = get_strategy_spec(args.strategy)

    syms = _parse_symbols(args.symbol, args.symbols if args.symbols else None)
    if len(syms) != spec.n_legs:
        raise SystemExit(f"Strategy {spec.id} requires {spec.n_legs} symbol(s); got {syms}")

    end_et = datetime.now(tz=ET)
    start_et = end_et.replace(year=end_et.year - args.years)

    project_root = Path(__file__).resolve().parents[2]
    snap_dir = make_snapshot_multi(syms, start_et=start_et, end_et=end_et, base_dir=project_root / "data" / "snapshots")

    # load dfs
    dfs = []
    for s in syms:
        p = snap_dir / f"bars_{s}.parquet" if (snap_dir / f"bars_{s}.parquet").exists() else (snap_dir / "bars.parquet")
        df = pd.read_parquet(p)
        if df.index.tz is None:
            df.index = pd.to_datetime(df.index, utc=True)
        dfs.append(df.sort_index())

    # params
    params = {}
    if args.params:
        params = json.loads(args.params)

    run_id = datetime.now(tz=ET).strftime("%Y%m%d_%H%M%S")
    out_dir = project_root / "results" / f"{spec.id}_{'-'.join(syms)}_{run_id}"

    cfg = BacktestConfig(symbol="-".join(syms))

    meta = json.loads((snap_dir / "meta.json").read_text())
    meta.update({"strategy_id": spec.id, "symbols": syms})

    out = run_backtest_df(
        dfs_utc=dfs,
        out_dir=out_dir,
        cfg=cfg,
        strategy_cls=spec.cls,
        strat_params=params,
        snapshot_meta=meta,
        persist_details=True,
        strategy_id=spec.id,
    )

    print("Backtest done:", out_dir)
    print("strategy:", spec.id)
    print("symbol(s):", syms)
    print("net_return_pct:", out.get("net_return_pct"))


if __name__ == "__main__":
    main()
