"""Alias module re-exporting the public API of the ``synthetic_evm`` package.

Prefer importing from ``synthetic_evm`` directly; this module exposes the same
``generate_portfolio``, ``validate_portfolio`` and ``export_csvs`` names.

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
