from __future__ import annotations

from datetime import datetime
from itertools import product
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import json

import numpy as np
import pandas as pd

from quant_harbor.alpaca_data import make_snapshot
from quant_harbor.backtest_runner import run_rsi2_backtest_df, BacktestConfig
from quant_harbor.split import split_train_val_test_last12m
from quant_harbor.walk_forward import make_quarterly_wfa_windows
from quant_harbor.scorecard import scorecard_v1

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _param_grid(entry_rsi: list[float], stop_pct: list[float], take_pct: list[float], max_bars_hold: list[int]):
    for er, sp, tp, mh in product(entry_rsi, stop_pct, take_pct, max_bars_hold):
        yield {
            "rsi_period": 2,
            "entry_rsi": float(er),
            "stop_pct": float(sp),
            "take_pct": float(tp),
            "max_bars_hold": int(mh),
        }


def _oos_metrics_for_candidate(pre: pd.DataFrame, windows: list, cfg: BacktestConfig, params: dict, out_dir: Path):
    """Evaluate a fixed params set on all WFA OOS windows.

    Writes per-window summaries under out_dir/windows/window_XX/summary.json (summary-only).
    Returns list of per-window summaries.
    """
    out = []
    for i, w in enumerate(windows):
        oos_df = pre[(pre.index >= w.oos_start) & (pre.index <= w.oos_end)]
        win_dir = out_dir / "windows" / f"window_{i:02d}"
        meta = {
            "segment": "wfa_oos_fixed_params",
            "window": i,
            "oos_start_utc": str(w.oos_start),
            "oos_end_utc": str(w.oos_end),
        }
        s = run_rsi2_backtest_df(oos_df, out_dir=win_dir, cfg=cfg, strat_params=params, snapshot_meta=meta, persist_details=False)
        out.append(s)
    return out


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="QQQ")
    ap.add_argument("--years", type=int, default=5)

    ap.add_argument("--train-months", type=int, default=12)
    ap.add_argument("--oos-months", type=int, default=3)

    # candidate grid
    ap.add_argument("--entry-rsi", default="10,15,20")
    ap.add_argument("--stop-pct", default="0.004,0.006,0.008")
    ap.add_argument("--take-pct", default="0.006,0.009,0.012")
    ap.add_argument("--max-bars-hold", default="4,8,12")

    # freeze selection rules (defaults per user choice)
    ap.add_argument("--min-pos-window-rate", type=float, default=0.70)

    args = ap.parse_args()

    entry_rsi = [float(x) for x in args.entry_rsi.split(",") if x.strip()]
    stop_pct = [float(x) for x in args.stop_pct.split(",") if x.strip()]
    take_pct = [float(x) for x in args.take_pct.split(",") if x.strip()]
    max_bars_hold = [int(x) for x in args.max_bars_hold.split(",") if x.strip()]

    end_et = datetime.now(tz=ET)
    start_et = end_et.replace(year=end_et.year - args.years)

    project_root = Path(__file__).resolve().parents[2]
    base_dir = project_root / "data" / "snapshots"
    snap_dir = make_snapshot(symbol=args.symbol, start_et=start_et, end_et=end_et, base_dir=base_dir)

    df = pd.read_parquet(snap_dir / "bars.parquet")
    if df.index.tz is None:
        df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()

    split = split_train_val_test_last12m(df)
    pre = pd.concat([split.train, split.val]).sort_index()

    windows = make_quarterly_wfa_windows(pre.index, train_months=args.train_months, oos_months=args.oos_months)
    if not windows:
        raise SystemExit("No WFA windows created (not enough history).")

    run_id = datetime.now(tz=ET).strftime("%Y%m%d_%H%M%S")
    out_root = project_root / "results" / f"rsi2_freezeA_{args.symbol}_{run_id}"
    out_root.mkdir(parents=True, exist_ok=True)

    cfg = BacktestConfig(symbol=args.symbol)

    # Evaluate all candidates across OOS windows
    candidates = list(_param_grid(entry_rsi, stop_pct, take_pct, max_bars_hold))
    rows = []

    for k, p in enumerate(candidates):
        cand_dir = out_root / "candidates" / f"cand_{k:04d}"
        summ = _oos_metrics_for_candidate(pre, windows, cfg, p, cand_dir)
        agg = _aggregate_oos(summ)
        rows.append(
            {
                "cand": k,
                "entry_rsi": p["entry_rsi"],
                "stop_pct": p["stop_pct"],
                "take_pct": p["take_pct"],
                "max_bars_hold": p["max_bars_hold"],
                **agg,
            }
        )

    cand_df = pd.DataFrame(rows)
    cand_df.to_parquet(out_root / "candidates_oos_agg.parquet", index=False)

    # Select frozen params:
    # 1) filter pos_window_rate >= threshold
    # 2) maximize median net_return
    # 3) tie-break by worst net_return
    # 4) tie-break by mean
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

    frozen_params = {
        "rsi_period": 2,
        "entry_rsi": float(best_row["entry_rsi"]),
        "stop_pct": float(best_row["stop_pct"]),
        "take_pct": float(best_row["take_pct"]),
        "max_bars_hold": int(best_row["max_bars_hold"]),
}

    # Run final TEST once (details persisted for dashboard)
    test_dir = out_root / "test"
    test_summary = run_rsi2_backtest_df(split.test, out_dir=test_dir, cfg=cfg, strat_params=frozen_params, snapshot_meta={"segment": "test_freezeA"}, persist_details=True)

    # Also persist per-window OOS results for the frozen candidate so Dashboard v2 can plot WFA.
    # Reuse the already-written per-window summaries under candidates/cand_XXXX/windows.
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

    # Create a minimal WFA summary for scorecard input (pos_window_rate + median/worst/mean)
    wfa_summary = {
        "wfa_mode": "fixed_params",
        "symbol": args.symbol,
        "strategy": "RSI2Daytrade",
        "windows": int(best_row.get("windows") or len(windows)),
        "pos_window_rate": float(best_row.get("pos_window_rate")) if best_row.get("pos_window_rate") is not None else None,
        "oos_net_return_mean": float(best_row.get("oos_net_return_mean")) if best_row.get("oos_net_return_mean") is not None else None,
        "oos_net_return_median": float(best_row.get("oos_net_return_median")) if best_row.get("oos_net_return_median") is not None else None,
        "oos_net_return_worst": float(best_row.get("oos_net_return_worst")) if best_row.get("oos_net_return_worst") is not None else None,
        "generated_utc": datetime.now(tz=UTC).isoformat(),
        "note": "freezeA: aggregated candidate performance across WFA OOS windows (fixed params across windows)",
        "n_trials": int(len(candidates)),
    }
    (out_root / "wfa_summary.json").write_text(json.dumps(wfa_summary, indent=2, default=str))

    final_report = {
        "run_kind": "freezeA",
        "wfa_mode": "fixed_params",
        "symbol": args.symbol,
        "strategy": "RSI2Daytrade",
        "snapshot_dir": str(snap_dir),
        "split_policy": "last12m_test",
        "cut_test_start_utc": str(split.cut_test_start_utc),
        "wfa": {
            "train_months": args.train_months,
            "oos_months": args.oos_months,
            "windows": len(windows),
            "selection_rule": {
                "min_pos_window_rate": float(args.min_pos_window_rate),
                "sort": ["oos_net_return_median desc", "oos_net_return_worst desc", "oos_net_return_mean desc"],
                "fallback_if_none_eligible": used_fallback,
            },
        },
        "frozen_params": frozen_params,
        "selected_candidate": best_row,
        "test_summary": test_summary,
        "generated_utc": datetime.now(tz=UTC).isoformat(),
    }
    (out_root / "final_report.json").write_text(json.dumps(final_report, indent=2, default=str))

    # Scorecard for dashboard sorting (basin inputs may be missing here; that's ok)
    sc = scorecard_v1(wfa_summary=wfa_summary, val_summary=None, test_summary=test_summary)
    scorecard_blob = {
        "generated_utc": datetime.now(tz=UTC).isoformat(),
        "meta": {
            "symbol": args.symbol,
            "strategy": "RSI2Daytrade",
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
    print("Frozen params:", json.dumps(frozen_params))
    print("Test net_return_pct:", test_summary.get("net_return_pct"))


if __name__ == "__main__":
    main()
