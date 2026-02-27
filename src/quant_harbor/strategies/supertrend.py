from __future__ import annotations

import backtrader as bt

from ._bracket_mixin import LongBracketMixin


class SuperTrendIndicator(bt.Indicator):
    """SuperTrend indicator.

    Produces:
    - st: supertrend line
    - dir: +1 uptrend, -1 downtrend

    Implementation notes:
    - Uses ATR and basic bands.
    - Uses iterative final band logic.
    """

    lines = ("st", "dir", "upper", "lower")

    params = dict(
        period=10,
        multiplier=3.0,
    )

    plotinfo = dict(subplot=False)

    def __init__(self):
        self.atr = bt.indicators.ATR(self.data, period=self.p.period)
        hl2 = (self.data.high + self.data.low) / 2.0
        self.basic_upper = hl2 + self.p.multiplier * self.atr
        self.basic_lower = hl2 - self.p.multiplier * self.atr

    def next(self):
        i = len(self) - 1
        if i == 0:
            self.lines.upper[0] = self.basic_upper[0]
            self.lines.lower[0] = self.basic_lower[0]
            # default to up
            self.lines.dir[0] = 1.0
            self.lines.st[0] = self.lines.lower[0]
            return

        prev_upper = self.lines.upper[-1]
        prev_lower = self.lines.lower[-1]
        prev_dir = self.lines.dir[-1]
        prev_close = self.data.close[-1]

        # final upper
        bu = self.basic_upper[0]
        if bu < prev_upper or prev_close > prev_upper:
            fu = bu
        else:
            fu = prev_upper

        # final lower
        bl = self.basic_lower[0]
        if bl > prev_lower or prev_close < prev_lower:
            fl = bl
        else:
            fl = prev_lower

        self.lines.upper[0] = fu
        self.lines.lower[0] = fl

        # direction switch
        # Use CURRENT final bands for the flip condition.
        close = self.data.close[0]
        dir_ = prev_dir
        if prev_dir > 0 and close < fl:
            dir_ = -1.0
        elif prev_dir < 0 and close > fu:
            dir_ = 1.0

        self.lines.dir[0] = dir_
        self.lines.st[0] = fl if dir_ > 0 else fu


class SuperTrend(LongBracketMixin, bt.Strategy):
    """SuperTrend strategy (long-only).

    Important behavior choice (this was the main bug source):
    - If we ONLY enter on a "Down → Up" flip, then any stop/take exit during an uptrend
      would leave us flat until the next full flip cycle. That can create long periods of
      *zero trades* depending on params/window.

    This implementation supports two entry modes:
    - flip-only entry (classic): enter on Down→Up
    - re-entry-on-uptrend: if flat while dir==Up, allow re-entry when price crosses back
      above the supertrend line (useful when bracket exits occur)

    Exit:
    - Exit when direction flips to down (-1) OR close crosses below supertrend line OR time stop.

    Risk:
    - stop/take via LongBracketMixin.
    """

    params = dict(
        period=10,
        multiplier=3.0,
        stop_pct=0.010,
        take_pct=0.015,
        max_bars_hold=260,
        allow_reentry=True,
    )

    def __init__(self):
        self._reset_orders()
        self.entry_bar = None
        self.entry_price = None

        self.st = SuperTrendIndicator(self.data, period=self.p.period, multiplier=self.p.multiplier)

        # cross of direction: from -1 to +1 => entry, +1 to -1 => exit
        self._prev_dir = None

    def next(self):
        if self.order_entry or self.order_stop or self.order_take:
            return

        dir_ = float(self.st.dir[0])
        prev = self._prev_dir
        self._prev_dir = dir_

        st_line = float(self.st.st[0])
        close0 = float(self.data.close[0])

        # Previous bar values (guard for first bar)
        if len(self) >= 2:
            st_prev = float(self.st.st[-1])
            close_prev = float(self.data.close[-1])
        else:
            st_prev, close_prev = st_line, close0

        flipped_up = (prev is not None) and (prev < 0) and (dir_ > 0)
        flipped_down = (prev is not None) and (prev > 0) and (dir_ < 0)

        # --- entries ---
        if not self.position:
            # Classic entry: down->up flip
            if flipped_up:
                self.order_entry = self.buy()
                return

            # Optional re-entry: if we're in uptrend but got stopped/took profit, re-enter
            # when price reclaims the supertrend line (cross up).
            if bool(self.p.allow_reentry) and dir_ > 0:
                cross_up = (close_prev <= st_prev) and (close0 > st_line)
                if cross_up:
                    self.order_entry = self.buy()
            return

        # --- exits ---
        # Exit on downtrend flip
        if flipped_down or dir_ < 0:
            self._cancel_children()
            self.order_entry = self.close()
            return

        # Safety exit: if price closes below ST line while still marked uptrend
        if close0 < st_line:
            self._cancel_children()
            self.order_entry = self.close()
            return

        # Time stop
        if self.entry_bar is not None and (len(self) - self.entry_bar) >= int(self.p.max_bars_hold):
            self._cancel_children()
            self.order_entry = self.close()
            return
