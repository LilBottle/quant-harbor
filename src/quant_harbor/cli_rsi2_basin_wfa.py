from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import json

import numpy as np
import pandas as pd

from quant_harbor.alpaca_data import make_snapshot
from quant_harbor.backtest_runner import run_rsi2_backtest_df, BacktestConfig
from quant_harbor.basin import BasinConfig, make_rsi2_basin_params
from quant_harbor.gates import GateConfig, apply_gates
from quant_harbor.split import split_train_val_test_last12m

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _qualify(summary: dict, gate_cfg: GateConfig) -> bool:
    g = apply_gates(summary, gate_cfg)
    pf = float(summary.get("profit_factor") or 0.0)
    return bool(g.get("gate_ok")) and (pf >= 1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="QQQ")
    ap.add_argument("--years", type=int, default=5)

    ap.add_argument("--wfa-windows", default="", help="Path to wfa_windows.parquet (optional). If omitted, inferred from --wfa-dir")
    ap.add_argument("--wfa-dir", default="", help="Path to results/rsi2_wfa_*/ directory (optional)")

    ap.add_argument("--base-params", default="", help="JSON file containing base params (expects keys entry_rsi/stop_pct/take_pct/max_bars_hold).")

    ap.add_argument("--pct-steps", default="0.05,0.10")
    ap.add_argument("--bar-steps", default="1,2")

    ap.add_argument("--max-dd-intra", type=float, default=10.0)
    ap.add_argument("--min-trades-annualized", type=int, default=200)
    ap.add_argument("--require-net-positive", action="store_true", default=True)
    ap.add_argument("--allow-net-negative", dest="require_net_positive", action="store_false")

    ap.add_argument("--max-windows", type=int, default=0, help="optional cap on number of windows to evaluate (0 = all)")

    args = ap.parse_args()

    if not args.wfa_windows and not args.wfa_dir:
        raise SystemExit("Provide --wfa-dir or --wfa-windows")

    wfa_windows_path = Path(args.wfa_windows) if args.wfa_windows else (Path(args.wfa_dir) / "wfa_windows.parquet")
    wfa_windows_path = wfa_windows_path.expanduser().resolve()
    if not wfa_windows_path.exists():
        raise SystemExit(f"wfa_windows.parquet not found: {wfa_windows_path}")

    # Base params
    if not args.base_params:
        raise SystemExit("Provide --base-params <json>. (We keep this explicit for audit.)")
    base = json.loads(Path(args.base_params).expanduser().read_text())
    base.setdefault("rsi_period", 2)
    basin_cfg = BasinConfig(
        pct_steps=tuple(float(x) for x in args.pct_steps.split(",") if x.strip()),
        bar_steps=tuple(int(x) for x in args.bar_steps.split(",") if x.strip()),
    )

    gate_cfg = GateConfig(
        maxdd_intrabar_pct=float(args.max_dd_intra),
        min_trades_annualized=int(args.min_trades_annualized),
        require_net_positive=bool(args.require_net_positive),
    )

    # Pull snapshot and split
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

    w = pd.read_parquet(wfa_windows_path)
    for c in ["train_start_utc", "train_end_utc", "oos_start_utc", "oos_end_utc"]:
        if c in w.columns:
            w[c] = pd.to_datetime(w[c], utc=True)

    if args.max_windows and args.max_windows > 0:
        w = w.head(int(args.max_windows))

    candidates = make_rsi2_basin_params(base, basin_cfg)
    candidates = sorted(candidates, key=lambda x: (x["entry_rsi"], x["stop_pct"], x["take_pct"], x["max_bars_hold"]))

    run_id = datetime.now(tz=ET).strftime("%Y%m%d_%H%M%S")
    out_root = project_root / "results" / f"rsi2_basin_wfa_{args.symbol}_{run_id}"
    out_root.mkdir(parents=True, exist_ok=True)

    cfg = BacktestConfig(symbol=args.symbol)

    window_rows = []
    for _, row in w.iterrows():
        win = int(row.get("window")) if ("window" in row) else None
        oos0 = row["oos_start_utc"]
        oos1 = row["oos_end_utc"]

        seg_df = pre[(pre.index >= oos0) & (pre.index <= oos1)]
        if seg_df.empty:
            window_rows.append({"window": win, "oos_start_utc": str(oos0), "oos_end_utc": str(oos1), "grid_points": 0, "passed_points": 0, "basin_pass_rate": None})
            continue

        passed = 0
        for i, p in enumerate(candidates):
            # Only need summary for basin qualification.
            sub = out_root / f"window_{win:02d}" / "grid" / f"pt_{i:04d}"
            s = run_rsi2_backtest_df(seg_df, out_dir=sub, cfg=cfg, strat_params=p, persist_details=False)
            if _qualify(s, gate_cfg):
                passed += 1

        total = len(candidates)
        window_rows.append(
            {
                "window": win,
                "oos_start_utc": str(oos0),
                "oos_end_utc": str(oos1),
                "grid_points": int(total),
                "passed_points": int(passed),
                "basin_pass_rate": float(passed / total) if total else None,
            }
        )
        print(f"[BASIN WFA {win:02d}] basin_pass_rate={passed/total:.4f} ({passed}/{total})")

    win_df = pd.DataFrame(window_rows)
    win_df.to_parquet(out_root / "basin_wfa_windows.parquet", index=False)

    rates = win_df["basin_pass_rate"].dropna().to_numpy(dtype=float)
    agg = {
        "symbol": args.symbol,
        "strategy": "RSI2Daytrade",
        "snapshot_dir": str(snap_dir),
        "wfa_windows_path": str(wfa_windows_path),
        "base_params": base,
        "basin_cfg": {"pct_steps": list(basin_cfg.pct_steps), "bar_steps": list(basin_cfg.bar_steps)},
        "gate_cfg": {
            "maxdd_intrabar_pct": gate_cfg.maxdd_intrabar_pct,
            "min_trades_annualized": gate_cfg.min_trades_annualized,
            "require_net_positive": gate_cfg.require_net_positive,
            "pf_min": 1.0,
        },
        "windows": int(len(win_df)),
        "basin_pass_rate_mean": float(np.mean(rates)) if len(rates) else None,
        "basin_pass_rate_median": float(np.median(rates)) if len(rates) else None,
        "basin_pass_rate_worst": float(np.min(rates)) if len(rates) else None,
        "generated_utc": datetime.now(tz=UTC).isoformat(),
    }

    (out_root / "basin_wfa_report.json").write_text(json.dumps(agg, indent=2, default=str))

    print("Basin WFA report written:", out_root)
    print(json.dumps({k: agg[k] for k in ["windows", "basin_pass_rate_mean", "basin_pass_rate_median", "basin_pass_rate_worst"]}, indent=2))


if __name__ == "__main__":
    main()
