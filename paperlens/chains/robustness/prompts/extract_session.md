# Extract every regression in this paper

The paper is the PDF at `{PDF_PATH}`. Read it — every page, using page ranges.

Write your results as **one JSON file per table** into `{TABLES_DIR}`.
Do not put table contents in your final reply; the files are the deliverable.

{ALREADY_DONE}

{REVIEWER_HINT}

---

## What to do

1. **Inventory.** Identify every numbered table in the paper — main body *and*
   appendices, including any online appendix bound into this PDF. For each,
   decide whether it reports estimated coefficients from a regression-style
   model (OLS, FE, IV/2SLS, probit/logit, Poisson, diff-in-diff, RD, quantile).
   Summary statistics, correlation matrices, cross-tabs and variable-definition
   tables are not regression tables. Balance tables **are** regression tables
   when they report coefficients with standard errors, and are not when they
   report only group means. **When in doubt, treat it as a regression table.**

2. **For each regression table**, write `{TABLES_DIR}/<slug>.json` matching the
   schema below. The slug is the table number with punctuation stripped:
   `Table 2` → `T2.json`, `Table A3` → `TA3.json`, `Table B.1` → `TB1.json`.

   Write each file **as soon as you finish that table**, before starting the
   next one. Do not batch the writes to the end. If you run out of budget
   part-way, everything already written is kept — everything held in your head
   is lost.

3. Skip non-regression tables entirely. Do not write files for them.

4. When every regression table has a file, reply with the manifest.

**If the paper contains no regression tables at all** — some short papers report
everything in figures or in the text — that is a legitimate answer, not a
failure. Set `no_regression_tables` to true and say in `inventory_note` what the
paper reports instead. Do not invent tables, and do not summarise the paper's
findings in your reply: an empty manifest with no flag is read as a failed run
and will be retried.

---

## The schema for each table file

```json
{TABLE_SCHEMA}
```

---

## Transcription rules

Downstream, someone who has the paper's *data* but not its *code* must be able
to re-run each regression from what you write, and a program will compare their
numbers to yours cell by cell. Two things matter above all: **transcribe
exactly**, and **describe the specification completely**.

### One regression per column

One entry in `regressions[]` per estimated column. Different columns — different
controls, different samples, different outcomes — are different regressions. If
the table has panels that each report their own regressions, emit one entry per
(panel, column) pair.

- `regression_id` — exactly `{ARTID}::<slug>::<panel>::C<column>`, using the
  panel letter or `_` when there are no panels, and the column label with
  punctuation stripped (column `(3)` → `C3`). The slug is the same one used for
  the filename.
- `outcome_variable` — the dependent variable for this column, as the paper names it.

### Every cell

`cells[]` holds every value printed in the column, including controls, the
constant, and the statistics rows at the bottom. Do not skip rows, do not
reorder them, do not round, and do not add rows the paper does not print.

- `row_label` / `column_label` — exactly as printed.
- `row_index` — 0-based position down the column, counting every printed row
  including standard-error rows and statistics rows.
- `col_index` — 0-based position of this column within the table.
- `raw_text` — verbatim, including stars and parentheses: `0.94***`, `(0.31)`, `Yes`.
- `numeric_value` — the number with stars, parentheses, commas and `%` stripped.
  Preserve the sign. Preserve every printed digit; never round or extend.
  Null for non-numeric cells.
- `row_type` — one of `coefficient`, `se`, `p_value`, `ci`, `t_stat`,
  `statistic_r2`, `statistic_n_obs`, `statistic_f`, `statistic_other`, `string`,
  `panel_header`.
- `refers_to` — on an `se` / `p_value` / `ci` / `t_stat` row, the `row_index` of
  the coefficient it belongs to. Null on coefficient rows. **This link is
  load-bearing**: it is how a standard error is matched to its estimate.
- `significance_stars` 0–3; `significance_level` 0.1 / 0.05 / 0.01 or null.
- `is_string` — true for non-numeric structural cells (`Yes`, `No`, `✓`).

**Read the table notes before assigning `row_type`.** Parenthesised values are
standard errors in most papers but t-statistics or p-values in some; the notes
say which. If they are t-statistics, use `t_stat`, not `se`.

### The target regressor

`target_regressors[]` names the coefficient(s) the column is *about* — the
treatment, the instrumented regressor, the policy variable. Give the row label
as printed, the `row_index` of its coefficient cell, and one sentence on why it
is the coefficient of interest.

Most columns have exactly one. Emit several when the paper's claim rests on
several jointly (e.g. a treatment and its interaction). Emit an empty list only
when the column genuinely has no focal coefficient — a balance table, or a pure
first stage where every regressor is an instrument. Do not guess from position;
if the text does not tell you, return an empty list and say so in `notes`.

### The specification

`spec` must be complete enough to re-run the regression given the data.

- **Enumerate every control individually.** Write `["age", "age squared",
  "years of schooling", "household size"]`, never `["demographic controls"]`.
  If the paper lists them in a footnote or appendix table, go there and copy them out.
- **Dereference every cross-reference.** "Controls are as in Table 3" or "see
  Appendix B for the sample" must be resolved to the actual content. Whoever
  reads your output cannot see the paper.
- **State sample restrictions operationally.** Not "among eligible households",
  but "households with income below 150% of the poverty line in the baseline
  survey (Section 4.2); N = 3,412".

Also set, where the paper says: `model_type`, `fixed_effects` (each dimension
separately), `iv`, `weights_type`, `weights_variable`, `cluster_level`,
`se_type`, `unit_of_observation`, `time_period`, `outcome_construction`,
`data_construction_steps`.

Leave a field null or `"unknown"` when the paper does not say. **Never infer a
specification detail from a sibling column or from convention** — a wrong value
is worse than a missing one, because it will be acted on.

### Evidence

`evidence[]` carries quotations substantiating the transcription: at minimum one
`table` or `table_notes` snippet per table. Each needs `snippet` (verbatim),
`page`, `source`, and `field`. Do not cite bare coefficient values — a number
like `-0.46` recurs throughout a paper and locates nothing.

**Do not judge whether a result is a headline, main, or robustness result, and
do not fill `headline_criteria`.** That judgement is made separately, with the
whole paper in view. Here, transcribe.

---

Extract only what the paper prints. If a value is not visible in the table,
leave it null; do not reconstruct it from other columns, and do not compute it.
