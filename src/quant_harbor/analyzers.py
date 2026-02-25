from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any, Optional

import backtrader as bt


@dataclass
class TradeRecord:
    entry_dt: str
    exit_dt: str
    direction: str
    size: float
    entry_price: float
    exit_price: float
    pnl: float
    pnl_comm: float
    bar_len: int


class TradeListAnalyzer(bt.Analyzer):
    """Collect a flat list of completed trades with timestamps.

    Critical note:
    - In backtrader, `trade.size` is often **0** when `trade.isclosed` (position already flat).
      Therefore we must NOT infer direction or compute exit price from `trade.size` at close.

    Implementation:
    - On `trade.justopened`, cache entry size/price by `trade.ref`.
    - On `trade.isclosed`, use cached entry size/price to compute direction and exit price.

    This avoids the fatal mislabeling (LONG->SHORT) and exit_price fallback bugs.
    """

    def start(self):
        self.trades: List[TradeRecord] = []
        self._open: Dict[int, Dict[str, Any]] = {}

    def notify_trade(self, trade):
        # cache entry info when a trade is opened
        if getattr(trade, "justopened", False):
            self._open[int(trade.ref)] = {
                "size": float(trade.size),
                "price": float(trade.price),
                "dtopen": float(trade.dtopen),
            }
            return

        if not trade.isclosed:
            return

        entry_dt = bt.num2date(trade.dtopen).isoformat()
        exit_dt = bt.num2date(trade.dtclose).isoformat()

        cached = self._open.pop(int(trade.ref), None) or {}
        entry_size = float(cached.get("size", 0.0))
        entry_price = float(cached.get("price", trade.price))

        # Direction from entry size sign; fall back to LONG if unknown.
        direction = "LONG" if entry_size >= 0 else "SHORT"

        # Compute exit price from PnL and entry size if possible.
        exit_price = float(entry_price)
        if entry_size != 0:
            exit_price = float(entry_price + (float(trade.pnl) / entry_size))

        rec = TradeRecord(
            entry_dt=entry_dt,
            exit_dt=exit_dt,
            direction=direction,
            size=float(abs(entry_size)) if entry_size != 0 else float(abs(trade.size)),
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            pnl=float(trade.pnl),
            pnl_comm=float(trade.pnlcomm),
            bar_len=int(trade.barlen),
        )
        self.trades.append(rec)

    def get_analysis(self):
        return [t.__dict__ for t in self.trades]


class EquityCurveAnalyzer(bt.Analyzer):
    """Record equity curve at bar close, and a conservative intrabar equity min.

    For long positions, intrabar min is marked at bar low.
    For flat, intrabar min equals close equity.

    Note: this is an approximation; it ignores microstructure and assumes instantaneous marking.
    """

    def start(self):
        self.rows: List[Dict[str, Any]] = []
        self._broker = self.strategy.broker

    def next(self):
        dt = self.strategy.data.datetime.datetime(0).isoformat()
        close_eq = float(self._broker.getvalue())

        cash = float(self._broker.getcash())

        # Approx intrabar worst-case equity across all data feeds.
        # For each leg: mark to low if long, high if short.
        intrabar_eq = cash
        for d in self.strategy.datas:
            pos = self.strategy.getposition(d)
            if pos.size == 0:
                continue
            if pos.size > 0:
                px = float(d.low[0])
            else:
                px = float(d.high[0])
            intrabar_eq += float(pos.size) * px

        # If no positions, intrabar_eq == cash; ensure we don't exceed close equity.
        if all(self.strategy.getposition(d).size == 0 for d in self.strategy.datas):
            intrabar_eq = close_eq

        self.rows.append(
            {
                'dt': dt,
                'equity_close': close_eq,
                'equity_intrabar_min': float(min(close_eq, intrabar_eq)),
                'cash': cash,
                'pos_size': float(pos.size),
                'close': float(self.strategy.data.close[0]),
            }
        )

    def get_analysis(self):
        return self.rows
