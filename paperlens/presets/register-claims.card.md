## Introduction

The most relevant findings of an empirical economics paper are usually stated in the abstract. This preset starts from this premise and produces one entry for every stated claim in the abstract, finds the sentences in the introduction that refer to that claim, and uses those to locate the relevant column of a regression table.

A stated claim is an atomic, directional finding the paper's abstract states or announces: a cause, an effect and a sign ("X increases Y"). A claim does not need to have numbers or to mention a specific scope that it applies to. We distinguish three types of claims:

- An **edge** is a causal effect of one variable on another.
- A **comparison** says that an effect is larger for one group than for another and qualifies the edges it compares.
- A statement the abstract makes that is not causal is recorded as **not causal**.

The claim's scope, as far as it can be determined from the sentences, is recorded in four fields: setting, period, population and identification.

Under each claim sit the **table results** that operationalise it: a regression column (or a figure) whose target regressor measures the cause, whose outcome measures the effect and whose sample lies inside the claim's scope. Results that estimate the same quantity are grouped into an estimand, and exactly one result per estimand is the **main** estimate, the one the authors would point to; the others are supporting, robustness or heterogeneity results. A regression linked as a main estimate is a *headline result* of the paper.

The protocol was written for *Robustness at Scale*, which uses the main estimates to decide which re-estimated regressions are headline results, and for the [Danish Causal Atlas](https://johanna-einsiedler.github.io/danish-causal-atlas/), which draws the edge claims of Danish register-data economics as one graph. The records are produced outside Metalens by an agent pipeline, described below, and imported into this schema together with the PDFs; Metalens anchors the quotations in the PDF and is where a person reviews them. The pipeline's second call takes the paper's transcribed regression tables as its input, so a paper's claims follow its tables: the [Economics regression tables](/preset?id=econ-regression-tables) preset for the Robustness-at-Scale corpus, [Register-data tables](/preset?id=register-tables) for the Danish corpus. Once imported, a claims dataset stands on its own, because every result carries the exhibit, column, row and point estimate it refers to; a companion tables dataset is optional and lets Metalens cross-check every point estimate against the independently transcribed cell.

## How it extracts

Two structured calls per article, each a single agent session (Claude Opus 5 through the Claude Agent SDK) with the PDF attached, bounded at 40 turns and a cost cap of 4 USD for the claims and 5 USD for the mapping. The first call needs only the PDF and extracts the claims the article states; the second needs the article's transcribed regression tables as well and maps each claim to the regressions that test it. The prompts are appendices E.3 and E.4 of the study.

**Claim extraction.** The abstract decides which findings exist. When an article prints no abstract, or its abstract describes what the article does without stating a finding, the introduction is used instead; when the introduction states no finding either, the conclusion is used. The deciding section is recorded with every output (on the Robustness-at-Scale corpus it was the abstract for 207 articles, the introduction for 72 and the conclusion for 29). For each sentence of the deciding section the model first judges whether the statement rests on the article's own empirical findings. If it does, the statement is labelled directional (an edge), comparative (a comparison) or not causal. One claim is written per cause–effect pair: a sentence that reports a negative impact on labour-market participation and on income yields two claims. A claim carries

- a directional statement without numbers and without scope, its type, its cause, its effect and, for an edge, its sign (+, −, 0, mixed);
- the anchor: the sentence of the deciding section that states or announces the finding, verbatim;
- the scope in four fields: setting, period, population, identification;
- the evidence: the verbatim sentences that state the cause, the effect and the direction, that is the anchor and any introduction sentence that completes it, with a support grade: *explicit* when the anchor alone states direction, magnitude and scope, *assembled* when introduction sentences pin them down, *weak* when a variable or the direction rests on the model's own inference;
- for a comparison, the edge claims it compares, the moderator (the dimension of the comparison, such as gender or horizon) and the relation (larger, stronger).

**Claim mapping.** The second call receives the PDF, the claims from the first call and a digest of the article's transcribed regressions: every column with its id, table, panel, column, outcome, model, fixed effects, number of controls, sample size and sample description, followed by its coefficient rows as printed. For each edge or comparison claim it

1. quotes the **bridge sentences** of the abstract or introduction that say how the finding was obtained or where it is shown: the design, the sample, the comparison, the exhibit. Only when the introduction does not identify the analysis is a sentence of the body that introduces the exhibit quoted instead;
2. **links every regression that tests the claim**: its target regressor measures the cause, its outcome measures the effect, and its sample lies inside the claim's scope. Each link names the regression id and the coefficient row that carries the claim;
3. **groups the linked estimates into estimands**: two estimates belong to the same estimand when a meta-analyst would average them. A different subgroup, a different horizon or a different definition of the treatment makes a different estimand; a different set of controls, other fixed effects, a robustness sample or another estimator of the same quantity does not;
4. marks **one main estimate per estimand**, the estimate the authors would point to, identified from three signals recorded with it: whether the abstract or introduction states its magnitude, as printed or transformed; whether it refers to the estimate without a number, naming its table, column, subgroup or horizon; and whether the body calls its specification preferred, baseline, main or benchmark. Every other estimate of the estimand is *supporting* if it is another specification the authors rely on, *robustness* if it is presented as a check, or *heterogeneity* if it is a subgroup split presented as a check. Two main links on one estimand are an error; a comparison gets one main estimate per side;
5. **derives stated magnitudes** that no table prints as such, because they are computed from several printed values: a main effect plus an interaction, an estimate relative to the mean, a difference between two columns. The main link is then the coefficient through which the cause enters, and the model writes out the arithmetic from the printed values to the stated number, so that a pointer by number can be checked;
6. **describes each link**: whether the regressor measures the cause itself, a proxy for it, or an assignment indicator standing in for it; whether the outcome is the effect itself or a proxy; whether the estimate's sign, read in its own orientation, agrees with the claim; and one sentence of reasoning;
7. records the **reason when nothing tests the claim**, and a confidence for each claim.

Evidence: every claim cites the abstract sentence that anchors it and the introduction sentences that complete it, verbatim with the page; every result names the exhibit it comes from. Metalens locates the quotations in the PDF on import and flags the ones it cannot find.

## Verification

One verification exercise has been completed for this preset: the consistency of two independent runs, on the [Danish register-data corpus](#to-be-published) of 244 articles. Two checks also run on every imported dataset without a person: Metalens locates each quoted sentence in the PDF and flags the quotations it cannot find, and, where a companion tables dataset exists, every point estimate a result carries is compared with the independently transcribed cell of the companion.

**Consistency between two runs.** Every article was run twice with the same model, Claude Opus 5 (`claude-opus-5`) in agent mode, the same prompts and the same inputs. To compare the two sets of claims, a further call matched the variables the runs name, so that two wordings of the same cause or effect count as one variable; an edge is then a cause–effect pair between matched variables.

| | |
|:--|--:|
| Articles run twice | 244 |
| Claims, first / second run | 1,837 / 1,799 |
| Same deciding section (abstract, introduction or conclusion) | 242 (99.2 %) |
| Cause–effect edges stated by at least one run | 1,030 |
| … stated by both runs | 941 (91.4 %) |
| Same sign, where both runs state the edge | 921 of 941 (97.9 %) |

The two runs mostly agree on which findings an article states, and where both state a finding they almost always give it the same direction.

On the Robustness-at-Scale corpus (308 articles with at least one regression table) the two calls yielded 1,881 claims: 1,325 edges, 197 comparisons and 359 not causal. Of the 1,522 edges and comparisons, 1,264 were matched to at least one main estimate and 257 to no regression. 3,999 regression rows are main estimates; 502 of them are pointed to by number in the abstract or introduction, 1,609 by reference and 1,888 are not pointed to.
