# Claim map, protocol v1 — from what the paper SAYS to what its tables SHOW

You have the paper (the attached PDF). Below are

- **A. the stated claims**: atomic, directional findings taken from the
  abstract and the introduction (`edge` claims and the `comparison` claims
  that qualify them), each with its scope; and
- **B. the extracted results**: one row per estimate already extracted from
  the paper's tables and figures — id, exhibit, outcome, target regressor,
  estimate, model, sample.

For EVERY stated claim, do three things.

1. **Introduction evidence.** Quote the sentence(s) of the abstract or
   introduction that say HOW the finding was obtained or WHERE it is shown:
   the design, the sample, the comparison, the exhibit. These sentences are
   the bridge from the claim to the results. Verbatim, at most 60 words each,
   with the page as printed and `section` (`abstract` | `introduction`). They
   may repeat the claim's own evidence; add the sentences that identify the
   analysis. Only when the introduction does not identify the analysis may you
   quote the body sentence that introduces the exhibit (`section`: `body`).

2. **Matches.** The extracted results that OPERATIONALISE the claim: the
   result's target regressor measures the claim's cause, its outcome measures
   the claim's effect, and its setting, period, population and design fall
   inside the claim's scope. For each:
   - `claim_id`: the result's id, exactly as listed.
   - `role`: `main` (the estimate the authors would point to for this
     finding) | `supporting` (another specification of the same finding they
     rely on) | `robustness` | `heterogeneity` (a subgroup split of it).
   - `treatment_relation`: `direct` (measures the cause itself) | `proxy` |
     `assignment` (a treatment-group, reform or instrument indicator standing
     in for the cause).
   - `outcome_relation`: `direct` | `proxy`.
   - `sign_consistent`: true | false | null — read in its OWN variable
     orientation, does the estimate agree with the claim's direction? (A
     net-of-tax-rate elasticity of +0.2 agrees with "the tax rate decreases
     income".) null when there is no estimate or it cannot be told.
   - `why`: one sentence.

   Sign agreement is NOT a criterion for matching: a result that tests the
   claim and disagrees with it is a match with `sign_consistent: false`, and
   that is a finding. Prefer few, right matches over many plausible ones; at
   most two `main` per claim unless the finding is genuinely carried by
   several estimates. The same result may serve more than one claim.
   For a `comparison` claim, match the results on BOTH sides of the
   comparison (`role`: `main`) and say in `why` which side each is.

3. **When nothing matches**, return `matches: []` and an `unmatched_reason`
   ("shown only graphically; no estimate was extracted", "the estimate is in
   an appendix table that was not extracted", ...). Do not force a match.

Give each claim a `confidence`: `high` | `medium` | `low`.

Return ONLY JSON matching the schema.
