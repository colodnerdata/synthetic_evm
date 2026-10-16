"""Command-line entry point for the synthetic EVM generator.

Examples
--------
Generate the default portfolio with a new random seed::

    uv run python generate_evm.py --seed 12345 --out output

Generate only selected scenarios::

    uv run python generate_evm.py --seed 42 --scenarios nominal combined_overrun --out output

The reporting life is derived automatically from the simulated EV behavior.
"""

from __future__ import annotations

import argparse

from synthetic_evm import export_csvs, generate_portfolio


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic project/WBS/month PV-EV-AC data.")
    parser.add_argument("--seed", type=int, default=20260922, help="Master random seed.")
    parser.add_argument("--out", default="synthetic_evm_output", help="Output directory.")
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=None,
        help="Scenario names to generate (one or more). Omit to generate all configured scenarios.",
    )
    args = parser.parse_args()

    data = generate_portfolio(
        seed=args.seed,
        scenarios=args.scenarios,
    )
    paths = export_csvs(data, args.out)

    horizons = data["monthly"].groupby("project_id")["month"].max()
    print(f"Generated {len(data['projects'])} projects with seed {args.seed}.")
    print(
        "Dynamic reporting horizons: "
        f"{int(horizons.min())}-{int(horizons.max())} months across this portfolio."
    )
    print(f"Projects:   {paths['projects']}")
    print(f"WBS totals: {paths['wbs_totals']}")
    print(f"Monthly:    {paths['monthly']}")


if __name__ == "__main__":
    main()
