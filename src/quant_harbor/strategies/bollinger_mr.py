from __future__ import annotations

import backtrader as bt


class BollingerMR(bt.Strategy):
    """Bollinger Bands mean reversion.

    Long-only baseline:
    - Entry: close < lower band
    - Exit: close >= mid band OR TP/SL OR max_bars_hold

    Notes:
    - Designed for RTH bars. No forced EOD flatten; may carry overnight.
    """

    params = dict(
        bb_period=20,
        bb_dev=2.0,
        stop_pct=0.008,
        take_pct=0.010,
        max_bars_hold=16,
    )

    def __init__(self):
        self.bb = bt.indicators.BollingerBands(self.data.close, period=self.p.bb_period, devfactor=self.p.bb_dev)
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
            if self.data.close[0] < self.bb.bot[0]:
                self.order = self.buy()
            return

        # exits
        if self.entry_price:
            stop = self.entry_price * (1.0 - self.p.stop_pct)
            take = self.entry_price * (1.0 + self.p.take_pct)
            if self.data.close[0] <= stop:
                self.order = self.close(); return
            if self.data.close[0] >= take:
                self.order = self.close(); return

        if self.data.close[0] >= self.bb.mid[0]:
            self.order = self.close(); return

        if self.entry_bar is not None and (len(self) - self.entry_bar) >= self.p.max_bars_hold:
            self.order = self.close(); return
