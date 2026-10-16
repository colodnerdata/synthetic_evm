"""Finish-to-Start logic and the work-package completion month.

Concepts
--------
Starts (``planned_start``, ``actual_start``) are zero-based month indices;
``work_package_completion_month`` is one-based. A WP that starts at index s and
needs e elapsed months occupies indices s .. s+e-1 and finishes in one-based
month s+e, which is also the zero-based index of the first month after it. So a
zero-lag FS successor may start at

    actual_start = max(planned_start, predecessor completion month)

and is pushed right only by a late predecessor, never pulled early. Elapsed
months are ceil(duration * x_c) computed with a 1e-12 guard, so a product that
floating point leaves a hair above a whole number is not rounded up a month.
"""

from __future__ import annotations

import numpy as np
import pytest

from synthetic_evm import SCENARIOS, engine
from tests.helpers import ClosestPredecessorRng, make_wp

COMPLETION_CASES = [
    ("on time", dict(planned_start=2, duration=4), 6),
    (
        "half rate takes twice as long",
        dict(planned_start=2, duration=4, g_kind="slow", g_params={"rate": 0.5}),
        10,
    ),
    (
        "an exact whole number of months is not rounded up",
        dict(planned_start=2, duration=4, g_kind="minor_delay", g_params={"delay": 0.25}),
        7,
    ),
    (
        "a fractional month rounds up",
        dict(planned_start=0, duration=4, g_kind="minor_delay", g_params={"delay": 0.30}),
        6,
    ),
    (
        # 11 * (1 / 0.11) is 100.00000000000001 in floating point, not 100.
        "a product a hair above a whole number is not another month",
        dict(planned_start=0, duration=11, g_kind="slow", g_params={"rate": 0.11}),
        100,
    ),
    (
        "ahead work can finish a month early",
        dict(planned_start=0, duration=8, g_kind="ahead", g_params={"rate": 1.25}),
        7,
    ),
    (
        "ahead work on a short package rounds back to plan",
        dict(planned_start=0, duration=4, g_kind="ahead", g_params={"rate": 1.25}),
        4,
    ),
    (
        "a package takes at least one month",
        dict(planned_start=3, duration=1, g_kind="ahead", g_params={"rate": 1.25}),
        4,
    ),
    (
        "LOE ignores g(t)",
        dict(planned_start=1, duration=6, evt="loe", g_kind="slow", g_params={"rate": 0.5}),
        7,
    ),
    ("the resolved actual start overrides the plan", dict(planned_start=2, duration=4, actual_start=5), 9),
]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [pytest.param(kwargs, expected, id=name) for name, kwargs, expected in COMPLETION_CASES],
)
def test_completion_month_known_answers(kwargs, expected):
    assert engine.work_package_completion_month(make_wp(**kwargs)) == expected


def _link(*wps):
    wps = list(wps)
    engine._assign_fs_relationships(wps, "fs_heavy", ClosestPredecessorRng())
    return wps


def test_on_time_predecessor_leaves_the_successor_on_its_planned_start():
    a, b = _link(make_wp(planned_start=0, duration=3), make_wp(planned_start=3, duration=2))
    assert (a.predecessor_idx, b.predecessor_idx) == (None, 0)
    assert (a.actual_start, b.actual_start) == (0, 3)


def test_late_predecessor_pushes_the_successor_right():
    a, b = _link(
        make_wp(planned_start=0, duration=3, g_kind="slow", g_params={"rate": 0.5}),
        make_wp(planned_start=3, duration=2),
    )
    assert engine.work_package_completion_month(a) == 6
    assert b.actual_start == 6
    assert b.planned_start == 3  # the baseline never moves


def test_early_predecessor_does_not_pull_the_successor_forward():
    a, b = _link(
        make_wp(planned_start=0, duration=8, g_kind="ahead", g_params={"rate": 1.25}),
        make_wp(planned_start=8, duration=2),
    )
    assert engine.work_package_completion_month(a) == 7
    assert b.actual_start == 8


def test_delay_propagates_down_a_chain_and_loe_stays_out_of_the_network():
    a = make_wp(planned_start=0, duration=3, g_kind="slow", g_params={"rate": 0.5})
    b = make_wp(planned_start=3, duration=2, g_kind="slow", g_params={"rate": 0.5})
    c = make_wp(planned_start=5, duration=1)
    loe = make_wp(planned_start=0, duration=12, evt="loe")
    a, b, c, loe = _link(a, b, c, loe)

    assert [wp.predecessor_idx for wp in (a, b, c, loe)] == [None, 0, 1, None]
    # A finishes in month 6, so B starts at 6 and (half rate, 2 months) finishes
    # in month 10, so C starts at 10.
    assert [wp.actual_start for wp in (a, b, c, loe)] == [0, 6, 10, 0]
    assert [wp.planned_start for wp in (a, b, c, loe)] == [0, 3, 5, 0]
    engine._validate_fs_relationships([a, b, c, loe])


