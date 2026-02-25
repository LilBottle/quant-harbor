from __future__ import annotations

import backtrader as bt


class LongBracketMixin:
    """Helper mixin for long-only strategies using post-fill bracket exits.

    Pattern:
    - Submit a BUY market order for entry.
    - On buy fill, submit two SELL children:
      - stop (Stop)
      - take (Limit)
    - On any exit fill, cancel the other child.

    This is more realistic than 'if close crosses stop/take then close()' because:
    - stop/take triggers are evaluated on intrabar high/low (bar-based approximation)
    - order types are explicit in the backtest artifacts

    Note: backtrader's exact fill semantics still depend on broker settings.
    """

    def _reset_orders(self):
        self.order_entry = None
        self.order_stop = None
        self.order_take = None

    def _cancel_children(self):
        for o in [getattr(self, "order_stop", None), getattr(self, "order_take", None)]:
            if o is not None:
                try:
                    self.cancel(o)
                except Exception:
                    pass
        self.order_stop = None
        self.order_take = None

    def _submit_children(self, entry_price: float, stop_pct: float, take_pct: float):
        stop_price = entry_price * (1.0 - float(stop_pct))
        take_price = entry_price * (1.0 + float(take_pct))

        # Use explicit order types.
        self.order_stop = self.sell(exectype=bt.Order.Stop, price=stop_price)
        self.order_take = self.sell(exectype=bt.Order.Limit, price=take_price)

    def notify_order(self, order):
        # allow strategy to call super().notify_order
        if order.status in [order.Submitted, order.Accepted]:
            return

        if order.status in [order.Canceled, order.Margin, order.Rejected]:
            # clear references if needed
            if order is getattr(self, "order_entry", None):
                self.order_entry = None
            if order is getattr(self, "order_stop", None):
                self.order_stop = None
            if order is getattr(self, "order_take", None):
                self.order_take = None
            return

        if order.status in [order.Completed]:
            # Entry fill
            if order.isbuy():
                self.entry_bar = len(self)
                self.entry_price = float(order.executed.price)
                self.order_entry = None
                self._submit_children(self.entry_price, self.p.stop_pct, self.p.take_pct)
                return

            # Exit fill
            if order.issell():
                # cancel the other child
                if order is getattr(self, "order_stop", None):
                    self.order_stop = None
                    self._cancel_children()
                elif order is getattr(self, "order_take", None):
                    self.order_take = None
                    self._cancel_children()
                else:
                    # manual close
                    self._cancel_children()

                self.entry_bar = None
                self.entry_price = None
                return
