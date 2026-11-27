## Introduction

An empirical economics article has three sides the Robustness-at-Scale pipeline produces: the regression tables it prints, the claims its abstract and introduction state with the results that carry them, and the values a sandboxed agent got when it re-ran the deposited code for every regression. This workflow runs all three over the same papers as one process: the tables, the claims and the re-estimations are linked as a group, every new paper enters all three with its PDF, the claims are checked against that paper's own tables, two deterministic steps write on each column what the other two sides say about it, and a reviewer looks at both readable sides of a paper together before anything is released.

Everything that needs the replication package, Stata or a sandboxed agent — the data check, the workspace, the re-estimation itself, the provenance — runs in the pipeline. Metalens holds its outputs as supplied data, does the deterministic bookkeeping on them with the same code the pipeline runs on disk, and puts the hand decisions in front of reviewers.

## Steps

1. **Regression tables** (supplied, into the `tables` dataset). The pipeline's table files and the article PDFs are uploaded into the run.
2. **Claims and their results** (supplied, into the `claims` dataset). The pipeline's claims files, with the same PDFs.
3. **Re-estimated regressions** (supplied, into the `reproduced` dataset). The pipeline's `reproduce/<artid>.result.json`, converted by `metalens/sync/export.py`: per regression, the status, the reason for a failure, the N achieved and one value per withheld cell. A paper the pipeline did not re-estimate has no file here.
4. **Contract checks** (deterministic). Each side re-checked against its declared contract.
5. **Number check** (deterministic). The printed cells of each regression column against each other.
6. **Estimates against the tables** (deterministic). Each cited point estimate against the coefficient cells of the same paper's tables, inside the group.
7. **Headline regressions** (deterministic, writes on the tables). The claim chain's rule: a column is a headline result when at least one main estimate of a stated claim links to it. Written on each column — `headline`, `n_main`, the claims it is the main estimate of, whether the text points to it — and on the paper; a main result that names no column of the tables is a finding.
8. **Grade the re-estimation** (deterministic, writes on the tables). The pipeline's grading: every withheld cell against its reproduced value at half the last printed digit, sign never ignored, a whole-paper rescaling by a power of ten detected and recorded. One verdict per column (`match`, `partial`, `mismatch`, `sign_flip`, `failed`, `not_attempted`, `not_returned`), the paper's match rate, and a finding per cell that did not reproduce. Every value lands as an audited correction by the engine.
9. **Joint review**. Every new paper is reviewed on both readable sides: its regression columns with their headline and reproduction verdicts, then its claims with their verdicts against the tables, in one worklist.
10. **Sign flips** (routed, never holds the release). A paper whose re-estimation reproduced a value at the right size with the wrong sign goes to a reviewer, who decides whether the published table, the transcription or the re-estimation is wrong.
11. **Release**, gated on the three datasets and the joint review. A release of one dataset releases the others with it.

## Review

The joint review walks the run's new papers one at a time, each on one page. Every regression table is shown whole, its panels stacked as printed, and gets one verdict; the reviewer checks what the validation coders check — the printed cells, each column's target regressor (marked in the table) and its SE type — not the specification fields. Under each table sit the claims that cite it, as cause → effect with their quotes and the results that carry them (the main estimate starred), each with its verdict against the table and a verdict of its own; a claim's fields are corrected there too. The gate opens when every record of every reviewed paper has a verdict; the release form shows what is left.

A correction a reviewer makes to a printed value goes back to the pipeline through `metalens/sync/import.py` as a row of its corrections file, which regrades the paper.
