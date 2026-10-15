"""Compatibility facade for the v2 package.

For new code, prefer importing from ``synthetic_evm`` directly. This file keeps
the original module name usable while the implementation is split cleanly.
"""

from synthetic_evm import export_csvs, generate_portfolio, validate_portfolio

__all__ = ["generate_portfolio", "validate_portfolio", "export_csvs"]


if __name__ == "__main__":
    data = generate_portfolio(seed=20260922)
    validate_portfolio(data)
    export_csvs(data, "synthetic_evm_output")
    print("Wrote projects.csv, wbs_totals.csv, and monthly.csv to synthetic_evm_output/")
