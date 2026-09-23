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
5. Scope as fields, never in the statement and never inside a variable: `scope_setting`,
   `scope_period`, `scope_population`, `scope_identification` — the setting, period, population
   and design of the STUDY. "labor income of wage earners" is effect "labor income" with
   population "wage earners". null when not stated.
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

For every `edge` and `comparison` claim, list under `results` the printed results that
OPERATIONALISE it: a table column (or a figure) whose target regressor measures the claim's
cause, whose outcome measures the claim's effect, and whose setting, period, population and
design fall inside the claim's scope. Read the whole paper for this part, appendix tables
included.

One result row is ONE printed estimate. For each:
- `source_table`, `panel`, `column`, `row_label`: where the coefficient is printed, as printed.
- `subgroup`: whose estimate it is when the column or panel restricts the claim's population
  ("men", "women", "top earnings quartile"); null when it covers the whole population of the
  claim. `horizon`: the window it covers when the paper reports several ("years 0-1", "year 5",
  "long run"), as printed in the panel or column heading; null when there is only one.
- `point_estimate`: the coefficient exactly as printed in that cell — sign and every digit;
  stars, parentheses, commas and % stripped; never rounded, never computed, never read off an
  axis. `estimate_se`: the standard error printed for it, if the table prints one.
- `role`: `main` (the estimate the authors would point to for this finding) | `supporting`
  (another specification of the same finding they rely on) | `robustness` | `heterogeneity`
  (a subgroup split of it).
- `treatment_relation`: `direct` (the regressor measures the cause itself) | `proxy` |
  `assignment` (a treatment-group, reform or instrument indicator standing in for the cause).
- `outcome_relation`: `direct` | `proxy`.
- `sign_consistent`: read in its OWN variable orientation, does the estimate agree with the
  claim's direction? (A net-of-tax-rate elasticity of +0.2 agrees with "the tax rate decreases
  income".) null when there is no estimate or it cannot be told.
- `why`: one sentence.

**One `main` per stratum.** A stratum is one (`subgroup`, `horizon`) pair of a claim. Within a
stratum exactly one result is the estimate the authors would point to — `role: main`. Every
further printed estimate of that same stratum is the same finding measured again and is
`supporting` (another specification they rely on), `robustness` (a check) or `heterogeneity`
(a split they only report as a check, not as the claim). So a claim with estimates for men and
women over two horizons has four `main` rows and as many alternatives as the paper prints;
a claim the paper estimates once under six specifications has ONE `main` and five alternatives.
Different definitions of the treatment (continuous exposure vs a discrete indicator, one reform
vs another) are alternatives, not strata — unless the abstract states them as separate findings.

Sign agreement is NOT a criterion for matching: a result that tests the claim and disagrees
with it is a match with `sign_consistent: false`, and that is a finding. Prefer few, right
matches over many plausible ones. The same result may serve more than one claim. For a
`comparison` claim, match the results on BOTH sides of the comparison (`role: main`, one per
side, each with its `subgroup`) and say in `why` which side each is.

A finding shown only in a figure: `exhibit: figure`, `source_table` the figure number,
`point_estimate` only when the figure PRINTS the value (an annotation such as "DD elasticity
= 0.214 (0.011)"); otherwise null.

When nothing matches, return `results: []` and say why in `notes` ("shown only graphically;
no estimate is printed", "the estimate is in an online appendix not bound into this PDF").
Do not force a match.

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
