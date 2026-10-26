## Introduction

Meta-analytic structural equation modelling (MASEM) pools correlation matrices from many primary studies and fits a structural model to the pooled matrix. The inputs are the relevant correlations, the reliabilities of the measures and the characteristics of the sample of a set of primary studies.

This preset focuses on direct information and extracts the effect sizes a paper reports, such as correlations in a matrix or in the text, and related bivariate statistics. For indirect information (item-level factor loadings and factor correlations), [a separate preset](/preset?id=masem-indirect) exists.

Both presets also extract sample characteristics (sample size, percentage female, mean age, country, language) and the reliabilities of the measures, each with an evidence link into the PDF.

## How it extracts

The user supplies the **canonical variable labels**, i.e. the constructs the meta-analysis is about, each with a short definition and the synonyms a paper might use, and the **effect-size codes** to extract (by default Pearson correlations and odds ratios). The prompt instructs the model to map each eligible measure in the paper to exactly one canonical label, to report only zero-order associations that are explicitly printed, and to ignore adjusted effects, regression coefficients, SEM paths, partial correlations and anything model-based. Matrix cells are read by row and column label. Statistics are extracted in the metric the paper prints them in.

If a paper reports on multiple samples, each sample is treated as an analytically distinct group of participants — an independent cohort, a subgroup, a wave — and linked to its own effect sizes and its own metadata. For every effect size the model also records the sample size it was computed on, the reliability of each measure in that sample (alpha preferred, then omega, ICC, kappa), and the instrument names.

Two optional parameters refine the task for a particular meta-analysis: **eligibility rules** replace the default rule that only the canonical variables are extracted, for instance to restrict pairs to those involving a focal construct, and **mapping guidance** adds a paragraph on typical informants, instruments and things to ignore. The parameters and the rendered prompt are shown below.

## Validation interface

Every extracted value is checked against the paper in one screen. The left panel lists the papers of the dataset; the centre shows the extracted values of one sample — the effect sizes as rows, the sample characteristics above them, every cell editable; the right panel shows the page of the PDF the value was read from. A click on a value scrolls the page to its quotation and highlights it, so a coder confirms or corrects each number against the source without leaving the screen. Edits are recorded as human corrections with the original value, the person and the time; a sample is marked verified or flagged at the end of its card, and the dataset's badge counts how much of it a person has checked.

```figure review-panel-direct
The review panel on Grydeland et al. (2012): the extracted effect sizes of the HEIA sample on the left, Table 2 of the paper on the right with the quoted cells highlighted; the selected odds ratio, 1.43, and its row in the table share the accent.
```

