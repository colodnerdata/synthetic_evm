"""The public portfolio contract: arguments, schema, determinism, rounding, horizon.

Concepts
--------
Randomness. A scenario draws from its own generator seeded by (master seed,
CRC32 of the scenario name), so other scenarios never influence its draws. Its
*position* in the run does matter: the project id and the BAC-partition style
are both assigned by position (the styles cycle through ``PARTITION_METHODS``).
A scenario therefore reproduces exactly when it keeps its position, whatever
follows or precedes it elsewhere in the list.

Cent reconciliation. Every amount is rounded to cents, and the residual goes on
the last non-zero element, so each total equals its target exactly in integer
cents.

Dynamic horizon. A project reports through max(planned duration, month of its
final earned value). That is checkable from the public tables alone: the last
month with EV > 0 gives the final-EV month.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import synthetic_evm
from synthetic_evm import SCENARIOS, generate_portfolio
from synthetic_evm import generator
from synthetic_evm.config import (
    DEFAULT_N_WBS,
    DEFAULT_PLAN_MONTHS,
    DEFAULT_TOTAL_BAC,
    PARTITION_METHODS,
)
from tests.helpers import DEFAULT_SEED


def _cents(series: pd.Series) -> pd.Series:
    return (series * 100).round().astype("int64")


def _scenario_rows(data, scenario: str) -> pd.DataFrame:
    projects = data["projects"]
    project_id = projects.loc[projects["scenario"] == scenario, "project_id"].item()
    monthly = data["monthly"]
    rows = monthly[monthly["project_id"] == project_id]
    return rows.drop(columns="project_id").reset_index(drop=True)


def test_public_api_is_importable():
    for name in synthetic_evm.__all__:
        assert hasattr(synthetic_evm, name), name


# --- arguments -----------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"plan_months": 0},
        {"n_wbs": 0},
        {"scenarios": ["no_such_scenario"]},
        {"scenarios": []},
        {"scenarios": ()},
    ],
)
def test_invalid_arguments_raise_value_error(kwargs):
    with pytest.raises(ValueError):
        generate_portfolio(**kwargs)


def test_error_messages_name_the_problem():
    with pytest.raises(ValueError, match="no_such_scenario"):
        generate_portfolio(scenarios=["no_such_scenario"])
    with pytest.raises(ValueError, match="at least one scenario"):
        generate_portfolio(scenarios=[])


def test_scenarios_may_be_any_iterable():
    data = generate_portfolio(seed=1, scenarios=(name for name in ["nominal"]))
    assert list(data["projects"]["scenario"]) == ["nominal"]


# --- schema ----------------------------------------------------------------------


def test_public_tables_have_the_documented_columns(default_portfolio):
    assert list(default_portfolio) == ["projects", "wbs_totals", "monthly"]
    assert list(default_portfolio["projects"].columns) == [
        "project_id",
        "scenario",
        "seed",
        "wbs_partition",
        "BAC",
        "planned_duration_months",
    ]
    assert list(default_portfolio["wbs_totals"].columns) == ["project_id", "wbs_id", "BAC"]
    assert list(default_portfolio["monthly"].columns) == [
        "project_id",
        "wbs_id",
        "month",
        "PV",
        "EV",
        "AC",
    ]


def test_column_types(default_portfolio):
    projects, wbs_totals, monthly = (default_portfolio[n] for n in default_portfolio)
    is_int = pd.api.types.is_integer_dtype
    is_float = pd.api.types.is_float_dtype
    is_text = pd.api.types.is_string_dtype
    assert all(is_text(projects[c]) for c in ("project_id", "scenario", "wbs_partition"))
    assert is_int(projects["seed"]) and is_int(projects["planned_duration_months"])
    assert is_float(projects["BAC"]) and is_float(wbs_totals["BAC"])
    assert is_text(wbs_totals["wbs_id"]) and is_text(monthly["wbs_id"])
    assert is_int(monthly["month"])
    assert all(is_float(monthly[c]) for c in ("PV", "EV", "AC"))


def test_identifier_formats_and_month_numbering(default_portfolio):
    projects = default_portfolio["projects"]
    monthly = default_portfolio["monthly"]
    assert list(projects["project_id"]) == [f"P{i:03d}" for i in range(1, len(projects) + 1)]
    assert set(monthly["wbs_id"]) == {f"{i:02d}" for i in range(1, DEFAULT_N_WBS + 1)}
    for _, rows in monthly.groupby(["project_id", "wbs_id"]):
        assert list(rows["month"]) == list(range(1, len(rows) + 1))


def test_project_rows_describe_the_run(default_portfolio):
    projects = default_portfolio["projects"]
    assert list(projects["scenario"]) == list(SCENARIOS)
    assert (projects["seed"] == DEFAULT_SEED).all()
    assert (projects["BAC"] == DEFAULT_TOTAL_BAC).all()
    assert (projects["planned_duration_months"] == DEFAULT_PLAN_MONTHS).all()
    cycle = [PARTITION_METHODS[i % len(PARTITION_METHODS)] for i in range(len(projects))]
    assert list(projects["wbs_partition"]) == cycle


def test_portfolio_parameters_are_respected():
    data = generate_portfolio(
        seed=3, scenarios=["nominal", "cost_overrun"], total_bac=5e7, plan_months=36, n_wbs=4
    )
    projects, wbs_totals, monthly = data["projects"], data["wbs_totals"], data["monthly"]
    assert (projects["BAC"] == 5e7).all()
    assert (projects["planned_duration_months"] == 36).all()
    assert (wbs_totals.groupby("project_id").size() == 4).all()
    assert (monthly.groupby("project_id")["month"].max() >= 36).all()
    after_plan = monthly[monthly["month"] > 36]
    assert np.allclose(after_plan["PV"], 0.0, atol=0.001)


# --- money -------------------------------------------------------------------------


def test_amounts_are_whole_cents_and_reconcile_exactly(default_portfolio):
    projects, wbs_totals, monthly = (default_portfolio[n] for n in default_portfolio)
    for frame, columns in ((projects, ["BAC"]), (wbs_totals, ["BAC"]), (monthly, ["PV", "EV", "AC"])):
        for column in columns:
            cents = frame[column] * 100
            assert np.allclose(cents, np.round(cents), rtol=0, atol=1e-3), column

    wbs_sum = _cents(wbs_totals["BAC"]).groupby(wbs_totals["project_id"]).sum()
    project_bac = _cents(projects.set_index("project_id")["BAC"])
    pd.testing.assert_series_equal(wbs_sum, project_bac.loc[wbs_sum.index], check_names=False)

    wbs_bac = _cents(wbs_totals.set_index(["project_id", "wbs_id"])["BAC"])
    for column in ("PV", "EV"):
        total = _cents(monthly.groupby(["project_id", "wbs_id"])[column].sum())
        pd.testing.assert_series_equal(total, wbs_bac.loc[total.index], check_names=False)


@pytest.mark.parametrize("seed", [DEFAULT_SEED, 1, 2])
def test_reporting_horizon_is_the_later_of_plan_and_final_earned_value(seed):
    data = generate_portfolio(seed=seed)
    plan = data["projects"].set_index("project_id")["planned_duration_months"]
    delayed = 0
    for project_id, rows in data["monthly"].groupby("project_id"):
        horizon = rows["month"].max()
        final_ev_month = rows.loc[rows["EV"] > 0, "month"].max()
        assert horizon == max(plan[project_id], final_ev_month), project_id
        delayed += horizon > plan[project_id]
    assert delayed > 0  # the extended-horizon branch is exercised


# --- determinism -------------------------------------------------------------------


def test_the_same_seed_reproduces_every_number():
    first = generate_portfolio(seed=7)
    second = generate_portfolio(seed=7)
    for name in first:
        pd.testing.assert_frame_equal(first[name], second[name])


def test_a_different_seed_changes_the_simulation():
    first = generate_portfolio(seed=7)["monthly"]
    second = generate_portfolio(seed=8)["monthly"]
    assert not first.equals(second)


def _stream(seed: int, name: str) -> np.ndarray:
    return generator._scenario_rng(seed, name).random(5)


def test_a_scenarios_random_stream_depends_only_on_the_seed_and_its_name():
    np.testing.assert_array_equal(_stream(7, "nominal"), _stream(7, "nominal"))
    assert not np.array_equal(_stream(7, "nominal"), _stream(7, "cost_overrun"))
    assert not np.array_equal(_stream(7, "nominal"), _stream(8, "nominal"))


def test_scenarios_that_follow_a_scenario_do_not_influence_it():
    first = generate_portfolio(seed=5, scenarios=["nominal", "cost_overrun"])
    second = generate_portfolio(seed=5, scenarios=["nominal", "volatile_execution", "ahead_of_plan"])
    pd.testing.assert_frame_equal(
        _scenario_rows(first, "nominal"), _scenario_rows(second, "nominal")
    )


def test_a_scenario_reproduces_when_it_keeps_its_position():
    full = generate_portfolio(seed=5)
    shorter = generate_portfolio(
        seed=5, scenarios=["nominal", "cost_overrun", "schedule_slip", "combined_overrun"]
    )
    pd.testing.assert_frame_equal(
        _scenario_rows(full, "combined_overrun"), _scenario_rows(shorter, "combined_overrun")
    )


def test_moving_a_scenario_changes_its_partition_style_and_so_its_numbers():
    full = generate_portfolio(seed=5)
    alone = generate_portfolio(seed=5, scenarios=["combined_overrun"])

    def style(data) -> str:
        projects = data["projects"]
        return projects.loc[projects["scenario"] == "combined_overrun", "wbs_partition"].item()

    assert style(full) != style(alone)
    assert not _scenario_rows(full, "combined_overrun").equals(
        _scenario_rows(alone, "combined_overrun")
    )


# --- rounding helpers --------------------------------------------------------------


def test_partition_rounding_preserves_the_exact_total_in_cents():
    rng = np.random.default_rng(4)
    for _ in range(200):
        total = round(float(rng.uniform(1e6, 2e8)), 2)
        raw = total * rng.dirichlet(np.full(10, 1.5))
        rounded = generator._round_partition_to_cents(raw, total)
        cents = np.round(rounded * 100)
        assert np.allclose(rounded * 100, cents, rtol=0, atol=1e-3)
        assert int(cents.sum()) == round(total * 100)


def test_series_rounding_reconciles_to_the_target_without_leaving_the_allowed_months():
    rng = np.random.default_rng(6)
    for _ in range(200):
        last = int(rng.integers(8, 25))
        values = np.zeros(30)
        values[3 : last + 1] = rng.uniform(0, 5e5, last - 2)
        target = round(float(values.sum()) + float(rng.uniform(-0.05, 0.05)), 2)

        rounded = generator._round_series_to_target(values, target, last_allowed=last)
        assert int(round(rounded.sum() * 100)) == round(target * 100)
        assert np.all(rounded[last + 1 :] == 0)
        assert np.allclose(rounded * 100, np.round(rounded * 100), rtol=0, atol=1e-3)


def test_series_rounding_known_answers():
    values = np.array([1.004, 2.004, 3.004])
    np.testing.assert_allclose(generator._round_series_to_target(values, 6.01), [1.0, 2.0, 3.01])
    np.testing.assert_allclose(
        generator._round_series_to_target(values, 6.01, last_allowed=1), [1.0, 2.01, 3.0]
    )
    # With nothing earned yet, the residual lands on the last allowed month.
    np.testing.assert_allclose(
        generator._round_series_to_target(np.zeros(3), 0.07, last_allowed=1), [0.0, 0.07, 0.0]
    )
    # A residual below half a cent changes nothing.
    np.testing.assert_allclose(generator._round_series_to_target(values, 6.0), [1.0, 2.0, 3.0])


# --- scenario semantics --------------------------------------------------------------


def test_cost_scenarios_move_the_cost_performance_index_as_named(seed_sweep):
    cpi = seed_sweep.groupby("scenario")["cpi"].mean()
    assert cpi["efficient_execution"] > 1.0 > cpi["cost_overrun"]
    assert cpi["efficient_execution"] - cpi["nominal"] > 0.05
    assert cpi["nominal"] - cpi["cost_overrun"] > 0.05
    assert cpi["nominal"] - cpi["combined_overrun"] > 0.05


def test_schedule_scenarios_finish_later_than_the_others(seed_sweep):
    assert (seed_sweep["horizon"] >= DEFAULT_PLAN_MONTHS).all()
    horizon = seed_sweep.groupby("scenario")["horizon"].mean()
    late = horizon[["schedule_slip", "combined_overrun"]].mean()
    on_plan = horizon[["nominal", "ahead_of_plan", "efficient_execution"]].mean()
    assert late - on_plan > 1.5
