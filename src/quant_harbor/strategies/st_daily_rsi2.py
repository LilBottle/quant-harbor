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

    Exits (configurable):
      - Optional RSI mean reversion exit: RSI >= exit_rsi (use_rsi_exit=True)
      - Optional profit trailing activation: when close > SMA(ma_period), replace fixed stop with trailing stop
        (enable_ma_profit_trail=True)
      - Regime risk-off: Daily SuperTrend flips down (dir < 0) => close
      - Time stop: max_bars_hold (15m bars)
      - Plus bracket children (stop/take) via LongBracketMixin

    Notes:
      - Default behavior remains the RSI exit to preserve backward compatibility.
      - The MA-profit-trailing mode is meant to let price action "prove" strength before switching to a trailing stop.
    """

    params = dict(
        # Daily SuperTrend
        st_period=14,
        st_multiplier=3.0,

        # Intraday RSI2
        rsi_period=2,
        entry_rsi=15.0,
        exit_rsi=50.0,
        use_rsi_exit=True,

        # Risk
        stop_pct=0.008,
        take_pct=0.012,
        max_bars_hold=24,

        # Bracket config (handled by LongBracketMixin)
        disable_take_profit=False,
        use_trailing_stop=False,
        trail_pct=0.0,

        # Profit trailing activation (optional)
        enable_ma_profit_trail=False,
        ma_period=5,

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
        self.sma = bt.indicators.SMA(self.data0.close, period=int(self.p.ma_period))
        self.st_d = SuperTrendIndicator(self.data1, period=self.p.st_period, multiplier=self.p.st_multiplier)

        self._profit_trail_active = False

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
            try:
                self.order_entry.addinfo(exit_reason="regime_flip_down" if flipped_down else "regime_not_up")
            except Exception:
                pass
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

        # --- optional profit trailing activation (while daily_up) ---
        if bool(self.p.enable_ma_profit_trail) and (not self._profit_trail_active):
            c0 = float(self.data0.close[0]) if self.data0.close[0] is not None else float('nan')
            sma0 = float(self.sma[0]) if self.sma[0] is not None else float('nan')
            if (not math.isnan(c0)) and (not math.isnan(sma0)) and (c0 > sma0):
                # Replace fixed stop with trailing stop once price is above SMA(ma_period).
                # This is used as a profit-protection / take-profit mechanism.
                if getattr(self, 'order_stop', None) is not None:
                    try:
                        self.cancel(self.order_stop)
                    except Exception:
                        pass
                    self.order_stop = None

                trail_pct = float(getattr(self.p, 'trail_pct', 0.0) or 0.0)
                # only activate if trail_pct is valid
                if trail_pct > 0:
                    self.order_stop = self.sell(
                        exectype=bt.Order.StopTrail,
                        trailpercent=trail_pct,
                        info=dict(exit_reason='profit_trail'),
                    )
                    self._profit_trail_active = True

        # --- exits (while daily_up) ---
        if bool(self.p.use_rsi_exit):
            rsi0 = float(self.rsi[0]) if self.rsi[0] is not None else float('nan')
            if (not math.isnan(rsi0)) and (rsi0 >= float(self.p.exit_rsi)):
                self._cancel_children()
                self.order_entry = self.close()
                try:
                    self.order_entry.addinfo(exit_reason="rsi_exit")
                except Exception:
                    pass
                return

        # time stop on intraday bars
        if self.entry_bar is not None and (len(self) - self.entry_bar) >= int(self.p.max_bars_hold):
            self._cancel_children()
            self.order_entry = self.close()
            try:
                self.order_entry.addinfo(exit_reason="time_stop")
            except Exception:
                pass
            return
