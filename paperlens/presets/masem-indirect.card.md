## Introduction

Meta-analytic structural equation modelling (MASEM) pools correlation matrices from many primary studies and fits a structural model to the pooled matrix. The coding step is the expensive part: for every study, the relevant correlations, the reliabilities of the measures and the characteristics of the sample have to be found in a PDF and typed into a table, and then checked by a second person. MASEMiner is a pair of presets for Metalens that do the first pass with a language model and leave the checking to a person, with every value shown next to the passage it was read from.

The two presets correspond to the two ways a primary study can carry MASEM-relevant information. The **direct-information** preset — this one — extracts effect sizes the paper reports as such: correlations in a matrix or in the text, and related bivariate statistics. The **indirect-information** preset extracts the measurement model — item-level factor loadings and factor correlations — from which a correlation matrix among subscales can be reconstructed. Both also extract sample characteristics (sample size, percentage female, mean age, country, language) and the reliabilities of the measures, each with an evidence link into the PDF.

MASEMiner was developed and validated by Ulrich Schroeders, Timo Gnambs and Johanna Einsiedler; the study is described in a preprint ([Schroeders, Gnambs & Einsiedler, 2026](https://doi.org/10.31234/osf.io/dceqs_v1), not yet peer-reviewed). The prompts and the raw model outputs of the validation are on the [OSF](https://osf.io/ec4ug).

## How it extracts

The user names the **scale** (its name and abbreviation), the **number of items**, and the **item wording** in order. The item texts matter more than the numbers: papers renumber, reorder and paraphrase items, and the prompt instructs the model to align loadings by wording first and by printed item number second.

For each distinct sample or group with its own loading table, the model extracts exactly one factor solution: the item-level loadings including cross-loadings and negative loadings as printed, the latent correlations between the retained factors as unique off-diagonal pairs, and the sample and analysis details — country, language, sample size, percentage female, mean age, whether the analysis was exploratory or confirmatory, the estimation method, and the number of retained factors.

Most of the prompt is decision rules, because factor-analytic papers rarely report one clean table. The rules say which solution to take when a paper reports several (first-order solutions before hierarchical or bifactor ones; within a model type, the solution with the most retained scale-relevant factors; author preference does not override this), how to treat multiple samples (never merged, never averaged), how to assign a loading to a factor (by printed column position, not by theory or boldface), what to do with orthogonal rotations (factor correlations set to zero), with suppressed cells (null, never zero), and how to tell a latent factor correlation from an observed subscale correlation (only the former is extracted). Factors are relabelled F1…Fn by printed position; the paper's own labels go into the notes.

The template adds the same four MASEMiner requirements as the direct-information preset: no imputation of unreported values, a fixed JSON schema, a three-level confidence rating per target (factor loadings, factor correlations, metadata) per sample, and an evidence snippet with page for every value, used to highlight the passage in review. In this preset the loading table is cited as a block, and a click on a loading in the review screen is located on the page using the item's wording as an anchor.

## Verification

The preset was developed on one factor-analytic meta-analysis and validated on a second. Agreement with the published human coding is reported as sensitivity, precision and Jaccard index (definitions as on the direct-information card; wrong values count as both a miss and a false positive). The model was GPT-5.5 (`gpt-5.5-2026-04-23`). Figures after correcting mistakes found in the human coding, with the original in parentheses.

**Development set** — Toronto Alexithymia Scale (TAS-20), Schroeders et al. (2022), k = 20 papers.

| Target | n | SEN | PRE | JAC |
|:--|--:|--:|--:|--:|
| Factor loadings | 2020 | 1.00 (.98) | 1.00 (.98) | 1.00 (.96) |
| Factor correlations | 127 | .93 | .95 | .89 |
| Mean age | 30 | 1.00 | 1.00 | 1.00 |
| Country | 30 | .97 | 1.00 | .97 |
| Language | 30 | 1.00 | 1.00 | 1.00 |
| Sample size | 30 | 1.00 (.93) | 1.00 (.93) | 1.00 (.88) |
| % female | 30 | .90 | .96 | .87 |
| EFA / CFA | 30 | 1.00 | 1.00 | 1.00 |
| Number of factors | 30 | 1.00 | 1.00 | 1.00 |

**Validation set** — Need for Cognition Scale (NCS-18), Schroeders et al. (2024), k = 10 papers.

| Target | n | SEN | PRE | JAC |
|:--|--:|--:|--:|--:|
| Factor loadings | 324 | 1.00 (.99) | 1.00 (.99) | .99 |
| Factor correlations | 11 | 1.00 (.91) | 1.00 (.91) | 1.00 (.83) |
| Mean age | 11 | 1.00 | 1.00 | 1.00 |
| Country | 11 | .91 | 1.00 | .91 |
| Language | 11 | .91 | 1.00 | .91 |
| Sample size | 11 | 1.00 | 1.00 | 1.00 |
| % female | 11 | 1.00 (.88) | 1.00 (.88) | 1.00 (.78) |
| EFA / CFA | 11 | 1.00 | 1.00 | 1.00 |
| Number of factors | 11 | 1.00 | 1.00 | 1.00 |

Loadings — more than two thousand cells in the development set — were transcribed essentially without error once the human coding was corrected. The residual disagreements are concentrated in the factor correlations of the development set, where papers report observed subscale correlations next to latent ones and the choice between them is the hard part for a person too. Country and language misses are papers that state neither and leave them to be inferred from affiliations.

Two of the four student-coder datasets being built on this platform use this preset (TAS-20 and NCS-18, ten papers each, blind and tool-assisted). The figures below are read live from those datasets as they are verified.

## Limitations

The decision rules favour ordinary first-order solutions. A meta-analysis that targets a bifactor or higher-order model needs the preference order changed in the prompt; the preset's parameters do not expose it.

Validation used scales with a stable item set. Scales with many short forms, or papers that analyse an item subset, exercise the item-alignment rules harder than the two studies did.

As for the direct-information preset: a single model at validation time, a human coding that itself contained mistakes, and the risk that a reviewer accepts a complete-looking loading table without checking the column assignment.

## Related work

- Schroeders, U., Gnambs, T., & Einsiedler, J. (2026). *MASEMiner – AI-assisted data extraction for meta-analytic structural equation modeling.* PsyArXiv. [doi:10.31234/osf.io/dceqs_v1](https://doi.org/10.31234/osf.io/dceqs_v1) — preprint, not peer-reviewed.
- Supplement: [osf.io/ec4ug](https://osf.io/ec4ug). Source code: [github.com/johanna-einsiedler/metalens](https://github.com/johanna-einsiedler/metalens).
- Schroeders, U., et al. (2022). The development set: TAS-20 factor structure (meta-analysis). [DOI to add]
- Schroeders, U., et al. (2024). The validation set: NCS-18 factor structure (meta-analysis). [DOI to add]
- Schroeders, U., Einsiedler, J., & Gnambs, T. (2026). *Factor structure and symptom profiles of the Montgomery–Åsberg Depression Rating Scale (MADRS): A meta-analytic mean and covariance structure analysis.* PsyArXiv. [doi:10.31234/osf.io/am3qp_v2](https://doi.org/10.31234/osf.io/am3qp_v2)
- Companion preset: [MASEMiner — direct information](/preset?id=masem-direct).
- Funding: DFG project 546292646, SPP 2317 META-REP.
