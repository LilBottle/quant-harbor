from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any, Callable, Tuple

import pandas as pd


@dataclass
class WfaWindow:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp


def make_quarterly_wfa_windows(index_utc: pd.DatetimeIndex, train_months: int = 12, oos_months: int = 3) -> List[WfaWindow]:
    """Create rolling walk-forward windows inside a given time span.

    - Each step advances by oos_months.
    - train window length fixed.

    Index must be tz-aware UTC.
    """
    if index_utc.tz is None:
        raise ValueError('index must be tz-aware UTC')

    start = index_utc.min()
    end = index_utc.max()

    windows: List[WfaWindow] = []

    oos_start = start + pd.DateOffset(months=train_months)
    while True:
        train_start = oos_start - pd.DateOffset(months=train_months)
        train_end = oos_start - pd.Timedelta(seconds=1)
        oos_end = oos_start + pd.DateOffset(months=oos_months) - pd.Timedelta(seconds=1)

        if oos_end > end:
            break

        windows.append(
            WfaWindow(
                train_start=train_start,
                train_end=train_end,
                oos_start=oos_start,
                oos_end=oos_end,
            )
        )

        oos_start = oos_start + pd.DateOffset(months=oos_months)

    return windows
