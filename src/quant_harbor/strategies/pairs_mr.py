from __future__ import annotations

import backtrader as bt


class PairsZScoreMR(bt.Strategy):
    """Pairs trading on ratio z-score (market-neutral template).

    Data requirements:
    - data0: leg A (e.g., QQQ)
    - data1: leg B (e.g., SPY)

    Signal:
    - ratio = A_close / B_close
    - z = (ratio - SMA(ratio)) / Std(ratio)

    Positions:
    - If z <= -z_entry: long ratio (long A, short B)
    - If z >= +z_entry: short ratio (short A, long B)
    - Exit when |z| <= z_exit OR max_bars_hold

    Notes:
    - This template assumes shorting is allowed. If you run in a cash-only account,
      you should restrict to long-only variants (e.g., trade one leg hedged with options).
    """

    params = dict(
        lookback=50,
        z_entry=2.0,
        z_exit=0.5,
        max_bars_hold=48,
        # position sizing (fraction of equity allocated to each leg notionally)
        leg_value_frac=0.45,
        min_std=1e-8,
    )

    def __init__(self):
        if len(self.datas) < 2:
            raise ValueError('PairsZScoreMR requires 2 data feeds')

        a = self.datas[0]
        b = self.datas[1]

        self.ratio = a.close / b.close
        sma = bt.indicators.SMA(self.ratio, period=self.p.lookback)
        std = bt.indicators.StdDev(self.ratio, period=self.p.lookback)
        self.z = (self.ratio - sma) / (std + self.p.min_std)

        self.order = None
        self.entry_bar = None
        self.side = 0  # +1 long ratio, -1 short ratio

    def next(self):
        if self.order:
            return

        # Exit logic
        if self.side != 0:
            if abs(float(self.z[0])) <= float(self.p.z_exit):
                self.order = self._close_pair(); return
            if self.entry_bar is not None and (len(self) - self.entry_bar) >= self.p.max_bars_hold:
                self.order = self._close_pair(); return

        if self.side == 0:
            if float(self.z[0]) <= -abs(self.p.z_entry):
                self.order = self._open_pair(+1); return
            if float(self.z[0]) >= abs(self.p.z_entry):
                self.order = self._open_pair(-1); return

    def _open_pair(self, side: int):
        a = self.datas[0]
        b = self.datas[1]

        value = float(self.broker.getvalue())
        leg_value = value * float(self.p.leg_value_frac)

        a_px = float(a.close[0])
        b_px = float(b.close[0])
        if a_px <= 0 or b_px <= 0:
            return None

        a_size = int(leg_value / a_px)
        b_size = int(leg_value / b_px)
        if a_size <= 0 or b_size <= 0:
            return None

        self.entry_bar = len(self)
        self.side = int(side)

        if side > 0:
            # long A, short B
            self.buy(data=a, size=a_size)
            self.sell(data=b, size=b_size)
        else:
            # short A, long B
            self.sell(data=a, size=a_size)
            self.buy(data=b, size=b_size)
        return None

    def _close_pair(self):
        a = self.datas[0]
        b = self.datas[1]
        self.entry_bar = None
        self.side = 0
        # close both legs
        self.close(data=a)
        self.close(data=b)
        return None
