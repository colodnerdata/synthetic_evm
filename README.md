# Synthetic EVM generator v4: longer WPs and right-tailed execution

This version preserves the submission-shaped public output from v3 while changing
only the latent simulation assumptions that drive it.

## Public output

The public API still emits only three tables. Work-package details, EVTs, FS
relationships, and g(t) parameters remain internal.

### `projects`

One row per project:

- `project_id`
- `scenario`
- `seed`
- `wbs_partition`
- `BAC`
- `planned_duration_months`

### `wbs_totals`

One row per project/WBS:

- `project_id`
- `wbs_id`
- `BAC`

### `monthly`

One row per project/WBS/month:

- `project_id`
- `wbs_id`
- `month`
- `PV`
- `EV`
- `AC`

`PV`, `EV`, and `AC` are incremental period values, not cumulative values.

## Longer work packages

The planned WP-duration distribution is now configured in `synthetic_evm/config.py`
rather than hard-coded in the engine.

The current distribution spans 1 through 12 months and has an expected duration
of about 6.9 months. One- and two-month packages remain possible but uncommon.
That preserves occasional 0/100 and 50/50 behavior, while longer packages mostly
use weighted milestones, percent complete, or LOE.

To change the duration mix, edit only:

```python
WP_DURATION_MONTHS
WP_DURATION_PROBS
```

## Right-tailed g(t) behavior

Schedule distortion is no longer drawn mostly from uniform ranges. Delay, slow
execution, recovery, and stall magnitudes now use bounded beta distributions
with most draws near the mild end and a smaller population extending farther to
the right.

This is intended to create an asymmetric completion-time population: many WPs
are near plan, some are moderately late, and a thinner tail is substantially
late. It does not simply shift every WP by the same amount.

The common right-tail shape parameters live in:

```python
G_RIGHT_TAIL
```

Each scenario also has:

```python
g_tail_scale
```

which changes the severity of the right tail without changing that scenario's
mix of `on_time`, `minor_delay`, `slow`, `ahead`, `recover`, and `stall` g(t)
types. For example, `combined_overrun` and `schedule_slip` have larger tail
scales than `nominal`, while `ahead_of_plan` and `efficient_execution` have
smaller ones.

The `ahead` profile remains deliberately modest so the delay side of the
population has the longer tail.

## Finish-to-Start logic

The simple zero-lag FS logic introduced in v3 remains unchanged.

Each WBS is assigned one of three latent logic archetypes:

- `parallel`: no explicit WP FS relationships
- `mixed_fs`: a moderate share of eligible WPs receive a predecessor
- `fs_heavy`: most eligible WPs receive a predecessor

An FS edge is only added when the existing planned dates already permit it, so
PV is not rescheduled simply to manufacture network logic. During execution, a
successor cannot begin technical progress before its predecessor actually
finishes. Longer WPs and the right-tailed g(t) behavior therefore have more room
to propagate schedule effects through the latent network.

LOE WPs remain excluded from explicit FS links.

## Dynamic reporting horizon

There is still no default or user-supplied reporting horizon. The engine builds
the latent WPs, resolves FS-constrained actual starts, and determines the final
EV month. Public rows are emitted through:

```text
max(planned duration, month of final EV)
```

PV stays on the 60-month baseline and is zero afterward. EV and AC can continue
past month 60 when the simulated execution requires it.

## Separation of concerns

- `synthetic_evm/config.py`: scenario assumptions, WP-duration mix, g(t) tail assumptions, WBS logic mix
- `synthetic_evm/engine.py`: latent WPs, FS propagation, g(t), and PV/EV/AC simulation
- `synthetic_evm/generator.py`: public output assembly and validation
- `synthetic_evm/io.py`: CSV export only
- `generate_evm.py`: thin command-line runner

## Use from Python

```python
from synthetic_evm import generate_portfolio, export_csvs

data = generate_portfolio(seed=12345)

projects = data["projects"]
wbs_totals = data["wbs_totals"]
monthly = data["monthly"]

export_csvs(data, "output")
```

## Command line

```bash
python generate_evm.py --seed 12345 --out output
```

Changing the seed changes the project/WBS/WP construction and execution draws
while keeping the same configured statistical assumptions.
