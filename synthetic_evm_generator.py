"""Compatibility facade for the original single-file module name.

For new code, prefer importing from ``synthetic_evm`` directly. This file keeps
the original ``synthetic_evm_generator`` import path usable now that the
implementation lives in the ``synthetic_evm`` package.

Run as a script it generates the default portfolio (seed 20260922) into
``synthetic_evm_output/``, the same files that are committed to the repo.
"""

from synthetic_evm import export_csvs, generate_portfolio, validate_portfolio

__all__ = ["generate_portfolio", "validate_portfolio", "export_csvs"]


if __name__ == "__main__":
    data = generate_portfolio(seed=20260922)
    validate_portfolio(data)
    export_csvs(data, "synthetic_evm_output")
    print("Wrote projects.csv, wbs_totals.csv, and monthly.csv to synthetic_evm_output/")
