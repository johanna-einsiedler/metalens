## Introduction

The output of the Robustness-at-Scale pipeline's re-estimation stage (appendix E.4 of the paper): for every regression of the blinded grid, a sandboxed agent ran the deposited Stata code and filled in the withheld cells, or recorded why it could not. One entry per regression; the cells are positioned by the row index of the published table, so the workflow's grade step can compare them cell by cell at printed precision.

This dataset is never reviewed on its own: the robustness workflow reads it against the regression tables of the same paper and writes the verdict on each column there.

## How it extracts

Nothing is extracted here. The entries are the targets of `reproduce/<artid>.result.json`, converted by the project's `metalens/sync/export.py`: the regression id shortened to `table::panel::column` as in the tables, the status (`ok` or `failed`) and method (`stata`, `bypass`, `not_attempted`), the reason for a failure and its detail, the N the re-estimation achieved, the do-file, the command and the captured specification the values came from, and one row per filled cell with its row index, the value as Stata printed it and the number.

The re-estimation itself runs in the pipeline, on a machine with Stata and the replication package, under the pipeline's integrity checks (a hashed snapshot of the workspace, a specification stamped by the run); Metalens holds the result as supplied data.

## Verification

The contract check re-reads every entry against this schema. The grading happens on the tables: the workflow's `grade` step compares each withheld cell of a regression column with the value here at half the last printed digit, sign never ignored, and writes the verdict, the counts and a finding per cell that did not reproduce on the column — where the reviewer sees it next to the printed table.
