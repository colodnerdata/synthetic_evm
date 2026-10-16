"""Public portfolio generator and submission-shaped output contract.

Public output is deliberately small:

``projects``
    One row per project with project-level totals/metadata.
``wbs_totals``
    One row per project/WBS with WBS BAC.
``monthly``
    One row per project/WBS/month with *incremental* PV, EV, and AC.

No cumulative values or derived EVM metrics are stored. Consumers can calculate
those from the raw submission values. Work packages and their synthetic truth
remain private to the engine.
"""

from __future__ import annotations

import zlib
from typing import Dict, Iterable, Mapping

import numpy as np
import pandas as pd

from .config import (
    DEFAULT_N_WBS,
    DEFAULT_PLAN_MONTHS,
    DEFAULT_TOTAL_BAC,
    PARTITION_METHODS,
    SCENARIOS,
)
from .engine import (
    build_wbs_plan,
    make_period_cost_effects,
    make_wbs_windows,
    partition_bac,
    simulate_wbs,
)


PUBLIC_TABLES = ("projects", "wbs_totals", "monthly")


def _scenario_rng(seed: int, scenario_name: str) -> np.random.Generator:
    """Create stable per-scenario randomness from the portfolio seed.

    Tying a child RNG to the scenario name means other scenarios never change
    this scenario's random stream. Its position still matters: ``project_id``
    and the BAC-partition style are both assigned by position (see
    ``generate_portfolio``), so moving a scenario changes its numbers.
    """
    scenario_code = zlib.crc32(scenario_name.encode("utf-8"))
    return np.random.default_rng(np.random.SeedSequence([int(seed), int(scenario_code)]))


def _round_partition_to_cents(values: np.ndarray, total: float) -> np.ndarray:
    """Round a BAC partition to cents while preserving the exact rounded total."""
    rounded = np.round(np.asarray(values, dtype=float), 2)
    correction = round(float(total), 2) - round(float(rounded.sum()), 2)
    rounded[-1] = round(float(rounded[-1] + correction), 2)
    return rounded


def _round_series_to_target(
    values: np.ndarray,
    target: float,
    last_allowed: int | None = None,
) -> np.ndarray:
    """Round incremental dollars to cents and reconcile the resulting sum."""
    rounded = np.round(np.asarray(values, dtype=float), 2)
    correction = round(float(target), 2) - round(float(rounded.sum()), 2)
    if abs(correction) < 0.005:
        return rounded

    if last_allowed is None:
        candidates = np.flatnonzero(np.abs(rounded) > 0)
    else:
        candidates = np.flatnonzero(np.abs(rounded[: last_allowed + 1]) > 0)

    idx = int(candidates[-1]) if len(candidates) else (
        last_allowed if last_allowed is not None else len(rounded) - 1
    )
    rounded[idx] = round(float(rounded[idx] + correction), 2)
    return rounded


