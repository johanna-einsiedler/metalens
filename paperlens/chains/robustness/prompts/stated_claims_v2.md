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
