from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Literal, Sequence

import backtrader as bt

from .rsi2 import RSI2Daytrade
from .bollinger_mr import BollingerMR
from .zscore_mr import ZScoreMR
from .vwap_mr import VWAPDeviationMR
from .pairs_mr import PairsZScoreMR


StrategyId = Literal[
    "rsi2",
    "bollinger_mr",
    "zscore_mr",
    "vwap_mr",
    "pairs_mr",
]


@dataclass
class StrategySpec:
    id: StrategyId
    name: str
    cls: type[bt.Strategy]
    # How many symbols/data feeds are required.
    n_legs: int = 1

    def default_param_grid(self) -> Dict[str, List[Any]]:
        """Return a default search space. Can be overridden per strategy."""
        raise NotImplementedError


class _RSI2(StrategySpec):
    def default_param_grid(self):
        return {
            "rsi_period": [2, 3, 4, 5],
            "entry_rsi": [10.0, 15.0, 20.0],
            "stop_pct": [0.004, 0.006, 0.008],
            "take_pct": [0.006, 0.009, 0.012],
            "max_bars_hold": [4, 8, 12],
        }


class _Boll(StrategySpec):
    def default_param_grid(self):
        return {
            "bb_period": [15, 20, 30],
            "bb_dev": [1.5, 2.0, 2.5],
            "stop_pct": [0.006, 0.008, 0.010],
            "take_pct": [0.008, 0.010, 0.012],
            "max_bars_hold": [12, 16, 24],
        }


class _Z(StrategySpec):
    def default_param_grid(self):
        return {
            "lookback": [30, 50, 80],
            "z_entry": [1.5, 2.0, 2.5],
            "z_exit": [0.0, 0.5],
            "stop_pct": [0.008, 0.010, 0.012],
            "take_pct": [0.010, 0.012, 0.015],
            "max_bars_hold": [12, 24, 36],
        }


class _VWAP(StrategySpec):
    def default_param_grid(self):
        return {
            "dev_entry": [0.004, 0.006, 0.008],
            "dev_exit": [0.0, 0.002],
            "stop_pct": [0.008, 0.010, 0.012],
            "take_pct": [0.008, 0.010, 0.012],
            "max_bars_hold": [8, 12, 16],
        }


class _Pairs(StrategySpec):
    def default_param_grid(self):
        return {
            "lookback": [30, 50, 80],
            "z_entry": [1.5, 2.0, 2.5],
            "z_exit": [0.25, 0.5, 0.75],
            "max_bars_hold": [24, 48, 72],
            "leg_value_frac": [0.35, 0.45],
        }


SPECS: Dict[str, StrategySpec] = {
    "rsi2": _RSI2(id="rsi2", name="RSI2 Mean Reversion", cls=RSI2Daytrade, n_legs=1),
    "bollinger_mr": _Boll(id="bollinger_mr", name="Bollinger Mean Reversion", cls=BollingerMR, n_legs=1),
    "zscore_mr": _Z(id="zscore_mr", name="Z-Score Mean Reversion", cls=ZScoreMR, n_legs=1),
    "vwap_mr": _VWAP(id="vwap_mr", name="VWAP Deviation Mean Reversion", cls=VWAPDeviationMR, n_legs=1),
    "pairs_mr": _Pairs(id="pairs_mr", name="Pairs Z-Score Mean Reversion", cls=PairsZScoreMR, n_legs=2),
}


def get_strategy_spec(strategy_id: str) -> StrategySpec:
    if strategy_id not in SPECS:
        raise KeyError(f"Unknown strategy_id={strategy_id}. Available: {sorted(SPECS.keys())}")
    return SPECS[strategy_id]
