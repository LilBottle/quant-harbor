from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Dict, List, Tuple


@dataclass
class BasinConfig:
    """Generic parameter-basin perturbation policy.

    - float params: multiplicative +/- pct_steps
    - int params: additive +/- int_steps
    - discrete_values: per-parameter explicit candidate values (always include base)

    This design makes the evaluation system strategy-agnostic: new strategies can be
    evaluated without changing basin logic, by supplying base params + (optional)
    discrete_values in the basin config.
    """

    pct_steps: Tuple[float, ...] = (0.05, 0.10, 0.20)
    int_steps: Tuple[int, ...] = (1, 2, 4)
    discrete_values: Dict[str, Tuple[Any, ...]] | None = None


def _pct_perturb(base: float, pct: float) -> List[float]:
    return [base * (1.0 - pct), base * (1.0 + pct)]


def make_basin_params(base: Dict[str, Any], cfg: BasinConfig) -> List[Dict[str, Any]]:
    """Create a perturbation grid around a base parameter point.

    Returns a list of params dicts. Includes the base point.

    Heuristics:
    - If a param is listed in cfg.discrete_values: use that discrete set (plus base).
    - Else if base value is int-like: use +/- int_steps.
    - Else if base value is float-like: use +/- pct_steps.

    Safety:
    - Values are de-duped by exact tuple of (sorted items).
    """

    discrete = cfg.discrete_values or {}

    # Build per-key value sets
    key_vals: Dict[str, List[Any]] = {}
    for k, v0 in base.items():
        vals = set([v0])
        if k in discrete:
            for x in discrete[k]:
                vals.add(x)
        else:
            if isinstance(v0, bool):
                vals.add(v0)
            elif isinstance(v0, int):
                for d in cfg.int_steps:
                    vals.add(max(1, int(v0) - int(d)))
                    vals.add(max(1, int(v0) + int(d)))
            elif isinstance(v0, float):
                for p in cfg.pct_steps:
                    vals.update(_pct_perturb(float(v0), float(p)))
            else:
                # unknown type: keep base only
                vals.add(v0)

        key_vals[k] = list(vals)

    keys = list(key_vals.keys())
    grid = []

    for combo in product(*[sorted(key_vals[k], key=lambda x: str(x)) for k in keys]):
        p = dict(zip(keys, combo))

        # Basic sanity clamps for common finance params
        if "entry_rsi" in p:
            try:
                er = float(p["entry_rsi"])
                if er <= 0 or er >= 100:
                    continue
            except Exception:
                pass
        for kk in ["stop_pct", "take_pct"]:
            if kk in p:
                try:
                    if float(p[kk]) <= 0:
                        continue
                except Exception:
                    pass

        grid.append(p)

    # de-dup
    uniq = {}
    for p in grid:
        k = tuple(sorted(p.items(), key=lambda kv: kv[0]))
        uniq[k] = p

    return list(uniq.values())


# Backward compatibility

def make_rsi2_basin_params(base: Dict[str, Any], cfg: BasinConfig) -> List[Dict[str, Any]]:
    return make_basin_params(base, cfg)
