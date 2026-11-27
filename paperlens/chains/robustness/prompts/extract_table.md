# Extract every regression in ONE table

You are reading one economics paper, attached as a PDF. Extract **{TABLE_NUMBER}**
("{TABLE_TITLE}", around page {PAGE}) and nothing else. Ignore every other
table.

Downstream, someone who has the paper's *data* but not its *code* must be able
to re-run each regression from what you write, and a program will compare
their numbers to yours cell by cell. Two things therefore matter above all:
**transcribe exactly**, and **describe the specification completely**.

If this table turns out not to report regression coefficients (summary
statistics, correlations, variable definitions), set `is_regression_table`
to false and return an empty `regressions` list.

---

## 1. One regression per column

Emit one entry in `regressions[]` per estimated column. Different columns of
the same table — different controls, different samples, different outcomes —
are different regressions. If the table has panels that each report their own
regressions, emit one entry per (panel, column) pair.

Set for each:

- `regression_id` — exactly `{ARTID}::{TABLE_SLUG}::<panel>::C<column>`, using
  the panel letter or `_` when there are no panels, and the column label with
  punctuation stripped (column `(3)` → `C3`).
- `table_number`, `table_title`, `page` — copy from the header above.
- `panel` — panel label, or null.
- `column` — the column label exactly as printed, e.g. `(3)`.
- `outcome_variable` — the dependent variable for this column, as the paper
  names it.

## 2. Transcribe every cell

`cells[]` holds every value printed in the column, including controls, the
constant, and the statistics rows at the bottom. Do not skip rows, do not
reorder them, do not round, and do not add rows the paper does not print.

Per cell:

- `row_label` / `column_label` — exactly as printed.
- `row_index` — 0-based position down the column, counting every printed row
  including standard-error rows and statistics rows.
- `col_index` — 0-based position of this column within the table.
- `raw_text` — the cell verbatim, including stars and parentheses:
  `0.94***`, `(0.31)`, `Yes`.
- `numeric_value` — the number with stars, parentheses, commas and `%`
  stripped: `0.94`, `0.31`. Preserve the sign. Preserve every printed digit;
  never round or extend. Null for non-numeric cells.
- `row_type` — one of:
  `coefficient`, `se`, `p_value`, `ci`, `t_stat`, `statistic_r2`,
  `statistic_n_obs`, `statistic_f`, `statistic_other`, `string`,
  `panel_header`.
- `statistic_detail` — for `statistic_other` only, what the statistic is
  (`adjusted R-squared`, `mean of dependent variable`, `number of clusters`).
- `refers_to` — on an `se` / `p_value` / `ci` / `t_stat` row, the `row_index`
  of the coefficient it belongs to. Null on coefficient rows. **This link is
  load-bearing**: it is how a standard error is matched to its estimate.
- `significance_stars` — 0–3. `significance_level` — 0.1 / 0.05 / 0.01, or null.
- `is_string` — true for non-numeric structural cells (`Yes`, `No`, `✓`), for
  example fixed-effect indicator rows. These are structure, not results.

**Read the table notes before assigning `row_type`.** Parenthesised values are
standard errors in most papers but t-statistics or p-values in some; the notes
say which. If they are t-statistics, use `row_type="t_stat"`, not `se`.

## 3. Identify the target regressor

`target_regressors[]` names the coefficient(s) the column is *about* — the
treatment, the instrumented regressor, the policy variable. Give the row label
as printed, the `row_index` of its coefficient cell, and one sentence on why
it is the coefficient of interest.

Most columns have exactly one. Emit several when the paper's claim rests on
several jointly (e.g. a treatment and its interaction). Emit an empty list
only when the column genuinely has no focal coefficient — a balance table, or
a pure first stage where every regressor is an instrument.

Do not guess from position. If you cannot tell which regressor is focal from
the paper's text, return an empty list and say so in `notes`.

## 4. Describe the specification

`spec` must be complete enough to re-run the regression given the data. Three
rules decide whether it is usable:

- **Enumerate every control individually.** Write
  `["age", "age squared", "years of schooling", "household size"]`, never
  `["demographic controls"]`. If the paper lists them in a footnote or an
  appendix table, go there and copy them out.
- **Dereference every cross-reference.** "Controls are as in Table 3" or
  "see Appendix B for the sample" must be resolved to the actual content.
  Whoever reads your output cannot see the paper.
- **State sample restrictions operationally.** Not "among eligible
  households", but "households with income below 150% of the poverty line in
  the baseline survey (Section 4.2); N = 3,412". Give the rule and the
  resulting N where the paper states it.

Also set, where the paper says: `model_type`, `fixed_effects` (each dimension
separately), `iv` (endogenous variable and its instruments), `weights_type`
and `weights_variable`, `cluster_level`, `se_type`, `unit_of_observation`,
`time_period`, `outcome_construction`, and `data_construction_steps`.

Leave a field null or `"unknown"` when the paper does not say. **Never infer a
specification detail from a sibling column or from convention** — a wrong
value is worse than a missing one, because it will be acted on.

Use `reproduction_notes` for anything else someone re-running this would need.

## 5. Do not classify the result

Leave `headline_criteria` unset, and do not judge whether this is a headline,
main, or robustness result.

That judgement is made in a separate pass with the whole paper in view,
because two of its criteria are comparative and cannot be settled from one
table: whether a specification is *preferred* is a claim relative to the
paper's other columns, and whether a table is a *robustness* table depends on
which other table carries the main result. Answering here would mean guessing.

## 6. Evidence

`evidence[]` carries the quotations that substantiate the extraction itself:
at minimum one `table` or `table_notes` snippet supporting the transcription.
Each item needs `snippet` (verbatim), `page`, `source`, and `field` naming
what it supports.

Do not cite bare coefficient values as evidence — a number like `-0.46`
recurs throughout a paper and does not locate anything.

---

Extract only what the paper prints. If a value is not visible in the table,
leave it null; do not reconstruct it from other columns, and do not compute it.
