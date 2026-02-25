from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import json

import pandas as pd

from quant_harbor.alpaca_data import make_snapshot
from quant_harbor.backtest_runner import run_rsi2_backtest_df, BacktestConfig
from quant_harbor.split import split_train_val_test_last12m

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='QQQ')
    ap.add_argument('--years', type=int, default=5, help='history length in years (data pull)')
    args = ap.parse_args()

    # Fetch last N years up to now
    end_et = datetime.now(tz=ET)
    start_et = end_et.replace(year=end_et.year - args.years)

    project_root = Path(__file__).resolve().parents[2]
    base_dir = project_root / 'data' / 'snapshots'
    snap_dir = make_snapshot(symbol=args.symbol, start_et=start_et, end_et=end_et, base_dir=base_dir)

    df = pd.read_parquet(snap_dir / 'bars.parquet')
    if df.index.tz is None:
        df.index = pd.to_datetime(df.index, utc=True)

    split = split_train_val_test_last12m(df)

    run_id = datetime.now(tz=ET).strftime('%Y%m%d_%H%M%S')
    out_root = project_root / 'results' / f"rsi2_split_{args.symbol}_{run_id}"

    cfg = BacktestConfig(symbol=args.symbol)
    strat_params = dict(
        rsi_period=2,
        entry_rsi=15.0,
        stop_pct=0.006,
        take_pct=0.009,
        max_bars_hold=8,
)

    # Attach snapshot meta so dashboard can show ranges.
    snap_meta = json.loads((snap_dir / 'meta.json').read_text())
    snap_meta['split_policy'] = 'last12m_test'
    snap_meta['cut_test_start_utc'] = str(split.cut_test_start_utc)

    summaries = {}
    for name, dfx in [('train', split.train), ('val', split.val), ('test', split.test)]:
        subdir = out_root / name
        meta = dict(snap_meta)
        meta['segment'] = name
        meta['segment_dt_min_utc'] = str(dfx.index.min())
        meta['segment_dt_max_utc'] = str(dfx.index.max())
        summaries[name] = run_rsi2_backtest_df(dfx, out_dir=subdir, cfg=cfg, strat_params=strat_params, snapshot_meta=meta)

    # Print quick summary
    print('RSI2 split backtest done:', out_root)
    for name in ['train','val','test']:
        s = summaries[name]
        print(f"[{name}] net_return_pct={s.get('net_return_pct'):.3f} maxdd_intra={s.get('max_drawdown_intrabar_pct'):.3f} trades={s.get('total_trades')} pf={s.get('profit_factor'):.3f}")


if __name__ == '__main__':
    main()
