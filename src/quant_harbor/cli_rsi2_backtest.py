from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse

from quant_harbor.alpaca_data import make_snapshot
from quant_harbor.backtest_runner import run_rsi2_backtest, BacktestConfig

ET = ZoneInfo("America/New_York")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='QQQ')
    ap.add_argument('--years', type=int, default=2, help='history length in years (used for PoC)')
    project_root = Path(__file__).resolve().parents[2]
    ap.add_argument('--out', default=str((project_root / 'results').resolve()))
    args = ap.parse_args()

    # PoC: fetch last N years up to now.
    end = datetime.now(tz=ET)
    start = end.replace(year=end.year - args.years)

    base_dir = Path(__file__).resolve().parent.parent / 'data' / 'snapshots'
    snap_dir = make_snapshot(symbol=args.symbol, start_et=start, end_et=end, base_dir=base_dir)

    run_id = datetime.now(tz=ET).strftime('%Y%m%d_%H%M%S')
    out_dir = Path(args.out) / f"rsi2_{args.symbol}_{run_id}"

    cfg = BacktestConfig(symbol=args.symbol)
    strat_params = dict(
        rsi_period=2,
        entry_rsi=15.0,
        stop_pct=0.006,
        take_pct=0.009,
        max_bars_hold=8,
)

    summary = run_rsi2_backtest(snapshot_dir=snap_dir, out_dir=out_dir, cfg=cfg, strat_params=strat_params)

    print('RSI2 backtest done')
    for k in [
        'symbol','net_pnl','net_return_pct',
        'max_drawdown_close_pct','max_drawdown_intrabar_pct',
        'total_trades','win_rate_pct','profit_factor','expectancy','sharpe'
    ]:
        print(f"{k}: {summary.get(k)}")
    print(f"results: {out_dir}")


if __name__ == '__main__':
    main()
