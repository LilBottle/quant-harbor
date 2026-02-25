from __future__ import annotations

import backtrader as bt


class ZScoreMR(bt.Strategy):
    """Z-score mean reversion on price vs rolling mean.

    Long-only baseline:
    - z = (close - SMA) / Std
    - Entry: z <= -z_entry
    - Exit: z >= -z_exit (typically 0) OR TP/SL OR max_bars_hold
    """

    params = dict(
        lookback=50,
        z_entry=2.0,
        z_exit=0.0,
        stop_pct=0.010,
        take_pct=0.012,
        max_bars_hold=24,
        min_std=1e-8,
    )

    def __init__(self):
        sma = bt.indicators.SMA(self.data.close, period=self.p.lookback)
        std = bt.indicators.StdDev(self.data.close, period=self.p.lookback)
        self.z = (self.data.close - sma) / (std + self.p.min_std)
        self.order = None
        self.entry_bar = None
        self.entry_price = None

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
        if order.status in [order.Completed]:
            if order.isbuy():
                self.entry_bar = len(self)
                self.entry_price = order.executed.price
            elif order.issell():
                self.entry_bar = None
                self.entry_price = None
        self.order = None

    def next(self):
        if self.order:
            return

        if not self.position:
            if self.z[0] <= -abs(self.p.z_entry):
                self.order = self.buy()
            return

        if self.entry_price:
            stop = self.entry_price * (1.0 - self.p.stop_pct)
            take = self.entry_price * (1.0 + self.p.take_pct)
            if self.data.close[0] <= stop:
                self.order = self.close(); return
            if self.data.close[0] >= take:
                self.order = self.close(); return

        if self.z[0] >= -abs(self.p.z_exit):
            self.order = self.close(); return

        if self.entry_bar is not None and (len(self) - self.entry_bar) >= self.p.max_bars_hold:
            self.order = self.close(); return
