"""Simulating work packages: PV/EV/AC conservation and the cost model.

Concepts
--------
PV and EV are two views of the same budget, so a work package's incremental PV
sums to its BAC once the horizon covers its planned finish, and its EV sums to
its BAC once the horizon covers its actual finish. PV follows the baseline, so
slipping a start moves EV and AC but never PV. Actual cost follows the
*resource* curve at actual progress:

    AC_m = resource_m * cost_factor * project_effect_m * expediting_m * noise_m
           (+ cost-only rework)

with 1% lognormal noise and an expediting multiplier
1 + premium * max(speed - 1, 0), where speed is the progress made in the month
relative to plan.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from synthetic_evm import SCENARIOS, engine
from tests.helpers import make_wp

# A scenario with the optional cost mechanisms switched off, so what is left is
# the resource curve, the cost factor and the 1% noise.
QUIET = dataclasses.replace(SCENARIOS["nominal"], rework_prob=0.0, recovery_premium=0.0)


def simulate(wp, report_months, cfg=QUIET, seed=0, effect=None):
    effect = np.ones(report_months) if effect is None else effect
    return engine._simulate_work_package(wp, report_months, cfg, effect, np.random.default_rng(seed))


def test_zero_hundred_earns_everything_in_the_last_planned_month():
    wp = make_wp(bac=1000.0, planned_start=2, duration=4, evt="0_100")
    pv, ev, _ = simulate(wp, 10)
    expected = np.zeros(10)
    expected[5] = 1000.0
    np.testing.assert_allclose(pv, expected, atol=1e-9)
    np.testing.assert_allclose(ev, expected, atol=1e-9)


def test_fifty_fifty_earns_half_in_the_first_month_and_half_in_the_last():
    wp = make_wp(bac=1000.0, planned_start=2, duration=4, evt="50_50")
    pv, ev, _ = simulate(wp, 10)
    expected = np.zeros(10)
    expected[2] = expected[5] = 500.0
    np.testing.assert_allclose(pv, expected, atol=1e-9)
    np.testing.assert_allclose(ev, expected, atol=1e-9)


def test_a_half_rate_work_package_earns_half_as_fast_for_twice_as_long():
    wp = make_wp(bac=800.0, planned_start=1, duration=4, g_kind="slow", g_params={"rate": 0.5})
    pv, ev, ac = simulate(wp, 14)

    expected_pv = np.zeros(14)
    expected_pv[1:5] = 200.0  # the plan: 800 over 4 months
    expected_ev = np.zeros(14)
    expected_ev[1:9] = 100.0  # the actual: 800 over 8 months
    np.testing.assert_allclose(pv, expected_pv, atol=1e-9)
    np.testing.assert_allclose(ev, expected_ev, atol=1e-9)

    assert ac.sum() == pytest.approx(800.0, rel=0.03)
    assert np.all(ac[:1] == 0) and np.all(ac[9:] == 0)  # no cost outside the actual work


def test_a_delay_defers_earned_value_without_losing_any():
    wp = make_wp(bac=400.0, duration=4, g_kind="minor_delay", g_params={"delay": 0.25})
    _, ev, _ = simulate(wp, 10)
    expected = np.zeros(10)
    expected[1:5] = 100.0
    np.testing.assert_allclose(ev, expected, atol=1e-9)


def test_level_of_effort_earns_exactly_as_planned_whatever_g_says():
    wp = make_wp(
        bac=600.0, planned_start=1, duration=6, evt="loe", g_kind="slow", g_params={"rate": 0.5}
    )
    pv, ev, _ = simulate(wp, 14)
    np.testing.assert_array_equal(ev, pv)


def test_slipping_the_start_moves_earned_value_and_cost_but_never_planned_value():
    on_time = make_wp(bac=400.0, planned_start=2, duration=4)
    slipped = make_wp(bac=400.0, planned_start=2, duration=4, actual_start=5)
    pv0, ev0, _ = simulate(on_time, 14)
    pv1, ev1, ac1 = simulate(slipped, 14)

    np.testing.assert_allclose(pv1, pv0)
    np.testing.assert_allclose(ev1[3:], ev0[:-3], atol=1e-9)  # shifted by actual - planned
    assert np.all(ev1[:5] == 0)
    assert np.all(ac1[:5] == 0)


def test_actual_cost_scales_with_the_cost_factor():
    wp = make_wp(bac=1000.0, duration=6, cost_factor=1.2)
    _, _, ac = simulate(wp, 12)
    assert ac.sum() == pytest.approx(1200.0, rel=0.02)


def test_the_project_wide_effect_multiplies_every_month():
    wp = make_wp(bac=1000.0, duration=6)
    _, _, plain = simulate(wp, 12, seed=4)
    _, _, pressured = simulate(wp, 12, seed=4, effect=np.full(12, 1.1))
    active = plain > 0
    np.testing.assert_allclose(pressured[active] / plain[active], 1.1)
    assert np.all(pressured[~active] == 0)


def test_expediting_premium_applies_only_to_faster_than_planned_months():
    wp = make_wp(
        bac=800.0,
        duration=8,
        g_kind="recover",
        g_params={"break_x": 0.25, "early_rate": 0.5, "late_rate": 1.5},
    )
    no_premium = dataclasses.replace(QUIET, recovery_premium=0.0)
    premium = dataclasses.replace(QUIET, recovery_premium=0.10)
    _, _, ac0 = simulate(wp, 14, cfg=no_premium, seed=4)
    _, _, ac1 = simulate(wp, 14, cfg=premium, seed=4)

    active = ac0 > 0
    ratio = ac1[active] / ac0[active]
    assert ratio.min() == pytest.approx(1.0)  # the slow early months carry no premium
    assert ratio.max() == pytest.approx(1.0 + 0.10 * (1.5 - 1.0))  # speed 1.5x plan
    assert np.all(ac1[~active] == 0)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_generated_work_packages_conserve_value_and_stay_non_negative(scenario):
    rng = np.random.default_rng(41)
    cfg = SCENARIOS[scenario]
    for _ in range(10):
        plan = engine.build_wbs_plan(1.5e7, 0, 59, 1e7, cfg, rng)
        months = max(60, plan.ev_completion_month) + 1
        for wp in plan.work_packages:
            pv, ev, ac = engine._simulate_work_package(wp, months, cfg, np.ones(months), rng)
            for series in (pv, ev, ac):
                assert series.shape == (months,)
                assert np.all(np.isfinite(series))
                assert np.all(series >= 0)
            assert pv.sum() == pytest.approx(wp.bac, rel=1e-9)
            assert ev.sum() == pytest.approx(wp.bac, rel=1e-9)
            assert np.all(np.cumsum(ev) <= wp.bac * (1 + 1e-9))
            assert np.all(ev[: wp.actual_start] == 0)  # nothing is earned before work starts
            if wp.evt == "loe":
                np.testing.assert_array_equal(ev, pv)


def test_rework_is_cost_only_and_stays_inside_the_reporting_horizon():
    never = dataclasses.replace(SCENARIOS["nominal"], rework_prob=0.0)
    always = dataclasses.replace(SCENARIOS["nominal"], rework_prob=1.0, rework_range=(0.01, 0.02))
    rng = np.random.default_rng(3)

    assert np.all(engine._make_rework_costs(1000.0, np.arange(5), never, 10, rng) == 0)
    no_active_months = np.array([], dtype=int)
    assert np.all(engine._make_rework_costs(1000.0, no_active_months, always, 10, rng) == 0)

    for _ in range(200):
        out = engine._make_rework_costs(1000.0, np.arange(3, 8), always, 10, rng)
        nonzero = np.flatnonzero(out)
        assert 1 <= len(nonzero) <= 2
        assert 3 <= nonzero[0] <= 7  # begins in an active month
        assert 10.0 - 1e-9 <= out.sum() <= 20.0 + 1e-9  # 1%-2% of the work package's BAC

    # Rework that begins in the final month cannot spill past it, nor lose cost.
    out = engine._make_rework_costs(1000.0, np.array([9]), always, 10, rng)
    assert out[:9].sum() == 0
    assert 10.0 - 1e-9 <= out[9] <= 20.0 + 1e-9


def test_period_cost_effects_are_bounded_and_reproducible():
    cfg = SCENARIOS["volatile_execution"]
    first = engine.make_period_cost_effects(66, cfg, np.random.default_rng(1))
    second = engine.make_period_cost_effects(66, cfg, np.random.default_rng(1))
    assert first.shape == (66,)
    np.testing.assert_array_equal(first, second)
    assert first.min() >= 0.70 and first.max() <= 1.60


def test_without_drift_or_shocks_the_period_effect_is_only_small_noise():
    cfg = dataclasses.replace(
        SCENARIOS["nominal"], drift_mean=0.0, drift_sd=0.0, shock_events=0.0
    )
    effects = engine.make_period_cost_effects(60, cfg, np.random.default_rng(2))
    assert np.all(np.abs(effects - 1.0) < 0.07)  # 1.2% lognormal noise: under 6 sigma


def test_overlapping_shocks_are_clipped_at_the_upper_bound():
    cfg = dataclasses.replace(
        SCENARIOS["nominal"], shock_events=50.0, shock_range=(0.5, 0.6)
    )
    effects = engine.make_period_cost_effects(60, cfg, np.random.default_rng(2))
    assert effects.max() == 1.60


def test_wbs_totals_are_the_sum_of_their_work_packages():
    cfg = SCENARIOS["nominal"]
    plan = engine.build_wbs_plan(2e7, 0, 59, 1e7, cfg, np.random.default_rng(51))
    months = max(60, plan.ev_completion_month)
    effect = np.ones(months)

    sim = engine.simulate_wbs(plan, months, cfg, effect, np.random.default_rng(52))
    assert sim.pv.shape == sim.ev.shape == sim.ac.shape == (months,)
    assert sim.pv.sum() == pytest.approx(plan.bac, rel=1e-9)
    assert sim.ev.sum() == pytest.approx(plan.bac, rel=1e-9)
    assert sim.ac.sum() > 0

    rng = np.random.default_rng(52)
    pv = np.zeros(months)
    ev = np.zeros(months)
    ac = np.zeros(months)
    for wp in plan.work_packages:
        wp_pv, wp_ev, wp_ac = engine._simulate_work_package(wp, months, cfg, effect, rng)
        pv += wp_pv
        ev += wp_ev
        ac += wp_ac
    np.testing.assert_allclose(sim.pv, pv)
    np.testing.assert_allclose(sim.ev, ev)
    np.testing.assert_allclose(sim.ac, ac)
