"""The committed sample must be exactly what the default seed generates.

The README promises that ``synthetic_evm_output/`` is the default-seed run. This
is the one deliberate snapshot test: any change to the engine or configuration
that alters the default-seed numbers fails here until the sample is regenerated,
so the change is made on purpose and reviewed as data.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tests.helpers import DEFAULT_SEED

SAMPLE = Path(__file__).resolve().parents[1] / "synthetic_evm_output"
REGENERATE = f"python generate_evm.py --seed {DEFAULT_SEED} --out synthetic_evm_output"
TEXT_COLUMNS = {"project_id": str, "wbs_id": str, "scenario": str, "wbs_partition": str}


@pytest.mark.parametrize("table", ["projects", "wbs_totals", "monthly"])
def test_the_committed_sample_matches_a_fresh_default_seed_run(table, default_portfolio):
    path = SAMPLE / f"{table}.csv"

    # A spreadsheet re-save leaves a byte-order mark the generator never writes.
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), (
        f"{path.name} starts with a byte-order mark; regenerate it with: {REGENERATE}"
    )

    committed = pd.read_csv(path, dtype=TEXT_COLUMNS)
    try:
        pd.testing.assert_frame_equal(
            committed, default_portfolio[table], check_dtype=False, atol=0.005, rtol=0
        )
    except AssertionError as error:
        pytest.fail(f"{path.name} differs from the default-seed run; regenerate it with: {REGENERATE}\n{error}")
