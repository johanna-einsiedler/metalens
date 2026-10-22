## Introduction

Meta-analytic structural equation modelling (MASEM) pools correlation matrices from many primary studies and fits a structural model to the pooled matrix. The coding step is the expensive part: for every study, the relevant correlations, the reliabilities of the measures and the characteristics of the sample have to be found in a PDF and typed into a table, and then checked by a second person. MASEMiner is a pair of presets for Metalens that do the first pass with a language model and leave the checking to a person, with every value shown next to the passage it was read from.

The two presets correspond to the two ways a primary study can carry MASEM-relevant information. The **direct-information** preset — this one — extracts effect sizes the paper reports as such: correlations in a matrix or in the text, and related bivariate statistics. The **indirect-information** preset extracts the measurement model — item-level factor loadings and factor correlations — from which a correlation matrix among subscales can be reconstructed. Both also extract sample characteristics (sample size, percentage female, mean age, country, language) and the reliabilities of the measures, each with an evidence link into the PDF.

MASEMiner was developed and validated by Ulrich Schroeders, Timo Gnambs and Johanna Einsiedler. The study — four use cases against human-coded reference databases, plus a verification study with four coders — is described in Schroeders, Gnambs and Einsiedler (2026); an earlier version is available as a [preprint](https://doi.org/10.31234/osf.io/dceqs_v1). All materials of the evaluation (prompts, codebooks, the reference databases, the MASEMiner JSON exports and the analysis scripts) are deposited at Harvard Dataverse; the DOI is added here on publication.

## How it extracts

The user supplies the **canonical variable labels** — the constructs the meta-analysis is about, each with a short definition and the synonyms a paper might use — and the **effect-size codes** to extract (by default Pearson correlations and odds ratios). The prompt instructs the model to map each eligible measure in the paper to exactly one canonical label, to report only zero-order associations that are explicitly printed, and to ignore adjusted effects, regression coefficients, SEM paths, partial correlations and anything model-based. Matrix cells are read by row and column label; missing cells are never inferred from symmetry. Statistics are extracted in the metric the paper prints them in; any transformation or harmonisation of effect sizes happens downstream, in the analyst's own meta-analytic workflow.

One **sample** is one analytically distinct group of participants — an independent cohort, a subgroup, a wave — and each sample carries its own effect sizes and its own metadata. Robustness checks and repeated mentions of the same participants do not become new samples. For every effect size the model also records the sample size it was computed on, the reliability of each measure in that sample (alpha preferred, then omega, ICC, kappa), and the instrument names.

The prompt template adds four requirements that hold across all MASEMiner extractions: no imputation or transformation of values the paper does not print (pre-computed scalars such as a percentage female computed from counts are allowed and must be flagged in the notes); a fixed JSON schema; a three-level confidence rating (high / medium / low) for each of three targets — effect sizes, reliabilities, sample metadata — per sample; and an evidence snippet for every value, with the PDF page and a coarse location, which Metalens uses to highlight the passage in the review screen.

Two optional parameters refine the task for a particular meta-analysis: **eligibility rules** replace the default sentence "Extract ONLY these canonical variables", for instance to restrict pairs to those involving a focal construct, and **mapping guidance** adds a paragraph on typical informants, instruments and things to ignore. The parameters and the rendered prompt are shown below.

## Verification

The preset was developed on one meta-analysis and validated on a second, unseen one, ten randomly drawn papers each. The model's output was compared value by value with a reference database — the values coded in the published meta-analysis, re-checked against the primary publications for the study. **Sensitivity** (SEN = TP / (TP + FN)) is the share of reference values the model recovered; **precision** (PRE = TP / (TP + FP)) the share of model values that were correct; the **Jaccard index** (JAC = TP / (TP + FN + FP)) combines both. Three kinds of error are counted: omissions (OMIT — a reference value the model did not report), over-extractions (OVER — a value for which no reference value exists, such as a correlation for a pair or a sample the meta-analysis did not target) and wrong values (WRONG — a reference value reported with a different number). A wrong value counts twice, as a miss and as a false positive, because it is the error a reviewer is least likely to notice. The model was GPT-5.6 (`gpt-5.6-sol`, OpenAI, September 2026). The tables give the unverified model output; in parentheses, the output after a person verified it in the interface (mean of two coders).

**Development set** — Gnambs (2013), self- and other-rated Big Five personality traits, k = 10 papers.

| Target | n | SEN | PRE | JAC | OMIT | OVER | WRONG |
|:--|--:|--:|--:|--:|--:|--:|--:|
| Effect size | 340 | 1.00 (1.00) | .54 (.54) | .54 (.54) | 0 (0) | 285 (285) | 0 (0.5) |
| Sample size | 295 | .98 (1.00) | .67 (.68) | .67 (.68) | 5 (0) | 140 (140) | 0 (0) |
| Reliability | 90 | .89 (.90) | .86 (.86) | .78 (.79) | 10 (9) | 13 (13) | 0 (0) |
| Mean age | 9 | .78 (.78) | 1.00 (1.00) | .78 (.78) | 2 (2) | 0 (0) | 0 (0) |
| Country | 12 | .75 (.92) | .90 (.92) | .69 (.85) | 2 (0) | 0 (0) | 1 (1) |
| % female | 11 | .27 (.41) | .75 (.70) | .25 (.35) | 7 (5) | 0 (0.5) | 1 (1.5) |

**Validation set** — Marker et al. (2022), video gaming and body mass, k = 10 papers.

| Target | n | SEN | PRE | JAC | OMIT | OVER | WRONG |
|:--|--:|--:|--:|--:|--:|--:|--:|
| Effect size | 34 | 1.00 (1.00) | .69 (.69) | .69 (.69) | 0 (0) | 15 (15) | 0 (0) |
| Sample size | 28 | 1.00 (.96) | .93 (.90) | .93 (.87) | 0 (0) | 2 (2) | 0 (1) |
| Reliability | – | – | – | – | – | – | – |
| Mean age | 8 | 1.00 (1.00) | 1.00 (.94) | 1.00 (.94) | 0 (0) | 0 (0.5) | 0 (0) |
| Country | 12 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| % female | 12 | .67 (.83) | .80 (.86) | .57 (.74) | 2 (0.5) | 0 (0) | 2 (1.5) |

*n* is the number of reference values; sample sizes are those attached to each effect size. Reliabilities were not coded in the reference database of the video-gaming meta-analysis.

Every target effect size was recovered in both sets, without a single wrong value. Precision is modest because the model also returns correlations the meta-analysis did not target: 285 in the development set, mostly correlations among informant ratings or among self-ratings and almost all from four publications; in the validation set, 12 of the 15 over-extractions come from one publication. These rows are visible as extra entries in the review screen and are deleted there; they are also the reason the eligibility-rules parameter exists. Every surplus effect size carries a surplus sample size, which is where the sample-size precision goes. Reliabilities and sample sizes were otherwise recovered almost completely, the omissions confined to one publication each. Descriptive information was less complete — the percentage of females in particular — mostly because the reference database and the model computed on different bases: the sex ratio before or after exclusions, a country read from the authors' affiliations, a percentage computed from frequencies where the model took the printed value.

In the verification study, four student coders trained in meta-analytic coding worked every use case twice: two verified the MASEMiner output in this interface — correcting, deleting and adding values — and two coded the same papers conventionally into a coding sheet. Verification changed the accuracy only marginally (the values in parentheses): coders added omitted values and corrected descriptive information, but rarely deleted surplus effect sizes, and wrong values that need the source table were corrected only in part. The MASEMiner workflow took 69–201 minutes per use case including 6–36 minutes of automated extraction (11.1 minutes per publication on average), against 131–306 minutes for conventional coding (19.3 minutes): a saving of 42 % over the four use cases, from 27 % (TAS-20) to 58 % (NCS-18). Conventional coding by the same assistants was not uniformly more accurate — fewer surplus effect sizes, but more wrong values for most targets.

The figures below are read live from the public datasets built with this preset on this server, as coders verify them.

## Limitations

The prompt is generic. A meta-analysis with unusual eligibility rules — only longitudinal correlations, only specific informant pairs — needs the rules written into the eligibility and guidance parameters, and the first few papers should be checked with that in mind.

The evaluation compares the model with one reference coding per meta-analysis: the published human coding, re-checked against the papers. A fair share of the remaining disagreements is contradictory or ambiguous information in the primary studies themselves — a percentage reported on two bases, a country stated nowhere.

Precision for effect sizes is bounded by over-extraction, not by wrong numbers: the values the model reports are, in these samples, the printed ones. A coder who accepts the output without reading the paper will nonetheless keep rows that do not belong. The verification study bears this out: coders rarely deleted surplus effect sizes, and the few wrong values that required consulting the source table were corrected only in part. The review screen makes deleting a row cheap, but automation bias is real.

One model was tested — the most recent OpenAI model at the time of validation — and the study deliberately did not benchmark models, whose behaviour changes with updates. Metalens runs any provider's model with this preset; figures for another model exist only once people have verified a dataset extracted with it. The schema can be extended to further statistics (means, standard deviations) and study characteristics (education, assessment mode); none of those has been evaluated.

## Related work

- Schroeders, U., Gnambs, T., & Einsiedler, J. (2026). *MASEMiner – AI-assisted data extraction for meta-analytic structural equation modeling.* Earlier version as a preprint: [doi:10.31234/osf.io/dceqs_v1](https://doi.org/10.31234/osf.io/dceqs_v1).
- Evaluation materials — prompts, codebooks, human-coded reference databases, MASEMiner exports (JSON) and analysis scripts: Harvard Dataverse, DOI to be added on publication.
- Source code: [github.com/johanna-einsiedler/metalens](https://github.com/johanna-einsiedler/metalens) (engine) and [github.com/johanna-einsiedler/maseminer](https://github.com/johanna-einsiedler/maseminer) (local install); hosted at [metalens.tech/maseminer](https://metalens.tech/maseminer).
- Gnambs, T. (2013). The elusive general factor of personality: The acquaintance effect. *European Journal of Personality, 27*(5), 507–520. [doi:10.1002/per.1933](https://doi.org/10.1002/per.1933) — the development set.
- Marker, C., Gnambs, T., & Appel, M. (2022). Exploring the myth of the chubby gamer: A meta-analysis on sedentary video gaming and body mass. *Social Science & Medicine, 301*, 112325. [doi:10.1016/j.socscimed.2019.05.030](https://doi.org/10.1016/j.socscimed.2019.05.030) — the validation set.
- Schroeders, U., Breuer, J., Einsiedler, J., et al. (2026). *Seven guiding questions for reproducible AI-assisted research in the social and behavioral sciences.* PsyArXiv. [doi:10.31234/osf.io/xdtqh_v1](https://doi.org/10.31234/osf.io/xdtqh_v1) — why the export keeps the model output next to the human revisions.
- Companion preset: [MASEMiner — indirect information](/preset?id=masem-indirect).
- Funding: Deutsche Forschungsgemeinschaft (DFG), project 546292646, Priority Programme SPP 2317 META-REP; University of Copenhagen Lighthouse grant "Scaling Scientific Synthesis" (129339).
