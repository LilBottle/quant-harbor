from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json

import backtrader as bt
import pandas as pd

from .analyzers import EquityCurveAnalyzer, TradeListAnalyzer
from .metrics import compute_drawdown_from_equity, compute_trade_metrics

UTC = ZoneInfo("UTC")


@dataclass
class BacktestConfig:
    symbol: str = "QQQ"
    cash: float = 2000.0
    slippage_bps_side: float = 5.0  # 5 bps per side
    commission_pct: float = 0.0
    # Sensitivity analysis: rerun the same strategy under alternative slippage levels.
    slippage_sensitivity_bps: tuple[float, ...] = (10.0, 20.0)


def _load_parquet(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if df.index.tz is None:
        df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def _to_bt_df(df_utc: pd.DataFrame) -> pd.DataFrame:
    if df_utc.index.tz is None:
        raise ValueError("df index must be tz-aware (UTC)")
    df_bt = df_utc.copy().sort_index()
    df_bt.index = df_bt.index.tz_convert(UTC).tz_localize(None)
    return df_bt


def _make_feed(df_bt: pd.DataFrame) -> bt.feeds.PandasData:
    return bt.feeds.PandasData(
        dataname=df_bt,
        datetime=None,
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
        openinterest=-1,
    )


def run_backtest_df(
    dfs_utc: list[pd.DataFrame],
    out_dir: Path,
    cfg: BacktestConfig,
    strategy_cls: type[bt.Strategy],
    strat_params: dict,
    snapshot_meta: dict | None = None,
    persist_details: bool = True,
    strategy_id: str | None = None,
) -> dict:
    """Generic backtest runner.

    - dfs_utc: list of OHLCV DataFrames (UTC tz-aware index). For single-leg strategies pass [df].

    Persists in out_dir:
    - summary.json (always)
    - trades.parquet / equity.parquet (if persist_details)
    - snapshot_meta.json (optional)
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    if not dfs_utc:
        raise ValueError("dfs_utc must be non-empty")

    # Ensure all legs tz-aware and sorted
    for df in dfs_utc:
        if df.index.tz is None:
            raise ValueError("all dfs_utc must have tz-aware UTC index")

    # Align multi-leg data to a common timestamp set to avoid backtrader once-mode shape issues.
    if len(dfs_utc) > 1:
        common = dfs_utc[0].index
        for df in dfs_utc[1:]:
            common = common.intersection(df.index)
        if len(common) == 0:
            raise ValueError('multi-leg dfs have no overlapping timestamps')
        dfs_utc = [df.loc[common].copy() for df in dfs_utc]

    df_bt_list = [_to_bt_df(df) for df in dfs_utc]

    def _run_once(slippage_bps_side: float) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
        cerebro = bt.Cerebro(stdstats=False)

        for i, df_bt in enumerate(df_bt_list):
            feed = _make_feed(df_bt)
            cerebro.adddata(feed, name=f"leg{i}")

        cerebro.broker.setcash(cfg.cash)
        cerebro.broker.setcommission(commission=cfg.commission_pct)
        cerebro.broker.set_slippage_perc(perc=float(slippage_bps_side) / 10000.0)

        cerebro.addstrategy(strategy_cls, **strat_params)

        cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd_close")
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", timeframe=bt.TimeFrame.Days, annualize=True)
        cerebro.addanalyzer(TradeListAnalyzer, _name="tradelist")
        cerebro.addanalyzer(EquityCurveAnalyzer, _name="equity")

        start_value = float(cerebro.broker.getvalue())
        results = cerebro.run()
        strat = results[0]
        end_value = float(cerebro.broker.getvalue())

        dd_close = strat.analyzers.dd_close.get_analysis()
        sharpe = strat.analyzers.sharpe.get_analysis()
        trades = strat.analyzers.tradelist.get_analysis()
        equity_rows = strat.analyzers.equity.get_analysis()

        trades_df = pd.DataFrame(trades)
        equity_df = pd.DataFrame(equity_rows)

        tmetrics = compute_trade_metrics(trades)

        dd_intrabar = None
        if not equity_df.empty and "equity_intrabar_min" in equity_df.columns:
            dd_intrabar = compute_drawdown_from_equity(equity_df["equity_intrabar_min"].to_numpy(dtype=float))

        # Data range (use first leg as reference)
        data_dt_min = str(dfs_utc[0].index.min())
        data_dt_max = str(dfs_utc[0].index.max())

        out = {
            "symbol": cfg.symbol,
            "start_value": start_value,
            "end_value": end_value,
            "net_pnl": end_value - start_value,
            "net_return_pct": (end_value / start_value - 1.0) * 100.0 if start_value else None,
            "max_drawdown_close_pct": dd_close.get("max", {}).get("drawdown", None),
            "max_drawdown_close_len": dd_close.get("max", {}).get("len", None),
            "max_drawdown_intrabar_pct": dd_intrabar,
            "sharpe": sharpe.get("sharperatio", None),
            "slippage_bps_side": float(slippage_bps_side),
            "commission_pct": cfg.commission_pct,
            "strategy": strategy_id or strategy_cls.__name__,
            "strategy_params": strat_params,
            "generated_utc": datetime.now(tz=UTC).isoformat(),
            "data_dt_min_utc": data_dt_min,
            "data_dt_max_utc": data_dt_max,
            **tmetrics,
        }

        return out, trades_df, equity_df

    out, trades_df, equity_df = _run_once(cfg.slippage_bps_side)

    if persist_details:
        trades_df.to_parquet(out_dir / "trades.parquet", index=False)
        equity_df.to_parquet(out_dir / "equity.parquet", index=False)

    # Slippage sensitivity (rerun summary-only at alternate slippage levels)
    sens = {}
    for bps in cfg.slippage_sensitivity_bps:
        if float(bps) == float(cfg.slippage_bps_side):
            continue
        s2, _, _ = _run_once(float(bps))
        sens[str(float(bps))] = {
            "net_pnl": s2.get("net_pnl"),
            "net_return_pct": s2.get("net_return_pct"),
            "profit_factor": s2.get("profit_factor"),
            "max_drawdown_intrabar_pct": s2.get("max_drawdown_intrabar_pct"),
            "sharpe": s2.get("sharpe"),
        }

    out["slippage_sensitivity"] = sens

    (out_dir / "summary.json").write_text(json.dumps(out, indent=2, default=str))

    if snapshot_meta is not None:
        (out_dir / "snapshot_meta.json").write_text(json.dumps(snapshot_meta, indent=2, default=str))

    return out


# -------- Backward-compatible wrappers (RSI2 legacy scripts) --------

def run_rsi2_backtest_df(
    df_utc: pd.DataFrame,
    out_dir: Path,
    cfg: BacktestConfig,
    strat_params: dict,
    snapshot_meta: dict | None = None,
    persist_details: bool = True,
) -> dict:
    from .strategies.rsi2 import RSI2Daytrade

    return run_backtest_df(
        dfs_utc=[df_utc],
        out_dir=out_dir,
        cfg=cfg,
        strategy_cls=RSI2Daytrade,
        strat_params=strat_params,
        snapshot_meta=snapshot_meta,
        persist_details=persist_details,
        strategy_id="RSI2Daytrade",
    )


def run_rsi2_backtest(snapshot_dir: Path, out_dir: Path, cfg: BacktestConfig, strat_params: dict) -> dict:
    data_path = snapshot_dir / "bars.parquet"
    meta_path = snapshot_dir / "meta.json"
    df = _load_parquet(data_path)
    snap_meta = None
    if meta_path.exists():
        try:
            snap_meta = json.loads(meta_path.read_text())
        except Exception:
            snap_meta = None

    return run_rsi2_backtest_df(df, out_dir=out_dir, cfg=cfg, strat_params=strat_params, snapshot_meta=snap_meta)
