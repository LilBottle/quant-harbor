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
from quant_harbor.scorecard import scorecard_v1
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--symbols", default="QQQ", help="Comma-separated symbols. For pairs: e.g. QQQ,SPY")
    ap.add_argument("--years", type=int, default=5)

    ap.add_argument("--train-months", type=int, default=12)
    ap.add_argument("--oos-months", type=int, default=3)

    ap.add_argument("--min-pos-window-rate", type=float, default=0.70)
    ap.add_argument("--grid-json", default="", help="Optional JSON dict of param space to override registry default")

    args = ap.parse_args()

    spec = get_strategy_spec(args.strategy)
    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if len(syms) != spec.n_legs:
        raise SystemExit(f"Strategy {spec.id} requires {spec.n_legs} symbol(s); got {syms}")

    end_et = datetime.now(tz=ET)
    start_et = end_et.replace(year=end_et.year - args.years)

    project_root = Path(__file__).resolve().parents[2]
    snap_dir = make_snapshot_multi(syms, start_et=start_et, end_et=end_et, base_dir=project_root / "data" / "snapshots")

    # Load bars per leg
    dfs = []
    for s in syms:
        p = snap_dir / f"bars_{s}.parquet"
        df = pd.read_parquet(p)
        if df.index.tz is None:
            df.index = pd.to_datetime(df.index, utc=True)
        dfs.append(df.sort_index())

    # Use leg0 for time splitting; slice all legs by the same UTC bounds
    split0 = split_train_val_test_last12m(dfs[0])

    def _slice_all(dfx_list: list[pd.DataFrame], start, end):
        return [df[(df.index >= start) & (df.index <= end)] for df in dfx_list]

    pre0 = pd.concat([split0.train, split0.val]).sort_index()
    windows = make_quarterly_wfa_windows(pre0.index, train_months=args.train_months, oos_months=args.oos_months)
    if not windows:
        raise SystemExit("No WFA windows created (not enough history).")

    # Candidate space
    space = spec.default_param_grid()
    if args.grid_json:
        space = json.loads(args.grid_json)

    candidates = _grid_from_space(space)

    run_id = datetime.now(tz=ET).strftime("%Y%m%d_%H%M%S")
    out_root = project_root / "results" / f"freezeA_{spec.id}_{'-'.join(syms)}_{run_id}"
    out_root.mkdir(parents=True, exist_ok=True)

    cfg = BacktestConfig(symbol="-".join(syms))

    def _eval_candidate_oos(params: dict, cand_dir: Path):
        summaries = []
        for i, w in enumerate(windows):
            oos_dfs = _slice_all(dfs, w.oos_start, w.oos_end)
            win_dir = cand_dir / "windows" / f"window_{i:02d}"
            meta = {
                "segment": "wfa_oos_fixed_params",
                "window": i,
                "oos_start_utc": str(w.oos_start),
                "oos_end_utc": str(w.oos_end),
                "strategy_id": spec.id,
                "symbols": syms,
            }
            s = run_backtest_df(oos_dfs, out_dir=win_dir, cfg=cfg, strategy_cls=spec.cls, strat_params=params, snapshot_meta=meta, persist_details=False, strategy_id=spec.id)
            summaries.append(s)
        return summaries

    def _aggregate_oos(summaries: list[dict]):
        rets = np.array([float(s.get("net_return_pct") or 0.0) for s in summaries], dtype=float)
        pnls = np.array([float(s.get("net_pnl") or 0.0) for s in summaries], dtype=float)
        pos_rate = float(np.mean(pnls > 0.0)) if len(pnls) else None
        return {
            "windows": int(len(summaries)),
            "pos_window_rate": pos_rate,
            "oos_net_return_mean": float(np.mean(rets)) if len(rets) else None,
            "oos_net_return_median": float(np.median(rets)) if len(rets) else None,
            "oos_net_return_worst": float(np.min(rets)) if len(rets) else None,
        }

    rows = []
    for k, p in enumerate(candidates):
        cand_dir = out_root / "candidates" / f"cand_{k:04d}"
        summ = _eval_candidate_oos(p, cand_dir)
        agg = _aggregate_oos(summ)
        rows.append({"cand": k, **p, **agg})

    cand_df = pd.DataFrame(rows)
    cand_df.to_parquet(out_root / "candidates_oos_agg.parquet", index=False)

    eligible = cand_df[cand_df["pos_window_rate"].fillna(-1.0) >= float(args.min_pos_window_rate)].copy()
    used_fallback = False
    if eligible.empty:
        eligible = cand_df.copy()
        used_fallback = True

    eligible = eligible.sort_values(
        by=["oos_net_return_median", "oos_net_return_worst", "oos_net_return_mean"],
        ascending=[False, False, False],
    )
    best_row = eligible.iloc[0].to_dict()

    # Cast frozen params back to the intended types defined by the grid.
    frozen_params = {}
    for k in space.keys():
        v = best_row[k]
        # infer type from grid definition
        proto = space[k][0]
        try:
            if isinstance(proto, int):
                frozen_params[k] = int(v)
            elif isinstance(proto, float):
                frozen_params[k] = float(v)
            else:
                frozen_params[k] = v
        except Exception:
            frozen_params[k] = v

    # Run TEST once with details
    test_dfs = _slice_all(dfs, split0.cut_test_start_utc, dfs[0].index.max())
    test_dir = out_root / "test"
    test_summary = run_backtest_df(test_dfs, out_dir=test_dir, cfg=cfg, strategy_cls=spec.cls, strat_params=frozen_params, snapshot_meta={"segment": "test_freezeA", "strategy_id": spec.id, "symbols": syms}, persist_details=True, strategy_id=spec.id)

    # WFA windows parquet for frozen params
    win_rows = []
    for i, w in enumerate(windows):
        win_dir = out_root / "candidates" / f"cand_{int(best_row['cand']):04d}" / "windows" / f"window_{i:02d}"
        s_path = win_dir / "summary.json"
        m_path = win_dir / "snapshot_meta.json"
        if not s_path.exists():
            continue
        s = json.loads(s_path.read_text())
        meta = json.loads(m_path.read_text()) if m_path.exists() else {}
        win_rows.append(
            {
                "window": i,
                "oos_start_utc": meta.get("oos_start_utc"),
                "oos_end_utc": meta.get("oos_end_utc"),
                "oos_net_pnl": s.get("net_pnl"),
                "oos_net_return_pct": s.get("net_return_pct"),
                "oos_maxdd_intrabar_pct": s.get("max_drawdown_intrabar_pct"),
                "oos_trades": s.get("total_trades"),
            }
        )
    if win_rows:
        pd.DataFrame(win_rows).to_parquet(out_root / "wfa_windows.parquet", index=False)

    wfa_summary = {
        "wfa_mode": "fixed_params",
        "symbol": "-".join(syms),
        "strategy": spec.id,
        "windows": int(best_row.get("windows") or len(windows)),
        "pos_window_rate": float(best_row.get("pos_window_rate")) if best_row.get("pos_window_rate") is not None else None,
        "oos_net_return_mean": float(best_row.get("oos_net_return_mean")) if best_row.get("oos_net_return_mean") is not None else None,
        "oos_net_return_median": float(best_row.get("oos_net_return_median")) if best_row.get("oos_net_return_median") is not None else None,
        "oos_net_return_worst": float(best_row.get("oos_net_return_worst")) if best_row.get("oos_net_return_worst") is not None else None,
        "generated_utc": datetime.now(tz=UTC).isoformat(),
        "note": "freezeA (generic): aggregated candidate performance across WFA OOS windows (fixed params)",
        "n_trials": int(len(candidates)),
    }
    (out_root / "wfa_summary.json").write_text(json.dumps(wfa_summary, indent=2, default=str))

    final_report = {
        "run_kind": "freezeA",
        "wfa_mode": "fixed_params",
        "strategy_id": spec.id,
        "symbols": syms,
        "snapshot_dir": str(snap_dir),
        "split_policy": "last12m_test",
        "cut_test_start_utc": str(split0.cut_test_start_utc),
        "wfa": {
            "train_months": args.train_months,
            "oos_months": args.oos_months,
            "windows": len(windows),
            "selection_rule": {
                "min_pos_window_rate": float(args.min_pos_window_rate),
                "sort": ["oos_net_return_median desc", "oos_net_return_worst desc", "oos_net_return_mean desc"],
                "fallback_if_none_eligible": used_fallback,
            },
            "n_trials": int(len(candidates)),
        },
        "frozen_params": frozen_params,
        "selected_candidate": best_row,
        "test_summary": test_summary,
        "generated_utc": datetime.now(tz=UTC).isoformat(),
    }
    (out_root / "final_report.json").write_text(json.dumps(final_report, indent=2, default=str))

    sc = scorecard_v1(wfa_summary=wfa_summary, val_summary=None, test_summary=test_summary)
    scorecard_blob = {
        "generated_utc": datetime.now(tz=UTC).isoformat(),
        "meta": {
            "symbol": "-".join(syms),
            "strategy": spec.id,
            "run_kind": "freezeA",
            "wfa_mode": "fixed_params",
            "chosen_params": frozen_params,
            "snapshot_dir": str(snap_dir),
        },
        "scorecard": sc,
        "sources": {
            "wfa_summary": str((out_root / "wfa_summary.json").relative_to(project_root)),
            "wfa_windows": str((out_root / "wfa_windows.parquet").relative_to(project_root)) if (out_root / "wfa_windows.parquet").exists() else None,
            "test_summary": str((test_dir / "summary.json").relative_to(project_root)),
            "final_report": str((out_root / "final_report.json").relative_to(project_root)),
            "candidates_oos_agg": str((out_root / "candidates_oos_agg.parquet").relative_to(project_root)),
        },
    }
    (out_root / "scorecard.json").write_text(json.dumps(scorecard_blob, indent=2, default=str))

    print("FreezeA done:", out_root)
    print("Frozen params:", json.dumps(frozen_params, default=str))
    print("Test net_return_pct:", test_summary.get("net_return_pct"))


if __name__ == "__main__":
    main()
