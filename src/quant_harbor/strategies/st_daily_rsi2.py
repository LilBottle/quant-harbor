from __future__ import annotations

import math

import backtrader as bt

from ._bracket_mixin import LongBracketMixin
from .supertrend import SuperTrendIndicator


class SuperTrendDailyRSI2(LongBracketMixin, bt.Strategy):
    """Daily SuperTrend regime + intraday RSI2 mean-reversion entries.

    Data feeds:
      - data0: intraday bars (15m)
      - data1: daily bars (resampled from data0 by the runner)

    Entry (long-only):
      - Daily SuperTrend direction is UP (dir > 0)
      - Intraday RSI <= entry_rsi (oversold)

    Exits:
      - Primary: RSI mean reversion: RSI >= exit_rsi
      - Regime risk-off: Daily SuperTrend flips down (dir < 0) => close
      - Time stop: max_bars_hold (15m bars)
      - Plus bracket children (stop/take or trailing stop) via LongBracketMixin

    Notes:
      - This is designed to reduce whipsaw by only taking RSI2 dips inside a higher-timeframe uptrend.
      - Requires the runner to add a daily resample feed.
    """

    params = dict(
        # Daily SuperTrend
        st_period=14,
        st_multiplier=3.0,

        # Intraday RSI2
        rsi_period=2,
        entry_rsi=15.0,
        exit_rsi=50.0,

        # Risk
        stop_pct=0.008,
        take_pct=0.012,
        max_bars_hold=24,

        # Exit tuning (handled by LongBracketMixin)
        disable_take_profit=False,
        use_trailing_stop=False,
        trail_pct=0.0,

        # If daily regime is UP at the first valid point, allow initial entry.
        enter_on_start=True,
    )

    def __init__(self):
        self._reset_orders()
        self.entry_bar = None
        self.entry_price = None
        self._prev_daily_dir = None

        if len(self.datas) < 2:
            raise RuntimeError(
                "SuperTrendDailyRSI2 requires 2 data feeds: intraday (data0) and daily (data1). "
                "Update backtest_runner to resample daily for this strategy_id."
            )

        self.data0 = self.datas[0]
        self.data1 = self.datas[1]  # daily

        self.rsi = bt.indicators.RSI(self.data0.close, period=self.p.rsi_period)
        self.st_d = SuperTrendIndicator(self.data1, period=self.p.st_period, multiplier=self.p.st_multiplier)

    def next(self):
        # block only on pending manual order
        if self.order_entry is not None:
            return

        # --- regime guard (daily supertrend must be valid) ---
        ddir = float(self.st_d.dir[0]) if self.st_d.dir[0] is not None else float('nan')
        if math.isnan(ddir):
            return

        daily_up = ddir > 0
        prev = self._prev_daily_dir
        self._prev_daily_dir = ddir
        flipped_down = (prev is not None) and (prev > 0) and (ddir < 0)

        # if daily flips down, force exit
        if self.position and (flipped_down or (not daily_up)):
            self._cancel_children()
            self.order_entry = self.close()
            return

        # --- entries ---
        if not self.position:
            if not daily_up:
                return

            # allow initial entry if regime starts up
            if bool(self.p.enter_on_start) and prev is None and daily_up:
                # still require RSI signal (avoid perma-long)
                pass

            rsi0 = float(self.rsi[0]) if self.rsi[0] is not None else float('nan')
            if math.isnan(rsi0):
                return

            if rsi0 <= float(self.p.entry_rsi):
                self.order_entry = self.buy()
            return

        # --- exits (while daily_up) ---
        rsi0 = float(self.rsi[0]) if self.rsi[0] is not None else float('nan')
        if (not math.isnan(rsi0)) and (rsi0 >= float(self.p.exit_rsi)):
            self._cancel_children()
            self.order_entry = self.close()
            return

        # time stop on intraday bars
        if self.entry_bar is not None and (len(self) - self.entry_bar) >= int(self.p.max_bars_hold):
            self._cancel_children()
            self.order_entry = self.close()
            return
