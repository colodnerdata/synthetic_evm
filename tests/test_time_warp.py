"""The schedule time warp g(t) and the right-tailed draws that parameterize it.

Concepts
--------
Let x be elapsed time divided by planned duration. A work package's technical
progress is u = g(x): monotone non-decreasing, g(0) = 0, capped at 1. Each
kind of g has a closed-form completion time x_c, the first x with g(x) = 1.
``_g_progress`` (forward) and ``_g_completion_x`` (inverse) are separate code
paths, so these checks tie them together and pin hand-worked values.

Delay magnitudes are ``low + scale * span * Beta(a, b)`` with a < b. A
Beta(a, b) has mean a / (a + b), which is below 1/2 when a < b; variance
ab / ((a + b)^2 (a + b + 1)); and skewness
2 (b - a) sqrt(a + b + 1) / ((a + b + 2) sqrt(ab)), positive when b > a. So the
draws should pile up near ``low`` and trail off to the right.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from synthetic_evm import SCENARIOS, engine
from synthetic_evm.config import G_RIGHT_TAIL
from tests.helpers import G_KINDS

X = np.linspace(0.0, 4.0, 4001)


def _sample_profiles(per_scenario: int = 300, seed: int = 11):
    rng = np.random.default_rng(seed)
    return [
        engine._choose_g_profile(cfg, rng)
        for cfg in SCENARIOS.values()
        for _ in range(per_scenario)
    ]


PROFILES = _sample_profiles()

# Hand-worked values: (kind, params, {x: expected progress}, completion x).
KNOWN_ANSWERS = [
    ("on_time", {}, {0.5: 0.5, 1.0: 1.0, 2.0: 1.0}, 1.0),
    ("minor_delay", {"delay": 0.25}, {0.25: 0.0, 1.0: 0.75, 1.25: 1.0}, 1.25),
    ("slow", {"rate": 0.5}, {1.0: 0.5, 2.0: 1.0, 3.0: 1.0}, 2.0),
    ("ahead", {"rate": 1.25}, {0.4: 0.5, 0.8: 1.0}, 0.8),
    (
        "recover",
        {"break_x": 0.5, "early_rate": 0.5, "late_rate": 1.5},
        {0.5: 0.25, 0.75: 0.625, 1.0: 1.0},
        1.0,
    ),
    (
        "stall",
        {"stall_start": 0.25, "stall_length": 0.5, "post_rate": 1.0},
        {0.25: 0.25, 0.5: 0.25, 0.75: 0.25, 1.0: 0.5, 1.5: 1.0},
        1.5,
    ),
]

# Parameter ranges: the configured minimum of each draw, the engine's cap, and
# the fixed uniform ranges.
PARAM_BOUNDS = {
    "minor_delay": {"delay": (G_RIGHT_TAIL["minor_delay"]["low"], 0.75)},
    "slow": {"rate": (1 / 2.0, 1 / (1 + G_RIGHT_TAIL["slow_slip"]["low"]))},
    "ahead": {"rate": (1.05, 1.25)},
    "recover": {
        "break_x": (0.30, 0.60),
        "early_rate": (1 / 2.0, 1 / (1 + G_RIGHT_TAIL["recover_early_slip"]["low"])),
        "late_rate": (1.04, 1.32),
    },
    "stall": {
        "stall_start": (0.18, 0.55),
        "stall_length": (G_RIGHT_TAIL["stall_length"]["low"], 0.90),
        "post_rate": (0.95, 1.25),
    },
}


def beta_moments(a: float, b: float) -> tuple[float, float, float]:
    """Mean, variance and skewness of Beta(a, b)."""
    mean = a / (a + b)
    var = a * b / ((a + b) ** 2 * (a + b + 1))
    skew = 2 * (b - a) * math.sqrt(a + b + 1) / ((a + b + 2) * math.sqrt(a * b))
    return mean, var, skew


def test_every_kind_is_drawn_by_some_scenario():
    assert {kind for kind, _ in PROFILES} == set(G_KINDS)


@pytest.mark.parametrize(("kind", "params", "points", "_completion"), KNOWN_ANSWERS)
def test_progress_matches_hand_worked_values(kind, params, points, _completion):
    xs = np.array(list(points))
    got = engine._g_progress(xs, kind, params)
    np.testing.assert_allclose(got, list(points.values()), atol=1e-12)


@pytest.mark.parametrize(("kind", "params", "_points", "completion"), KNOWN_ANSWERS)
def test_completion_time_matches_hand_worked_values(kind, params, _points, completion):
    assert engine._g_completion_x(kind, params) == pytest.approx(completion)


def test_progress_is_monotone_from_zero_into_the_unit_interval():
    for kind, params in PROFILES:
        u = engine._g_progress(X, kind, params)
        assert u[0] == 0.0, (kind, params)
        assert u.min() >= 0.0 and u.max() <= 1.0, (kind, params)
        assert np.all(np.diff(u) >= -1e-12), (kind, params)


def test_completion_time_is_the_first_x_at_which_progress_reaches_one():
    for kind, params in PROFILES:
        x_c = engine._g_completion_x(kind, params)
        at = engine._g_progress(np.array([x_c]), kind, params)[0]
        just_before = engine._g_progress(np.array([x_c * (1 - 1e-6)]), kind, params)[0]
        assert at == pytest.approx(1.0, abs=1e-9), (kind, params)
        assert just_before < 1.0, (kind, params)
        later = engine._g_progress(X[X >= x_c], kind, params)
        assert np.all(later >= 1.0 - 1e-9), (kind, params)


def test_sampled_parameters_stay_inside_the_configured_and_capped_ranges():
    eps = 1e-12
    for kind, params in PROFILES:
        if kind == "on_time":
            assert params == {}
            continue
        assert set(params) == set(PARAM_BOUNDS[kind]), kind
        for name, (low, high) in PARAM_BOUNDS[kind].items():
            assert low - eps <= params[name] <= high + eps, (kind, name, params[name])


def test_completion_time_relative_to_plan_follows_each_kind():
    for kind, params in PROFILES:
        x_c = engine._g_completion_x(kind, params)
        assert x_c > 0.0, (kind, params)
        if kind == "on_time":
            assert x_c == 1.0
        elif kind in {"minor_delay", "slow"}:
            assert x_c > 1.0, (kind, params)
        elif kind == "ahead":
            assert x_c < 1.0, (kind, params)
        elif kind == "stall":
            # After a pause of length `gap` the remaining (1 - a) of progress
            # takes (1 - a) / r, so the stall ends the WP late exactly when the
            # pause outlasts what the faster post-stall rate can recover.
            a, gap, r = params["stall_start"], params["stall_length"], params["post_rate"]
            assert (x_c > 1.0) == (gap > (1 - a) * (1 - 1 / r)), (kind, params)


def test_unknown_kind_is_rejected_everywhere():
    with pytest.raises(ValueError):
        engine._g_progress(np.zeros(1), "bogus", {})
    with pytest.raises(ValueError):
        engine._g_completion_x("bogus", {})
    cfg = dataclasses.replace(SCENARIOS["nominal"], g_probs={"bogus": 1.0})
    with pytest.raises(ValueError):
        engine._choose_g_profile(cfg, np.random.default_rng(0))


@pytest.mark.parametrize("name", sorted(G_RIGHT_TAIL))
def test_right_tail_draws_follow_the_configured_beta(name):
    spec = G_RIGHT_TAIL[name]
    n = 30_000
    rng = np.random.default_rng(2024)
    draws = np.array([engine._right_tail_draw(name, 1.0, rng) for _ in range(n)])

    mean_b, var_b, skew_b = beta_moments(spec["a"], spec["b"])
    expected_mean = spec["low"] + spec["span"] * mean_b
    expected_sd = spec["span"] * math.sqrt(var_b)
    standard_error = expected_sd / math.sqrt(n)

    assert abs(draws.mean() - expected_mean) < 5 * standard_error
    assert abs(draws.std() - expected_sd) < 0.04 * expected_sd
    sample_skew = float(np.mean((draws - draws.mean()) ** 3) / draws.std() ** 3)
    assert sample_skew > 0.3
    assert abs(sample_skew - skew_b) < 0.15
    assert np.median(draws) < draws.mean()  # right skew pulls the mean above the median
    assert draws.min() >= spec["low"]
    assert draws.max() <= spec["low"] + spec["span"]


def test_tail_scale_stretches_the_excess_over_the_minimum_linearly():
    low = G_RIGHT_TAIL["slow_slip"]["low"]
    base = engine._right_tail_draw("slow_slip", 1.0, np.random.default_rng(5))
    doubled = engine._right_tail_draw("slow_slip", 2.0, np.random.default_rng(5))
    assert doubled - low == pytest.approx(2 * (base - low))


def test_cap_bounds_the_draw_without_moving_its_minimum():
    low = G_RIGHT_TAIL["slow_slip"]["low"]
    rng = np.random.default_rng(8)
    draws = np.array(
        [engine._right_tail_draw("slow_slip", 3.0, rng, cap=0.5) for _ in range(2000)]
    )
    assert draws.max() == 0.5  # a scale of 3 pushes many draws onto the cap
    assert draws.min() >= low
