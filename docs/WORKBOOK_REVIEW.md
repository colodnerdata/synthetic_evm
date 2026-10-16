# `EVM Concepts Graphs.xlsx` — design review against the Lambda Catalog principles

Suggestions only: **the workbook itself has not been modified.** The principles come from `AGENTS.md` / `CONTRIBUTING.md` / `docs/DECISIONS.md` in [`Statistical_Analysis_Lambda_Catalog`](https://github.com/colodnerdata/Statistical_Analysis_Lambda_Catalog).

**How this was reviewed.** The `.xlsx` was read statically (workbook XML, defined names, sheet and table formulas, chart series, drawings). There was no desktop Excel in the review environment, so nothing was recalculated and no chart was rendered; findings about *behavior* (e.g. volatility, label masking) are inferred from the formulas and should be confirmed in Excel. Each item says which principle it comes from and how big the change is.

## What already conforms

- **`sigmoid` / `expit` are named LAMBDAs, one concept each, `expit` built once and reused** — the catalog's "name the math once" pattern. `sigmoid` already carries a Name Manager comment.
- **Calculation lives on one sheet (`Chart Data`) and the chart sheets only read it.** This matches the catalog's "sheet writes, charts read named series" separation.
- **Inputs are named** (`BAC`, `Planned_Duration`, `BCP_Period`, `Time__now`, …) and formulas use them, rather than hard-coded addresses.
- **A structured table (`Cumulative_EVM`) holds the per-period curves**, so formulas read as `[EV]/[AC]` instead of `K3/L3`.
- **Charts reference real data series**, with no line shapes faking a reference. The one drawing object (a text box on *Earned Schedule*) is explanatory text, which is the allowed use.

## Priority 1 — correctness and hygiene

| # | Finding | Principle | Suggested change |
|---|---|---|---|
| 1 | **`cell_to_left` refers to `!XFD1`** — a broken, sheet-less reference left from a relative-reference name. | A defined name that cannot resolve is a latent `#REF!`; the catalog drops/guards these on every build (`sync_workbook_names`). | Delete it. |
| 2 | **Five defined names are never used by any cell, table or chart:** `t_actual`, `Final_SV`, `Final_CV`, `New_BAC`, `cell_to_left`. | One name per concept, no dead scaffolding. | Delete the dead ones, or finish the feature they were for. |
| 3 | **`New_BAC` and `BCP_BAC` both point at `'Chart Data'!$B$8`.** | One name per cell, so a rename cannot half-land. | Keep `BCP_BAC`; delete `New_BAC`. |
| 4 | **`AC`, `EV` and `pv` are spill-anchor names (`ANCHORARRAY` of `L3`, `K3`, `F3`) that collide with table-column names and with Excel's `PV` function.** `Cumulative_EVM[EV]` and the name `EV` mean different things. | The catalog's naming convention keeps constructor/spill-reader names distinct from column headers and native functions. | Rename to `PV_Curve`, `EV_Curve`, `AC_Curve`. `pv` is not read by any formula or chart and can simply be deleted; check `EV` / `AC` with *Trace Dependents* before renaming, since a static scan cannot separate them from the same-named table columns. |
| 5 | **Only 1 of 27 defined names has a Name Manager comment.** | *"Every defined name carries a Name Manager comment"* (`AGENTS.md`, Sheet writer conventions), enforced in the catalog by `test_every_defined_name_carries_a_comment`. | Add a one-line `Comment` to each name — what it holds, its unit (months / currency / fraction), and where it is edited. |
| 6 | **`EV Noise` and `AC Noise` are labelled (`A20`, `B20`) but their input cells (`A21`, `B21`) are empty and no formula reads them.** `Jitter_Offset` (`C21`) is the only one wired. | Don't ship half-built controls; an input a user can see should do something. | Either add the values and use them in the `EV` / `AC` curve formulas, or remove the labels. |
| 7 | **Related inputs are independent and can contradict each other.** `Actual_Duration` is typed (30) while `Planned_Duration` is 24 and `Final SV` is 3; `Actual_Cost` is `BAC+B13` but `Final CV` is never used by name; `BCP Planned Duration` is `ROUND(Planned_Duration*1.14,0)` with the **1.14 hard-coded in the formula**. | Derive, don't duplicate; a magic number inside a formula is a hidden input. | Decide which of each related set is the input and derive the other; expose `1.14` as a labelled input (e.g. "BCP Duration Growth %") beside `BCP Percent`. |

## Priority 2 — conform the sheet to the catalog's layout grammar

| # | Finding | Principle | Suggested change |
|---|---|---|---|
| 8 | **No cell is visually marked as an input.** The only fills in the workbook's styles are saturated chart colors; `A3`, `B3`, `A6`, … have no fill. | Cell styling: user-editable inputs use `INPUT_COLOR` `(251, 226, 213)`; zone headings use `HEADER_COLOR` `(202, 237, 251)` bold; sub-headers `SUBHDR_COLOR` `(220, 230, 241)`. | Fill every input cell light orange (`#FBE2D5`), section headings light blue (`#CAEDFB`). Keep derived cells (`A8`, `B8`, `B11`) unfilled so "orange = I can type here" is true everywhere. |
| 9 | **No data validation and no conditional formatting.** `BCP_Period` can exceed `Planned_Duration`; `Time__now` can exceed `Actual_Duration`; a truncation of 0 or a negative one is accepted. | The catalog gives every numeric input a `Validation` and a red CF at the floor; statuses are blank when legal and name their own subject when they fire. | Add whole-number / decimal validation to each input (`BCP_Period` between 1 and `Planned_Duration`−1; `Time__now` between 0 and `Actual_Duration`; truncations > 0), plus a red CF fill on violations. |
| 10 | **Three input blocks (`A2:C24`) mix labels and values with no zone heading; the column-header row, units and meaning are implicit.** Zone labels exist only for the table side (`E1`, `AG1`, `AM1`, …). | Rows 1–3 grammar: row 1 labels, row 2 the control/column header, row 3 the value; every zone has a heading at row 1. | Put a "INPUTS" heading at `A1`, and a short unit in each label ("Planned Duration (months)", "BAC (currency)"). Consider moving the inputs onto their own sheet (the catalog keeps the spec block and the outputs on distinct zones). |
| 11 | **Eight data zones (`E…BI`) sit side by side with no gap columns or outline groups.** | Every adjacent zone pair is separated by one narrow ungrouped gap column, and each zone has its own outline group so zones collapse independently. | Insert a width-2 gap column between zones; group each zone's content columns (leave the gap ungrouped). Do **not** group the spill columns (`E`, `F`, `G`, `H` hold dynamic arrays) — a collapsed group over a spill leaves stale values in the catalog's experience. |
| 12 | **No frozen header and no instructions content.** Opening the workbook gives no hint of which cells to edit. | Frozen header band; an Instructions sheet travels with the workbook. | Freeze at `A4` using select-then-freeze (never via `SplitRow`). Add a short *Instructions* sheet (what each input does, in the order to change them). |

## Priority 3 — formulas: simplify, name, and avoid volatility

| # | Finding | Principle | Suggested change |
|---|---|---|---|
| 13 | **`t_rebaselined` and `t_actual` are `OFFSET` names.** `OFFSET` is volatile, so each recalculates on every calculation pass. `t_rebaselined` is read ~65 times (every cell of the `H` spill and the `Cumulative_EVM` columns). The charts reference plain ranges, not these names, so nothing here needs `OFFSET`. | `DECISIONS.md`: non-volatile `TAKE`/dynamic arrays for names read by formulas; `OFFSET` is reserved for **chart-series names**, which cannot use `#`. | `t_rebaselined` → `=TAKE('Chart Data'!$G$3#,…)` or simply `SEQUENCE(Post_BCP_Planned_Duration+1,1,0,1)` once, referenced via `G3#`. Delete `t_actual` (unused). |
| 14 | **`I3` computes the period number from `ROW(...)-ROW([#Headers])-1` and then clamps to `Actual_Duration`.** | The catalog retired `ROW()-offset` arithmetic: a formula should not depend on the row it occupies. | Make `t` a single spill: `=SEQUENCE(MIN(Planned_Duration,…)+1,1,0,1)` (or whichever bound sizes the table), and drop the per-row `IF(ROW(...))`. The table's own row-count then comes from one cell. |
| 15 | **`H3` is a ~70-line `LET` inline in a cell**, containing the whole re-baselined PV logic: the AC and ES lookups at the BCP, the sigmoid re-scaling and the piecewise result. | *"The status cells hold a CALL, not the logic"* — clicking a cell should show what is checked, not a wall of nested logic; logic that reads inputs lives in a named LAMBDA. | Extract to a workbook LAMBDA, e.g. `Rebaselined_PV(t, bcp, finish, origDur, truncation, acAtBCP, esAtBCP, bcpBAC)`, with the two `XLOOKUP`s done in the caller. `H3` becomes `=Rebaselined_PV(t_rebaselined, BCP_Period, …)`, and the LAMBDA is documented once in its name comment. Keep it sheet-agnostic (no `'Chart Data'!` inside the body). |
| 16 | **Repeated construction of the same `sigmoid(t/MAX(t), …)` call** appears in `F`, `K`, `L` and again inside `H`'s `LET` (`originalPV`). `AD3` repeats several of `H`'s lookups. | Build once, reference by name (`Fit_Context`-style materialization): don't recompute at every call site. | Compute shared scalars once (e.g. `AC_at_BCP`, `ES_at_BCP`) in named single cells beside the BCP inputs and reference them from `H3` and `AD3`. |
| 17 | **`0.000000000001` is hard-coded in `H3` as the equality tolerance.** | A literal tolerance is a hidden constant; the catalog declares one per concern. | Name it (`Sigmoid_Tolerance`, 1E-12) next to the other constants, with a comment. |
| 18 | **Chart-feeding helper columns use `NA()` masking** (e.g. `S`–`Z`, `AA`, `AC`). | Charts: with `ShowValue`/`ShowCategoryName` use `NA()`; with **Value From Cells** labels the mask must be `""` or the label prints `#N/A` — the two are not interchangeable. | Spot-check the data labels (`[CELLRANGE]` appears on every chart): wherever a label reads from cells, confirm the source range contains `""` rather than `#N/A` for masked rows. |
| 19 | **The *Earned Schedule* chart carries its definition as a static text box** ("ES = PV⁻¹(EV(Now))"). | Chart titles and captions that depend on inputs are cell-linked (`.Formula = "='Sheet'!$Q$14"`), not literals. | Safe as-is (it has no input-dependent number). If you add numbers ("ES = 11.4 months"), link the text to a cell. Also link the chart titles ("EVM Concepts - …") to cells if they should track a scenario name. |

## Priority 4 — structure

| # | Finding | Suggested change |
|---|---|---|
| 20 | **Sheet-scoped vs workbook-scoped names.** Every input name is workbook-scoped. The catalog reserves workbook scope for sheet-agnostic LAMBDAs and scopes everything that points at a sheet's cells to that sheet. | If this workbook will be copied into another (the catalog's "easy" reuse path), make the cell-pointing names sheet-scoped to `Chart Data` and leave `sigmoid` / `expit` (and the proposed `Rebaselined_PV`) workbook-scoped. |
| 21 | **Not connected to the generator.** The workbook builds its curves from a sigmoid and typed inputs; the generator produces `monthly.csv`. | Add an optional data path: a `Source` table that pastes `monthly.csv` for one `project_id`, and cumulate PV/EV/AC with `SCAN`/`SUMIFS` so the same seven charts illustrate generated projects. Keep the sigmoid as the "teaching" default. This is a design call, not a conformance fix. |
| 22 | **Documentation of the workbook itself.** | The README gets a short inventory (done); the *Instructions* sheet from #12 can reuse it. A static explainer sheet should follow the catalog's rule of regenerating from one script rather than hand-edited cells, if more sheets are added. |

## Suggested order

1. Delete the dead/broken names (#1–#3) and add Name Manager comments (#5) — minutes, no risk.
2. Resolve the unwired inputs and magic number (#6–#7).
3. Input styling, validation and freeze panes (#8–#9, #12) — quick and the most visible conformance gain.
4. Replace `OFFSET`/`ROW()` arithmetic (#13–#14), then extract `Rebaselined_PV` (#15–#17) — the real design work; recalculate and compare every chart before and after.
5. Zone gaps/outline groups and sheet-scoped names (#11, #20) only after the above, since they move cells that the chart series address directly.
