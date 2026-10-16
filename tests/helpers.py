"""Constants and builders shared by the test modules."""

from __future__ import annotations

import numpy as np

from synthetic_evm.engine import WorkPackagePlan

# The default master seed of generate_portfolio and generate_evm.py.
DEFAULT_SEED = 20260922

# The schedule time-warp kinds that every scenario's ``g_probs`` must name.
G_KINDS = ("on_time", "minor_delay", "slow", "ahead", "recover", "stall")


def make_wp(
    *,
    bac: float = 1000.0,
    planned_start: int = 0,
    duration: int = 4,
    evt: str = "percent_complete",
    g_kind: str = "on_time",
    g_params: dict | None = None,
    cost_factor: float = 1.0,
    actual_start: int | None = None,
    predecessor_idx: int | None = None,
) -> WorkPackagePlan:
    """Build a work package whose resources are consumed uniformly.

    Earned value follows the resources, except 0/100 (everything in the last
    month) and 50/50 (half in the first month, half in the last).
    """
    resource = np.full(duration, 1.0 / duration)
    pv = resource.copy()
    if evt == "0_100":
        pv = np.zeros(duration)
        pv[-1] = 1.0
    elif evt == "50_50":
        pv = np.zeros(duration)
        pv[0] = 0.5
        pv[-1] += 0.5
    return WorkPackagePlan(
        bac=bac,
        planned_start=planned_start,
        duration=duration,
        evt=evt,
        resource_weights=resource,
        pv_weights=pv,
        g_kind=g_kind,
        g_params=g_params or {},
        cost_factor=cost_factor,
        predecessor_idx=predecessor_idx,
        actual_start=actual_start,
    )


class ClosestPredecessorRng:
    """Generator stand-in that makes the FS assignment deterministic.

    ``random()`` is 0, so every eligible successor is linked, and ``choice``
    picks the most probable candidate, i.e. the predecessor with the smallest
    gap to the successor's planned start.
    """

    def random(self) -> float:
        return 0.0

    def choice(self, candidates, p=None):
        return candidates[int(np.argmax(p))]
