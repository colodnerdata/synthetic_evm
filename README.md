# Synthetic EVM Generator

Synthetic project / WBS / month **PV, EV and AC** data for earned value management (EVM) analysis, teaching and tooling. Each run simulates a portfolio of $100M, 60-month projects — one per configured scenario — and emits three flat tables shaped like a real contractor submission. The work packages, earned-value techniques, schedule logic and cost behavior that produce the numbers stay **latent**: the output contains only what a program office would actually receive.

The generator is seeded and deterministic: the same seed and settings reproduce every number.

## Getting started

Requires Python 3.13+ (see `.python-version`), `numpy` and `pandas`.

```bash
# With uv (uses pyproject.toml / uv.lock)
uv sync
uv run python generate_evm.py --seed 12345 --out output

# Or with pip
pip install -r requirements.txt
python generate_evm.py --seed 12345 --out output
```

The runner prints the project count and the range of dynamic reporting horizons, then writes `projects.csv`, `wbs_totals.csv` and `monthly.csv` to `--out`.

| Option | Default | Meaning |
|---|---|---|
| `--seed` | `20260922` | Master random seed. Change it for a new portfolio with the same statistical assumptions. |
| `--out` | `synthetic_evm_output` | Output directory (created if missing). |
| `--scenarios` | all | Space-separated scenario names, e.g. `--scenarios nominal combined_overrun`. |

`synthetic_evm_output/` is a committed sample generated with the default seed, so you can inspect the shape of the data without running anything. (Generated CSVs elsewhere are gitignored. The committed `monthly.csv` was re-saved by Excel — it carries a UTF-8 BOM and its `wbs_id` values read `1`, not `01`. Regenerating gives the exact same numbers with `01`.)

### Choose a starting point

