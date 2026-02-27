from __future__ import annotations

import backtrader as bt

from ._bracket_mixin import LongBracketMixin


class BollingerMR(LongBracketMixin, bt.Strategy):
    """Bollinger Bands mean reversion.

    Long-only baseline:
    - Entry: close < lower band
    - Exit: close >= mid band OR time stop

    Stop/Take:
    - Implemented via explicit Stop/Limit child orders submitted after entry fill.

    Notes:
    - Designed for RTH bars. No forced EOD flatten; may carry overnight.
    """

    params = dict(
        bb_period=20,
        bb_dev=2.0,
        stop_pct=0.008,
        take_pct=0.010,
        max_bars_hold=16,

        # Exit tuning
        disable_take_profit=False,
        use_trailing_stop=False,
        trail_pct=0.0,
    )

    def __init__(self):
        self.bb = bt.indicators.BollingerBands(self.data.close, period=self.p.bb_period, devfactor=self.p.bb_dev)
        self._reset_orders()
        self.entry_bar = None
        self.entry_price = None

    def next(self):
        if self.order_entry or self.order_stop or self.order_take:
            return

        if not self.position:
            if self.data.close[0] < self.bb.bot[0]:
                self.order_entry = self.buy()
            return

        # Mean reversion exit
        if self.data.close[0] >= self.bb.mid[0]:
            self._cancel_children()
            self.order_entry = self.close()
            return

        # Time stop
        if self.entry_bar is not None and (len(self) - self.entry_bar) >= self.p.max_bars_hold:
            self._cancel_children()
            self.order_entry = self.close()
