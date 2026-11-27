# Classify: is this paper primarily empirical or primarily theoretical?

You are reading one economics paper, attached as a PDF. Decide whether its
**primary contribution** is empirical or theoretical, and extract its basic
bibliographic metadata.

This is a gate: papers classified as theoretical are dropped from a
regression-reproduction pipeline, so a wrong "theoretical" is more costly than
a wrong "empirical".

## Primarily EMPIRICAL

The paper's central contribution rests on analysing data:

- estimates a causal effect from observational data
- runs or analyses an experiment (field, lab, or survey)
- documents new descriptive facts from data as the main contribution
- structurally estimates a model's parameters **and reports standard errors**
- meta-analyses a body of empirical results

## Primarily THEORETICAL

The paper's central contribution is a model, a proof, or a method:

- proves propositions or characterises equilibria
- develops a new model, with data used only to illustrate
- calibrates a model and reports counterfactual simulations rather than
  estimated effects with inference
- explores a model numerically
- surveys a theoretical literature
- develops econometric theory or a new estimator (even with a small
  application section)

## Borderline rules

1. **Structural estimation with reported standard errors is empirical.**
   Calibration to match moments, with no inference, is theoretical.
2. **A theory paper with a short empirical application is theoretical** if the
   application exists to illustrate the model rather than to establish a
   result.
3. **An empirical paper with a motivating model is empirical** if the model
   exists to interpret the estimates.
4. **A methods paper is theoretical** even when it demonstrates the method on
   real data.
5. **When in doubt, choose empirical.** Including a borderline paper costs one
   wasted extraction; excluding an empirical paper silently removes a valid
   target from the study.

## Consistency requirement

`is_empirical` MUST equal `primary_type ∈ {empirical_causal,
empirical_experiment, empirical_descriptive,
empirical_structural_estimation, empirical_meta_analysis}`.

## Evidence

Provide 1–3 evidence items supporting the classification. Each needs a
verbatim `snippet`, the `page` it appears on, the `source` section, and
`field` set to `classification.is_empirical`. Quote the sentence that most
directly reveals the paper's primary contribution — usually from the abstract
or the last paragraph of the introduction.

## Identification design (added 2026-10-10)

For an EMPIRICAL paper, also name the identification design of its **headline
result** in `identification_design`, and quote the sentence (abstract or
introduction, verbatim) that reveals it in `design_evidence` (one or two items,
`field` set to `classification.identification_design`). Choose the design the
paper ARGUES, not the estimator it runs: a difference-in-differences estimated
by OLS with indicators is `did_event`; a lottery exploited by OLS is `rct`.

| value | when |
|---|---|
| `rct` | randomised assignment: field, lab or survey experiment, or a lottery/random audit the paper treats as random assignment |
| `did_event` | difference-in-differences, event study, staggered adoption, triple difference, before/after with a comparison group |
| `iv` | instrumental variables, 2SLS, shift-share/Bartik, judge or examiner leniency, fuzzy designs estimated as IV |
| `rdd` | regression discontinuity or kink at a cutoff in a running variable (sharp or fuzzy; spatial RD) |
| `soo_matching` | selection on observables as the identification argument: regression adjustment, matching, propensity-score weighting, double machine learning |
| `panel_fe` | panel fixed effects with a time-varying treatment and no explicit event structure; within-unit variation is the argument |
| `bunching` | bunching at a kink or notch in an incentive schedule |
| `synthetic_control` | synthetic control for one or a few treated units |
| `other` | an empirical paper whose headline result uses none of these (descriptive, structural, time series, calibration) |
| `unknown` | you cannot tell from the abstract and introduction |

A theoretical paper gets `unknown`. When several designs appear, name the one
behind the result the abstract states first.
