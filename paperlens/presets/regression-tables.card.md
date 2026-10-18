## Introduction

An empirical economics article reports its results as regression tables: a column per estimated specification, a row per regressor, a standard error under each coefficient, and a block of fit statistics at the bottom. Re-estimating those results from the authors' replication package, or asking how robust they are, needs the printed numbers in machine-readable form together with the specification each column estimates — which controls, which fixed effects, which sample. This preset is the schema for that transcription.

One entry is one estimated column — a (panel, column) pair when the table has panels. It carries every printed cell with its row type and the link from each standard error to its coefficient, the regressor the column is about, and a specification complete enough to re-run the regression given the data. The records are **produced outside Metalens**, by an agent pipeline that reads each article and writes one file per regression table; Metalens imports them with the PDFs, anchors the evidence, and is where a person reviews and corrects them.

The schema and the pipeline were built for *Robustness at Scale*, a study that re-estimates published OLS results from their replication packages across 17 economics journals, 2005–2025. The corpus went from 5,302 articles to 493 OLS-only candidates to 384 with a usable package and PDF.

## How it extracts

One agent session per article (Claude Opus, with file-writing tools and the PDF). The session first inventories every numbered table in the article, main text and appendices, and decides for each whether it reports estimated coefficients; summary statistics, correlation matrices, cross-tabs and variable-definition tables are not regression tables, balance tables are when they report coefficients with standard errors. It then writes one JSON file per regression table, as soon as that table is done, so an interrupted run keeps what was finished.

Five rules govern the transcription, and they are the rules a reviewer should hold the data to:

- **Transcribe exactly.** Every printed cell of the column, top to bottom, verbatim (`0.94***`, `(0.31)`, `Yes`) and parsed (`0.94`, `0.31`, a string marker). Nothing skipped, reordered, rounded or added. The table notes decide whether parenthesised values are standard errors, t-statistics or p-values.
- **Keep the link.** A standard-error row names the row index of the coefficient it belongs to. Nothing in a flat transcription preserves this; the re-estimation step depends on it.
- **Enumerate every control; dereference every cross-reference; state the sample operationally.** `["age", "age squared", "years of schooling"]`, never `"demographic controls"`; "controls as in Table 3" is resolved to the list; "eligible households" becomes the rule and the N.
- **Never infer from a sibling column or from convention.** A field the paper does not state is null or `unknown`. A wrong specification detail is worse than a missing one, because it will be acted on.
- **No judgement about the result.** Whether a column is the headline, a main or a robustness result is decided in a separate pass with the whole paper in view; here the pipeline only transcribes. The regressor of interest is named from the paper's text, with a sentence on why, never guessed from position.

Evidence: at least one verbatim snippet from the table or its notes per table, with the page; never a bare coefficient value, which recurs through a paper and locates nothing.

### The pipeline's instructions

The prompt below is the session prompt (appendix E.2 of the study), reproduced verbatim. The placeholders are filled by the pipeline: `{PDF_PATH}` the article's PDF; `{TABLES_DIR}` the output folder for that article; `{ARTID}` the article id that prefixes every `regression_id`; `{TABLE_SCHEMA}` the JSON skeleton of one table file — the fields of this preset, with `cells`, `target_regressors` and the specification nested as documented under Fields; `{ALREADY_DONE}` the list of tables already written when a session is resumed; `{REVIEWER_HINT}` a reviewer's note when an article is re-extracted after the completeness check (empty otherwise). The `headline_criteria` field the prompt mentions belongs to the later classification pass and is not part of this preset.

````text
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
````

## Verification

Four checks, three complete.

