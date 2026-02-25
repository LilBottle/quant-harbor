from __future__ import annotations

import backtrader as bt


class IntradayVWAP(bt.Indicator):
    """Session VWAP reset each day (based on data.datetime.date())."""

    lines = ("vwap",)

    def __init__(self):
        self.addminperiod(1)
        self._cum_pv = 0.0
        self._cum_v = 0.0
        self._last_date = None

    def next(self):
        d = self.data.datetime.date(0)
        if self._last_date is None or d != self._last_date:
            self._cum_pv = 0.0
            self._cum_v = 0.0
            self._last_date = d

        px = float(self.data.close[0])
        v = float(getattr(self.data, 'volume')[0]) if hasattr(self.data, 'volume') else 0.0

        self._cum_pv += px * v
        self._cum_v += v

        if self._cum_v > 0:
            self.lines.vwap[0] = self._cum_pv / self._cum_v
        else:
            self.lines.vwap[0] = px


class VWAPDeviationMR(bt.Strategy):
    """VWAP deviation mean reversion.

    Long-only baseline:
    - dev = (close - vwap) / vwap
    - Entry: dev <= -dev_entry
    - Exit: dev >= -dev_exit (typically 0) OR TP/SL OR max_bars_hold

    Notes:
    - Works best on liquid ETFs.
    - No forced EOD flatten; may carry overnight. Overnight gap risk is represented only via next RTH bar.
    """

    params = dict(
        dev_entry=0.006,  # 0.6% below vwap
        dev_exit=0.0,
        stop_pct=0.010,
        take_pct=0.010,
        max_bars_hold=12,
    )

    def __init__(self):
        self.vwap = IntradayVWAP(self.data)
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

        vwap = float(self.vwap.vwap[0])
        if vwap <= 0:
            return
        dev = (float(self.data.close[0]) - vwap) / vwap

        if not self.position:
            if dev <= -abs(self.p.dev_entry):
                self.order = self.buy()
            return

        if self.entry_price:
            stop = self.entry_price * (1.0 - self.p.stop_pct)
            take = self.entry_price * (1.0 + self.p.take_pct)
            if float(self.data.close[0]) <= stop:
                self.order = self.close(); return
            if float(self.data.close[0]) >= take:
                self.order = self.close(); return

        if dev >= -abs(self.p.dev_exit):
            self.order = self.close(); return

        if self.entry_bar is not None and (len(self) - self.entry_bar) >= self.p.max_bars_hold:
            self.order = self.close(); return
