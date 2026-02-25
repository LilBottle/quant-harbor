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
from quant_harbor.gates import GateConfig, WfaGateAggregateConfig, apply_gates, aggregate_wfa_oos_gate_results
from quant_harbor.split import split_train_val_test_last12m

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _param_grid(entry_rsi: list[float], stop_pct: list[float], take_pct: list[float], max_bars_hold: list[int]):
    for er, sp, tp, mh in product(entry_rsi, stop_pct, take_pct, max_bars_hold):
        yield dict(
            rsi_period=2,
            entry_rsi=float(er),
            stop_pct=float(sp),
            take_pct=float(tp),
            max_bars_hold=int(mh),
)


def _best_on_val(val_df: pd.DataFrame, out_dir: Path, cfg: BacktestConfig, candidates: list[dict], gate_cfg: GateConfig):
    """Pick best params based on VAL only.

    v1 rule:
    - primary: maximize net_pnl on VAL among candidates that pass VAL gates
    - fallback: if none pass, pick max net_pnl regardless

    Persists each candidate summary under out_dir/val/cand_XXX.
    """
    best_params = None
    best_summary = None
    best_gate = None
    best_net = -1e18

    best_any_params = None
    best_any_summary = None
    best_any_gate = None
    best_any_net = -1e18

    for j, p in enumerate(candidates):
        sub = out_dir / "val" / f"cand_{j:03d}"
        s = run_rsi2_backtest_df(val_df, out_dir=sub, cfg=cfg, strat_params=p, persist_details=False)
        g = apply_gates(s, gate_cfg)

        net = float(s.get("net_pnl") or 0.0)

        if net > best_any_net:
            best_any_net = net
            best_any_params = p
            best_any_summary = s
            best_any_gate = g

        if g["gate_ok"] and net > best_net:
            best_net = net
            best_params = p
            best_summary = s
            best_gate = g

    if best_params is not None:
        return best_params, best_summary, best_gate, True
    return best_any_params, best_any_summary, best_any_gate, False


