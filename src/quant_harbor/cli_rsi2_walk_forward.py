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

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _param_grid(
    entry_rsi: list[float],
    stop_pct: list[float],
    take_pct: list[float],
    max_bars_hold: list[int],
    rsi_period: int = 2,
):
    keys = ["entry_rsi", "stop_pct", "take_pct", "max_bars_hold"]
    values = [entry_rsi, stop_pct, take_pct, max_bars_hold]
    for combo in product(*values):
        d = dict(zip(keys, combo))
        d["rsi_period"] = rsi_period
        yield d


def _score_train(summary: dict) -> float:
    """Training score for picking params.

    v1: maximize net_pnl (already includes slippage), tiebreaker PF.
    """
    net = float(summary.get("net_pnl") or 0.0)
    pf = float(summary.get("profit_factor") or 0.0)
    # PF used as small tiebreaker only.
    return net + 0.01 * pf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="QQQ")
    ap.add_argument("--years", type=int, default=5, help="history length in years (data pull)")

    ap.add_argument("--train-months", type=int, default=12)
    ap.add_argument("--oos-months", type=int, default=3)

    ap.add_argument("--entry-rsi", default="10,15,20")
    ap.add_argument("--stop-pct", default="0.004,0.006,0.008")
    ap.add_argument("--take-pct", default="0.006,0.009,0.012")
    ap.add_argument("--max-bars-hold", default="4,8,12")

    ap.add_argument("--max-dd-intra", type=float, default=10.0, help="soft filter on TRAIN during param selection")
    ap.add_argument("--min-trades", type=int, default=200, help="soft filter on TRAIN during param selection")

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
    out_root = project_root / "results" / f"rsi2_wfa_{args.symbol}_{run_id}"
    out_root.mkdir(parents=True, exist_ok=True)

    snap_meta = json.loads((snap_dir / "meta.json").read_text())
    snap_meta["split_policy"] = "last12m_test"
    snap_meta["cut_test_start_utc"] = str(split.cut_test_start_utc)
    snap_meta["wfa_train_months"] = args.train_months
    snap_meta["wfa_oos_months"] = args.oos_months

    cfg = BacktestConfig(symbol=args.symbol)

    rows = []

    candidates = list(_param_grid(entry_rsi, stop_pct, take_pct, max_bars_hold))

    for i, w in enumerate(windows):
        win_dir = out_root / f"window_{i:02d}"

        train_df = pre[(pre.index >= w.train_start) & (pre.index <= w.train_end)]
        oos_df = pre[(pre.index >= w.oos_start) & (pre.index <= w.oos_end)]

        # pick best params on TRAIN
        best = None
        best_train_summary = None
        best_score = -1e18

        for j, p in enumerate(candidates):
            train_out = win_dir / "train" / f"cand_{j:03d}"
            meta = dict(snap_meta)
            meta.update(
                segment="wfa_train",
                window=i,
                cand=j,
                train_start_utc=str(w.train_start),
                train_end_utc=str(w.train_end),
                oos_start_utc=str(w.oos_start),
                oos_end_utc=str(w.oos_end),
            )

            s = run_rsi2_backtest_df(train_df, out_dir=train_out, cfg=cfg, strat_params=p, snapshot_meta=meta)

            dd = float(s.get("max_drawdown_intrabar_pct") or 1e9)
            trades = int(s.get("total_trades") or 0)

            # soft filters on train to avoid picking pathological params
            if dd > args.max_dd_intra:
                continue
            if trades < args.min_trades:
                continue

            score = _score_train(s)
            if score > best_score:
                best_score = score
                best = p
                best_train_summary = s

        if best is None:
            # fallback: pick best by score without filters
            for j, p in enumerate(candidates):
                train_out = win_dir / "train" / f"cand_{j:03d}"
                s = json.loads((train_out / "summary.json").read_text()) if (train_out / "summary.json").exists() else None
                if s is None:
                    continue
                score = _score_train(s)
                if score > best_score:
                    best_score = score
                    best = p
                    best_train_summary = s

        # Evaluate chosen params on OOS
        oos_out = win_dir / "oos"
        meta2 = dict(snap_meta)
        meta2.update(
            segment="wfa_oos",
            window=i,
            chosen_params=best,
            train_start_utc=str(w.train_start),
            train_end_utc=str(w.train_end),
            oos_start_utc=str(w.oos_start),
            oos_end_utc=str(w.oos_end),
        )
        oos_summary = run_rsi2_backtest_df(oos_df, out_dir=oos_out, cfg=cfg, strat_params=best, snapshot_meta=meta2)

        rows.append(
            dict(
                window=i,
                train_start_utc=w.train_start,
                train_end_utc=w.train_end,
                oos_start_utc=w.oos_start,
                oos_end_utc=w.oos_end,
                chosen_entry_rsi=float(best.get("entry_rsi")),
                chosen_stop_pct=float(best.get("stop_pct")),
                chosen_take_pct=float(best.get("take_pct")),
                chosen_max_bars_hold=int(best.get("max_bars_hold")),
                train_net_return_pct=float(best_train_summary.get("net_return_pct")) if best_train_summary else np.nan,
                train_pf=float(best_train_summary.get("profit_factor")) if best_train_summary else np.nan,
                train_maxdd_intra=float(best_train_summary.get("max_drawdown_intrabar_pct")) if best_train_summary else np.nan,
                train_trades=int(best_train_summary.get("total_trades")) if best_train_summary else 0,
                oos_net_return_pct=float(oos_summary.get("net_return_pct")) if oos_summary else np.nan,
                oos_pf=float(oos_summary.get("profit_factor")) if oos_summary else np.nan,
                oos_maxdd_intra=float(oos_summary.get("max_drawdown_intrabar_pct")) if oos_summary else np.nan,
                oos_trades=int(oos_summary.get("total_trades")) if oos_summary else 0,
            )
        )

        print(
            f"[WFA {i:02d}] OOS net%={oos_summary.get('net_return_pct'):.3f} pf={oos_summary.get('profit_factor'):.3f} maxdd_intra={oos_summary.get('max_drawdown_intrabar_pct'):.3f}"
        )

    wfa_df = pd.DataFrame(rows)
    wfa_df.to_parquet(out_root / "wfa_windows.parquet", index=False)

    # Aggregate
    oos = wfa_df["oos_net_return_pct"].to_numpy(dtype=float)
    pos_rate = float(np.mean(oos > 0.0)) if len(oos) else np.nan
    agg = {
        "wfa_mode": "retune_per_window",
        "symbol": args.symbol,
        "strategy": "RSI2Daytrade",
        "windows": int(len(wfa_df)),
        "pos_window_rate": pos_rate,
        "oos_net_return_mean": float(np.nanmean(oos)) if len(oos) else None,
        "oos_net_return_median": float(np.nanmedian(oos)) if len(oos) else None,
        "oos_net_return_worst": float(np.nanmin(oos)) if len(oos) else None,
        "generated_utc": datetime.now(tz=UTC).isoformat(),
    }
    (out_root / "wfa_summary.json").write_text(json.dumps(agg, indent=2, default=str))

    print("WFA done:", out_root)
    print(json.dumps(agg, indent=2))


if __name__ == "__main__":
    main()
