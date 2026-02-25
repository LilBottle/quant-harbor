from __future__ import annotations

import backtrader as bt


class RSI2Daytrade(bt.Strategy):
    params = dict(
        rsi_period=2,
        entry_rsi=15.0,
        stop_pct=0.006,  # 0.6%
        take_pct=0.009,  # 0.9%
        max_bars_hold=8,
    )

    def __init__(self):
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period, safediv=True)
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
            if self.rsi[0] < self.p.entry_rsi:
                self.order = self.buy()
            return

        # Manage exits
        if self.entry_price:
            stop = self.entry_price * (1.0 - self.p.stop_pct)
            take = self.entry_price * (1.0 + self.p.take_pct)

            if self.data.close[0] <= stop:
                self.order = self.close()
                return
            if self.data.close[0] >= take:
                self.order = self.close()
                return

        if self.entry_bar is not None:
            if (len(self) - self.entry_bar) >= self.p.max_bars_hold:
                self.order = self.close()