def test_overlapping_work_is_not_eligible_for_a_link():
    a, b = _link(make_wp(planned_start=0, duration=5), make_wp(planned_start=3, duration=2))
    assert b.predecessor_idx is None and b.actual_start == 3


def test_parallel_archetype_links_nothing():
    wps = [make_wp(planned_start=0, duration=3), make_wp(planned_start=3, duration=2)]
    engine._assign_fs_relationships(wps, "parallel", ClosestPredecessorRng())
    assert all(wp.predecessor_idx is None for wp in wps)
    assert [wp.actual_start for wp in wps] == [0, 3]


def test_unknown_logic_archetype_is_rejected():
    with pytest.raises(ValueError):
        engine._assign_fs_relationships([make_wp()], "bogus", np.random.default_rng(0))


def _plans(scenario: str, count: int, seed: int):
    rng = np.random.default_rng(seed)
    return [
        engine.build_wbs_plan(1e7, 0, 59, 1e7, SCENARIOS[scenario], rng) for _ in range(count)
    ]


def test_generated_networks_obey_the_fs_rules():
    links = pushed = 0
    for scenario in SCENARIOS:
        for plan in _plans(scenario, 40, seed=100):
            wps = plan.work_packages
            for wp in wps:
                assert wp.actual_start is not None
                assert wp.actual_start >= wp.planned_start
                if wp.predecessor_idx is None:
                    assert wp.actual_start == wp.planned_start
                    continue
                pred = wps[wp.predecessor_idx]
                assert plan.logic_type != "parallel"
                assert "loe" not in (wp.evt, pred.evt)
                # Eligibility is judged on the baseline, so the edge cannot move PV...
                assert pred.planned_start + pred.duration <= wp.planned_start
                # ...and the predecessor starts strictly earlier, so there are no cycles.
                assert pred.planned_start < wp.planned_start
                finish = engine.work_package_completion_month(pred)
                assert wp.actual_start == max(wp.planned_start, finish)
                links += 1
                pushed += wp.actual_start > wp.planned_start
    # Guard against the checks above passing vacuously (about 1000 links and
    # 165 pushes are generated; the bounds leave room for a different stream).
    assert links > 300
    assert pushed > 50


def test_link_density_follows_the_logic_archetype():
    packages = [plan.work_packages for plan in _plans("nominal", 150, seed=7)]
    counts = {}
    for archetype in ("parallel", "mixed_fs", "fs_heavy"):
        rng = np.random.default_rng(13)
        total = 0
        for wps in packages:
            engine._assign_fs_relationships(wps, archetype, rng)
            total += sum(wp.predecessor_idx is not None for wp in wps)
        counts[archetype] = total
    assert counts["parallel"] == 0
    assert counts["mixed_fs"] > 0
    assert counts["fs_heavy"] > 1.2 * counts["mixed_fs"]


def test_assignment_is_reproducible_for_a_fixed_seed():
    first = _plans("combined_overrun", 5, seed=3)
    second = _plans("combined_overrun", 5, seed=3)
    for a, b in zip(first, second, strict=True):
        assert [wp.predecessor_idx for wp in a.work_packages] == [
            wp.predecessor_idx for wp in b.work_packages
        ]
        assert [wp.actual_start for wp in a.work_packages] == [
            wp.actual_start for wp in b.work_packages
        ]


class TestValidator:
    """``_validate_fs_relationships`` must reject each way a network can be wrong."""

    def test_accepts_a_consistent_network(self):
        a = make_wp(planned_start=0, duration=3, actual_start=0)
        b = make_wp(planned_start=3, duration=2, predecessor_idx=0, actual_start=3)
        engine._validate_fs_relationships([a, b])

    def test_rejects_an_unresolved_start(self):
        with pytest.raises(AssertionError, match="resolved actual start"):
            engine._validate_fs_relationships([make_wp(actual_start=None)])

    def test_rejects_a_work_package_that_is_its_own_predecessor(self):
        wp = make_wp(actual_start=0, predecessor_idx=0)
        with pytest.raises(AssertionError, match="own predecessor"):
            engine._validate_fs_relationships([wp])

    def test_rejects_a_link_that_violates_the_planned_baseline(self):
        a = make_wp(planned_start=0, duration=5, actual_start=0)
        b = make_wp(planned_start=3, duration=2, predecessor_idx=0, actual_start=5)
        with pytest.raises(AssertionError, match="planned baseline"):
            engine._validate_fs_relationships([a, b])

    def test_rejects_a_successor_that_starts_before_its_predecessor_finishes(self):
        a = make_wp(
            planned_start=0, duration=3, g_kind="slow", g_params={"rate": 0.5}, actual_start=0
        )
        b = make_wp(planned_start=3, duration=2, predecessor_idx=0, actual_start=3)
        with pytest.raises(AssertionError, match="before actual predecessor completion"):
            engine._validate_fs_relationships([a, b])
