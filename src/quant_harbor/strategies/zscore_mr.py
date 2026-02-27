from __future__ import annotations

import backtrader as bt

from ._bracket_mixin import LongBracketMixin


class ZScoreMR(LongBracketMixin, bt.Strategy):
    """Z-score mean reversion on price vs rolling mean.

    Long-only baseline:
    - z = (close - SMA) / Std
    - Entry: z <= -z_entry
    - Exit: z >= -z_exit (typically 0) OR time stop

    Stop/Take:
    - Implemented via explicit Stop/Limit child orders submitted after entry fill.
    """

    params = dict(
        lookback=50,
        z_entry=2.0,
        z_exit=0.0,
        stop_pct=0.010,
        take_pct=0.012,
        max_bars_hold=24,
        min_std=1e-8,

        # Exit tuning
        disable_take_profit=False,
        use_trailing_stop=False,
        trail_pct=0.0,
    )

    def __init__(self):
        sma = bt.indicators.SMA(self.data.close, period=self.p.lookback)
        std = bt.indicators.StdDev(self.data.close, period=self.p.lookback)
        self.z = (self.data.close - sma) / (std + self.p.min_std)

        self._reset_orders()
        self.entry_bar = None
        self.entry_price = None

    def next(self):
        if self.order_entry or self.order_stop or self.order_take:
            return

        if not self.position:
            if self.z[0] <= -abs(self.p.z_entry):
                self.order_entry = self.buy()
            return

        # Mean reversion exit
        if self.z[0] >= -abs(self.p.z_exit):
            self._cancel_children()
            self.order_entry = self.close();
            return

        # Time stop
        if self.entry_bar is not None and (len(self) - self.entry_bar) >= self.p.max_bars_hold:
            self._cancel_children()
            self.order_entry = self.close();
            return
