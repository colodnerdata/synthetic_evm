"""Session fixtures. Generating portfolios is the slowest step, so it is shared."""

from __future__ import annotations

import pandas as pd
import pytest

from synthetic_evm import generate_portfolio
from tests.helpers import DEFAULT_SEED

SWEEP_SEEDS = range(12)


@pytest.fixture(scope="session")
def default_portfolio():
    """The default-seed portfolio, generated once. Treat it as read-only."""
    return generate_portfolio(seed=DEFAULT_SEED)


@pytest.fixture
def portfolio(default_portfolio):
    """A private copy of the default portfolio that a test may mutate."""
    return {name: frame.copy() for name, frame in default_portfolio.items()}


@pytest.fixture(scope="session")
def seed_sweep() -> pd.DataFrame:
    """Project-level outcomes for every scenario over several seeds.

    Columns: ``scenario``, ``cpi`` (total EV / total AC) and ``horizon``
    (reporting months).
    """
    rows = []
    for seed in SWEEP_SEEDS:
        data = generate_portfolio(seed=seed)
        monthly = data["monthly"]
        totals = monthly.groupby("project_id")[["EV", "AC"]].sum()
        horizon = monthly.groupby("project_id")["month"].max()
        scenario = data["projects"].set_index("project_id")["scenario"]
        for project_id in totals.index:
            rows.append(
                {
                    "scenario": scenario[project_id],
                    "cpi": totals.loc[project_id, "EV"] / totals.loc[project_id, "AC"],
                    "horizon": int(horizon[project_id]),
                }
            )
    return pd.DataFrame(rows)
