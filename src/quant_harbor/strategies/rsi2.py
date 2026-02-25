from __future__ import annotations

import backtrader as bt

from ._bracket_mixin import LongBracketMixin


class RSI2Daytrade(LongBracketMixin, bt.Strategy):
    params = dict(
        rsi_period=2,
        entry_rsi=15.0,
        stop_pct=0.006,  # 0.6%
        take_pct=0.009,  # 0.9%
        max_bars_hold=8,
    )

    def __init__(self):
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period, safediv=True)
        self._reset_orders()
        self.entry_bar = None
        self.entry_price = None

    def next(self):
        # if any pending order exists, wait
        if self.order_entry or self.order_stop or self.order_take:
            return

        if not self.position:
            if self.rsi[0] < self.p.entry_rsi:
                self.order_entry = self.buy()
            return

        # Time stop / max hold
        if self.entry_bar is not None:
            if (len(self) - self.entry_bar) >= self.p.max_bars_hold:
                self._cancel_children()
                self.order_entry = self.close()