Table 2 on page 6 of Grydeland, M., Bergh, I. H., Bjelland, M., et al. (2012). Correlates of weight status among Norwegian 11-year-olds: The HEIA study. *BMC Public Health, 12*, 1053 ([doi:10.1186/1471-2458-12-1053](https://doi.org/10.1186/1471-2458-12-1053)), an open-access article under the Creative Commons Attribution licence (CC BY 2.0). The values are the model's unverified extraction of this paper from the validation study.

## Verification

The preset was developed on one meta-analysis and validated on a second that the authors did not use while developing the prompt; ten randomly drawn papers were used from each. The model's output was compared against a gold-standard database: the values coded in the published meta-analysis, re-checked against the primary publications for the study. Further, we conducted an additional validation study where, for each dataset, (1) two coders recorded the values from the PDFs only and (2) two coders were presented with the validation interface and tasked to verify the extractions. Table 1 and Table 2 compare the model-only extractions with those corrected by the coders through MASEMiner; Table 3 compares the coders' performance with and without MASEMiner. We report:

- **Sensitivity** (SEN = TP / (TP + FN)): the share of reference values the model recovered.
- **Precision** (PRE = TP / (TP + FP)): the share of the model's values that were correct.
- **Jaccard index** (JAC = TP / (TP + FN + FP)): sensitivity and precision in one number.
- **Omissions** (OMIT): a reference value the model did not report.
- **Over-extractions** (OVER): a value for which no reference value exists, such as a correlation for a pair or a sample the meta-analysis did not target.
- **Wrong values** (WRONG): a reference value reported with a different number.

A wrong value counts double for precision, recall and the Jaccard index: it is recorded both as a false negative, because the reference value was not recovered, and as a false positive, because a wrong number was returned (FN = OMIT + WRONG, FP = OVER + WRONG). This is a deliberate choice, since an omission or a surplus row in this particular setup is easy to spot for a reviewer, a wrong number is not. The model was GPT-5.6 (`gpt-5.6-sol`, OpenAI, September 2026). The tables give the unverified model output; in parentheses, the output after the coders of the verification study had reviewed and corrected it in this review screen, averaged over those coders.

The validation runs used the prompt as rendered by the earlier MASEMiner application on 25 September 2026: its template of 7 June 2026 with the variable definitions and, for the self-other set, the eligibility rules pasted into the prompt text (the exact texts are part of the evaluation materials). This preset's prompt is that text with four changes: the eligibility rules and the mapping guidance are parameters instead of edits to the text; evidence is required for every non-null sample metadata value, with rules for values obtained by inference; every evidence field must carry its `samples[i]` prefix; and the paper metadata include the journal. The extracted fields and the schema id are the same.

**Table 1 · Development set** — Gnambs (2013), self- and other-rated Big Five personality traits, k = 10 papers.

| Target | n | SEN | PRE | JAC | OMIT | OVER | WRONG |
|:--|--:|--:|--:|--:|--:|--:|--:|
| Effect size | 340 | 1.00 (1.00) | .54 (.54) | .54 (.54) | 0 (0) | 285 (285) | 0 (0.5) |
| Sample size | 295 | .98 (1.00) | .67 (.68) | .67 (.68) | 5 (0) | 140 (140) | 0 (0) |
| Reliability | 90 | .89 (.90) | .86 (.86) | .78 (.79) | 10 (9) | 13 (13) | 0 (0) |
| Mean age | 9 | .78 (.78) | 1.00 (1.00) | .78 (.78) | 2 (2) | 0 (0) | 0 (0) |
| Country | 12 | .75 (.92) | .90 (.92) | .69 (.85) | 2 (0) | 0 (0) | 1 (1) |
| % female | 11 | .27 (.41) | .75 (.70) | .25 (.35) | 7 (5) | 0 (0.5) | 1 (1.5) |

**Table 2 · Validation set** — Marker et al. (2022), video gaming and body mass, k = 10 papers.

| Target | n | SEN | PRE | JAC | OMIT | OVER | WRONG |
|:--|--:|--:|--:|--:|--:|--:|--:|
| Effect size | 34 | 1.00 (1.00) | .69 (.69) | .69 (.69) | 0 (0) | 15 (15) | 0 (0) |
| Sample size | 28 | 1.00 (.96) | .93 (.90) | .93 (.87) | 0 (0) | 2 (2) | 0 (1) |
| Reliability | – | – | – | – | – | – | – |
| Mean age | 8 | 1.00 (1.00) | 1.00 (.94) | 1.00 (.94) | 0 (0) | 0 (0.5) | 0 (0) |
| Country | 12 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| % female | 12 | .67 (.83) | .80 (.86) | .57 (.74) | 2 (0.5) | 0 (0) | 2 (1.5) |

*n* is the number of reference values; sample sizes are those attached to each effect size. Reliabilities were not coded in the reference database of the video-gaming meta-analysis.

Every target effect size was recovered in both sets, without a single wrong value. Precision is modest because the model also returns correlations the meta-analysis did not target: 285 in the development set, mostly correlations among informant ratings or among self-ratings and almost all from four publications; in the validation set, 12 of the 15 over-extractions come from one publication. These rows are visible as extra entries in the review screen and are deleted there; they are also the reason the eligibility-rules parameter exists. Reliabilities and sample sizes were otherwise recovered almost completely; the omissions were confined to one publication each. Descriptive information was less complete, the percentage of females in particular, mostly because the reference database and the model computed on different bases, e.g. the sex ratio before or after exclusions.

**Table 3 · Coders with and without MASEMiner** — the verification study: four student coders trained in meta-analytic coding; in each use case some coded the papers conventionally into a coding sheet, the others reviewed and corrected the MASEMiner output in this screen. Time is the mean per publication; the MASEMiner time includes the automated extraction. Accuracy is for the effect sizes, against the reference database.

| Use case | Conventional coding | MASEMiner + review | Time saved | Effect sizes, conventional (SEN / PRE) | Effect sizes, MASEMiner + review (SEN / PRE) |
|:--|--:|--:|--:|--:|--:|
| Self–other correlations | 30.6 min | 20.1 min (3.6 extraction) | 34 % | .99 / .65 | 1.00 / .54 |
| Video gaming and body mass | 17.2 min | 7.9 min (1.9 extraction) | 54 % | .91 / .91 | 1.00 / .69 |

Reviewing the model's output took a third to a half of the time of coding by hand, at comparable accuracy. The reviewed output recovers every effect size (coding by hand missed some), while its precision stays where the model left it — the coders rarely deleted the surplus correlations, and wrong values that needed the source table were corrected only in part.

The figures below are read live from the public datasets built with this preset on this server, as coders verify them.

## Limitations

The prompt is fairly generic and might not work well for a MASEM with unusual eligibility rules, e.g. only longitudinal correlations or only specific informant pairs.

One model was tested — the most recent OpenAI model at the time of validation. The schema can be extended to further statistics (means, standard deviations) and study characteristics (education, assessment mode); however, none of those has been evaluated.

## Related work

- Schroeders, U., Gnambs, T., & Einsiedler, J. (2026). *MASEMiner – AI-assisted data extraction for meta-analytic structural equation modeling.* Earlier version as a preprint: [doi:10.31234/osf.io/dceqs_v1](https://doi.org/10.31234/osf.io/dceqs_v1).
- Evaluation materials — prompts, codebooks, human-coded reference databases, MASEMiner exports (JSON) and analysis scripts: Harvard Dataverse, DOI to be added on publication.
- Source code: [github.com/johanna-einsiedler/metalens](https://github.com/johanna-einsiedler/metalens) (engine) and [github.com/johanna-einsiedler/maseminer](https://github.com/johanna-einsiedler/maseminer) (local install); hosted at [metalens.tech/maseminer](https://metalens.tech/maseminer).
- Gnambs, T. (2013). The elusive general factor of personality: The acquaintance effect. *European Journal of Personality, 27*(5), 507–520. [doi:10.1002/per.1933](https://doi.org/10.1002/per.1933) — the development set.
- Marker, C., Gnambs, T., & Appel, M. (2022). Exploring the myth of the chubby gamer: A meta-analysis on sedentary video gaming and body mass. *Social Science & Medicine, 301*, 112325. [doi:10.1016/j.socscimed.2019.05.030](https://doi.org/10.1016/j.socscimed.2019.05.030) — the validation set.
- Schroeders, U., Breuer, J., Einsiedler, J., et al. (2026). *Seven guiding questions for reproducible AI-assisted research in the social and behavioral sciences.* PsyArXiv. [doi:10.31234/osf.io/xdtqh_v1](https://doi.org/10.31234/osf.io/xdtqh_v1) — why the export keeps the model output next to the human revisions.
- Companion preset: [MASEMiner — indirect information](/preset?id=masem-indirect).
- Funding: Deutsche Forschungsgemeinschaft (DFG), project 546292646, Priority Programme SPP 2317 META-REP; University of Copenhagen Lighthouse grant "Scaling Scientific Synthesis" (129339).
