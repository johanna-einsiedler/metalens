## Introduction

The most relevant findings of an empirical economics paper are usually stated in the abstract. This preset starts from this premise and produces one entry for every stated claim in the abstract, finds the sentences in the introduction that refer to that claim, and uses those to locate the relevant column of a regression table.

A stated claim is an atomic, directional finding the paper's abstract states or announces: a cause, an effect and a sign ("X increases Y"). A claim does not need to have numbers or to mention a specific scope that it applies to. We distinguish three types of claims:

- An **edge** is a causal effect of one variable on another.
- A **comparison** says that an effect is larger for one group than for another and qualifies the edges it compares.
- A statement the abstract makes that is not causal is recorded as **not causal**.

The claim's scope, as far as it can be determined from the sentences, is recorded in four fields: setting, period, population and identification.

Each claim is mapped to one or more **table results** that operationalise it: a regression column (or a figure) whose target regressor measures the cause, whose outcome measures the effect and whose sample lies within the claim's scope. Results that estimate the same quantity are grouped into an estimand, and exactly one result per estimand is marked as the **main** estimate; this is judged from how the authors refer to it in the text. The other estimates are supporting, robustness or heterogeneity results. A regression linked as a main estimate is termed a *headline result* of the paper.

```figure claim-chain
One claim followed from the abstract to the corpus graph
```

*One claim followed through the records, for an illustrative paper on parental death and adult earnings. Steps 1–3 are what this preset records: the claim read from the abstract (1), the introduction sentences that support it (2), and the estimates they link to, grouped into estimands with one main estimate (3). Steps 4–8 are what the Danish Causal Atlas builds from these records: the scope read from the estimates' samples (4), the design behind the main estimate (5), the claim's wordings placed on a shared vocabulary (6), the signed edge in the paper's graph (7) and the merged edge in the corpus graph (8).*

