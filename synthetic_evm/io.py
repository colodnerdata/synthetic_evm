"""CSV export helpers for the public synthetic EVM tables."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd


OUTPUT_ORDER = ("projects", "wbs_totals", "monthly")


def export_csvs(
    data: Mapping[str, pd.DataFrame],
    out_dir: str | Path,
    prefix: str = "",
) -> dict[str, Path]:
    """Write the three public tables to CSV and return their paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}
    for name in OUTPUT_ORDER:
        filename = f"{prefix}{name}.csv"
        path = out / filename
        data[name].to_csv(path, index=False, float_format="%.2f")
        written[name] = path
    return written
