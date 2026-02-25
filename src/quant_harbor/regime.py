from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


RegimeLabel = Literal["Trend", "Range", "Neutral"]


@dataclass
class RegimeConfig:
    # windows are in bars
    n_er: int = 80
    n_adx: int = 80
    n_bw: int = 80
    # band settings for bandwidth
    bb_period: int = 20
    bb_dev: float = 2.0

    # weights
    w_er: float = 0.45
    w_adx: float = 0.35
    w_bw: float = 0.20

    # thresholds
    trend_th: float = 0.60
    range_th: float = 0.40

    # smoothing
    ema_span: int = 20


def efficiency_ratio(close: pd.Series, n: int) -> pd.Series:
    close = close.astype(float)
    net = (close - close.shift(n)).abs()
    churn = close.diff().abs().rolling(n).sum()
    er = net / churn.replace(0.0, np.nan)
    return er.clip(0.0, 1.0)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    """Wilder ADX (simple pandas implementation)."""
    high = high.astype(float)
    low = low.astype(float)
    close = close.astype(float)

    up = high.diff()
    down = -low.diff()

    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    tr1 = (high - low).abs()
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder smoothing via ewm(alpha=1/n, adjust=False)
    atr = tr.ewm(alpha=1.0 / n, adjust=False).mean()

    plus_di = 100.0 * pd.Series(plus_dm, index=close.index).ewm(alpha=1.0 / n, adjust=False).mean() / atr
    minus_di = 100.0 * pd.Series(minus_dm, index=close.index).ewm(alpha=1.0 / n, adjust=False).mean() / atr

    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)).replace([np.inf, -np.inf], np.nan)
    adx = dx.ewm(alpha=1.0 / n, adjust=False).mean()
    return adx


def bollinger_bandwidth(close: pd.Series, period: int, dev: float) -> pd.Series:
    close = close.astype(float)
    ma = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    upper = ma + dev * sd
    lower = ma - dev * sd
    bw = (upper - lower) / ma.replace(0.0, np.nan)
    return bw


def zscore(x: pd.Series, n: int) -> pd.Series:
    mu = x.rolling(n).mean()
    sd = x.rolling(n).std(ddof=0)
    return (x - mu) / sd.replace(0.0, np.nan)


def compute_regime(df: pd.DataFrame, cfg: RegimeConfig = RegimeConfig()) -> pd.DataFrame:
    """Compute trend_score / regime label for a 15m OHLCV dataframe (UTC-indexed)."""
    close = df["close"]

    er = efficiency_ratio(close, cfg.n_er)
    adx_v = adx(df["high"], df["low"], close, cfg.n_adx)

    # normalize adx: clip((ADX-20)/10,0,1)
    adx01 = ((adx_v - 20.0) / 10.0).clip(0.0, 1.0)

    bw = bollinger_bandwidth(close, cfg.bb_period, cfg.bb_dev)
    bwz = zscore(bw, cfg.n_bw)
    # clip bandwidth z to [0,1] using a 0..2 range
    bw01 = (bwz / 2.0).clip(0.0, 1.0)

    trend_raw = cfg.w_er * er + cfg.w_adx * adx01 + cfg.w_bw * bw01
    trend = trend_raw.ewm(span=cfg.ema_span, adjust=False).mean()

    regime = pd.Series(np.where(trend > cfg.trend_th, "Trend", np.where(trend < cfg.range_th, "Range", "Neutral")), index=df.index)

    out = pd.DataFrame(
        {
            "trend_score": trend.astype(float),
            "er": er.astype(float),
            "adx": adx_v.astype(float),
            "bw": bw.astype(float),
            "bw_z": bwz.astype(float),
            "regime": regime,
        },
        index=df.index,
    )
    return out
