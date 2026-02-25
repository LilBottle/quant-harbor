from __future__ import annotations

import math
from typing import Optional


def deflated_sharpe_ratio(
    sharpe: float,
    n_trials: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    sr_ref: float = 0.0,
) -> Optional[float]:
    """Compute an approximate Deflated Sharpe Ratio (DSR).

    This is a pragmatic implementation inspired by Bailey & López de Prado.

    Purpose:
    - Penalize Sharpe that may be due to multiple testing / selection bias.

    Inputs:
    - sharpe: observed Sharpe ratio (annualized)
    - n_trials: number of independent trials (candidate strategies/params tested)
    - skew: return skewness (0 if unknown)
    - kurtosis: return kurtosis (use 3.0 for normal)
    - sr_ref: reference Sharpe threshold (default 0)

    Output:
    - DSR in [0,1] as a p-value-like confidence (higher is better)

    Notes:
    - If inputs are insufficient (n_trials<2) returns None.
    - This is an approximation; for institutional use, validate against a dedicated implementation.
    """

    try:
        sr = float(sharpe)
        n = int(n_trials)
        if n < 2:
            return None

        # Expected max SR under multiple testing (approx for normal IID)
        # E[max] ~ sqrt(2*log(n))
        emax = math.sqrt(2.0 * math.log(float(n)))

        # Std dev of SR estimate with non-normal correction
        # var(SR) ≈ (1 - skew*SR + ((kurtosis-1)/4)*SR^2) / (T-1)
        # Here we don't have T robustly; we approximate using a conservative denom.
        # Treat as scale factor on uncertainty.
        denom = 252.0  # conservative proxy (1 trading year of daily obs)
        var = (1.0 - float(skew) * sr + ((float(kurtosis) - 1.0) / 4.0) * (sr ** 2)) / max(denom - 1.0, 1.0)
        sd = math.sqrt(max(var, 1e-12))

        # z-score vs deflated threshold
        # threshold = sr_ref + emax*sd
        thresh = float(sr_ref) + emax * sd
        z = (sr - thresh) / sd

        # Convert z to CDF (normal)
        # Phi(z) = 0.5*(1+erf(z/sqrt(2)))
        dsr = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        return float(max(0.0, min(1.0, dsr)))
    except Exception:
        return None
