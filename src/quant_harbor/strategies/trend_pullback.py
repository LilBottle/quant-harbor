from __future__ import annotations

import backtrader as bt

from ._bracket_mixin import LongBracketMixin


class TrendPullback(LongBracketMixin, bt.Strategy):
    """Trend Pullback strategy (long-only).

    Intent:
    - Only trade with the trend (fast MA above slow MA).
    - Enter on a pullback and resume signal.

    Baseline logic:
    - Trend filter: fast_ma > slow_ma
    - Pullback: close dips below fast_ma by a small ATR multiple (or percent)
    - Resume: close recovers above fast_ma

    Exit:
    - Trend breaks (fast_ma < slow_ma) OR time stop.

    Risk:
    - stop/take via LongBracketMixin.
    """

    params = dict(
        fast=20,
        slow=100,
        ma_type="ema",  # "sma"|"ema"
        atr_period=14,
        pullback_atr=0.5,  # how deep pullback must be (in ATR units)
        stop_pct=0.010,
        take_pct=0.015,
        max_bars_hold=260,
    )

    def __init__(self):
        self._reset_orders()
        self.entry_bar = None
        self.entry_price = None

        ma_type = str(self.p.ma_type).lower()
        if ma_type == "sma":
            self.fast_ma = bt.indicators.SMA(self.data.close, period=self.p.fast)
            self.slow_ma = bt.indicators.SMA(self.data.close, period=self.p.slow)
        else:
            self.fast_ma = bt.indicators.EMA(self.data.close, period=self.p.fast)
            self.slow_ma = bt.indicators.EMA(self.data.close, period=self.p.slow)

        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)

        # state: were we in a pullback (below fast MA) while trend ok?
        self._pullback_armed = False

    def next(self):
        if self.order_entry or self.order_stop or self.order_take:
            return

        trend_ok = self.fast_ma[0] > self.slow_ma[0]

        if not self.position:
            if not trend_ok:
                self._pullback_armed = False
                return

            # arm when we dip sufficiently below fast_ma
            pb_level = self.fast_ma[0] - float(self.p.pullback_atr) * self.atr[0]
            if self.data.close[0] < pb_level:
                self._pullback_armed = True
                return

            # trigger when pullback armed and we recover above fast_ma
            if self._pullback_armed and self.data.close[0] > self.fast_ma[0]:
                self._pullback_armed = False
                self.order_entry = self.buy()
            return

        # in position
        if not trend_ok:
            self._cancel_children()
            self.order_entry = self.close()
            return

        if self.entry_bar is not None and (len(self) - self.entry_bar) >= int(self.p.max_bars_hold):
            self._cancel_children()
            self.order_entry = self.close()
            return
