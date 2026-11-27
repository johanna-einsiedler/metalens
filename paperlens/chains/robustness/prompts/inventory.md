# Inventory the paper's tables

You are reading one economics paper, attached as a PDF. List **every numbered
table** in it — main body *and* appendices, including any online appendix
bound into this PDF.

For each table report:

- `table_number` — as printed (`Table 2`, `Table A3`, `Table B.1`)
- `table_title` — the full caption
- `page` — the page it appears on, as printed in the paper
- `is_regression_table` — see below
- `n_columns_estimated` — how many **regression columns** it reports

## What counts as a regression table

`is_regression_table = true` when the table reports estimated coefficients
from a regression-style model: OLS, fixed effects, IV/2SLS, probit/logit,
Poisson, diff-in-diff, RD, quantile regression, or similar.

`is_regression_table = false` for:

- summary-statistics and descriptive tables
- correlation matrices
- purely descriptive cross-tabs
- data-source or variable-definition tables

**Balance tables are regression tables when they report regression
coefficients** (e.g. treatment-control differences with standard errors),
and descriptive when they only report group means.

**When in doubt, mark it a regression table.** A table wrongly included costs
one cheap extraction call that returns no regressions; a table wrongly
excluded is a silently missing result.

## Counting columns

`n_columns_estimated` is the number of distinct regressions the table
reports — normally one per numbered column. If the table has panels that each
report their own regressions across the same columns, count
panels × columns. This number is used only to size the extraction, so a close
estimate is fine.

List tables in the order they appear. Do not extract any coefficient values
in this pass.
