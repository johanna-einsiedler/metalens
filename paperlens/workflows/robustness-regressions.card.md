## Introduction

An empirical economics article reports its results as regression tables. The Robustness-at-Scale pipeline reads each article and transcribes every regression column — every printed cell with its row type and the link from a standard error to its coefficient, the regressor the column is about, the specification needed to re-run it — into one file per table. Those files are the data; this workflow is how they become a dataset on Metalens.

## Steps

1. **Regression tables** (supplied). The pipeline's JSON files and the article PDFs are uploaded into the run; Metalens renders the pages, anchors every quoted cell on them, and stores one document per paper under the `econ-regression-tables` contract. Nothing is extracted by a model inside Metalens at this step.
2. **Contract check** (deterministic, free). The declared contract — required fields, types, options, ranges — is re-checked on the values as stored, so a correction that breaks the shape is found before the release. Findings are notes on the records; they never change a reviewer's verdict.
3. **Number check** (deterministic, free). The printed cells of each column are checked against each other: every standard error names its coefficient, the parsed number is the printed one, stars agree with the significance level and the p-value, a t-statistic with its coefficient and standard error, the stated N with the printed one. Findings are notes on the records, shown first in the review.
4. **Release**. Gated on the review of the drawn columns: the release is cut only when each one is verified or flagged, or when the owner releases anyway, which the release records.

## Review

Thirty of the new columns are drawn at random, stratified by table (`mode: sample`, `n: 30`), and reviewed before the release; when a run adds fewer, every column is reviewed. The reviewer sees each drawn column as the printed table, corrects cells in place, and verifies or flags it. A flag or a value-changing correction counts as a disagreement; the badge reports the draw, the checked share and the estimated error rate with its interval for the columns the draw speaks for.

## Versions

The workflow pins the preset by its data contract. A change to the preset's fields, to a step, or to the review declaration is a new version of the workflow; a change of wording is not.
