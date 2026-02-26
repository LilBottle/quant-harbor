from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


RegimeLabel = Literal["Uptrend", "Downtrend", "Range", "Neutral", "Trend"]


@dataclass
class RegimeConfig:
    # windows are in bars
    n_er: int = 80
    n_adx: int = 80
    n_bw: int = 80

    # direction signal
    # Use n_dir bars of return vs realized vol to infer up/down direction.
    # For 15m bars, 80 is only ~3 trading days; too twitchy for multi-month regimes.
    n_dir: int = 520          # ~1 trading month (26 bars/day)
    n_vol: int = 520
    dir_tanh_k: float = 0.35  # smaller => less saturation / fewer sign flips
    dir_th: float = 0.10      # minimum abs(direction_score) to call up/down
    dir_ema_span: int = 40    # smooth direction a bit

    # band settings for bandwidth
    bb_period: int = 20
    bb_dev: float = 2.0

    # weights (trend *strength* only)
    # We add a directional-strength component (abs(direction_score)) because
    # intraday ADX can stay depressed even during strong multi-month drift.
    w_er: float = 0.35
    w_adx: float = 0.25
    w_bw: float = 0.15
    w_dir: float = 0.25

    # ADX normalization for intraday bars tends to be lower than daily.
    # Map ADX to 0..1 via clip((ADX - adx_floor)/adx_scale, 0, 1)
    # NOTE: defaults tuned more permissively for 15m.
    adx_floor: float = 8.0
    adx_scale: float = 10.0

    # thresholds (strength)
    # NOTE: prior trend_th=0.60 was too strict: in our 15m QQQ test it produced zero "Trend" bars.
    trend_th: float = 0.48
    range_th: float = 0.34

    # structured range vs neutral controls
    # Range = low trend_strength AND (direction is weak OR direction is unstable).
    range_dir_abs_th: float = 0.18
    range_dir_std_window: int = 520
    range_dir_std_th: float = 0.35

    # Trend direction gating: only call Up/Down when direction is strong enough.
    trend_dir_abs_th: float = 0.22

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


def direction_score(close: pd.Series, n_dir: int, n_vol: int, tanh_k: float) -> pd.Series:
    """Return a smooth direction score in [-1, 1].

    We measure n_dir-bar return relative to realized volatility (n_vol-bar std of returns),
    then squash with tanh so extreme values don't dominate.
    """
    close = close.astype(float)
    r = close.pct_change()
    vol = r.rolling(n_vol).std(ddof=0)
    ret_n = close.pct_change(n_dir)
    z = ret_n / vol.replace(0.0, np.nan)
    return np.tanh(float(tanh_k) * z)


def compute_regime(df: pd.DataFrame, cfg: RegimeConfig = RegimeConfig()) -> pd.DataFrame:
    """Compute trend strength + direction for a 15m OHLCV dataframe (UTC-indexed)."""
    close = df["close"]

    # --- strength components ---
    er = efficiency_ratio(close, cfg.n_er)
    adx_v = adx(df["high"], df["low"], close, cfg.n_adx)

    # normalize adx for intraday: clip((ADX-adx_floor)/adx_scale,0,1)
    adx01 = ((adx_v - float(cfg.adx_floor)) / float(cfg.adx_scale)).clip(0.0, 1.0)

    bw = bollinger_bandwidth(close, cfg.bb_period, cfg.bb_dev)
    bwz = zscore(bw, cfg.n_bw)
    # clip bandwidth z to [0,1]. Use a wider mapping so typical z values don't all clip to 0.
    # z<=-1 -> 0, z==0 -> 0.33, z==2 -> 1.0
    bw01 = ((bwz + 1.0) / 3.0).clip(0.0, 1.0)

    # --- direction (separate from strength) ---
    dir_s_raw = direction_score(close, cfg.n_dir, cfg.n_vol, cfg.dir_tanh_k)
    dir_s = dir_s_raw.ewm(span=cfg.dir_ema_span, adjust=False).mean()
    dir_strength01 = dir_s.abs().clip(0.0, 1.0)

    trend_raw = (
        cfg.w_er * er
        + cfg.w_adx * adx01
        + cfg.w_bw * bw01
        + cfg.w_dir * dir_strength01
    )
    trend = trend_raw.ewm(span=cfg.ema_span, adjust=False).mean()

    # --- labels ---
    direction_label = pd.Series(
        np.where(dir_s > cfg.dir_th, "Up", np.where(dir_s < -cfg.dir_th, "Down", "Flat")),
        index=df.index,
    )

    dir_abs = dir_s.abs()
    dir_std = dir_s.rolling(int(cfg.range_dir_std_window)).std(ddof=0)

    is_trend_strength = trend > float(cfg.trend_th)
    is_range_strength = trend < float(cfg.range_th)

    # Range should be high-confidence: low strength AND (weak direction OR unstable direction)
    is_range = is_range_strength & (
        (dir_abs < float(cfg.range_dir_abs_th))
        | (dir_std > float(cfg.range_dir_std_th))
    )

    # Trend direction only when both strength and direction are strong
    is_trend = is_trend_strength & (dir_abs >= float(cfg.trend_dir_abs_th))

    regime = pd.Series("Neutral", index=df.index, dtype=object)
    regime[is_range] = "Range"
    regime[is_trend & (direction_label == "Up")] = "Uptrend"
    regime[is_trend & (direction_label == "Down")] = "Downtrend"
    # If strength says trend but direction is ambiguous, keep as Neutral (default).

    out = pd.DataFrame(
        {
            "trend_score": trend.astype(float),
            "direction_score": dir_s.astype(float),
            "er": er.astype(float),
            "adx": adx_v.astype(float),
            "bw": bw.astype(float),
            "bw_z": bwz.astype(float),
            "direction": direction_label,
            "dir_abs": dir_abs.astype(float),
            "dir_std": dir_std.astype(float),
            "regime": regime,
        },
        index=df.index,
    )
    return out
