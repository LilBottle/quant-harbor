from __future__ import annotations

import backtrader as bt

from ._bracket_mixin import LongBracketMixin


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
        v = float(getattr(self.data, "volume")[0]) if hasattr(self.data, "volume") else 0.0

        self._cum_pv += px * v
        self._cum_v += v

        if self._cum_v > 0:
            self.lines.vwap[0] = self._cum_pv / self._cum_v
        else:
            self.lines.vwap[0] = px


class VWAPDeviationMR(LongBracketMixin, bt.Strategy):
    """VWAP deviation mean reversion.

    Long-only baseline:
    - dev = (close - vwap) / vwap
    - Entry: dev <= -dev_entry
    - Exit: dev >= -dev_exit (typically 0) OR time stop

    Stop/Take:
    - Implemented via explicit Stop/Limit child orders submitted after entry fill.

    Notes:
    - Works best on liquid ETFs.
    - No forced EOD flatten; may carry overnight.
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
        self._reset_orders()
        self.entry_bar = None
        self.entry_price = None

    def next(self):
        if self.order_entry or self.order_stop or self.order_take:
            return

        vwap = float(self.vwap.vwap[0])
        if vwap <= 0:
            return
        dev = (float(self.data.close[0]) - vwap) / vwap

        if not self.position:
            if dev <= -abs(self.p.dev_entry):
                self.order_entry = self.buy()
            return

        if dev >= -abs(self.p.dev_exit):
            self._cancel_children()
            self.order_entry = self.close();
            return

        if self.entry_bar is not None and (len(self) - self.entry_bar) >= self.p.max_bars_hold:
            self._cancel_children()
            self.order_entry = self.close();
            return
