## Introduction

Meta-analytic structural equation modelling (MASEM) pools correlation matrices from many primary studies and fits a structural model to the pooled matrix. The coding step is the expensive part: for every study, the relevant correlations, the reliabilities of the measures and the characteristics of the sample have to be found in a PDF and typed into a table, and then checked by a second person. MASEMiner is a pair of presets for Metalens that do the first pass with a language model and leave the checking to a person, with every value shown next to the passage it was read from.

The two presets correspond to the two ways a primary study can carry MASEM-relevant information. The **direct-information** preset — this one — extracts effect sizes the paper reports as such: correlations in a matrix or in the text, and related bivariate statistics. The **indirect-information** preset extracts the measurement model — item-level factor loadings and factor correlations — from which a correlation matrix among subscales can be reconstructed. Both also extract sample characteristics (sample size, percentage female, mean age, country, language) and the reliabilities of the measures, each with an evidence link into the PDF.

MASEMiner was developed and validated by Ulrich Schroeders, Timo Gnambs and Johanna Einsiedler; the study is described in a preprint ([Schroeders, Gnambs & Einsiedler, 2026](https://doi.org/10.31234/osf.io/dceqs_v1), not yet peer-reviewed). The prompts and the raw model outputs of the validation are on the [OSF](https://osf.io/ec4ug).

## How it extracts

The user supplies the **canonical variable labels** — the constructs the meta-analysis is about, each with a short definition and the synonyms a paper might use — and the **effect-size codes** to extract (by default Pearson correlations and odds ratios). The prompt instructs the model to map each eligible measure in the paper to exactly one canonical label, to report only zero-order associations that are explicitly printed, and to ignore adjusted effects, regression coefficients, SEM paths, partial correlations and anything model-based. Matrix cells are read by row and column label; missing cells are never inferred from symmetry.

One **sample** is one analytically distinct group of participants — an independent cohort, a subgroup, a wave — and each sample carries its own effect sizes and its own metadata. Robustness checks and repeated mentions of the same participants do not become new samples. For every effect size the model also records the sample size it was computed on, the reliability of each measure in that sample (alpha preferred, then omega, ICC, kappa), and the instrument names.

The prompt template adds four requirements that hold across all MASEMiner extractions: no imputation or transformation of values the paper does not print (pre-computed scalars such as a percentage female computed from counts are allowed and must be flagged in the notes); a fixed JSON schema; a three-level confidence rating (high / medium / low) for each of three targets — effect sizes, reliabilities, sample metadata — per sample; and an evidence snippet for every value, with the PDF page and a coarse location, which Metalens uses to highlight the passage in the review screen.

Two optional parameters refine the task for a particular meta-analysis: **eligibility rules** replace the default sentence "Extract ONLY these canonical variables", for instance to restrict pairs to those involving a focal construct, and **mapping guidance** adds a paragraph on typical informants, instruments and things to ignore. The parameters and the rendered prompt are shown below.

## Verification

The preset was developed on one meta-analysis and validated on a second, unseen one. For each, the model's output was compared with the published human coding, value by value. **Sensitivity** (SEN = TP / (TP + FN)) is the share of human-coded values the model recovered; **precision** (PRE = TP / (TP + FP)) is the share of model values that were correct; the **Jaccard index** (JAC = TP / (TP + FN + FP)) combines both. A value that is present but wrong counts twice, as a false negative and a false positive. The model was GPT-5.5 (`gpt-5.5-2026-04-23`). Where the comparison uncovered mistakes in the original human coding, the table reports the agreement after correcting them, with the uncorrected figure in parentheses.

**Development set** — Gnambs (2013), self- and other-rated Big Five personality traits, k = 10 papers.

| Target | n | SEN | PRE | JAC |
|:--|--:|--:|--:|--:|
| Effect size | 265 | .97 (.96) | .66 | .65 (.64) |
| Sample size | 265 | .95 | .64 | .62 |
| Reliability | 55 | 1.00 | 1.00 | 1.00 |
| Mean age | 12 | 1.00 | 1.00 | 1.00 |
| Country | 12 | .83 | .83 | .71 |
| % female | 12 | .90 | .90 | .82 |

**Validation set** — Marker et al. (2022), video gaming and body mass, k = 10 papers.

| Target | n | SEN | PRE | JAC |
|:--|--:|--:|--:|--:|
| Effect size | 25 | 1.00 (.96) | .66 (.65) | .66 (.63) |
| Sample size | 25 | .92 (.84) | .64 (.62) | .61 (.55) |
| Reliability | – | – | – | – |
| Mean age | 12 | .92 (.67) | .92 (.67) | .85 (.50) |
| Country | 12 | 1.00 | 1.00 | 1.00 |
| % female | 12 | .83 | .83 | .71 |

The pattern is the same in both sets: the model finds nearly every effect size the human coders recorded (sensitivity .97 to 1.00), and about a third of what it returns are correlations the coders did not want — associations between an eligible variable and a non-target measure, or between two operationalisations of one construct. These over-extractions are visible as extra rows in the review screen and are removed there; they are also the reason the eligibility-rules parameter exists. The low sample-size precision follows directly, because every extra effect size carries a sample size. Reliabilities, mean age and the coded metadata are recovered almost completely; the two country errors in the development set were papers that report the country only in the author affiliations.

Four further datasets are being built on this platform by student coders, one per meta-analysis above and one per indirect-information study, with ten papers each: a blind coding without the tool and a tool-assisted coding that starts from the model's output. The figures below are read live from those datasets as they are verified.

## Limitations

The prompt is generic. A meta-analysis with unusual eligibility rules — only longitudinal correlations, only specific informant pairs — needs the rules written into the eligibility and guidance parameters, and the first few papers should be checked with that in mind.

The evaluation compares the model with one published human coding, which itself contained mistakes; the corrected figures depend on the authors' re-reading of the papers.

Precision for effect sizes is bounded by over-extraction, not by wrong numbers: the values the model reports are, in these samples, almost always the printed ones. A coder who accepts the output without reading the paper will nonetheless keep rows that do not belong. The review screen is built to make deleting them cheap, but the risk of automation bias is real.

One model was tested at the time of validation. Metalens runs any provider's model with this preset; figures for other models are not available until a dataset verified by people exists for them.

## Related work

- Schroeders, U., Gnambs, T., & Einsiedler, J. (2026). *MASEMiner – AI-assisted data extraction for meta-analytic structural equation modeling.* PsyArXiv, 9 June 2026. [doi:10.31234/osf.io/dceqs_v1](https://doi.org/10.31234/osf.io/dceqs_v1) — preprint, not peer-reviewed.
- Supplement with the prompts and the JSON outputs of both validation studies: [osf.io/ec4ug](https://osf.io/ec4ug).
- Source code: [github.com/johanna-einsiedler/metalens](https://github.com/johanna-einsiedler/metalens) (engine) and [github.com/johanna-einsiedler/maseminer](https://github.com/johanna-einsiedler/maseminer) (local install); hosted at [metalens.tech/maseminer](https://metalens.tech/maseminer).
- Gnambs, T. (2013). The development set: self–other agreement on Big Five traits (meta-analysis). [DOI to add]
- Marker, C., et al. (2022). The validation set: video gaming and body mass (meta-analysis). [DOI to add]
- Schroeders, U., Einsiedler, J., & Gnambs, T. (2026). *Factor structure and symptom profiles of the Montgomery–Åsberg Depression Rating Scale (MADRS): A meta-analytic mean and covariance structure analysis.* PsyArXiv. [doi:10.31234/osf.io/am3qp_v2](https://doi.org/10.31234/osf.io/am3qp_v2) — an application of the indirect-information preset.
- Companion preset: [MASEMiner — indirect information](/preset?id=masem-indirect).
- Funding: Deutsche Forschungsgemeinschaft (DFG), project 546292646, Priority Programme SPP 2317 META-REP.
