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
    """Collect a flat list of completed trades with timestamps."""

    def start(self):
        self.trades: List[TradeRecord] = []

    def notify_trade(self, trade):
        if not trade.isclosed:
            return

        entry_dt = bt.num2date(trade.dtopen).isoformat()
        exit_dt = bt.num2date(trade.dtclose).isoformat()
        direction = 'LONG' if trade.size > 0 else 'SHORT'

        # backtrader trade.price is average entry price
        rec = TradeRecord(
            entry_dt=entry_dt,
            exit_dt=exit_dt,
            direction=direction,
            size=float(trade.size),
            entry_price=float(trade.price),
            exit_price=float(trade.price + (trade.pnl / trade.size) if trade.size else trade.price),
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

        pos = self.strategy.position
        cash = float(self._broker.getcash())

        if pos.size != 0:
            # Conservative mark at bar low/high depending on direction
            if pos.size > 0:
                px = float(self.strategy.data.low[0])
            else:
                px = float(self.strategy.data.high[0])
            intrabar_eq = cash + float(pos.size) * px
        else:
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
