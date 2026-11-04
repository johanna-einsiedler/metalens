## Introduction

An empirical economics article has two sides the Robustness-at-Scale pipeline reads: the regression tables it prints, and the claims its abstract and introduction state with the results that carry them. This workflow runs both over the same papers as one process: the tables dataset and the claims dataset are linked as a group, every new paper enters both, the claims are checked against that paper's own tables, and a reviewer looks at both sides of a paper together before anything is released.

## Steps

1. **Regression tables** (supplied, into the `tables` dataset). The pipeline's table files and the article PDFs are uploaded into the run.
2. **Claims and their results** (supplied, into the `claims` dataset). The pipeline's claims files, with the same PDFs.
3. **Contract checks** (deterministic). Each side re-checked against its declared contract.
4. **Number check** (deterministic). The printed cells of each regression column against each other.
5. **Estimates against the tables** (deterministic). Each cited point estimate against the coefficient cells of the same paper's tables, inside the group.
6. **Corpus edges** (a model call per chunk of claims, then a merge). The claims' causes and effects mapped to shared concepts and grouped into edges.
7. **Joint review**. Every new paper is reviewed on both sides: its regression columns, then its claims with their verdicts against the tables, in one worklist.
8. **Release**, gated on the tables, the claims, the committed edges and the joint review. A release of one dataset releases the other with it.

## Review

The joint review walks the run's new papers one at a time. For each paper the reviewer sees the regression columns as the printed table, verifies or flags them, then the claims as cause → effect with their quotes and the results that carry them, each with its verdict against the tables. The gate opens when every record of every reviewed paper has a verdict; the release form shows what is left.
