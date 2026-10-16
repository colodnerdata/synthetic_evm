# Testing

How the generator is tested: what each test module protects, the concepts behind the numerical checks, and how continuous integration runs them. This is the plan of record for the suite in `tests/`.

## Running the suite

```bash
uv sync --locked      # runtime and dev dependencies, pinned by uv.lock
uv run pytest         # the whole suite
uv run ruff check .   # lint
```

The suite runs in well under a minute. Nothing in it writes into the repository: every export goes to a temporary directory.

## Principles

1. **Independent oracles.** An expected value comes from a closed form (Beta moments, the Dirichlet concentration formula), a hand-worked example (a half-rate work package takes twice as long), or a recomputation from the public tables (the reporting horizon from the last month with earned value). It is never produced by calling the function under test.
2. **Properties over snapshots.** Most checks assert invariants that must hold for every seed: conservation, monotonicity, ordering. The one snapshot is the committed sample, described below.
3. **Deterministic.** Every random draw comes from a fixed seed, so a statistical check cannot flake. Tolerances still come from the statistics — five standard errors for a sample mean, margins well inside the observed gap between scenarios — so that a different but legitimate random stream would pass too.
4. **Checks that can fail.** Validators are tested by corrupting a valid portfolio one way at a time. Non-vacuity guards assert that the interesting branch (an FS push, an extended horizon) actually occurred.
5. **Warnings are errors.** `filterwarnings = ["error"]` makes a numerical warning (overflow, invalid value) or a deprecation fail the run instead of scrolling past.

## Layout

| Module | Protects | Oracle |
|---|---|---|
| `test_config.py` | The assumptions users edit: distributions sum to 1, ranges are ordered, every scenario names exactly the six schedule behaviors, tail scales are ordered as documented. | Direct inspection of `config.py`. |
| `test_time_warp.py` | The schedule time warp `g(t)` and the right-tailed delay draws. | Hand-worked values; agreement of the forward and inverse functions; Beta moments. |
| `test_fs_logic.py` | Completion months, Finish-to-Start assignment and propagation, and the FS validator. | Hand-worked chains with a scripted generator; invariants over generated networks; corrupted networks. |
| `test_planning.py` | BAC partitions, WBS windows, spread profiles, earned-value techniques, work-package plans. | The Dirichlet closed form; structural invariants. |
| `test_simulation.py` | Monthly PV, EV and AC of a work package, rework, the project-wide cost effect, WBS aggregation. | Hand-worked examples; conservation of value. |
| `test_generator.py` | The public contract: arguments, schema, whole-cent amounts, the dynamic horizon, determinism, scenario position, scenario semantics. | Recomputation from the public tables; integer-cent arithmetic. |
| `test_validate.py` | Every failure mode of `validate_portfolio`. | A valid portfolio corrupted one way at a time. |
| `test_io_cli.py` | CSV export, `generate_evm.py` and the `synthetic_evm_generator.py` alias. | Round trips; running the script as a subprocess. |
| `test_sample_output.py` | The committed sample equals the default-seed run. | Regeneration. |
| `test_charts_workbook.py` | The `pCsvPath` Power Query parameter overwrite: M text literals, the COM call sequence, every skip and error path, the CLI wiring, the committed workbook's DataMashup, and the Windows-only dependency. | A fake Excel object model; M's escaping rules; the workbook read as a zip. |
| `test_docs.py` | Links and anchors, the README example, options, defaults and named constants. | The docs read against the code. |

`tests/helpers.py` holds the shared builders (`make_wp`, a scripted generator for the FS assignment) and `tests/conftest.py` the session fixtures, so the portfolios the tests share are generated once.

## Concepts under test

**Time warp.** Let `x` be elapsed time divided by planned duration. A work package's technical progress is `u = g(x)`: monotone non-decreasing, `g(0) = 0`, capped at 1. Each kind of `g` has a closed-form completion time `x_c`, the first `x` with `g(x) = 1`. `_g_progress` (forward) and `_g_completion_x` (inverse) are separate code paths, so the suite checks `g(x_c) = 1` and `g < 1` just before it, plus values worked by hand: a rate of 1/2 gives `g(2) = 1`, a delay of 1/4 gives `x_c = 1.25`. Only minor delay and slow work are always late. A short stall followed by a rate above 1 catches up and can finish early; a stall ends the package late exactly when its length exceeds `(1 − a)(1 − 1/r)`, where `a` is when it starts and `r` the rate after it.

**Right-tailed delays.** A delay is `low + scale × span × Beta(a, b)` with `a < b`. A `Beta(a, b)` has mean `a/(a+b)`, variance `ab / ((a+b)²(a+b+1))` and skewness `2(b−a)√(a+b+1) / ((a+b+2)√(ab))`, positive when `b > a`. The suite compares the sample mean (within five standard errors), spread and skewness of tens of thousands of draws with these moments, and checks that the excess over `low` scales linearly with `g_tail_scale` and that a cap bounds the draw without moving its minimum.