**1. Completeness against the PDF text layer.** For PDFs with a text layer, a regular expression finds every "Table N" caption and scans the text up to the next caption for a regression signature: parenthesised or bracketed decimals, and decimals followed by stars. The scan returned 2,517 captions; 1,106 with a regression signature matched an extracted table; 137 with a signature had no match. Of those, 62 carry a title identifying a descriptive table (summary statistics, balance, correlations), 7 were mentions of a table in running text, and the remaining 68 were reviewed by hand against the PDF: 2 were regression tables the extraction should have contained. One was a genuine miss and was re-extracted with the reviewer's judgement added to the instructions; the other had been extracted under a table number the scan normalised differently.

**2. Agreement with re-estimated values.** Where the authors' replication code runs, the printed values can be compared with what the code produces. Of 44,005 cells compared, 39,453 (89.7 %) agree at printed precision — within half of the last printed digit. The disagreements are mostly blanks, not wrong numbers: a cell the code does not produce rather than a mistranscribed one. The comparison surfaced 28 transcription errors, which were corrected.

**3. A second, independent run.** All articles were extracted a second time with the same model, prompt and code. Agreement is measured first on *which tables* are found and then, for tables both runs found, on *which values* are transcribed — as sets of values, because the two runs sometimes split the same printed table into a different number of columns.

| | |
|:--|--:|
| Articles with an identical set of regression tables | 370 of 384 (96.4 %) |
| Tables found by at least one run | 1,111 |
| of which found by both runs | 1,096 (98.6 %) |
| Coefficient values agreeing (tables found by both) | 96.7 % |
| Standard errors, t, p and confidence intervals agreeing | 96.1 % |
| All numeric cells agreeing | 95.7 % |
| Tables transcribed identically | 878 of 1,096 (80.1 %) |
| Agreement on runnable regressions | 97.9 % |
| Agreement on headline regressions | 95.0 % |

Transcription is close to deterministic; what varies is the inventory — the judgement whether a table of group means with standard errors, or a decomposition, is a regression table. That variation sits where a human coder would also hesitate, and it affects which tables enter the data rather than the values recorded from them.

**4. Human coding, in progress.** 35 articles are being coded by people on this platform, once blind and once starting from the pipeline's output. The figures below are read live from those datasets as they are verified.

### What the corpus contains

| | |
|:--|--:|
| Articles extracted | 384 |
| with at least one regression table | 308 |
| Estimated columns (regressions) | 9,231 |
| Printed cells transcribed | 87,987 |
| Coefficients | 28,978 |
| Standard errors, t, p and confidence intervals | 29,594 |
| Sample sizes | 7,224 |
| R², F and other statistics | 12,315 |
| String markers and panel headers | 9,876 |
| Columns with an identified regressor of interest | 92.2 % |

## Limitations

The checks measure transcription and stability, not correctness of the specification fields: whether the enumerated controls are the right ones is only tested indirectly, when a re-estimation reproduces the printed number.

The corpus is OLS-only by construction; the schema carries instruments, weights and clustering, but those fields have not been exercised at scale.

The pipeline ran one model; a different model or a later version of the same one will produce a different inventory at the margin described above. The schema id of this preset identifies the data contract, not the pipeline version — the pipeline records its own prompt hash and model per article.

A reviewer sees one column at a time against the PDF. The errors that survive are those a column-wise reading does not expose: a coefficient placed in a sibling column, or a control list copied from the wrong table.

## Related work

- *Robustness at Scale* (working paper; author list not final). The appendix on the extraction of published results is the source of the figures above. [reference to add]
- Young, A. (2019). Channeling Fisher: Randomization tests and the statistical insignificance of seemingly significant experimental results. *Quarterly Journal of Economics.* [DOI to add]
- Young, A. (2022). Consistency without inference: Instrumental variables in practical application. *European Economic Review.* [DOI to add]
- Broderick, T., Giordano, R., & Meager, R. (2020). An automatic finite-sample robustness metric: When can dropping a little data make a big difference? [arXiv:2011.14999](https://arxiv.org/abs/2011.14999).
- Sibling preset for Danish register-data articles, same cell grammar, flatter specification: [Register-data tables](/preset?id=register-tables).
