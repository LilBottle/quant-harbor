from __future__ import annotations

import math

import backtrader as bt

from ._bracket_mixin import LongBracketMixin
from .supertrend import SuperTrendIndicator


class SuperTrendDailyRSI2(LongBracketMixin, bt.Strategy):
    """Daily SuperTrend regime + intraday RSI2 mean-reversion entries.
    
    V2 PRODUCTION VERSION:
    - No Lookahead: Uses Daily SuperTrend dir[-1] (confirmed yesterday).
    - ROC Filter: Ensures price drop magnitude before entry.
    - Two-stage Time Stop: Cut losers fast (72b), let winners run (216b).
    - ATR Trailing: Dynamic profit protection based on market volatility.
    """

    params = dict(
        # Daily SuperTrend (Lookback)
        st_period=14,
        st_multiplier=3.0,

        # Intraday RSI2
        rsi_period=2,
        entry_rsi=15.0,

        # Entry Filters
        roc_period=4,
        roc_entry_th=0.0, # e.g. -0.005

        # Risk & Time
        stop_pct=0.008,
        take_pct=0.0,
        max_bars_hold=72,        # Stage 1: Soft time stop if pnl <= 0
        max_bars_hold_max=216,   # Stage 2: Hard max hold time

        # ATR Trailing Stop
        atr_period=14,
        atr_mult=2.5,            # Stop distance = atr_mult * ATR
        enable_ma_profit_trail=False,
        ma_period=5,
        trail_pnl_th=0.008,      # Activation profit threshold
        trail_pct=0.0,           # Fixed trail (fallback if atr_mult is 0)

        # Config
        enter_on_start=True,
    )

    def __init__(self):
        self._reset_orders()
        self.entry_bar = None
        self.entry_price = None
        self._prev_daily_dir = None

        if len(self.datas) < 2:
            raise RuntimeError("SuperTrendDailyRSI2 requires 2 data feeds: intraday (data0) and daily (data1).")

        self.data0 = self.datas[0]
        self.data1 = self.datas[1]  # daily

        self.rsi = bt.indicators.RSI(self.data0.close, period=self.p.rsi_period)
        self.sma = bt.indicators.SMA(self.data0.close, period=int(self.p.ma_period))
        self.st_d = SuperTrendIndicator(self.data1, period=self.p.st_period, multiplier=self.p.st_multiplier)
        self.roc = bt.indicators.ROC(self.data0.close, period=int(self.p.roc_period))
        self.atr = bt.indicators.ATR(self.data0, period=int(self.p.atr_period))

        self._profit_trail_active = False
        self._highest_high = 0.0
        self._trail_stop_price = 0.0
        self._trail_start_bar = -1

    def notify_order(self, order):
        super().notify_order(order)
        if order.status == order.Completed and order.issell():
            self._profit_trail_active = False
            self._highest_high = 0.0
            self._trail_stop_price = 0.0
            self._trail_start_bar = -1

    def next(self):
        if self.order_entry is not None:
            return

        # --- 1. REGIME GUARD (NO LOOKAHEAD: USE dir[-1]) ---
        # Backtrader: self.st_d.dir[-1] is the value from the PREVIOUS daily bar.
        if len(self.st_d.dir) < 2:
            return
        
        ddir = float(self.st_d.dir[-1])
        daily_up = ddir > 0
        
        # Trend Flip Logic (Yesterday was UP, now Yesterday is DOWN)
        # Note: In backtrader next(), [-1] always refers to the previous point in time.
        prev_ddir = float(self.st_d.dir[-2]) if len(self.st_d.dir) >= 3 else ddir
        flipped_down = (prev_ddir > 0) and (ddir < 0)

        if self.position and (flipped_down or (not daily_up)):
            self._cancel_children()
            self.order_entry = self.close()
            try: self.order_entry.addinfo(exit_reason="regime_flip_down")
            except: pass
            return

        # --- 2. TWO-STAGE TIME STOP ---
        if self.entry_bar is not None:
            hold_bars = len(self) - self.entry_bar
            c0 = float(self.data0.close[0])
            pnl = (c0 / self.entry_price) - 1.0 if self.entry_price else 0.0

            # Cut losers fast
            if hold_bars >= int(self.p.max_bars_hold) and pnl <= 0:
                self._cancel_children()
                self.order_entry = self.close()
                try: self.order_entry.addinfo(exit_reason="time_stop_loss")
                except: pass
                return

            # Hard max time
            if hold_bars >= int(self.p.max_bars_hold_max):
                self._cancel_children()
                self.order_entry = self.close()
                try: self.order_entry.addinfo(exit_reason="time_stop_max")
                except: pass
                return

        # --- 3. ENTRIES (WITH ROC FILTER) ---
        if not self.position:
            if not daily_up:
                return
            
            rsi0 = float(self.rsi[0])
            roc0 = float(self.roc[0])
            if math.isnan(rsi0) or math.isnan(roc0):
                return

            if rsi0 <= float(self.p.entry_rsi) and roc0 <= float(self.p.roc_entry_th):
                self.order_entry = self.buy()
            return

        # --- 4. PROFIT TRAIL ACTIVATION ---
        if bool(self.p.enable_ma_profit_trail) and (not self._profit_trail_active):
            c0 = float(self.data0.close[0])
            sma0 = float(self.sma[0])
            pnl = (c0 / self.entry_price) - 1.0 if self.entry_price else 0.0

            if c0 > sma0 and pnl >= float(self.p.trail_pnl_th):
                self._profit_trail_active = True
                self._trail_start_bar = len(self)
                self._highest_high = float(self.data0.high[0])
                
                # Initial Trail Price calculation (ATR vs % Fixed)
                h0 = self._highest_high
                if float(self.p.atr_mult) > 0:
                    dist = float(self.p.atr_mult) * float(self.atr[0])
                    self._trail_stop_price = h0 - dist
                else:
                    self._trail_stop_price = h0 * (1.0 - float(self.p.trail_pct))
                
                # Safety: Trail stop cannot be worse than the current fixed catastrophic stop
                # (Entry * (1-stop_pct))
                hard_stop = self.entry_price * (1.0 - float(self.p.stop_pct))
                self._trail_stop_price = max(self._trail_stop_price, hard_stop)

        # --- 5. MANUAL TRAILING STOP (HIGH PRECISION) ---
        if self._profit_trail_active:
            h0 = float(self.data0.high[0])
            if h0 > self._highest_high:
                self._highest_high = h0
                if float(self.p.atr_mult) > 0:
                    dist = float(self.p.atr_mult) * float(self.atr[0])
                    new_stop = h0 - dist
                else:
                    new_stop = h0 * (1.0 - float(self.p.trail_pct))
                
                # Stop price only goes UP
                self._trail_stop_price = max(self._trail_stop_price, new_stop)
            
            l0 = float(self.data0.low[0])
            if len(self) > self._trail_start_bar and l0 <= self._trail_stop_price:
                self._cancel_children()
                self.order_entry = self.close()
                try: self.order_entry.addinfo(exit_reason="profit_trail")
                except: pass
                return
            return