def _eval_wfa_oos_gates(wfa_dir: Path, gate_cfg: GateConfig, agg_cfg: WfaGateAggregateConfig) -> dict:
    """Evaluate hard gates over an existing WFA run folder (OOS windows) + aggregate.

    Professional intent:
    - Apply the SAME hard gate to each OOS window.
    - Aggregate with a minimum pass-rate requirement (institutional-style: most windows should pass).

    Expects structure produced by cli_rsi2_walk_forward:
    - window_XX/oos/summary.json

    Writes:
    - wfa_gate_report.json
    """
    rows = []
    for win_dir in sorted([p for p in wfa_dir.glob("window_*" ) if p.is_dir()]):
        try:
            i = int(win_dir.name.split("_")[-1])
        except Exception:
            i = win_dir.name

        summ_path = win_dir / "oos" / "summary.json"
        if not summ_path.exists():
            continue

        s = json.loads(summ_path.read_text())
        g = apply_gates(s, gate_cfg)
        rows.append(
            {
                "window": i,
                "oos_net_pnl": s.get("net_pnl"),
                "oos_net_return_pct": s.get("net_return_pct"),
                "oos_maxdd_intrabar_pct": s.get("max_drawdown_intrabar_pct"),
                "oos_trades": s.get("total_trades"),
                "gate_ok": g["gate_ok"],
                "gate_reasons": g["gate_reasons"],
            }
        )

    agg = aggregate_wfa_oos_gate_results(rows, hard_cfg=gate_cfg, agg_cfg=agg_cfg)

    out = {
        "gate_version": "t4.3_v2",
        "wfa": agg,
        "rows": rows,
        "generated_utc": datetime.now(tz=UTC).isoformat(),
    }

    (wfa_dir / "wfa_gate_report.json").write_text(json.dumps(out, indent=2, default=str))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="QQQ")
    ap.add_argument("--years", type=int, default=5, help="history length in years (data pull)")

    # gate config
    ap.add_argument("--max-dd-intra", type=float, default=10.0)
    ap.add_argument("--min-trades-annualized", type=int, default=200, help="minimum annualized trades")
    ap.add_argument("--require-net-positive", action="store_true", default=True)
    ap.add_argument("--allow-net-negative", dest="require_net_positive", action="store_false")

    # candidate grid (for choosing a frozen param set on VAL)
    ap.add_argument("--entry-rsi", default="10,15,20")
    ap.add_argument("--stop-pct", default="0.004,0.006,0.008")
    ap.add_argument("--take-pct", default="0.006,0.009,0.012")
    ap.add_argument("--max-bars-hold", default="4,8,12")

    # optionally evaluate an existing WFA run's OOS gates
    ap.add_argument("--wfa-dir", default="", help="Path to results/rsi2_wfa_*/ directory (optional)")
    ap.add_argument("--min-oos-pass-rate", type=float, default=0.70, help="WFA OOS gate pass-rate threshold")

    args = ap.parse_args()

    gate_cfg = GateConfig(
        maxdd_intrabar_pct=float(args.max_dd_intra),
        min_trades_annualized=int(args.min_trades_annualized),
        require_net_positive=bool(args.require_net_positive),
    )

    # If requested, evaluate gates on WFA OOS outputs
    if args.wfa_dir:
        wfa_dir = Path(args.wfa_dir).expanduser().resolve()
        wfa_agg_cfg = WfaGateAggregateConfig(min_pass_rate=float(args.min_oos_pass_rate))
        rep = _eval_wfa_oos_gates(wfa_dir, gate_cfg, wfa_agg_cfg)
        print("WFA gate report written:", wfa_dir / "wfa_gate_report.json")
        print(
            json.dumps(
                {
                    "wfa_windows": rep.get("wfa", {}).get("wfa_windows"),
                    "wfa_pass_rate": rep.get("wfa", {}).get("wfa_pass_rate"),
                    "wfa_gate_ok": rep.get("wfa", {}).get("wfa_gate_ok"),
                    "wfa_gate_reasons": rep.get("wfa", {}).get("wfa_gate_reasons"),
                    "min_oos_pass_rate": float(args.min_oos_pass_rate),
                },
                indent=2,
            )
        )

    # Pull data snapshot + split
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

    entry_rsi = [float(x) for x in args.entry_rsi.split(",") if x.strip()]
    stop_pct = [float(x) for x in args.stop_pct.split(",") if x.strip()]
    take_pct = [float(x) for x in args.take_pct.split(",") if x.strip()]
    max_bars_hold = [int(x) for x in args.max_bars_hold.split(",") if x.strip()]

    candidates = list(_param_grid(entry_rsi, stop_pct, take_pct, max_bars_hold))

    run_id = datetime.now(tz=ET).strftime("%Y%m%d_%H%M%S")
    out_root = project_root / "results" / f"rsi2_gate_{args.symbol}_{run_id}"
    out_root.mkdir(parents=True, exist_ok=True)

    cfg = BacktestConfig(symbol=args.symbol)

    # Choose frozen params using VAL
    chosen_params, val_summary, val_gate, passed_val = _best_on_val(
        split.val, out_dir=out_root, cfg=cfg, candidates=candidates, gate_cfg=gate_cfg
    )

    # Evaluate chosen params on TEST
    test_out = out_root / "test"
    test_summary = run_rsi2_backtest_df(split.test, out_dir=test_out, cfg=cfg, strat_params=chosen_params, persist_details=False)
    test_gate = apply_gates(test_summary, gate_cfg)

    report = {
        "symbol": args.symbol,
        "strategy": "RSI2Daytrade",
        "snapshot_dir": str(snap_dir),
        "gate_cfg": {
            "maxdd_intrabar_pct": gate_cfg.maxdd_intrabar_pct,
            "min_trades_annualized": gate_cfg.min_trades_annualized,
            "require_net_positive": gate_cfg.require_net_positive,
        },
        "chosen_params": chosen_params,
        "val": {
            "passed_val_gates": bool(passed_val),
            "summary": val_summary,
            "gate": val_gate,
        },
        "test": {
            "summary": test_summary,
            "gate": test_gate,
        },
        "generated_utc": datetime.now(tz=UTC).isoformat(),
    }

    (out_root / "gate_report.json").write_text(json.dumps(report, indent=2, default=str))

    print("Gate report written:", out_root)
    print("VAL gate_ok:", val_gate.get("gate_ok") if val_gate else None, "| TEST gate_ok:", test_gate.get("gate_ok"))


if __name__ == "__main__":
    main()