def generate_portfolio(
    seed: int = 20260922,
    scenarios: Iterable[str] | None = None,
    total_bac: float = DEFAULT_TOTAL_BAC,
    plan_months: int = DEFAULT_PLAN_MONTHS,
    n_wbs: int = DEFAULT_N_WBS,
) -> Dict[str, pd.DataFrame]:
    """Generate submission-shaped synthetic EV data.

    Parameters
    ----------
    seed:
        Master random seed. Change it to produce a new portfolio.
    scenarios:
        Scenario names to generate. Defaults to every configured scenario;
        when given, it must name at least one.
    total_bac:
        BAC for *each* project. Default is $100M.
    plan_months:
        Baseline duration. Default is 60 months.
    n_wbs:
        Number of WBS elements per project. Default is 10.

    Reporting horizon
    -----------------
    There is no reporting-horizon input. The engine first builds all latent WPs
    and their g(t) schedule behavior, determines the month in which the final EV
    will be earned, and then emits rows through::

        max(planned_duration_months, final_EV_month)

    Keeping the planned duration as the minimum guarantees that the complete PMB
    is present even for projects that finish early. Late projects extend only as
    far as their simulated performance requires.

    Returns
    -------
    dict[str, pandas.DataFrame]
        ``projects``: project_id, scenario, seed, wbs_partition, BAC,
        planned_duration_months

        ``wbs_totals``: project_id, wbs_id, BAC

        ``monthly``: project_id, wbs_id, month, PV, EV, AC

    Raises
    ------
    ValueError
        If ``plan_months`` or ``n_wbs`` is less than 1, if ``scenarios`` is
        given but empty, or if it names a scenario that is not configured.

    Notes
    -----
    Work packages, EVTs, spread profiles, g(t), and cost factors are latent.
    They are intentionally not exposed in the returned dataset.
    """
    if plan_months < 1:
        raise ValueError("plan_months must be positive")
    if n_wbs < 1:
        raise ValueError("n_wbs must be positive")

    scenario_names = list(SCENARIOS) if scenarios is None else list(scenarios)
    if not scenario_names:
        raise ValueError(
            "scenarios must name at least one scenario; omit it to generate all configured scenarios"
        )
    unknown = [name for name in scenario_names if name not in SCENARIOS]
    if unknown:
        raise ValueError(f"Unknown scenarios: {unknown}")

    project_rows = []
    wbs_rows = []
    monthly_rows = []

    for p_idx, scenario_name in enumerate(scenario_names, start=1):
        rng = _scenario_rng(seed, scenario_name)
        cfg = SCENARIOS[scenario_name]
        project_id = f"P{p_idx:03d}"

        # Cycle the named partition schemes so a default portfolio demonstrates
        # all of them rather than trusting randomness to be pedagogically useful.
        # The style follows the scenario's position in the run, not its name.
        partition_method = PARTITION_METHODS[(p_idx - 1) % len(PARTITION_METHODS)]
        raw_wbs_bacs = partition_bac(total_bac, n_wbs, partition_method, rng)
        wbs_bacs = _round_partition_to_cents(raw_wbs_bacs, total_bac)
        wbs_windows = make_wbs_windows(n_wbs, plan_months, rng)
        mean_wbs_bac = total_bac / n_wbs

        # Phase 1: create the latent WP structures. Because each WP's g(t) is now
        # known, the project reporting life can be derived rather than guessed.
        wbs_plans = []
        for w_idx, wbs_bac in enumerate(wbs_bacs):
            wbs_start, wbs_end = wbs_windows[w_idx]
            wbs_plans.append(
                build_wbs_plan(
                    wbs_bac=float(wbs_bac),
                    wbs_start=wbs_start,
                    wbs_end=wbs_end,
                    mean_wbs_bac=mean_wbs_bac,
                    cfg=cfg,
                    rng=rng,
                )
            )

        final_ev_month = max(plan.ev_completion_month for plan in wbs_plans)
        report_months = max(int(plan_months), int(final_ev_month))
        project_period_cost_effect = make_period_cost_effects(report_months, cfg, rng)

        project_rows.append(
            {
                "project_id": project_id,
                "scenario": scenario_name,
                "seed": int(seed),
                "wbs_partition": partition_method,
                "BAC": round(float(total_bac), 2),
                "planned_duration_months": int(plan_months),
            }
        )

        # Phase 2: simulate the already-defined WPs only through the natural
        # project reporting horizon and publish their WBS-level aggregation.
        for w_idx, (wbs_bac, plan) in enumerate(zip(wbs_bacs, wbs_plans)):
            wbs_id = f"{w_idx + 1:02d}"
            sim = simulate_wbs(
                plan=plan,
                report_months=report_months,
                cfg=cfg,
                project_period_cost_effect=project_period_cost_effect,
                rng=rng,
            )

            # PV reconciles to WBS BAC and any penny correction stays within PMB.
            pv = _round_series_to_target(sim.pv, float(wbs_bac), last_allowed=plan_months - 1)

            # The dynamic horizon guarantees the WBS has finished earning value,
            # so EV also reconciles to BAC instead of being arbitrarily truncated.
            ev = _round_series_to_target(sim.ev, float(wbs_bac))
            ac = np.round(sim.ac, 2)

            wbs_rows.append(
                {
                    "project_id": project_id,
                    "wbs_id": wbs_id,
                    "BAC": round(float(wbs_bac), 2),
                }
            )

            for month in range(1, report_months + 1):
                idx = month - 1
                monthly_rows.append(
                    {
                        "project_id": project_id,
                        "wbs_id": wbs_id,
                        "month": month,
                        "PV": float(pv[idx]),
                        "EV": float(ev[idx]),
                        "AC": float(ac[idx]),
                    }
                )

    data = {
        "projects": pd.DataFrame(project_rows),
        "wbs_totals": pd.DataFrame(wbs_rows),
        "monthly": pd.DataFrame(monthly_rows),
    }
    validate_portfolio(data)
    return data


