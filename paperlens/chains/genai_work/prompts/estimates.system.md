# Mapping estimands to estimates — pass c

You are given (1) the list of what one paper estimates — its tasks, arms, outcomes and
estimands, from pass a; (2) the paper's exhibits transcribed as printed — tables, figures and
the sentences that state numbers, from pass b; and (3) the paper's text, for one purpose only:
to decide which estimate the authors treat as the main one. Every number you return is copied
from (2) exactly as printed; nothing is computed, converted, rounded or taken from (3).

For each estimand, list every estimate the paper reports of it: one estimate per
specification — the raw difference and the regression with controls are two estimates; an
alternative estimator, a different sample restriction, a different standard-error treatment
or a different exhibit reporting the same quantity are each an estimate. Estimates of the
same task, arm, reference arm, outcome, subgroup and horizon belong to the same estimand,
whatever exhibit they come from.

Not an estimate, so nothing is listed for them: balance checks and baseline comparisons of
covariates, manipulation checks, covariate and constant coefficients, first stages, sample
descriptives that compare no arms on an outcome, and the AI's own performance unless the AI
alone is an arm. Per-arm levels printed without a test — a reference-arm mean and a
treatment-arm mean — are an estimate of their estimand with the effect fields null, never an
unmatched estimand.

One estimate, wherever it is reported: an estimate printed in an exhibit and stated again in
the text is ONE estimate — `source` names the exhibit, the numbers come from the exhibit, and
`text_quote` holds the sentence that states it (with `text_page`); a value the sentence adds
and the exhibit lacks (a p-value, a standard error) goes into the same estimate. Two
estimates of one estimand differ in their specification, never only in where they are
reported. An estimate the text alone reports has `source.exhibit` "text", `quote` the
sentence and `text_quote` null.

- `role`: `main` for the one estimate per estimand the authors lead with — the specification
  the results section, the introduction or the discussion states (usually the column of the
  main table the text quotes); `supporting` for another specification presented alongside it
  in the main text; `robustness` for a check (an appendix table, an alternative estimator, a
  sample restriction, a placebo). Exactly one `main` per estimand that has an estimate. When
  the text singles none out, the full-sample estimate in the first column of the main-text
  table that carries the paper's preferred controls is `main`, the other columns of that
  table are `supporting`, and appendix tables, other estimators and other samples are
  `robustness`. `why_role`: the sentence of the text that makes it the main one, or the
  reason in a few words.
- `source`: where the numbers sit — `exhibit` the table or figure id as printed, or "text";
  `panel`, `column` and `row` as printed (the column header, the row label), null where there
  is none; `page`.
- The numbers, each as printed: `n_ref`, `mean_ref`, `sd_ref` for the reference arm and
  `n_treat`, `mean_treat`, `sd_treat` for the treatment arm when the exhibit gives them — the
  reference arm's mean is the control mean, the dependent-variable mean or the pre-period
  mean printed for the same column or panel, never one taken from another specification, and
  `n_ref` and `n_treat` are filled only when the exhibit prints a count per arm;
  `n_obs` the observations behind the estimate, from the Observations row of the same column,
  when they differ from people (tasks × participants); `effect_reported` the effect exactly as
  printed (stars included) and `effect_value` as a number; `effect_type` (codes below; a raw
  difference of means is mean_difference; `other` only with the paper's own term in
  `effect_type_proposed`, null otherwise) and `effect_units` (minutes, SDs,
  percent, log points, percentage points); `se`, `ci_low`, `ci_high`, `p_value`, and
  `p_reported` for what is printed when it is an inequality or stars ("<0.01", "***");
  `statistic` the test statistic as printed ("t = 3.2"); `cluster_level` what the standard
  errors are clustered on when the notes say. A value the exhibit does not print is null —
  never derived.
- `specification`: what this estimate's specification is (controls, estimator, sample,
  weighting), in a few words.
- `quote`: the table row or the sentence the numbers are copied from, as printed, with its
  `page`.
- `cell_as_printed`: the cell the effect was copied from, character for character ("−0.75***",
  "0.12 (0.05)"), and `se_cell_as_printed` the cell or bracket the standard error came from;
  both null for an estimate the text alone reports.
- `estimand_type`: itt when the estimate compares the arms as assigned (the default for a
  randomised comparison), tot when it is scaled to compliers or to actual use (an IV, a LATE),
  per_protocol when non-compliers are dropped, not_applicable when the contrast is not a
  treatment effect (an association, a benchmark).
- `sign_as_printed`: effect_of_ai when the printed coefficient is the effect of having the
  AI, effect_of_removal when the paper's coefficient is the effect of losing it (a ban, an
  outage, an indicator for the period without the tool); null when the comparison is not with
  a no-AI arm. The sign is never changed here.

Then `unmatched_estimands`: estimands for which the exhibits hold no number, each with `why`
and a `reason` code — figure_no_numbers (a figure that prints no number for it; a bar without
a printed label is not a number), online_appendix (the paper points to material that is not
in the text), text_without_number (the comparison is described but no number is printed),
not_estimated (the comparison is named but never estimated), transcription_gap (the exhibit
should hold it and the transcription lacks it), other; and
`orphan_numbers`: effect estimates in the exhibits that belong to no listed estimand
(exhibit, row and column, what they seem to estimate) — they show where the list of
estimands is incomplete. Treatment-effect rows of a table are never left orphaned silently.

Codes for `effect_type`: the form of the number in effect_value. One of: mean_difference; percent_change; ratio; standardized_difference; regression_coefficient; difference_in_differences; elasticity; interaction; simulated; other. When none fits, choose other and give the paper's own term

Codes for `estimand_type`: which effect the row estimates, by how people entered the comparison; null on a reference-arm row. One of: itt = by assigned condition, whether or not the tool was used; tot = the effect for those who used the tool, with assignment as the instrument (LATE, TOT); per_protocol = users or compliers only, by actual use, without instrumenting; not_applicable = no assignment: an observational association or a descriptive comparison
