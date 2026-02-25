from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Tuple

import pandas as pd


@dataclass
class SplitResult:
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame
    cut_test_start_utc: pd.Timestamp


def split_train_val_test_last12m(df_utc: pd.DataFrame) -> SplitResult:
    """Time-series split with last 12 months as Test.

    Assumes df index is tz-aware UTC timestamps.
    - test: [cut, end]
    - train+val: [start, cut)

    Within train+val, split 80/20 by time.
    """
    if df_utc.index.tz is None:
        raise ValueError('df must have tz-aware UTC index')

    end = df_utc.index.max()
    cut = end - pd.DateOffset(months=12)

    test = df_utc[df_utc.index >= cut]
    pre = df_utc[df_utc.index < cut]

    if len(pre) == 0:
        raise ValueError('not enough history before test window')

    # train/val split by time 80/20
    split_point = pre.index.min() + (pre.index.max() - pre.index.min()) * 0.8

    train = pre[pre.index <= split_point]
    val = pre[pre.index > split_point]

    return SplitResult(train=train, val=val, test=test, cut_test_start_utc=cut)
