from __future__ import annotations

import math

import backtrader as bt


class RiskStopPctSizer(bt.Sizer):
    """Position sizing from fixed account risk and stop distance.

    Goal:
      - Risk at most `risk_pct` of current account equity per trade.
      - Stop distance is approximated from strategy parameter `stop_pct`.
        (Many Quant Harbor strategies define stop as entry_price * (1-stop_pct)).

    Size calculation (long-only):
      risk_budget = account_value * risk_pct
      risk_per_share = price * stop_pct
      size = floor(risk_budget / risk_per_share)

    Caps:
      - cannot spend more than `max_cash_pct` of available cash
      - min size is 0 (skip) or 1 depending on settings

    Notes:
      - This is an approximation: actual fills, gaps, and intrabar stop behavior can differ.
      - For strategies without stop_pct, size falls back to 1.
    """

    params = dict(
        risk_pct=0.01,       # 1% equity risk per trade
        max_cash_pct=0.95,   # don't deploy 100% cash by default
        min_size=1,
        max_size=None,       # optional hard cap
    )

    def _getsizing(self, comminfo, cash, data, isbuy):
        if not isbuy:
            return 0

        strat = self.strategy
        price = float(getattr(data, 'close')[0])
        if not price or not math.isfinite(price) or price <= 0:
            return 0

        stop_pct = float(getattr(getattr(strat, 'p', None), 'stop_pct', 0.0) or 0.0)
        if not math.isfinite(stop_pct) or stop_pct <= 0:
            return int(self.p.min_size)

        value = float(strat.broker.getvalue())
        risk_budget = value * float(self.p.risk_pct)
        risk_per_share = price * stop_pct
        if risk_per_share <= 0:
            return int(self.p.min_size)

        size_risk = math.floor(risk_budget / risk_per_share)

        # Cash cap
        max_spend = float(cash) * float(self.p.max_cash_pct)
        size_cash = math.floor(max_spend / price)

        size = int(max(0, min(size_risk, size_cash)))

        if self.p.max_size is not None:
            size = int(min(size, int(self.p.max_size)))

        if size <= 0:
            return 0
        return max(int(self.p.min_size), size)