| If you want to… | Start here |
|---|---|
| Get a dataset | Run `generate_evm.py` (above) or open the committed sample in `synthetic_evm_output/`. |
| Use it from code | See [Use from Python](#use-from-python). |
| Change what the portfolio looks like | Edit `synthetic_evm/config.py` — see [Tuning the assumptions](#tuning-the-assumptions). |
| Understand how the numbers are produced | Read [How the simulation works](#how-the-simulation-works), then `synthetic_evm/engine.py`. |
| See the EVM concepts as charts | Open `EVM Concepts Graphs.xlsx` — see [The concepts workbook](#the-concepts-workbook). |

## Public output

Three tables. Money is in currency units with two decimals; nothing is cumulative and no EVM metrics (CPI, SPI, EAC…) are stored — compute those from the raw values.

### `projects`

One row per project (one project per scenario).

| Column | Meaning |
|---|---|
| `project_id` | `P001`, `P002`, … in scenario order. |
| `scenario` | Scenario name from `config.SCENARIOS`. |
| `seed` | The master seed of the run. |
| `wbs_partition` | How BAC was split across WBS elements: `balanced`, `moderate`, `concentrated` or `dominant_wbs`. Cycled by project so a default run shows all four. |
| `BAC` | Budget at completion for the project. |
| `planned_duration_months` | Length of the baseline (PMB). |

### `wbs_totals`

One row per project/WBS: `project_id`, `wbs_id` (`01`, `02`, … — keep it as text), `BAC`. WBS BACs sum to the project BAC to the cent.

### `monthly`

One row per project/WBS/month: `project_id`, `wbs_id`, `month` (1-based), `PV`, `EV`, `AC`.

`PV`, `EV` and `AC` are **incremental period values, not cumulative**. Cumulate them with a running sum per project (or per WBS) to get the S-curves.

### Invariants

`validate_portfolio` runs at the end of every `generate_portfolio` call and raises `AssertionError` if any of these break:

- keys are unique (`project_id`; `project_id`+`wbs_id`; `project_id`+`wbs_id`+`month`);
- WBS BACs reconcile to project BAC, and per-WBS ΣPV and ΣEV reconcile to WBS BAC, to the cent;
- every WBS has one row for every month from 1 to its project's horizon (rectangular);
- PV is zero after the planned duration; PV, EV and AC are non-negative;
- a project that runs past its baseline ends on a month that earned value.

## How the simulation works

The simulation is split so a reader can follow one idea at a time. Every concept below is *latent* — none appears in the output.

1. **Partition.** Project BAC is split across WBS elements with a Dirichlet draw (`partition_bac`), then each WBS gets an overlapping planned window across the 60-month baseline (`make_wbs_windows`).
2. **Work packages (WPs).** Each WBS gets 5–20 WPs (more for larger WBS elements). A WP has a budget, planned start, duration (1–12 months, see `WP_DURATION_*`), an **earned-value technique** (0/100, 50/50, weighted milestone, percent complete or LOE — short techniques only on short WPs), a resource-consumption shape, and a persistent cost factor.
3. **Schedule behavior `g(t)`.** Each WP gets a monotone time warp mapping elapsed time to technical progress. It is drawn from the scenario's `g_probs`:

   | Kind | Behavior |
   |---|---|
   | `on_time` | Progress tracks the plan. |
   | `minor_delay` | Starts progressing after a right-tailed delay. |
   | `slow` | Constant slower rate; takes `1 + slip` times the planned duration. |
   | `ahead` | Modestly faster than plan (deliberately a thinner tail than delay). |
   | `recover` | Slow early, faster late. Faster-than-plan months carry an expediting cost premium. |
   | `stall` | Progress pauses for a right-tailed length, then resumes. |

   Slippage magnitudes are bounded **Beta** draws with most mass near the mild end and a thin, substantially late tail (`G_RIGHT_TAIL`), scaled per scenario by `g_tail_scale`. LOE earns with the passage of time and ignores `g(t)`.
4. **Finish-to-Start logic.** Each WBS is `parallel` (no links), `mixed_fs` or `fs_heavy` (`WBS_LOGIC_PROFILES`). A zero-lag FS edge is only added when the *planned* dates already allow it, so PV is never rescheduled to manufacture logic. During execution a successor cannot start before its predecessor actually finishes — it is pushed right only when the predecessor is late, never pulled early. LOE is excluded from the network.
5. **Dynamic reporting horizon.** The engine determines the month in which the last EV is earned, then reports through `max(planned duration, final EV month)`. PV stays on the baseline and is zero afterward; EV and AC continue past month 60 when execution requires it, so late projects genuinely run long and every WBS reaches 100% EV. There is no horizon input.
6. **Cost.** AC follows the *resource* curve at actual progress × the WP cost factor × a project-wide monthly effect (drift, noise and sparse cost shocks shared by all WBS elements) × the recovery premium, plus occasional cost-only rework (AC rises, EV does not).
7. **Rounding.** Monthly values are rounded to cents and the residual is applied to the last non-zero month so PV and EV reconcile exactly.

### Reproducibility

Each scenario draws from its own generator seeded by `(master seed, CRC32 of the scenario name)`. Adding or reordering scenarios therefore does not change another scenario's simulated behavior (its `project_id` may change). Within a scenario, the order of random draws is part of the contract: adding a draw to the engine changes every result for a given seed, so treat that as a breaking change to previously generated datasets.

## Tuning the assumptions

All assumptions live in `synthetic_evm/config.py`; the engine only converts them into PV/EV/AC.

| To change… | Edit |
|---|---|
| Default project shape | `DEFAULT_TOTAL_BAC`, `DEFAULT_PLAN_MONTHS`, `DEFAULT_N_WBS` (or pass them to `generate_portfolio`) |
| The WP duration mix (currently 1–12 months, mean ≈ 6.9) | `WP_DURATION_MONTHS` and `WP_DURATION_PROBS` (equal length, sum to 1) |
| The shape of the delay tail | `G_RIGHT_TAIL` |
| How severe a scenario's delay tail is, without changing its mix of g(t) kinds | that scenario's `g_tail_scale` |
| How often each kind of schedule behavior occurs | that scenario's `g_probs` |
| Cost level, volatility, shocks, rework, recovery premium | the other `ScenarioConfig` fields (documented on the class) |
| How much of each WBS is FS-linked | `WBS_LOGIC_PROFILES` (`weight` is the archetype mix, `link_probability` the per-WP link chance) |
| Add a scenario | add a `ScenarioConfig` entry to `SCENARIOS` |

`g_tail_scale` is the knob that separates, for example, `combined_overrun` and `schedule_slip` (larger tails) from `ahead_of_plan` and `efficient_execution` (smaller ones).

## Use from Python

```python
from synthetic_evm import generate_portfolio, export_csvs

data = generate_portfolio(seed=12345)  # also: scenarios=[...], total_bac=, plan_months=, n_wbs=

projects = data["projects"]
wbs_totals = data["wbs_totals"]
monthly = data["monthly"]

# Cumulative S-curves for one project
p1 = monthly[monthly["project_id"] == "P001"]
s_curves = p1.groupby("month")[["PV", "EV", "AC"]].sum().cumsum()

export_csvs(data, "output")
```

`synthetic_evm_generator.py` is a compatibility facade for the original single-file module name; new code should import from `synthetic_evm`.

## Project layout

| Path | Responsibility |
|---|---|
| `synthetic_evm/config.py` | Scenario assumptions, WP-duration mix, `g(t)` tail shape, WBS logic mix. Assumptions only — no mechanics. |
| `synthetic_evm/engine.py` | Latent WPs, `g(t)`, FS propagation, and the PV/EV/AC simulation. Never touches the public contract. |
| `synthetic_evm/generator.py` | Public output assembly, cent reconciliation and `validate_portfolio`. |
| `synthetic_evm/io.py` | CSV export only. |
| `generate_evm.py` | Thin command-line runner. |
| `synthetic_evm_generator.py` | Backward-compatible import path. |
| `synthetic_evm_output/` | Committed sample output (default seed). |
| `EVM Concepts Graphs.xlsx` | Hand-built workbook illustrating EVM concepts (in progress). |
| `docs/WORKBOOK_REVIEW.md` | Suggested changes to the workbook, mapped to the Lambda Catalog design principles. |

## The concepts workbook

`EVM Concepts Graphs.xlsx` is **independent of the generator**: it does not read the CSVs. A single `Chart Data` sheet drives seven chart sheets (Program Baseline, Collected Data, Variances, Earned Schedule, Future Actuals and EV, Rebaselining, Complete) that build up the EVM story one concept at a time. Its inputs are the labelled cells at the top-left of `Chart Data` (planned duration, BAC, baseline-change point, actual duration and cost, "now", and the sigmoid truncation parameters that shape the S-curves); the `Cumulative_EVM` table beside them holds the PV/EV/AC curves and derived CPI, SPI and earned schedule. It is a work in progress; [`docs/WORKBOOK_REVIEW.md`](docs/WORKBOOK_REVIEW.md) lists suggested clean-ups.
