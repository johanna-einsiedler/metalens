# TASK

You are reading one empirical paper, attached as a PDF. Extract what the paper SAYS it found
and trace each finding to what its tables SHOW. Return exactly ONE JSON object matching the
schema at the end.

Two readers depend on this. A reviewer will check every quote against the PDF and every
number against the printed table. A program will compare each point estimate you write with
an independent transcription of the same table cell. Therefore: quote VERBATIM, transcribe
numbers EXACTLY as printed, and never invent a claim the abstract does not make.

# WHAT COUNTS AS A CLAIM — the abstract decides, the introduction refines

A claim of this paper is a finding its ABSTRACT states or announces. The abstract decides
WHICH findings exist; nothing else does. Work through the abstract sentence by sentence.

1. Every abstract sentence that states or announces a finding of THIS paper is an anchor.
2. For each anchor, write the atomic, directional claim(s) it carries.
   - If the anchor states them outright ("Bequests increase absolute wealth inequality but
     reduce relative inequality"): one claim per cause–effect pair, `support: explicit`.
   - If the anchor only announces the finding in general terms ("we find significant effects
     on self-reported income", "we provide evidence of taxable income responses"): use the
     INTRODUCTION to refine it — which variables, which direction — and split it into its
     atomic parts. Every refined claim cites the abstract anchor AND the introduction sentence
     that completes it: `refined_by_introduction: true`, `support: assembled` (or `weak` if
     the direction is your own inference).
   - Refinement never adds what the anchor does not announce: an anchor about "earnings and
     mental health" cannot carry a claim about residential location.
3. A finding that appears ONLY in the introduction — that no abstract sentence states or
   announces — is NOT a claim. Do not list it, however prominent it is.

For the claims themselves use only the abstract and the introduction (everything before the
first section that follows the introduction). The body, tables and figures are read in the
second part, to locate the results.

If the PDF prints no abstract, set `paper_metadata.abstract_source` to `none` and return
`claims: []`. Never assemble an abstract from the introduction or the conclusion.

# RULES FOR EACH CLAIM

1. Atomic — one cause, one effect, as granular as the sentence allows. "X increases labor and
   capital income" is two claims. Never use placeholder variables ("one income type", "the other
   group"): one claim per CONCRETE pair. A sentence that names several measured things is that
   many claims: "Both women and men experience mental health deterioration, leading to increased
   use of psychological assistance and prescriptions for mental health conditions and opioids"
   is THREE claims — parental death → + use of psychological assistance, → + prescriptions for
   mental-health conditions, → + opioid prescriptions. Split to the variable the paper actually
   measures, never to the umbrella it stands for.
2. Directional only. `statement` reads `<cause> increases | decreases | has no effect on |
   has mixed effects on <effect>`. NO numbers, no "significantly", no method, no setting, no
   population in it. A stated magnitude goes, verbatim, in `magnitude_stated`.
3. Sign in the paper's own orientation: `+`, `-`, `0` (the authors report testing and finding
   no effect), `mixed` (the sign differs across groups or settings and the text says so).
   Name `cause` and `effect` the way the abstract names them: short noun phrases, no
   transformations ("earnings", not "log earnings"). Use the authors' OWN variable; do not
   convert it to its mirror image (net-of-tax rate vs tax rate).
4. Keep the hierarchy: `cause_construct` / `effect_construct`. When a paper measures one
   construct through several variables, each claim names its own concrete variable AND the
   construct it indicates: the three claims above all carry `effect_construct: "mental health"`,
   written identically. That is what lets them be read together later without merging them now.
   Null when the variable IS the construct ("earnings" needs no umbrella). Never put the
   construct in `cause` / `effect`, and never invent one the paper does not talk about.
   Say where the link comes from: `construct_basis` is `stated` when the paper itself presents
   these variables as measuring the construct, `inferred` when reading them as one construct is
   YOUR step — and then `construct_note` writes that auxiliary assumption in one sentence
   ("opioid prescriptions are treated as an indicator of mental health"). It is an assumption a
   reviewer must be able to reject, so it is never left implicit.
5. Scope as fields, never in the statement and never inside a variable: `scope_setting`,
   `scope_period`, `scope_population`, `scope_identification` — the setting, period, population
   and design of the STUDY. "labor income of wage earners" is effect "labor income" with
   population "wage earners". `scope_population` is the WHOLE population the paper studies
   ("adult children aged 25-50 who lost a parent") and is filled even when the estimates below
   are split into subgroups — the subgroups are slices of it. null only when the paper does not
   say.
6. A subgroup does NOT split the claim. "men's earnings decline by 2 percent, while women's
   decline by 3 percent" is ONE claim — parental death → − earnings — whose two estimates are
   two `results` rows with `subgroup: "men"` and `subgroup: "women"`. Split into separate claims
   only when the DIRECTION differs between the groups (then each claim names its group in
   `scope_population`, and the abstract's comparison is a `comparison` claim on top).
7. Anchor and introduction sentence, VERBATIM. `anchor_quote` is the abstract sentence (at most
   60 words). `intro_sentence` is the introduction sentence that completes the claim or says
   HOW the finding was obtained or WHERE it is shown — the design, the sample, the comparison,
   the exhibit; it is the bridge from the claim to the results. Only when the introduction does
   not identify the analysis may it be the body sentence that introduces the exhibit. A
   paraphrase fails the check.
8. Support grade, never upgraded. `explicit`: the anchor alone states cause, effect and
   direction. `assembled`: anchor plus introduction sentence together state all three. `weak`:
   a variable or the direction is your inference — say which in `notes`.
9. Type. `edge`: a causal effect of one variable on another. `comparison`: the ABSTRACT
   compares effects (larger for A than B) — give `qualifies` (the ids of the edge claims
   compared), `moderator`, `relation`; a comparison never creates a variable, and if an edge it
   qualifies is not otherwise stated, add that edge. `not_causal`: an abstract sentence that
   presents a descriptive fact, a method or data as a finding — list it (statement, anchor;
   no cause / effect / sign) so nothing is silently dropped.
10. Only this paper's findings. Not prior literature, motivation, hypotheses or what "could"
   happen. Tested nulls ARE findings.
11. No duplicates. The same (cause, effect, sign, scope) is ONE claim.
12. Ids `S1`, `S2`, … in the order of the abstract.

# FROM WHAT THE PAPER SAYS TO WHAT ITS TABLES SHOW

Work from the claim DOWN, in three steps. Read the whole paper for this part, appendix tables
included.

## Step 1 — which quantities does the paper estimate for this claim?

List the ESTIMANDS: the distinct quantities the paper reports for this cause–effect pair. Two
estimates are the same estimand when a meta-analyst would average them, and different estimands
when averaging them would be wrong. What makes a different estimand:

- a different **subgroup** of the population (men / women, a birth cohort, an industry) — and a
  subgroup the abstract names ANYWHERE counts, not only in the sentence that carries the claim:
  "women with young children experience a larger decline" adds the estimands
  `S1/women-with-young-children/…` and `S1/women-without-young-children/…` to the EARNINGS claim.
  The `comparison` claim that states the contrast then points at those same estimands (it carries
  the two sides as its own results); it is never the only place a subgroup's number lives,
- a different **horizon** (years 0–1 vs years 6–10, short vs long run). When the abstract's own
  magnitude names one ("2 percent in the fifth year"), that horizon is an estimand: the number
  the paper headlines must have an estimand to land in,
- a different **definition of the treatment** (continuous exposure vs a discrete indicator, one
  reform vs another) — the number means something different, so it is its own estimand.

What does NOT make a different estimand: another set of controls, other fixed effects, a
robustness sample, a different estimator for the same quantity. Those are the same estimand
measured again.

A subgroup the abstract names ANYWHERE belongs to the claim it slices, even when a different
sentence names it. "Women with young children experience a comparatively larger earnings decline"
adds two estimands to the earnings claim — `subgroup: "women with young children"` and
`subgroup: "women without young children"` — and the `comparison` claim that states the contrast
then matches those same two estimates rather than carrying estimates of its own.

Give every estimand a short key in `estimand`: `<claim id>/<subgroup>/<horizon>`, e.g.
`S1/men/years-0-1`, `S3/all/5y`, `S2/all/discrete-exposure`. Use `all` where the dimension does
not apply. The key is identical on every estimate of that quantity.

## Step 2 — the estimates of each estimand

One row in `results` is ONE printed estimate. For each estimand, exactly one row is the
estimate the authors would point to: `role: main`. Every further printed estimate of the SAME
estimand is `supporting` (another specification they rely on), `robustness` (a check) or
`heterogeneity` (a split reported only as a check). **Two `main` rows with the same `estimand`
is an error**: either the key is too coarse, or you have not chosen the preferred estimate.

So a claim the paper estimates for men and women over two horizons has four `main` rows plus
whatever alternatives it prints; a claim it estimates once under six specifications has ONE
`main` and five alternatives.

For each result row:
- `source_table`, `panel`, `column`, `row_label`: where the coefficient is printed, as printed.
- `subgroup` / `horizon`: the dimensions of the estimand, null where the dimension does not apply.
- `point_estimate`: the coefficient exactly as printed in that cell — sign and every digit;
  stars, parentheses, commas and % stripped; never rounded, never computed, never read off an
  axis. `estimate_se`: the standard error printed for it, if the table prints one.
- `treatment_relation`: `direct` (the regressor measures the cause itself) | `proxy` |
  `assignment` (a treatment-group, reform or instrument indicator standing in for the cause).
  `outcome_relation`: `direct` | `proxy`.
- `sign_consistent`: read in its OWN variable orientation, does the estimate agree with the
  claim's direction? (A net-of-tax-rate elasticity of +0.2 agrees with "the tax rate decreases
  income".) null when there is no estimate or it cannot be told.
- `why`: one sentence.

Sign agreement is NOT a criterion for matching: a result that tests the claim and disagrees with
it is a match with `sign_consistent: false`, and that is a finding. The same result may serve
more than one claim. For a `comparison` claim, match the results on BOTH sides (`role: main`,
one per side, each with its own `subgroup` and `estimand`) and say in `why` which side each is.

Two different questions, kept apart, because a number without its analysis cannot be compared
with anything:

- **Which analysis is this?** `exhibit` and `source_table` (+ `panel`, `column`) — the table or
  figure whose specification, sample and estimator produced the number. Fill them even when the
  number itself is not printed there. `exhibit: none` only when the paper reports the analysis
  in prose alone ("in unreported regressions we find …"): that estimate has no method a reader
  can check, and saying so is the point.
- **Where does the number come from?** `value_from`: `printed` when the exhibit prints it (a
  table cell, a figure annotation such as "DD elasticity = 0.214 (0.011)" — never read off an
  axis); `text` when the exhibit shows the result but only the prose gives the number ("women
  visit psychologists 0.10 more times per year"); `absent` when no number is reported at all.

So a finding shown in Figure 3 whose value appears only in the introduction is
`exhibit: "figure"`, `source_table: "Figure 3"`, `panel: "A"`, `value_from: "text"`, with the
sentence as the evidence for `point_estimate` — the figure tells a reader what was estimated and
how, the sentence gives the number. Only `printed` table values can be cross-checked against an
independent transcription of the table, so the distinction is never cosmetic.

## Step 3 — the checks (they never delete a claim)

- If the abstract or the introduction states a magnitude, it must reappear as the
  `point_estimate` of one `main` row of this claim. If no extracted estimate reproduces it, keep
  the claim and say so in `notes` — a number you cannot place is a finding about the paper.
- A claim whose results you cannot find: `results: []` and the reason in `notes` ("shown only
  graphically; no estimate is printed", "the estimate is in an online appendix not bound into
  this PDF"). Never drop a claim because it carries no number, and never force a match.

# THE THREE QUOTES

Every claim carries its abstract anchor as evidence on `anchor_quote` (`source: "abstract"`)
and, when it has one, its introduction sentence on `intro_sentence` (`source:
"introduction"`). Every table result carries the table line that holds the coefficient as
evidence on `point_estimate`, with `source` naming the exhibit and column ("Table 3, column
(2)"); the snippet is the printed row — label and values — not a bare number, because a bare
number recurs throughout a paper and locates nothing.

# CONFIDENCE CALIBRATION

`claim`: high when the anchor states cause, effect and direction outright; medium when the
introduction had to supply one of them; low when you inferred a variable or the direction.
`scope`: high when the abstract or introduction states it; low when taken from the body.
`results`: high when the table cell is unambiguous and the role is clear; medium when the
match rests on a proxy or an assignment variable; low when you are unsure the column tests
this claim.