def validate_portfolio(data: Mapping[str, pd.DataFrame]) -> None:
    """Validate the invariants implied by the public output contract."""
    missing = [name for name in PUBLIC_TABLES if name not in data]
    if missing:
        raise AssertionError(f"Missing public tables: {missing}")

    projects = data["projects"]
    wbs_totals = data["wbs_totals"]
    monthly = data["monthly"]

    if projects["project_id"].duplicated().any():
        raise AssertionError("project_id must be unique in projects.")
    if wbs_totals.duplicated(["project_id", "wbs_id"]).any():
        raise AssertionError("project_id/wbs_id must be unique in wbs_totals.")
    if monthly.duplicated(["project_id", "wbs_id", "month"]).any():
        raise AssertionError("project_id/wbs_id/month must be unique in monthly.")

    # Every project's WBS BACs must reconcile to its project BAC to the cent.
    wbs_sum = wbs_totals.groupby("project_id")["BAC"].sum().round(2)
    project_bac = projects.set_index("project_id")["BAC"].round(2)
    if not wbs_sum.equals(project_bac.loc[wbs_sum.index]):
        raise AssertionError("WBS BAC does not reconcile to project BAC.")

    # Each project may have a different dynamic horizon, but within a project
    # every WBS has one row for every month from 1 through that horizon.
    project_horizon = monthly.groupby("project_id")["month"].max().astype(int).to_dict()
    counts = monthly.groupby(["project_id", "wbs_id"]).size()
    min_month = monthly.groupby(["project_id", "wbs_id"])["month"].min()
    max_month = monthly.groupby(["project_id", "wbs_id"])["month"].max()
    for (project_id, wbs_id), count in counts.items():
        horizon = int(project_horizon[project_id])
        if int(count) != horizon or int(min_month.loc[(project_id, wbs_id)]) != 1 or int(max_month.loc[(project_id, wbs_id)]) != horizon:
            raise AssertionError("Monthly table is not rectangular within each project.")

    # Planned value must reconcile to WBS BAC and stop after the planned finish.
    pv_sum = monthly.groupby(["project_id", "wbs_id"])["PV"].sum().round(2)
    wbs_bac = wbs_totals.set_index(["project_id", "wbs_id"])["BAC"].round(2)
    if not pv_sum.equals(wbs_bac.loc[pv_sum.index]):
        raise AssertionError("Incremental PV does not reconcile to WBS BAC.")

    plan_months = projects.set_index("project_id")["planned_duration_months"].to_dict()
    for project_id, finish_month in plan_months.items():
        horizon = project_horizon[project_id]
        if horizon < int(finish_month):
            raise AssertionError("Dynamic reporting horizon ends before the planned duration.")

        late_pv = monthly.loc[
            (monthly["project_id"] == project_id) & (monthly["month"] > int(finish_month)),
            "PV",
        ]
        if not np.allclose(late_pv.to_numpy(), 0.0, rtol=0.0, atol=0.001):
            raise AssertionError("PV exists after the planned project duration.")

    if (monthly[["PV", "EV", "AC"]] < -0.001).any().any():
        raise AssertionError("PV, EV, and AC must be non-negative.")

    # Dynamic reporting means every WBS is complete by its project's final row.
    ev_sum = monthly.groupby(["project_id", "wbs_id"])["EV"].sum().round(2)
    if not ev_sum.equals(wbs_bac.loc[ev_sum.index]):
        raise AssertionError("EV does not reconcile to WBS BAC at the dynamic horizon.")

    # There should be no arbitrary trailing months. If a project extends beyond
    # its baseline, its last emitted month must contain earned value somewhere.
    project_ev = monthly.groupby(["project_id", "month"])["EV"].sum()
    for project_id, horizon in project_horizon.items():
        if horizon > int(plan_months[project_id]):
            if float(project_ev.loc[(project_id, horizon)]) <= 0.0:
                raise AssertionError("Delayed project has trailing months after final EV.")
