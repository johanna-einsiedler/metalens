## Introduction

Meta-analytic structural equation modelling (MASEM) pools correlation matrices from many primary studies and fits a structural model to the pooled matrix. The inputs are the relevant correlations, the reliabilities of the measures and the characteristics of the sample of a set of primary studies.

This preset focuses on indirect information and extracts the measurement model — item-level factor loadings and factor correlations — from which a correlation matrix among subscales can be reconstructed. For directly reported effect sizes (correlations in a matrix or in the text), [a separate preset](/preset?id=masem-direct) exists.

Both presets also extract sample characteristics (sample size, percentage female, mean age, country, language) and the reliabilities of the measures, each with an evidence link into the PDF.

## How it extracts

The user names the **scale** (its name and abbreviation), the **number of items**, and the **item wording** in order. The item texts matter more than the numbers: papers renumber, reorder and paraphrase items, and the prompt instructs the model to align loadings by wording first and by printed item number second.

For each distinct sample or group with its own loading table, the model extracts exactly one factor solution: the item-level loadings including cross-loadings and negative loadings as printed, the latent correlations between the retained factors as unique off-diagonal pairs, and the sample and analysis details — country, language, sample size, percentage female, mean age, whether the analysis was exploratory or confirmatory, the estimation method, and the number of retained factors.

Most of the prompt is decision rules, because factor-analytic papers rarely report one clean table. The rules say which solution to take when a paper reports several (first-order solutions before hierarchical or bifactor ones; within a model type, the solution with the most retained scale-relevant factors; author preference does not override this), how to treat multiple samples (never merged, never averaged), how to assign a loading to a factor (by printed column position, not by theory or boldface), what to do with orthogonal rotations (factor correlations set to zero), with suppressed cells (null, never zero), and how to tell a latent factor correlation from an observed subscale correlation (only the former is extracted). Factors are relabelled F1…Fn by printed position; the paper's own labels go into the notes.

The template adds the same four MASEMiner requirements as the direct-information preset: no imputation of unreported values, a fixed JSON schema, a three-level confidence rating per target (factor loadings, factor correlations, metadata) per sample, and an evidence snippet with page for every value, used to highlight the passage in review. In this preset the loading table is cited as a block, and a click on a loading in the review screen is located on the page using the item's wording as an anchor.

## Verification

The preset was developed on one factor-analytic meta-analysis and validated on a second, ten randomly drawn papers each; very long full texts were excluded from the development set to keep costs down. Agreement with the reference database — the published human coding, re-checked against the primary publications — is reported as sensitivity, precision and Jaccard index, with the counts of omissions, over-extractions and wrong values behind them (definitions as on the direct-information card; a wrong value counts as both a miss and a false positive). The model was GPT-5.6 (`gpt-5.6-sol`, OpenAI, September 2026). The tables give the unverified model output; in parentheses, the output after a person verified it in the interface (mean of two coders).

**Development set** — Toronto Alexithymia Scale (TAS-20), Schroeders et al. (2022), k = 10 papers.

| Target | n | SEN | PRE | JAC | OMIT | OVER | WRONG |
|:--|--:|--:|--:|--:|--:|--:|--:|
| Factor loadings | 980 | 1.00 (.98) | 1.00 (.99) | 1.00 (.97) | 0 (10) | 0 (0) | 1 (8) |
| Factor correlations | 66 | .91 (.88) | 1.00 (.99) | .91 (.87) | 6 (7.5) | 0 (0) | 0 (0.5) |
| Mean age | 12 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Country | 14 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Language | 14 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Sample size | 14 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| % female | 14 | .86 (.93) | 1.00 (1.00) | .86 (.93) | 2 (1) | 0 (0) | 0 (0) |
| EFA / CFA | 14 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Number of factors | 14 | 1.00 (.93) | 1.00 (.93) | 1.00 (.88) | 0 (0) | 0 (0) | 0 (1) |

**Validation set** — Need for Cognition Scale (NCS-18), Schroeders et al. (2024), k = 10 papers.

| Target | n | SEN | PRE | JAC | OMIT | OVER | WRONG |
|:--|--:|--:|--:|--:|--:|--:|--:|
| Factor loadings | 324 | .92 (.97) | .98 (1.00) | .90 (.97) | 18 (9) | 0 (0) | 7 (1) |
| Factor correlations | 11 | .82 (.86) | 1.00 (1.00) | .82 (.86) | 2 (1.5) | 0 (0) | 0 (0) |
| Mean age | 6 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Country | 11 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Language | 11 | .91 (.91) | 1.00 (1.00) | .91 (.91) | 1 (1) | 0 (0) | 0 (0) |
| Sample size | 11 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| % female | 8 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| EFA / CFA | 11 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Number of factors | 11 | .91 (.95) | .91 (.95) | .83 (.92) | 0 (0) | 0 (0) | 1 (0.5) |

