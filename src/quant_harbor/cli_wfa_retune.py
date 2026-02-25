from __future__ import annotations

from datetime import datetime
from itertools import product
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import json

import numpy as np
import pandas as pd

from quant_harbor.alpaca_data import make_snapshot_multi
from quant_harbor.backtest_runner import BacktestConfig, run_backtest_df
from quant_harbor.split import split_train_val_test_last12m
from quant_harbor.strategies.registry import get_strategy_spec
from quant_harbor.walk_forward import make_quarterly_wfa_windows

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _grid_from_space(space: dict) -> list[dict]:
    keys = list(space.keys())
    vals = [space[k] for k in keys]
    out = []
    for combo in product(*vals):
        out.append(dict(zip(keys, combo)))
    return out


def _slice_all(dfs: list[pd.DataFrame], start, end) -> list[pd.DataFrame]:
    return [df[(df.index >= start) & (df.index <= end)] for df in dfs]


def _score_train(summary: dict) -> float:
    # v1: maximize net_pnl, tie-break PF
    net = float(summary.get("net_pnl") or 0.0)
    pf = float(summary.get("profit_factor") or 0.0)
    return net + 0.01 * pf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--symbols", default="QQQ", help="Comma-separated symbols. For pairs: e.g. QQQ,SPY")
    ap.add_argument("--years", type=int, default=5)

    ap.add_argument("--train-months", type=int, default=12)
    ap.add_argument("--oos-months", type=int, default=3)

    ap.add_argument("--grid-json", default="", help="Optional JSON dict of param space to override registry default")

    # soft filters during train selection
    ap.add_argument("--max-dd-intra", type=float, default=10.0)
    ap.add_argument("--min-trades", type=int, default=200)

    args = ap.parse_args()

    spec = get_strategy_spec(args.strategy)
    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if len(syms) != spec.n_legs:
        raise SystemExit(f"Strategy {spec.id} requires {spec.n_legs} symbol(s); got {syms}")

    end_et = datetime.now(tz=ET)
    start_et = end_et.replace(year=end_et.year - args.years)

    project_root = Path(__file__).resolve().parents[2]
    snap_dir = make_snapshot_multi(syms, start_et=start_et, end_et=end_et, base_dir=project_root / "data" / "snapshots")

    dfs = []
    for s in syms:
        df = pd.read_parquet(snap_dir / f"bars_{s}.parquet")
        if df.index.tz is None:
            df.index = pd.to_datetime(df.index, utc=True)
        dfs.append(df.sort_index())

    split0 = split_train_val_test_last12m(dfs[0])
    pre0 = pd.concat([split0.train, split0.val]).sort_index()

    windows = make_quarterly_wfa_windows(pre0.index, train_months=args.train_months, oos_months=args.oos_months)
    if not windows:
        raise SystemExit("No WFA windows created (not enough history).")

    space = spec.default_param_grid()
    if args.grid_json:
        space = json.loads(args.grid_json)
    candidates = _grid_from_space(space)

    run_id = datetime.now(tz=ET).strftime("%Y%m%d_%H%M%S")
    out_root = project_root / "results" / f"wfa_retune_{spec.id}_{'-'.join(syms)}_{run_id}"
    out_root.mkdir(parents=True, exist_ok=True)

    cfg = BacktestConfig(symbol="-".join(syms))

    snap_meta = json.loads((snap_dir / "meta.json").read_text())
    snap_meta.update(
        {
            "split_policy": "last12m_test",
            "cut_test_start_utc": str(split0.cut_test_start_utc),
            "wfa_train_months": args.train_months,
            "wfa_oos_months": args.oos_months,
            "wfa_mode": "retune_per_window",
            "strategy_id": spec.id,
            "symbols": syms,
        }
    )

    rows = []
    for i, w in enumerate(windows):
        train_dfs = _slice_all(dfs, w.train_start, w.train_end)
        oos_dfs = _slice_all(dfs, w.oos_start, w.oos_end)

        best = None
        best_train = None
        best_score = -1e18

        # Evaluate candidates on TRAIN (summary-only)
        for j, p in enumerate(candidates):
            tmp_dir = out_root / f"window_{i:02d}" / "train" / f"cand_{j:04d}"
            meta = dict(snap_meta)
            meta.update(
                {
                    "segment": "wfa_train_retune",
                    "window": i,
                    "cand": j,
                    "train_start_utc": str(w.train_start),
                    "train_end_utc": str(w.train_end),
                    "oos_start_utc": str(w.oos_start),
                    "oos_end_utc": str(w.oos_end),
                }
            )
            s = run_backtest_df(train_dfs, out_dir=tmp_dir, cfg=cfg, strategy_cls=spec.cls, strat_params=p, snapshot_meta=meta, persist_details=False, strategy_id=spec.id)

            dd = float(s.get("max_drawdown_intrabar_pct") or 1e9)
            trades = int(s.get("total_trades") or 0)
            if dd > float(args.max_dd_intra):
                continue
            if trades < int(args.min_trades):
                continue

            score = _score_train(s)
            if score > best_score:
                best_score = score
                best = p
                best_train = s

        if best is None:
            # fallback: pick best by score without filters
            for j, p in enumerate(candidates):
                tmp_dir = out_root / f"window_{i:02d}" / "train" / f"cand_{j:04d}"
                s_path = tmp_dir / "summary.json"
                if not s_path.exists():
                    continue
                s = json.loads(s_path.read_text())
                score = _score_train(s)
                if score > best_score:
                    best_score = score
                    best = p
                    best_train = s

        # Evaluate chosen params on OOS
        oos_dir = out_root / f"window_{i:02d}" / "oos"
        meta2 = dict(snap_meta)
        meta2.update(
            {
                "segment": "wfa_oos_retune",
                "window": i,
                "chosen_params": best,
                "train_start_utc": str(w.train_start),
                "train_end_utc": str(w.train_end),
                "oos_start_utc": str(w.oos_start),
                "oos_end_utc": str(w.oos_end),
            }
        )
        oos = run_backtest_df(oos_dfs, out_dir=oos_dir, cfg=cfg, strategy_cls=spec.cls, strat_params=best, snapshot_meta=meta2, persist_details=False, strategy_id=spec.id)

        rows.append(
            {
                "window": i,
                "train_start_utc": str(w.train_start),
                "train_end_utc": str(w.train_end),
                "oos_start_utc": str(w.oos_start),
                "oos_end_utc": str(w.oos_end),
                "oos_net_return_pct": oos.get("net_return_pct"),
                "oos_net_pnl": oos.get("net_pnl"),
                "oos_maxdd_intrabar_pct": oos.get("max_drawdown_intrabar_pct"),
                "oos_trades": oos.get("total_trades"),
                "chosen_params": json.dumps(best, default=str),
                "train_net_return_pct": best_train.get("net_return_pct") if best_train else None,
            }
        )

        print(f"[WFA retune {i:02d}] oos_net%={oos.get('net_return_pct'):.3f} pf={oos.get('profit_factor')}")

    wfa_df = pd.DataFrame(rows)
    wfa_df.to_parquet(out_root / "wfa_windows.parquet", index=False)

    # Aggregate
    pnls = np.array([float(x or 0.0) for x in wfa_df["oos_net_pnl"].tolist()], dtype=float)
    rets = np.array([float(x or 0.0) for x in wfa_df["oos_net_return_pct"].tolist()], dtype=float)
    pos_rate = float(np.mean(pnls > 0.0)) if len(pnls) else None

    agg = {
        "wfa_mode": "retune_per_window",
        "symbol": "-".join(syms),
        "strategy": spec.id,
        "windows": int(len(wfa_df)),
        "pos_window_rate": pos_rate,
        "oos_net_return_mean": float(np.mean(rets)) if len(rets) else None,
        "oos_net_return_median": float(np.median(rets)) if len(rets) else None,
        "oos_net_return_worst": float(np.min(rets)) if len(rets) else None,
        "n_trials": int(len(candidates)),
        "generated_utc": datetime.now(tz=UTC).isoformat(),
        "snapshot_dir": str(snap_dir),
    }
    (out_root / "wfa_summary.json").write_text(json.dumps(agg, indent=2, default=str))

    print("WFA retune done:", out_root)


if __name__ == "__main__":
    main()
