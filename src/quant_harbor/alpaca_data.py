from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from pathlib import Path

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from .config import load_alpaca_env

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts.astimezone(UTC)


def fetch_bars(symbol: str, start_et: datetime, end_et: datetime, timeframe: TimeFrame = TimeFrame(15, TimeFrame.Minute.unit)) -> pd.DataFrame:
    """Fetch OHLCV bars from Alpaca Market Data.

    Returns a DataFrame indexed by UTC timestamps, columns: open, high, low, close, volume.
    """
    env = load_alpaca_env()
    client = StockHistoricalDataClient(env.api_key, env.secret)

    # Corporate actions / adjustments:
    # - We default to adjusted bars (splits + dividends) to approximate total-return behavior.
    # - This should always be recorded in snapshot meta for audit.
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=_to_utc(start_et),
        end=_to_utc(end_et),
        adjustment="all",
        # Most free plans only allow IEX. Using SIP will 403.
        feed="iex",
    )

    bars = client.get_stock_bars(req)
    df = bars.df

    # alpaca-py returns multi-index (symbol, timestamp)
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
        df = df[df["symbol"] == symbol]
        df = df.set_index("timestamp")

    # Standardize columns
    df = df.sort_index()
    df.index = pd.to_datetime(df.index, utc=True)

    out = pd.DataFrame(
        {
            "open": df["open"].astype(float),
            "high": df["high"].astype(float),
            "low": df["low"].astype(float),
            "close": df["close"].astype(float),
            "volume": df["volume"].astype(float),
        },
        index=df.index,
    )
    return out


def filter_rth_15m(df_utc: pd.DataFrame) -> pd.DataFrame:
    """Filter to RTH 09:30–16:00 ET for 15m bars."""
    if df_utc.index.tz is None:
        raise ValueError("df index must be timezone-aware (UTC)")

    df_et = df_utc.tz_convert(ET)

    # Keep bars whose timestamp is within session (inclusive start, exclusive end)
    # 15m bars labeled by bar end time varies by vendor; for MVP we filter by clock time.
    t = df_et.index.time
    start = datetime(2000, 1, 1, 9, 30, tzinfo=ET).time()
    end = datetime(2000, 1, 1, 16, 0, tzinfo=ET).time()

    mask = (t >= start) & (t < end)
    return df_et.loc[mask].tz_convert(UTC)


def snapshot_to_parquet(df_utc: pd.DataFrame, out_dir: Path, meta: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / "bars.parquet"
    meta_path = out_dir / "meta.json"

    df_utc.to_parquet(data_path)

    with meta_path.open("w") as f:
        json.dump(meta, f, indent=2, default=str)

    return data_path


def make_snapshot(symbol: str, start_et: datetime, end_et: datetime, base_dir: Path) -> Path:
    """Fetch + filter + write snapshot for a single symbol.

    Snapshot layout:
    - bars.parquet
    - meta.json
    """
    return make_snapshot_multi([symbol], start_et=start_et, end_et=end_et, base_dir=base_dir)


def make_snapshot_multi(symbols: list[str], start_et: datetime, end_et: datetime, base_dir: Path) -> Path:
    """Fetch + filter + write snapshot for multiple symbols.

    Snapshot layout:
    - bars_<SYMBOL>.parquet for each symbol
    - meta.json

    This enables multi-leg strategies (e.g., pairs).
    """
    now = datetime.now(tz=UTC)
    ts = now.strftime("%Y%m%dT%H%M%S") + f"_{now.microsecond:06d}Z"
    sym_tag = "-".join(symbols)
    # Include PID to avoid collisions when running multiple jobs in parallel.
    import os

    snap_id = f"alpaca_{sym_tag}_15m_{ts}_pid{os.getpid()}"
    out_dir = base_dir / snap_id
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "snapshot_id": snap_id,
        "symbols": list(symbols),
        "timeframe": "15m",
        "start_et": str(start_et),
        "end_et": str(end_et),
        "created_utc": ts,
        "rth_filter": "09:30-16:00 ET",
        "source": "alpaca-py StockHistoricalDataClient",
        "feed": "iex",
        "adjustment": "all",
        "bar_timestamp_semantics": "vendor-dependent (15m bar label may be start/end); verify close auction handling for 15:45–16:00 bar if strategy relies on close",
        "note": "index is UTC; RTH filtered by clock time (MVP)",
    }

    first_rth = None
    for sym in symbols:
        raw = fetch_bars(symbol=sym, start_et=start_et, end_et=end_et, timeframe=TimeFrame(15, TimeFrame.Minute.unit))
        rth = filter_rth_15m(raw)
        rth.to_parquet(out_dir / f"bars_{sym}.parquet")
        if first_rth is None:
            first_rth = rth

    # Backward compatibility: for single-symbol snapshots also write bars.parquet
    if len(symbols) == 1 and first_rth is not None:
        sym = symbols[0]
        first_rth.to_parquet(out_dir / "bars.parquet")
        meta["symbol"] = sym

    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    return out_dir
