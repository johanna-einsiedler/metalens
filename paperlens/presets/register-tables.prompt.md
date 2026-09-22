# TASK

You are reading one empirical paper, attached as a PDF. Extract EVERY regression table —
main body and appendices, including any online appendix bound into this PDF — one entry per
estimated column. Return exactly ONE JSON object matching the schema at the end.

Downstream, someone who has the paper's data but not its code must be able to re-run each
regression from what you write, and a program will compare their numbers to yours cell by
cell. Two things therefore matter above all: transcribe exactly, and describe the
specification completely.

# WHICH TABLES

A regression table reports estimated coefficients from a regression-style model: OLS, fixed
effects, IV/2SLS, probit/logit, Poisson, diff-in-diff, RD, event study, quantile regression
or similar. Balance tables are regression tables when they report regression coefficients
(treatment–control differences with standard errors), descriptive when they only report
group means. Summary-statistics tables, correlation matrices, cross-tabs and
variable-definition tables give NO entries. When in doubt, include the table.

A paper with no regression table returns `regressions: []`.

# ONE ENTRY PER COLUMN

Emit one entry per estimated column. Different columns of the same table — different
controls, different samples, different outcomes — are different regressions. If the table
has panels that each report their own regressions, emit one entry per (panel, column) pair.

- `regression_id` — exactly `T<table>::<panel>::C<column>`: the table number, the panel
  letter or `_` when there are no panels, and the column label with punctuation stripped
  (Table 3 column (2), no panels → `T3::_::C2`; Table A1 panel B column (1) → `TA1::B::C1`).
- `table_number`, `table_title`, `panel`, `column`, `column_label` — as printed.
- `outcome_variable` — the dependent variable for this column, as the paper names it.

# TRANSCRIBE EVERY CELL

`cells` holds every value printed in the column, top to bottom, including controls, the
constant and the statistics rows at the bottom. Do not skip rows, do not reorder them, do not
round, and do not add rows the paper does not print.

- `row_label` / `column_label` — exactly as printed.
- `row_index` — 0-based position down the column, counting every printed row including
  standard-error rows and statistics rows.
- `raw_text` — the cell verbatim, including stars and parentheses: `0.94***`, `(0.31)`, `Yes`.
- `numeric_value` — the number with stars, parentheses, commas and `%` stripped: `0.94`,
  `0.31`. Preserve the sign. Preserve every printed digit; never round or extend. null for
  non-numeric cells.
- `row_type` — `coefficient`, `se`, `t_stat`, `p_value`, `ci`, `statistic_r2`,
  `statistic_n_obs`, `statistic_f`, `statistic_other`, `string` (non-numeric structural
  cells such as `Yes` / `No` / `✓` — structure, not results), `panel_header`.
- `refers_to` — on an `se` / `t_stat` / `p_value` / `ci` row, the `row_index` of the
  coefficient it belongs to. null on coefficient rows. This link is load-bearing: it is how a
  standard error is matched to its estimate.
- `significance_stars` — 0–3, read from the stars printed on the cell.

Read the table notes before assigning `row_type`. Parenthesised values are standard errors in
most papers but t-statistics or p-values in some; the notes say which. If they are
t-statistics, use `t_stat`, not `se`.

# THE TARGET REGRESSOR

`target_regressors` names the coefficient(s) the column is ABOUT — the treatment, the
instrumented regressor, the policy variable — by row label as printed. Most columns have
exactly one. Give several only when the paper's claim rests on them jointly (a treatment and
its interaction). Give an empty list only when the column genuinely has no focal coefficient
— a balance table, or a pure first stage where every regressor is an instrument — and say so
in `notes`. Do not guess from position.

# THE SPECIFICATION

Complete enough to re-run the regression given the data. Three rules decide whether it is
usable:

- Enumerate every control individually: `["age", "age squared", "years of schooling"]`,
  never `["demographic controls"]`. If the paper lists them in a footnote or an appendix
  table, go there and copy them out.
- Dereference every cross-reference. "Controls are as in Table 3" or "see Appendix B for the
  sample" must be resolved to the actual content. Whoever reads your output cannot see the
  paper.
- State sample restrictions operationally. Not "among eligible households", but "households
  with income below 150% of the poverty line in the baseline survey (Section 4.2); N =
  3,412". Give the rule and the resulting N where the paper states it.

Also set, where the paper says: `model_type`, `fixed_effects` (each dimension separately),
`iv` (endogenous variable and its instruments), `weights_type` and `weights_variable`,
`cluster_level`, `se_type`, `unit_of_observation`, `time_period`, `outcome_construction`,
`data_construction_steps`. Leave a field null or `unknown` when the paper does not say.
Never infer a specification detail from a sibling column or from convention — a wrong value
is worse than a missing one, because it will be acted on. `reproduction_notes` holds
anything else someone re-running this would need.

Do not judge whether a column is a headline, main or robustness result; that judgement
belongs to the claims extraction, which has the whole paper in view.

# EVIDENCE

Cite each entry's table once: the caption line or the table-notes sentence that supports the
transcription, with `source` the table number ("Table 3"). Do not cite bare coefficient
values — a number like `-0.46` recurs throughout a paper and does not locate anything.

# CONFIDENCE CALIBRATION

`spec`: high when the paper states outcome, target regressor, model, controls and sample for
this column; medium when a cross-reference had to be resolved; low when something was left
unknown that the paper probably states elsewhere. `cells`: high when every printed value was
legible and its row type unambiguous; medium when the notes had to decide what parentheses
hold; low when a value was hard to read.