*n* is the number of reference values; EFA / CFA is whether the analysis was exploratory or confirmatory.

In the development set all 980 loadings were recovered, with a single wrong value — a typographical error of the source table, reproduced. Six factor correlations from two publications were not extracted because the model took the reported values for correlations between subscale sum scores, which the prompt excludes by rule; that distinction is the hard part for a person too. In the validation set every deviation traces to two publications: for one the model extracted only two factors of a three-factor solution (the 18 omitted loadings, the two omitted correlations and the wrong number of factors); in the other, three items went to a neighbouring factor and one loading had the wrong sign (the seven wrong values). Across the four use cases, misassignments to a wrong factor concerned 6 of 2,397 target values. Publications with cleanly typeset tables were extracted without error.

In the verification study, four student coders trained in meta-analytic coding worked every use case twice: two verified the MASEMiner output in this interface — correcting, deleting and adding values — and two coded the same papers conventionally into a coding sheet. Verification changed the accuracy only marginally (the values in parentheses): coders added omitted values and corrected descriptive information, but rarely deleted surplus effect sizes, and wrong values that need the source table were corrected only in part. The MASEMiner workflow took 69–201 minutes per use case including 6–36 minutes of automated extraction (11.1 minutes per publication on average), against 131–306 minutes for conventional coding (19.3 minutes): a saving of 42 % over the four use cases, from 27 % (TAS-20) to 58 % (NCS-18). Conventional coding by the same assistants was not uniformly more accurate — fewer surplus effect sizes, but more wrong values for most targets.

The figures below are read live from the public datasets built with this preset on this server, as coders verify them.

## Limitations

The decision rules favour ordinary first-order solutions. A meta-analysis that targets a bifactor or higher-order model needs the preference order changed in the prompt; the preset's parameters do not expose it.

Validation used scales with a stable item set. Scales with many short forms, or papers that analyse an item subset, exercise the item-alignment rules harder than the two studies did.

Very long full texts were excluded from the TAS-20 development set, so the figures do not cover dissertations or reports with extensive supplementary material, where context limits may bite.

As for the direct-information preset: a single model at validation time (GPT-5.6, September 2026), a reference coding that is one human coding re-checked against the papers, and the risk that a reviewer accepts a complete-looking loading table without checking the column assignment — the verification study found wrong values that needed the source table corrected only in part.

## Related work

- Schroeders, U., Gnambs, T., & Einsiedler, J. (2026). *MASEMiner – AI-assisted data extraction for meta-analytic structural equation modeling.* Earlier version as a preprint: [doi:10.31234/osf.io/dceqs_v1](https://doi.org/10.31234/osf.io/dceqs_v1).
- Evaluation materials — prompts, codebooks, human-coded reference databases, MASEMiner exports (JSON) and analysis scripts: Harvard Dataverse, DOI to be added on publication. Source code: [github.com/johanna-einsiedler/metalens](https://github.com/johanna-einsiedler/metalens).
- Schroeders, U., Kubera, F., & Gnambs, T. (2022). The structure of the Toronto Alexithymia Scale (TAS-20): A meta-analytic confirmatory factor analysis. *Assessment, 29*(8), 1806–1823. [doi:10.1177/10731911211033894](https://doi.org/10.1177/10731911211033894) — the development set.
- Schroeders, U., Morgenstern, M., Jankowsky, K., & Gnambs, T. (2024). Short-scale construction using meta-analytic ant colony optimization: A demonstration with the Need for Cognition Scale. *European Journal of Psychological Assessment, 40*(5), 376–395. [doi:10.1027/1015-5759/a000818](https://doi.org/10.1027/1015-5759/a000818) — the validation set.
- Gnambs, T., & Staufenbiel, T. (2016). Parameter accuracy in meta-analyses of factor structures. *Research Synthesis Methods, 7*(2), 168–186. [doi:10.1002/jrsm.1190](https://doi.org/10.1002/jrsm.1190) — when a loading matrix can stand in for the correlation matrix.
- Companion preset: [MASEMiner — direct information](/preset?id=masem-direct).
- Funding: Deutsche Forschungsgemeinschaft (DFG), project 546292646, Priority Programme SPP 2317 META-REP; University of Copenhagen Lighthouse grant "Scaling Scientific Synthesis" (129339).