The protocol was written for the [Danish Causal Atlas](https://johanna-einsiedler.github.io/danish-causal-atlas/), which draws the edge claims of Danish register-data economics as one graph. The pipeline is made up of two separate calls: one call extracts the claims, and a second call maps them to regression results. The second call requires the paper's transcribed regression tables as its input, as produced by the [Economics regression tables](/preset?id=econ-regression-tables) preset. Once imported, however, a claims dataset can be used independently of the regression tables, since every result includes the exhibit, column, row and point estimate it refers to.

## How it extracts

Two structured calls per article, each a single agent session with the PDF attached, bounded at 40 turns. The first call needs only the PDF and extracts the claims the article states; the second needs the article's transcribed regression tables ([Economics regression tables](/preset?id=econ-regression-tables)) as well and maps each claim to the regressions that test it. Both prompts are reproduced at the end of this section.

**Claim extraction.** The abstract decides which findings exist. When an article prints no abstract, or its abstract describes what the article does without stating a finding, the introduction is used instead; when the introduction states no finding either, the conclusion is used. The deciding section is recorded with every output. For each sentence of the deciding section the model first judges whether the statement rests on the article's own empirical findings. If it does, the statement is labelled directional (an edge), comparative (a comparison) or not causal. One claim is written per cause–effect pair: a sentence that reports a negative impact on labour-market participation and on income yields two claims. A claim carries

- a directional statement without numbers and without scope, its type, its cause, its effect and, for an edge, its sign (+, −, 0, mixed);
- the anchor: the sentence of the deciding section that states or announces the finding, verbatim;
- the scope in four fields: setting, period, population, identification;
- the evidence: the verbatim sentences that state the cause, the effect and the direction, that is the anchor and any *bridge sentences*, sentences of the introduction that complete it, with a support grade (below);
- for a comparison, the edge claims it compares, the moderator (the dimension of the comparison, such as gender or horizon) and the relation (larger, stronger).

The support grade says how much of the claim the sentences themselves state. The examples are illustrative:

- *explicit*: the anchor alone states direction, magnitude and scope, as in "Children who lose a parent before age 18 earn about 10 percent less at age 30."
- *assembled*: introduction sentences pin them down. The abstract only announces "We study how losing a parent affects children's later earnings"; the introduction adds "Losing a parent before age 18 lowers earnings at age 30 by 10 percent."
- *weak*: a variable or the direction rests on the model's own inference. The abstract says "Parental death leaves lasting economic scars", and no sentence names the outcome, so reading the scars as lower earnings is the model's inference.

**Claim mapping.** The second call receives the PDF, the claims from the first call and a digest of the article's transcribed regressions, in the shape of the [Economics regression tables](/preset?id=econ-regression-tables) preset: every column with its id, table, panel, column, outcome, model, fixed effects, number of controls, sample size and sample description, followed by its coefficient rows as printed. For each edge or comparison claim it

1. quotes the **bridge sentences** of the abstract or introduction that say how the finding was obtained or where it is shown: the design, the sample, the comparison, the exhibit. Only when the introduction does not identify the analysis is a sentence of the body that introduces the exhibit quoted instead;
2. **links every regression that tests the claim**: its target regressor measures the cause, its outcome measures the effect, and its sample lies inside the claim's scope. Each link names the regression id and the coefficient row that carries the claim;
3. **groups the linked estimates into estimands**: two estimates belong to the same estimand when a meta-analyst would average them. A different subgroup, a different horizon or a different definition of the treatment makes a different estimand; a different set of controls, other fixed effects, a robustness sample or another estimator of the same quantity does not;
4. marks **one main estimate per estimand**, the estimate the authors would point to, identified from three signals recorded with it: whether the abstract or introduction states its magnitude, as printed or transformed; whether it refers to the estimate without a number, naming its table, column, subgroup or horizon; and whether the body calls its specification preferred, baseline, main or benchmark. Every other estimate of the estimand is *supporting* if it is another specification the authors rely on, *robustness* if it is presented as a check, or *heterogeneity* if it is a subgroup split presented as a check. Two main links on one estimand are an error; a comparison gets one main estimate per side;
5. **derives stated magnitudes** that no table prints as such, because they are computed from several printed values: a main effect plus an interaction, an estimate relative to the mean, a difference between two columns. The main link is then the coefficient through which the cause enters, and the model writes out the arithmetic from the printed values to the stated number, so that a pointer by number can be checked;
6. **describes each link**: whether the regressor measures the cause itself, a proxy for it, or an assignment indicator standing in for it; whether the outcome is the effect itself or a proxy; whether the estimate's sign, read in its own orientation, agrees with the claim; and one sentence of reasoning;
7. records the **reason when nothing tests the claim**, and a confidence for each claim.

Evidence: every claim cites the abstract sentence that anchors it and the introduction sentences that complete it, verbatim with the page; every result names the exhibit it comes from. Metalens locates the quotations in the PDF on import and flags the ones it cannot find.

**The prompts**, as run. The claim-extraction prompt is followed by the article's abstract as text; the claim-mapping prompt by the claims from the first call (one line per claim with its statement, scope and up to three evidence quotes) and one line per transcribed regression column with its coefficient rows.

````fold Claim extraction prompt (stated claims, protocol v2)
# Stated claims, protocol v2 — the abstract decides, the introduction refines

You have the paper (the attached PDF) and, at the end of this prompt, its
ABSTRACT as text.

## What counts as a claim

A claim of this paper is a finding its **ABSTRACT** states or announces. The
abstract decides WHICH findings exist; nothing else does. Work through the
abstract sentence by sentence.

1. Every abstract sentence that states or announces a finding of THIS paper is
   an **anchor**.
2. For each anchor, write the atomic, directional claim(s) it carries.
   - If the anchor states them outright ("Bequests increase absolute wealth
     inequality but reduce relative inequality"): one claim per cause-effect
     pair, `support: explicit`.
   - If the anchor only announces the finding in general terms ("we find
     significant effects on self-reported income", "we provide evidence of
     taxable income responses"): use the INTRODUCTION to refine it -- which
     variables, which direction -- and split it into its atomic parts. Every
     refined claim cites the abstract anchor AND the introduction sentence(s)
     that complete it: `refined_by_introduction: true`, `support: assembled`
     (or `weak` if the direction is your own inference).
   - Refinement never adds what the anchor does not announce: an anchor about
     "earnings and mental health" cannot carry a claim about residential
     location.
3. A finding that appears ONLY in the introduction -- that no abstract sentence
   states or announces -- is NOT a claim. Do not list it, however prominent it
   is in the introduction.

Use only the abstract and the introduction (everything before the first section
that follows the introduction). Never the body, tables, figures or conclusion.

## Rules for each claim

1. **Atomic.** One cause, one effect. "X increases labor and capital income" is
   two claims. Never use placeholder variables ("one income type", "the other
   group"): one claim per CONCRETE pair.
2. **Directional only.** `statement` reads
   `<cause> increases | decreases | has no effect on | has mixed effects on <effect>`.
   NO numbers, no "significantly", no method, no setting, no population in it.
   A stated magnitude goes, verbatim, in `magnitude_stated`.
3. **Sign in the paper's own orientation**: `+`, `-`, `0` (the authors report
   testing and finding no effect), `mixed` (the sign differs across groups or
   settings and the text says so). Name `cause` and `effect` the way the
   abstract names them: short noun phrases, no transformations ("earnings", not
   "log earnings"). Use the authors' OWN variable; do not convert it to its
   mirror image (net-of-tax rate vs tax rate) -- that is reconciled later.
4. **Scope as fields, never in the statement and never inside a variable.**
   `setting`, `period`, `population`, `identification`. "labor income of wage
   earners" is effect "labor income" with `scope.population` "wage earners";
   two findings that differ only in who they are about are two claims with the
   same cause and effect and different scope. null when not stated.
5. **Anchor and evidence.** `anchor.quote` is the abstract sentence, VERBATIM
   from the abstract text below. `evidence[]` is the full set of verbatim
   sentences that JOINTLY state the cause, the effect and the direction: the
   anchor, plus any introduction sentences that complete it. Each item:
   `quote` (at most 60 words), `page` (as printed), `section` (`abstract` |
   `introduction`), `role` (`anchor` | `names_variables` | `gives_direction` |
   `gives_magnitude` | `scope`). Every quote is machine-checked: the anchor
   against the abstract text, every quote against the PDF. A paraphrase fails.
6. **Support grade, never upgraded.** `explicit`: the anchor alone states
   cause, effect and direction. `assembled`: anchor plus introduction
   sentence(s) are needed and together state all three. `weak`: a variable or
   the direction is your inference -- say which in `support_note`.
7. **Type.** `edge`: a causal effect of one variable on another.
   `comparison`: the ABSTRACT compares effects (larger for A than B) -- give
   `qualifies` (ids of the edge claims compared), `moderator`, `relation`; a
   comparison never creates a variable, and if an edge it qualifies is not
   otherwise stated, add that edge. `not_causal`: an abstract sentence that
   presents a descriptive fact, a method or data as a finding -- list it
   (`statement`, anchor, evidence; no cause / effect / sign) so nothing is
   silently dropped.
8. **Only this paper's findings.** Not prior literature, motivation, hypotheses
   or what "could" happen. Tested nulls ARE findings.
9. **No duplicates.** The same (cause, effect, sign, scope) is ONE claim.
10. Ids `S1`, `S2`, ... in the order of the abstract.

If the abstract states or announces no finding at all, return
`{"claims": [], "not_applicable": "<why>"}`.

Return ONLY JSON matching the schema.


---

## ADAPTER for this corpus (overrides one sentence above)

No abstract text is attached to this prompt. Take the abstract from the PDF, as
printed, and return it VERBATIM in `abstract_text`. Every `anchor.quote` must be
a sentence of that text. Where the protocol says "the abstract text below", read
"the abstract you transcribed into `abstract_text`". Set `claim_source: "abstract"`.

**When no abstract decides** (a deliberate extension of the protocol, recorded in
`claim_source` so these papers can be told apart): many short papers, and older
articles, print no abstract, and a few print one that describes what the paper
does without stating a finding. Then:

1. the INTRODUCTION decides which findings exist: every introduction sentence
   that states or announces a finding of this paper is an anchor, and the rest
   of the protocol applies unchanged with "introduction" in place of
   "abstract" (refinement may use the rest of the introduction). Set
   `claim_source: "introduction"`. If an abstract is printed, still return it in
   `abstract_text`; otherwise `abstract_text: null`.
2. Only if the introduction states no finding either, the CONCLUSION decides, in
   the same way; set `claim_source: "conclusion"`, and evidence quotes may then
   come from the conclusion.

Never assemble an abstract from several sections, and never take claims from
the body or the tables. If no section states a finding, return `claims: []` and
`not_applicable` saying why.

## The four-question test (apply to every sentence of the deciding section)

1. **Is it this paper's own result?** Not motivation, prior literature, a
   hypothesis, a description of the data or the method, or what "could" happen.
   If not, it is not a claim. This question must hold for every claim.
2. **Does it relate two things with a direction?** "X increases | decreases |
   has no effect on Y". Then it is an `edge`. Associational wording counts the
   same as causal wording: "is associated with", "predicts", "is correlated
   with", "goes with" are edges; the authors' design belongs in
   `scope.identification`, not in the claim type. A tested null is an edge
   with sign `0`. One cause and one effect per claim.
3. **Does it compare the size of effects?** "Larger for women than for men",
   "stronger in the long run". Then it is a `comparison`, naming the edges it
   compares; add those edges if the abstract does not state them elsewhere.
4. **Otherwise** it is `not_causal`: a descriptive fact, a quantity estimated
   without a cause-effect relation, a method presented as a finding. List it
   so nothing is silently dropped; it is not matched to regressions.

A sentence passes 1 and then exactly one of 2 or 3, or it is `not_causal`.
````

````fold Claim mapping prompt (claim map, protocol v1)
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


---

## ADDENDUM for this corpus (where it differs from the text above, this wins)

### The results in B are extracted regression columns

Each entry in B is one regression column with its id, table, panel, column,
outcome, model and sample, followed by its coefficient rows as printed:
`[row] "label" = value`. In every match:

- `regression_id`: the id of the column, exactly as listed (this replaces
  `claim_id` in the text above);
- `target_row`: the `[row]` number of the ONE coefficient in that column that
  carries the claim. A match is to a column AND a specific coefficient in it,
  never to a column alone. If the same column carries the claim through two
  coefficients, give two matches.

### Estimands: exactly one `main` per estimand

List the ESTIMANDS: the distinct quantities the paper reports for this
cause-effect pair. Two estimates are the same estimand when a meta-analyst
would average them, and different estimands when averaging them would be
wrong. What makes a different estimand:

- a different **subgroup** of the population (men / women, a birth cohort, an
  industry);
- a different **horizon** (years 0-1 vs years 6-10, short vs long run);
- a different **definition of the treatment** (continuous exposure vs a
  discrete indicator, one reform vs another).

What does NOT make a different estimand: another set of controls, other fixed
effects, a robustness sample, a different estimator for the same quantity.
Those are the same estimand measured again.

Give every estimand a short key in `estimand`: `<claim id>/<subgroup>/<horizon>`,
e.g. `S1/men/years-0-1`, `S3/all/5y`, `S2/all/discrete-exposure`. Use `all`
where the dimension does not apply. The key is identical on every estimate of
that quantity. Also fill `subgroup` and `horizon` (null where they do not apply).

For each estimand, exactly one row is the estimate the authors would point to:
`role: main`. Every further printed estimate of the SAME estimand is
`supporting` (another specification they rely on), `robustness` (a check) or
`heterogeneity` (a split reported only as a check). **Two `main` rows with the
same `estimand` is an error**: either the key is too coarse, or you have not
chosen the preferred estimate. This replaces "at most two `main` per claim".

For a `comparison` claim, match the results on BOTH sides (`role: main`, one
per side, each with its own `subgroup` and `estimand`) and say in `why` which
side each is.

### Link EVERY regression that tests the claim

Whether the introduction mentions a result is NOT a condition for linking it.
Every extracted regression whose target regressor measures the cause, whose
outcome measures the effect and whose sample falls inside the scope is linked,
with its role. The introduction and the abstract matter for the next point.

### Which linked results the paper POINTS TO

For every `main` row, say whether the abstract or the introduction singles that
estimate out, in `pointed_to`:

- `number`: the abstract or introduction states this estimate's magnitude, as
  printed or transformed (a percent, an elasticity, a difference of two
  coefficients). Quote the sentence in `pointer_quote`, verbatim.
- `reference`: the abstract or introduction refers to this result without a
  number: it names the table, the column, the subgroup or the horizon this
  estimate belongs to ("Table 3 shows", "the effect for women"). Quote it.
- `none`: the text does not single this estimate out. `pointer_quote: null`.

Also set `preferred_wording: true` when the BODY of the paper calls this
specification preferred, baseline, main or benchmark (quote nothing; one
sentence in `why` suffices). Otherwise `false`.

A pointer is evidence, not a filter: a `main` the text does not point to stays
linked as `main`. For `supporting`, `robustness` and `heterogeneity` rows set
`pointed_to: "none"` and `preferred_wording: false`.

### When the stated number is COMPUTED from several printed estimates

A stated magnitude is sometimes not one coefficient but a combination: a sum
of a main effect and an interaction, a ratio to a mean, a difference between
two columns. Then the `main` row is the coefficient the claim's cause enters
through, and `derivation` spells out the arithmetic from printed values to the
stated number, naming each row used: "Incentive 6.17 + Match × incentive
−4.16 = 2.01 letters, 13% of the matched mean 15.3". A derivation uses only
numbers a reader can find in the cited table or text. Leave `derivation` null
when the stated number is the printed coefficient itself (possibly rescaled).

### Columns without coefficient rows

Some extracted columns print no row typed as a coefficient: a table of effects
per dollar, of marginal effects, of elasticities. Their rows are listed as
`[row] "label" = value` all the same. Name the row that holds the estimate in
`target_row` exactly as for a coefficient; the harness records that the row is
not a coefficient row.
````

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
