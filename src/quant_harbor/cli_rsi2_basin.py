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


def _qualify(summary: dict, gate_cfg: GateConfig) -> dict:
    """Basin qualification rule from plan (v1).

    A point is qualified if:
    - Hard gates pass (MaxDD intrabar, annualized trades, net>0 if required)
    - AND profit_factor >= 1.0 (plan requirement)
    """
    g = apply_gates(summary, gate_cfg)
    pf = float(summary.get("profit_factor") or 0.0)
    ok = bool(g.get("gate_ok")) and (pf >= 1.0)
    reasons = list(g.get("gate_reasons") or [])
    if pf < 1.0:
        reasons.append("pf<1.0")
    return {"qual_ok": ok, "qual_reasons": reasons, "pf": pf, "gate": g}


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("--symbol", default="QQQ")
    ap.add_argument("--years", type=int, default=5, help="history length in years (data pull)")

    # Basin base point
    ap.add_argument("--gate-report", default="", help="Path to results/*/gate_report.json to load chosen_params")
    ap.add_argument("--entry-rsi", type=float, default=None)
    ap.add_argument("--stop-pct", type=float, default=None)
    ap.add_argument("--take-pct", type=float, default=None)
    ap.add_argument("--max-bars-hold", type=int, default=None)

    # Basin config
    ap.add_argument("--pct-steps", default="0.05,0.10,0.20")
    ap.add_argument("--bar-steps", default="1,2,4")

    # Gate config (use same as T4.3)
    ap.add_argument("--max-dd-intra", type=float, default=10.0)
    ap.add_argument("--min-trades-annualized", type=int, default=200)
    ap.add_argument("--require-net-positive", action="store_true", default=True)
    ap.add_argument("--allow-net-negative", dest="require_net_positive", action="store_false")

    ap.add_argument("--segment", choices=["val", "train", "pre"], default="val", help="where to evaluate basin")

    args = ap.parse_args()

    # Build base params
    base = {
        "rsi_period": 2,
}

    if args.gate_report:
        rep = json.loads(Path(args.gate_report).expanduser().read_text())
        base.update(rep.get("chosen_params", {}))

    # Explicit overrides
    if args.entry_rsi is not None:
        base["entry_rsi"] = float(args.entry_rsi)
    if args.stop_pct is not None:
        base["stop_pct"] = float(args.stop_pct)
    if args.take_pct is not None:
        base["take_pct"] = float(args.take_pct)
    if args.max_bars_hold is not None:
        base["max_bars_hold"] = int(args.max_bars_hold)

    missing = [k for k in ["entry_rsi", "stop_pct", "take_pct", "max_bars_hold"] if k not in base]
    if missing:
        raise SystemExit(f"Missing base params: {missing}. Provide --gate-report or explicit args.")

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

    if args.segment == "val":
        seg_df = split.val
    elif args.segment == "train":
        seg_df = split.train
    else:
        seg_df = pd.concat([split.train, split.val]).sort_index()

    candidates = make_rsi2_basin_params(base, basin_cfg)

    run_id = datetime.now(tz=ET).strftime("%Y%m%d_%H%M%S")
    out_root = project_root / "results" / f"rsi2_basin_{args.symbol}_{run_id}"
    out_root.mkdir(parents=True, exist_ok=True)

    cfg = BacktestConfig(symbol=args.symbol)

    rows = []
    for i, p in enumerate(sorted(candidates, key=lambda x: (x["entry_rsi"], x["stop_pct"], x["take_pct"], x["max_bars_hold"]))):
        sub = out_root / "grid" / f"pt_{i:04d}"
        s = run_rsi2_backtest_df(seg_df, out_dir=sub, cfg=cfg, strat_params=p, persist_details=False)
        q = _qualify(s, gate_cfg)
        rows.append(
            {
                "i": i,
                "entry_rsi": float(p["entry_rsi"]),
                "stop_pct": float(p["stop_pct"]),
                "take_pct": float(p["take_pct"]),
                "max_bars_hold": int(p["max_bars_hold"]),
                "net_pnl": s.get("net_pnl"),
                "net_return_pct": s.get("net_return_pct"),
                "maxdd_intra": s.get("max_drawdown_intrabar_pct"),
                "trades": s.get("total_trades"),
                "pf": q["pf"],
                "qual_ok": q["qual_ok"],
                "qual_reasons": ";".join(q["qual_reasons"]),
            }
        )

    basin_df = pd.DataFrame(rows)
    basin_df.to_parquet(out_root / "basin_grid.parquet", index=False)

    total = int(len(basin_df))
    passed = int(basin_df["qual_ok"].sum()) if total else 0
    pass_rate = float(passed / total) if total else None

    # "area" proxy for discrete grid: fraction passing
    report = {
        "symbol": args.symbol,
        "strategy": "RSI2Daytrade",
        "segment": args.segment,
        "snapshot_dir": str(snap_dir),
        "base_params": {k: base[k] for k in ["rsi_period", "entry_rsi", "stop_pct", "take_pct", "max_bars_hold"] if k in base},
        "basin_cfg": {
            "pct_steps": list(basin_cfg.pct_steps),
            "bar_steps": list(basin_cfg.bar_steps),
        },
        "gate_cfg": {
            "maxdd_intrabar_pct": gate_cfg.maxdd_intrabar_pct,
            "min_trades_annualized": gate_cfg.min_trades_annualized,
            "require_net_positive": gate_cfg.require_net_positive,
            "pf_min": 1.0,
        },
        "grid_points": total,
        "passed_points": passed,
        "basin_pass_rate": pass_rate,
        "generated_utc": datetime.now(tz=UTC).isoformat(),
    }

    (out_root / "basin_report.json").write_text(json.dumps(report, indent=2, default=str))

    print("Basin report written:", out_root)
    print(json.dumps({k: report[k] for k in ["grid_points", "passed_points", "basin_pass_rate"]}, indent=2))


if __name__ == "__main__":
    main()