**Conservation of value.** PV and EV are two views of one budget. A work package's PV sums to its BAC once the horizon covers the planned finish, and its EV once it covers the actual finish. PV follows the baseline, so slipping a start moves EV and AC but never PV; LOE earns exactly as planned whatever `g` says. Cost follows the resource curve at actual progress, scaled by the cost factor and the project-wide effect, with an expediting premium only in months faster than plan.

**Finish-to-Start timing.** A zero-based start plus the elapsed months is both the one-based finish month and the zero-based index of the first month after it. A zero-lag successor therefore starts at `max(planned start, predecessor finish month)`: pushed right by a late predecessor, never pulled early. Eligibility is judged on the baseline, which is why an FS edge cannot move PV and the network cannot contain a cycle. Elapsed months use `ceil(duration × x_c − 1e-12)` so that a product floating point leaves a hair above a whole number (`11 × (1/0.11) = 100.00000000000001`) is not rounded up a month.

**Whole cents.** Each amount is rounded to cents and the residual placed on the last non-zero element, so a total equals its target exactly in integer cents.

**Partition concentration.** For Dirichlet(α) weights over `n` elements the expected Herfindahl index is `E[Σ wᵢ²] = (α+1)/(nα+1)`, which orders `balanced` (α = 10), `moderate` (2.5) and `concentrated` (0.7). The suite compares the sample mean with the closed form.

**Randomness and position.** A scenario draws from its own generator seeded by the master seed and a hash of its name, so other scenarios never influence its draws. Its position still matters: the project id and the BAC-partition style are both assigned by position. A scenario therefore reproduces exactly when it keeps its position, whatever follows or precedes it elsewhere in the list, and changes when it moves.

**Power Query parameter.** The charts workbook's `pCsvPath` is an M expression: a text literal followed by a `meta` record. In an M text literal a double quote is written `""` and `#(` opens an escape sequence, so a literal `#(` is written `#(#)(`. The overwrite is idempotent: it saves only when the value changes, so a run that writes to the same place leaves the workbook untouched, and it always closes the workbook and quits Excel.

**Dynamic horizon.** A project reports through `max(planned duration, month of final EV)`. The suite recomputes that from the public tables alone: the last month with `EV > 0`.

## Scenario semantics

A few checks assert that scenarios behave as their names say: efficient execution has a cost performance index above 1 and cost overrun below it, and the schedule-slip scenarios finish later than the on-plan ones. They average a 12-seed sweep and use margins well inside the observed gaps. Retuning a scenario on purpose can move those gaps; the thresholds are the place to follow it.

## The committed sample

`synthetic_evm_output/` is the default-seed run. `test_sample_output.py` regenerates it in memory and compares every table, with amounts to the cent, and rejects a byte-order mark such as a spreadsheet re-save leaves. It is the one deliberate snapshot: a change to the engine or configuration that alters the default-seed numbers fails it until the sample is regenerated and committed with that change.

```bash
uv run python generate_evm.py --seed 20260922 --out synthetic_evm_output
```

## Not covered

- The Excel workbooks (`monthly.xlsx`, `EVM Concepts Graphs.xlsx`) are hand-built and are reviewed in [WORKBOOK_REVIEW.md](WORKBOOK_REVIEW.md); the suite does not open them.
- Real Excel. The COM calls behind the charts-workbook overwrite are checked against a fake object model, and `monthly_charts.xlsx` is checked as a zip; opening it in Excel and running the overwrite on Windows are outside the suite.
- Individual random draws are not pinned, apart from the committed sample.
- Run time is not measured.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and on pull requests to `main`:

- **Tests** run `uv run pytest` on Python 3.13 and 3.14 after `uv sync --locked`, which also fails when `uv.lock` no longer matches `pyproject.toml`.
- **Lint** runs `uv run ruff check .`. The rule set (pyflakes and the pycodestyle error classes) is declared in `pyproject.toml` and the ruff version is pinned by `uv.lock`, so the result is reproducible.

## Changing the model

- **A new schedule behavior** needs a branch in `_g_progress`, `_g_completion_x` and `_choose_g_profile`, an entry in every scenario's `g_probs`, and updates to `G_KINDS` in `tests/helpers.py` and to `KNOWN_ANSWERS` and `PARAM_BOUNDS` in `tests/test_time_warp.py`. `test_config.py` fails until every scenario names exactly the known behaviors.
- **A new scenario** is picked up by the tests that iterate over `SCENARIOS`. Add it to the semantics checks if its name promises a behavior.
- **A deliberate change to the default-seed numbers** regenerates the sample in the same commit (command above).
- **A new configuration constant, function or CLI option mentioned in the README** is checked against the code by `test_docs.py`; the lists it pins are edited in the same commit.
