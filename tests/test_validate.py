"""validate_portfolio must fail loudly for each way the public contract can break.

A validator that never fails is worthless, so every test corrupts a valid
portfolio in exactly one way and expects the matching complaint.
"""

from __future__ import annotations

import pandas as pd
import pytest

from synthetic_evm import validate_portfolio


def delayed_project(portfolio) -> tuple[str, int, int]:
    """(project_id, planned months, reporting months) of a project that overruns its baseline."""
    plan = portfolio["projects"].set_index("project_id")["planned_duration_months"]
    horizon = portfolio["monthly"].groupby("project_id")["month"].max()
    for project_id, months in horizon.items():
        if months > plan[project_id]:
            return project_id, int(plan[project_id]), int(months)
    raise AssertionError("the default portfolio has no project that overruns its baseline")


def test_a_valid_portfolio_passes(portfolio):
    assert validate_portfolio(portfolio) is None


def test_a_missing_table(portfolio):
    del portfolio["monthly"]
    with pytest.raises(AssertionError, match="Missing public tables"):
        validate_portfolio(portfolio)


def test_duplicate_project_ids(portfolio):
    projects = portfolio["projects"]
    portfolio["projects"] = pd.concat([projects, projects.iloc[[0]]], ignore_index=True)
    with pytest.raises(AssertionError, match="project_id must be unique"):
        validate_portfolio(portfolio)


def test_duplicate_wbs_rows(portfolio):
    wbs = portfolio["wbs_totals"]
    portfolio["wbs_totals"] = pd.concat([wbs, wbs.iloc[[0]]], ignore_index=True)
    with pytest.raises(AssertionError, match="unique in wbs_totals"):
        validate_portfolio(portfolio)


def test_duplicate_monthly_rows(portfolio):
    monthly = portfolio["monthly"]
    portfolio["monthly"] = pd.concat([monthly, monthly.iloc[[0]]], ignore_index=True)
    with pytest.raises(AssertionError, match="unique in monthly"):
        validate_portfolio(portfolio)


def test_wbs_budgets_that_do_not_sum_to_the_project_budget(portfolio):
    portfolio["wbs_totals"].loc[0, "BAC"] += 0.01
    with pytest.raises(AssertionError, match="WBS BAC does not reconcile to project BAC"):
        validate_portfolio(portfolio)


def test_a_wbs_that_is_missing_a_month(portfolio):
    monthly = portfolio["monthly"]
    project = monthly[monthly["project_id"] == "P001"]
    last = project[(project["wbs_id"] == "01") & (project["month"] == project["month"].max())]
    portfolio["monthly"] = monthly.drop(last.index)
    with pytest.raises(AssertionError, match="not rectangular"):
        validate_portfolio(portfolio)


def test_planned_value_that_does_not_sum_to_the_wbs_budget(portfolio):
    portfolio["monthly"].loc[0, "PV"] += 1.0
    with pytest.raises(AssertionError, match="Incremental PV does not reconcile"):
        validate_portfolio(portfolio)


def test_planned_value_after_the_planned_finish(portfolio):
    project_id, plan, _ = delayed_project(portfolio)
    monthly = portfolio["monthly"]
    final_planned_month = monthly[(monthly["project_id"] == project_id) & (monthly["month"] == plan)]
    source = final_planned_month["PV"].idxmax()  # a WBS still earning planned value
    wbs_id = monthly.loc[source, "wbs_id"]
    target = monthly.index[
        (monthly["project_id"] == project_id)
        & (monthly["wbs_id"] == wbs_id)
        & (monthly["month"] == plan + 1)
    ][0]
    moved = monthly.loc[source, "PV"]
    assert moved > 0
    # Moving the amount keeps the WBS total intact, so only the timing check can object.
    monthly.loc[target, "PV"] += moved
    monthly.loc[source, "PV"] -= moved
    with pytest.raises(AssertionError, match="PV exists after the planned project duration"):
        validate_portfolio(portfolio)


def test_a_negative_amount(portfolio):
    portfolio["monthly"].loc[0, "AC"] = -1.0
    with pytest.raises(AssertionError, match="must be non-negative"):
        validate_portfolio(portfolio)


def test_earned_value_that_does_not_sum_to_the_wbs_budget(portfolio):
    portfolio["monthly"].loc[0, "EV"] += 1.0
    with pytest.raises(AssertionError, match="EV does not reconcile"):
        validate_portfolio(portfolio)


def test_trailing_months_after_the_final_earned_value(portfolio):
    project_id, _, horizon = delayed_project(portfolio)
    monthly = portfolio["monthly"]
    extra = monthly[(monthly["project_id"] == project_id) & (monthly["month"] == horizon)].copy()
    extra["month"] = horizon + 1
    extra[["PV", "EV", "AC"]] = 0.0
    portfolio["monthly"] = pd.concat([monthly, extra], ignore_index=True)
    with pytest.raises(AssertionError, match="trailing months"):
        validate_portfolio(portfolio)


def test_a_horizon_shorter_than_the_baseline(portfolio):
    projects = portfolio["projects"]
    projects.loc[projects["project_id"] == "P001", "planned_duration_months"] = 100
    with pytest.raises(AssertionError, match="ends before the planned duration"):
        validate_portfolio(portfolio)
