## Introduction

An empirical economics article reports its results as regression tables. The Robustness-at-Scale pipeline reads each article and transcribes every regression column — every printed cell with its row type and the link from a standard error to its coefficient, the regressor the column is about, the specification needed to re-run it — into one file per table. Those files are the data; this workflow is how they become a dataset on Metalens.

## Steps

1. **Regression tables** (supplied). The pipeline's JSON files and the article PDFs are uploaded into the run; Metalens renders the pages, anchors every quoted cell on them, and stores one document per paper under the `econ-regression-tables` contract. Nothing is extracted by a model inside Metalens at this step.
2. **Contract check** (deterministic, free). The declared contract — required fields, types, options, ranges — is re-checked on the values as stored, so a correction that breaks the shape is found before the release. Findings are notes on the records; they never change a reviewer's verdict.
3. **Release**. Gated on the review of every new column: the release is cut only when each one is verified or flagged, or when the owner releases anyway, which the release records.

## Review

Every column of a new paper is reviewed before the release (`mode: all`). The reviewer sees the column as the printed table, corrects cells in place, and verifies or flags the column. A sampled review — thirty columns drawn at random, stratified by table — is declared in a later version of this workflow once the sampler is available.

## Versions

The workflow pins the preset by its data contract. A change to the preset's fields, to a step, or to the review declaration is a new version of the workflow; a change of wording is not.
