"""gamma x phi x inventory-limit sensitivity grid (`mm sweep`, R164-R166).

A pure grid-iteration utility: running one grid point means running a
full day-simulation, which this module doesn't own (that's the CLI's
simulation-runner glue) — it takes a `run_one` callable instead, so this
module has no dependency on the orchestrator.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(frozen=True, slots=True)
class SweepPoint:
    gamma: float
    phi: float
    inventory_limit_eur: float
    pnl_eur: float
    sharpe: float
    max_abs_inventory_eur: float
    n_compliance_breaches: int


def run_sweep(
    gammas: list[float],
    phis: list[float],
    inventory_limits_eur: list[float],
    run_one: Callable[[float, float, float], SweepPoint],
) -> list[SweepPoint]:
    return [
        run_one(gamma, phi, limit)
        for gamma in gammas
        for phi in phis
        for limit in inventory_limits_eur
    ]


def sweep_to_frame(points: list[SweepPoint]) -> pd.DataFrame:
    return pd.DataFrame([asdict(p) for p in points])
