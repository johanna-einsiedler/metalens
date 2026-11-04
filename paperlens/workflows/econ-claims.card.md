## Introduction

The Robustness-at-Scale pipeline also reads what each article claims: the statements in the abstract and introduction, the cause and the effect each names, the sign, and the results in the tables that carry them. Those claims files enter a dataset through this workflow, paired with the tables dataset of the same papers through the companion cross-check.

## Steps

1. **Claims and their results** (supplied). The pipeline's claims files and the article PDFs are uploaded into the run; Metalens anchors the quoted sentences on the pages and stores one document per paper under the `register-claims` contract.
2. **Contract check** (deterministic). The declared contract re-checked on the stored values.
3. **Estimates against the tables** (deterministic). Each cited point estimate against the coefficient cells of the companion tables dataset: a resolved column with no cell equal to the estimate is an error, an exhibit no column matches is a warning. The step is blocked until the dataset names its companion.
4. **Corpus edges** (one model call per chunk of claims, then a deterministic merge). The model maps each claim's cause and effect to a shared concept vocabulary; the merge groups the claims into one edge per (cause, effect) pair with its majority sign, the number of claims and articles behind it, and the studies. An update sees the committed edges and the claims added since; the draft is reviewed row by row — kept, revised, new, removed — with the new claims that explain each change.
5. **Release**, gated on the review of every new claim and on the committed edges.

## Review

Every claim of a new paper is reviewed before the release, and so is every draft of the corpus edges. The reviewer reads the claim as cause → effect with its quotes and the results that carry it, each with its verdict against the tables, and verifies or flags it.
