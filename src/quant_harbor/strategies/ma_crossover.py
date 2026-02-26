from __future__ import annotations

import backtrader as bt

from ._bracket_mixin import LongBracketMixin


class MovingAverageCrossover(LongBracketMixin, bt.Strategy):
    """Moving Average Crossover (long-only).

    Baseline:
    - Trend entry when fast MA crosses above slow MA.
    - Exit when fast crosses below slow OR time stop.

    Orders:
    - Entry: market buy
    - Exit: market close (plus bracket stop/take after fill)

    Notes:
    - Uses explicit stop/take children via LongBracketMixin.
    """

    params = dict(
        fast=20,
        slow=50,
        ma_type="sma",  # "sma"|"ema"
        stop_pct=0.010,
        take_pct=0.015,
        max_bars_hold=260,  # ~10 trading days on 15m bars
    )

    def __init__(self):
        self._reset_orders()
        self.entry_bar = None
        self.entry_price = None

        ma_type = str(self.p.ma_type).lower()
        if ma_type == "ema":
            self.fast_ma = bt.indicators.EMA(self.data.close, period=self.p.fast)
            self.slow_ma = bt.indicators.EMA(self.data.close, period=self.p.slow)
        else:
            self.fast_ma = bt.indicators.SMA(self.data.close, period=self.p.fast)
            self.slow_ma = bt.indicators.SMA(self.data.close, period=self.p.slow)

        self.cross = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)

    def next(self):
        if self.order_entry or self.order_stop or self.order_take:
            return

        if not self.position:
            if self.cross[0] > 0:
                self.order_entry = self.buy()
            return

        # exit on cross down
        if self.cross[0] < 0:
            self._cancel_children()
            self.order_entry = self.close()
            return

        # time stop
        if self.entry_bar is not None and (len(self) - self.entry_bar) >= int(self.p.max_bars_hold):
            self._cancel_children()
            self.order_entry = self.close()
            return
