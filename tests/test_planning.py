"""Planning: BAC partitions, WBS windows, and the latent work packages.

Concepts
--------
Partition shares are Dirichlet(alpha) weights over n elements. Their expected
concentration (the Herfindahl index) is

    E[sum_i w_i^2] = (alpha + 1) / (n * alpha + 1),

which falls as alpha rises, so ``balanced`` (alpha 10) < ``moderate`` (2.5) <
``concentrated`` (0.7). Spread weights describe how a work package consumes
its budget over its duration; every profile is a probability vector over the
months. A work package's technique (EVT) decides when its value is *earned*:
0/100 in the last month, 50/50 in the first and last, weighted milestones at
2-5 months, percent complete and LOE along the resource curve.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from synthetic_evm import SCENARIOS, engine
from synthetic_evm.config import PARTITION_METHODS, SPREAD_PROFILES, WP_DURATION_MONTHS
from tests.helpers import G_KINDS

EVTS = {"0_100", "50_50", "weighted_milestone", "percent_complete", "loe"}


@pytest.mark.parametrize("method", PARTITION_METHODS)
def test_partition_shares_are_positive_and_sum_to_the_total(method):
    rng = np.random.default_rng(3)
    for n in (1, 2, 5, 10, 25):
        shares = engine.partition_bac(1e8, n, method, rng)
        assert len(shares) == n
        assert np.all(shares > 0)
        assert shares.sum() == pytest.approx(1e8, rel=1e-12)


def test_unknown_partition_method_is_rejected():
    with pytest.raises(ValueError):
        engine.partition_bac(1e8, 10, "bogus", np.random.default_rng(0))


@pytest.mark.parametrize(
    ("method", "alpha"), [("balanced", 10.0), ("moderate", 2.5), ("concentrated", 0.7)]
)
def test_partition_concentration_matches_the_dirichlet_closed_form(method, alpha):
    n, draws = 10, 600
    rng = np.random.default_rng(17)
    hhi = np.array([np.sum(engine.partition_bac(1.0, n, method, rng) ** 2) for _ in range(draws)])
    expected = (alpha + 1) / (n * alpha + 1)
    assert abs(hhi.mean() - expected) < 5 * hhi.std(ddof=1) / math.sqrt(draws)


def test_dominant_partition_gives_one_element_between_30_and_45_percent():
    rng = np.random.default_rng(5)
    for _ in range(200):
        shares = engine.partition_bac(1.0, 10, "dominant_wbs", rng)
        assert np.sum((shares >= 0.30) & (shares <= 0.45)) >= 1


@pytest.mark.parametrize(("plan_months", "n"), [(60, 10), (36, 4), (12, 3), (60, 1)])
def test_wbs_windows_span_the_whole_baseline(plan_months, n):
    rng = np.random.default_rng(9)
    for _ in range(100):
        windows = engine.make_wbs_windows(n, plan_months, rng)
        assert len(windows) == n
        assert windows[0][0] == 0
        assert windows[-1][1] == plan_months - 1  # the PMB reaches the stated finish
        for start, end in windows:
            assert 0 <= start <= end <= plan_months - 1
        for start, end in windows[:-1]:
            remaining = plan_months - start
            assert min(10, remaining) <= end - start + 1 <= min(30, remaining)


@pytest.mark.parametrize("profile", SPREAD_PROFILES)
@pytest.mark.parametrize("duration", range(1, 13))
def test_spread_weights_are_a_probability_vector_over_the_months(profile, duration):
    weights = engine._spread_weights(duration, profile, np.random.default_rng(duration))
    assert weights.shape == (duration,)
    assert np.all(weights >= 0)
    assert weights.sum() == pytest.approx(1.0)
    if duration == 1:
        assert weights[0] == 1.0


def test_spread_profile_shapes():
    rng = np.random.default_rng(3)
    for duration in range(3, 13):
        for _ in range(20):
            uniform = engine._spread_weights(duration, "uniform", rng)
            np.testing.assert_allclose(uniform, 1.0 / duration)
            assert np.all(np.diff(engine._spread_weights(duration, "front_loaded", rng)) < 0)
            assert np.all(np.diff(engine._spread_weights(duration, "back_loaded", rng)) > 0)
            middle = engine._spread_weights(duration, "middle_peak", rng)
            assert 0 < int(np.argmax(middle)) < duration - 1


def test_unknown_spread_profile_is_rejected():
    with pytest.raises(ValueError):
        engine._spread_weights(3, "bogus", np.random.default_rng(0))


@pytest.mark.parametrize(
    ("duration", "allowed"),
    [
        (1, {"0_100", "percent_complete", "loe"}),
        (2, {"50_50", "weighted_milestone", "percent_complete", "loe"}),
        (3, {"weighted_milestone", "percent_complete", "loe"}),
        (12, {"weighted_milestone", "percent_complete", "loe"}),
    ],
)
def test_short_techniques_are_reserved_for_short_work_packages(duration, allowed):
    rng = np.random.default_rng(1)
    assert {engine._choose_evt(duration, rng) for _ in range(2000)} == allowed


def test_planned_value_profile_of_each_technique():
    rng = np.random.default_rng(2)
    resource = np.full(6, 1.0 / 6)

    np.testing.assert_array_equal(
        engine._planned_pv_weights(6, "0_100", resource, rng), [0, 0, 0, 0, 0, 1]
    )
    np.testing.assert_array_equal(
        engine._planned_pv_weights(6, "50_50", resource, rng), [0.5, 0, 0, 0, 0, 0.5]
    )
    for evt in ("percent_complete", "loe"):
        weights = engine._planned_pv_weights(6, evt, resource, rng)
        np.testing.assert_array_equal(weights, resource)
        assert weights is not resource  # a copy: editing it cannot alter the plan

    with pytest.raises(ValueError):
        engine._planned_pv_weights(6, "bogus", resource, rng)


@pytest.mark.parametrize("duration", range(1, 13))
def test_weighted_milestones_earn_at_a_few_months_including_the_last(duration):
    rng = np.random.default_rng(duration)
    resource = np.full(duration, 1.0 / duration)
    for _ in range(100):
        weights = engine._planned_pv_weights(duration, "weighted_milestone", resource, rng)
        assert weights.sum() == pytest.approx(1.0)
        assert weights[-1] > 0
        milestones = int(np.count_nonzero(weights))
        if duration <= 2:
            assert milestones == duration
        else:
            assert 2 <= milestones <= min(5, duration)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_wbs_plan_invariants(scenario):
    rng = np.random.default_rng(21)
    cfg = SCENARIOS[scenario]
    for wbs_start, wbs_end, bac in [(0, 59, 1.5e7), (10, 40, 4e6), (45, 59, 9e6), (0, 8, 2e6)]:
        for _ in range(15):
            plan = engine.build_wbs_plan(bac, wbs_start, wbs_end, 1e7, cfg, rng)
            wps = plan.work_packages
            assert 5 <= len(wps) <= 20
            assert plan.bac == bac
            assert sum(wp.bac for wp in wps) == pytest.approx(bac, rel=1e-9)
            for wp in wps:
                assert wp.duration in WP_DURATION_MONTHS
                assert wp.duration <= wbs_end - wbs_start + 1
                assert wbs_start <= wp.planned_start
                assert wp.planned_start + wp.duration - 1 <= wbs_end
                assert wp.resource_weights.shape == wp.pv_weights.shape == (wp.duration,)
                assert wp.resource_weights.sum() == pytest.approx(1.0)
                assert wp.pv_weights.sum() == pytest.approx(1.0)
                assert 0.72 <= wp.cost_factor <= 1.45
                assert wp.evt in EVTS
                assert wp.g_kind in G_KINDS
            assert plan.ev_completion_month == max(
                engine.work_package_completion_month(wp) for wp in wps
            )


def test_larger_wbs_elements_get_more_work_packages():
    cfg = SCENARIOS["nominal"]

    def mean_count(bac: float) -> float:
        rng = np.random.default_rng(31)
        sizes = [
            len(engine.build_wbs_plan(bac, 0, 59, 1e7, cfg, rng).work_packages)
            for _ in range(150)
        ]
        return float(np.mean(sizes))

    # Expected counts are 7 + 4.5 * sqrt(size ratio): about 9.5 against 14.8.
    assert mean_count(3e7) > mean_count(3e6) + 3
