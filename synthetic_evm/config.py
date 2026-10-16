"""Configuration and scenario assumptions for the synthetic EVM generator.

This module contains *assumptions*, not simulation mechanics. Keeping scenario
settings here makes it easy to tune the portfolio without touching the code
that converts those assumptions into PV/EV/AC.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple


# Default project shape: one $100M project, 60 months, 10 WBS elements. These
# are the defaults of ``generate_portfolio`` and are not scenario-specific.
DEFAULT_TOTAL_BAC = 100_000_000.0
DEFAULT_PLAN_MONTHS = 60
DEFAULT_N_WBS = 10

# How project BAC is split across WBS elements (see ``engine.partition_bac``).
# ``generate_portfolio`` cycles through these in order by project, so a default
# eight-scenario portfolio exercises each one twice.
PARTITION_METHODS = ("balanced", "moderate", "concentrated", "dominant_wbs")

# Shape of a WP's planned resource consumption over its duration (see
# ``engine._spread_weights``). Chosen uniformly at random per WP.
SPREAD_PROFILES = ("uniform", "front_loaded", "back_loaded", "middle_peak", "two_peak")

# Planned WP duration distribution, in months. It is centered around roughly
# seven months (mean about 6.9), with a small population of short WPs retained
# so 0/100 and 50/50 techniques still occur occasionally. Longer WPs naturally
# fall to weighted-milestone, percent-complete, or LOE EVTs.
#
# WP_DURATION_PROBS pairs element-for-element with WP_DURATION_MONTHS and must
# have the same length; the engine normalizes it, but keep it summing to 1.0.
WP_DURATION_MONTHS = tuple(range(1, 13))
WP_DURATION_PROBS = (
    0.01,  # 1 month
    0.02,  # 2 months
    0.04,  # 3 months
    0.08,  # 4 months
    0.12,  # 5 months
    0.17,  # 6 months
    0.17,  # 7 months
    0.14,  # 8 months
    0.10,  # 9 months
    0.07,  # 10 months
    0.05,  # 11 months
    0.03,  # 12 months
)

# Shape parameters used to create right-tailed schedule slippage. Beta(a, b)
# with a < b puts most observations near the low end while preserving a thinner
# but materially longer right tail. The resulting g(t) population therefore has
# lots of modest deviations and occasional ugly ones rather than uniform delay.
#
# Each draw is ``low + g_tail_scale * span * Beta(a, b)`` (see
# ``engine._right_tail_draw``), in units of planned WP duration: 0.25 means a
# quarter of the planned length. ``low`` is the minimum effect, ``span`` the
# width of the beta's range at ``g_tail_scale = 1``. The engine also caps each
# kind (``minor_delay`` 0.75, ``slow_slip`` and ``recover_early_slip`` 1.00,
# ``stall_length`` 0.90) so a heavy tail cannot produce an absurd WP.
G_RIGHT_TAIL = {
    "minor_delay": {"low": 0.04, "span": 0.46, "a": 1.25, "b": 3.20},
    "slow_slip": {"low": 0.08, "span": 0.70, "a": 1.25, "b": 2.80},
    "recover_early_slip": {"low": 0.10, "span": 0.70, "a": 1.30, "b": 2.80},
    "stall_length": {"low": 0.08, "span": 0.55, "a": 1.20, "b": 2.60},
}

# Latent WBS schedule-logic archetypes. These do not appear in the public output.
# A relationship is simple zero-lag Finish-to-Start (FS): a successor cannot
# begin actual technical progress until its predecessor has actually completed.
#
# ``parallel`` preserves the original behavior with no explicit WP logic.
# ``mixed_fs`` links a moderate share of eligible WPs.
# ``fs_heavy`` links most WPs for which a sensible planned predecessor exists.
WBS_LOGIC_PROFILES = {
    "parallel": {"weight": 0.35, "link_probability": 0.00},
    "mixed_fs": {"weight": 0.45, "link_probability": 0.50},
    "fs_heavy": {"weight": 0.20, "link_probability": 0.85},
}


@dataclass(frozen=True)
class ScenarioConfig:
    """Parameters controlling schedule and cost behavior for one scenario.

    g_probs
        Probability weights for WP-level actual-progress time warps. Keys must
        be exactly the six kinds ``on_time``, ``minor_delay``, ``slow``,
        ``ahead``, ``recover`` and ``stall`` (see ``engine._choose_g_profile``);
        values are normalized, so they need not sum to 1.
    g_tail_scale
        Multiplier on right-tail schedule-slippage magnitude. Values above 1
        make delay/stall tails more severe without changing the scenario's
        categorical mix of g(t) types.
    cost_mean / cost_sd
        Distribution of persistent WP cost efficiency. A factor above 1.0
        means the same underlying work consumes more actual cost than budget.
    shock_events / shock_range
        Sparse project-wide temporary cost pressure, e.g. overtime or supplier
        price effects. ``shock_events`` is the Poisson mean event count.
    drift_mean / drift_sd
        Gradual project-wide cost-factor drift over the project's reporting life.
    rework_prob / rework_range
        Probability and BAC-relative magnitude of cost-only rework at a WP.
    recovery_premium
        Incremental cost pressure when technical progress is accelerated.
    """

    g_probs: Mapping[str, float]
    g_tail_scale: float
    cost_mean: float
    cost_sd: float
    shock_events: float
    shock_range: Tuple[float, float]
    drift_mean: float
    drift_sd: float
    rework_prob: float
    rework_range: Tuple[float, float]
    recovery_premium: float


# Named scenarios, in generation order. Each becomes one project; the project's
# ``scenario`` column carries the key. Tune a scenario by editing its values
# here; add one by adding an entry. A scenario's random stream depends on its
# *name* and the master seed, not its position, so adding scenarios does not
# change the others' results (project_id numbering does shift).
SCENARIOS = {
    "nominal": ScenarioConfig(
        g_probs={
            "on_time": 0.55,
            "minor_delay": 0.16,
            "slow": 0.08,
            "ahead": 0.08,
            "recover": 0.08,
            "stall": 0.05,
        },
        g_tail_scale=0.85,
        cost_mean=1.00,
        cost_sd=0.045,
        shock_events=1.0,
        shock_range=(0.03, 0.10),
        drift_mean=0.00,
        drift_sd=0.025,
        rework_prob=0.04,
        rework_range=(0.005, 0.020),
        recovery_premium=0.03,
    ),
    "schedule_slip": ScenarioConfig(
        g_probs={
            "on_time": 0.15,
            "minor_delay": 0.26,
            "slow": 0.25,
            "ahead": 0.02,
            "recover": 0.15,
            "stall": 0.17,
        },
        g_tail_scale=1.25,
        cost_mean=1.03,
        cost_sd=0.055,
        shock_events=1.5,
        shock_range=(0.04, 0.13),
        drift_mean=0.025,
        drift_sd=0.030,
        rework_prob=0.06,
        rework_range=(0.008, 0.025),
        recovery_premium=0.05,
    ),
    "cost_overrun": ScenarioConfig(
        g_probs={
            "on_time": 0.48,
            "minor_delay": 0.16,
            "slow": 0.08,
            "ahead": 0.08,
            "recover": 0.12,
            "stall": 0.08,
        },
        g_tail_scale=0.90,
        cost_mean=1.14,
        cost_sd=0.070,
        shock_events=2.0,
        shock_range=(0.05, 0.18),
        drift_mean=0.055,
        drift_sd=0.030,
        rework_prob=0.10,
        rework_range=(0.010, 0.035),
        recovery_premium=0.05,
    ),
    "combined_overrun": ScenarioConfig(
        g_probs={
            "on_time": 0.10,
            "minor_delay": 0.22,
            "slow": 0.25,
            "ahead": 0.01,
            "recover": 0.18,
            "stall": 0.24,
        },
        g_tail_scale=1.35,
        cost_mean=1.16,
        cost_sd=0.080,
        shock_events=2.5,
        shock_range=(0.06, 0.22),
        drift_mean=0.070,
        drift_sd=0.035,
        rework_prob=0.13,
        rework_range=(0.012, 0.045),
        recovery_premium=0.08,
    ),
    "early_delay_recovery": ScenarioConfig(
        g_probs={
            "on_time": 0.10,
            "minor_delay": 0.15,
            "slow": 0.08,
            "ahead": 0.03,
            "recover": 0.48,
            "stall": 0.16,
        },
        g_tail_scale=1.15,
        cost_mean=1.06,
        cost_sd=0.055,
        shock_events=1.5,
        shock_range=(0.04, 0.14),
        drift_mean=0.020,
        drift_sd=0.025,
        rework_prob=0.07,
        rework_range=(0.008, 0.028),
        recovery_premium=0.10,
    ),
    "ahead_of_plan": ScenarioConfig(
        g_probs={
            "on_time": 0.28,
            "minor_delay": 0.05,
            "slow": 0.03,
            "ahead": 0.48,
            "recover": 0.12,
            "stall": 0.04,
        },
        g_tail_scale=0.60,
        cost_mean=1.01,
        cost_sd=0.050,
        shock_events=1.0,
        shock_range=(0.03, 0.10),
        drift_mean=0.005,
        drift_sd=0.025,
        rework_prob=0.04,
        rework_range=(0.005, 0.018),
        recovery_premium=0.06,
    ),
    "volatile_execution": ScenarioConfig(
        g_probs={
            "on_time": 0.12,
            "minor_delay": 0.18,
            "slow": 0.14,
            "ahead": 0.12,
            "recover": 0.22,
            "stall": 0.22,
        },
        g_tail_scale=1.30,
        cost_mean=1.06,
        cost_sd=0.095,
        shock_events=3.0,
        shock_range=(0.06, 0.24),
        drift_mean=0.025,
        drift_sd=0.055,
        rework_prob=0.15,
        rework_range=(0.010, 0.050),
        recovery_premium=0.08,
    ),
    "efficient_execution": ScenarioConfig(
        g_probs={
            "on_time": 0.40,
            "minor_delay": 0.08,
            "slow": 0.04,
            "ahead": 0.28,
            "recover": 0.16,
            "stall": 0.04,
        },
        g_tail_scale=0.70,
        cost_mean=0.92,
        cost_sd=0.045,
        shock_events=0.7,
        shock_range=(0.02, 0.08),
        drift_mean=-0.025,
        drift_sd=0.020,
        rework_prob=0.025,
        rework_range=(0.004, 0.015),
        recovery_premium=0.03,
    ),
}
