# The abstract's claims against the record — step 2

You are given the result claims of one paper's abstract, each with the sentences of the
introduction that restate it, and the record another pass extracted from the paper's methods,
results and appendix: its tasks, arms and outcomes, its estimands (one task, one arm against
a reference arm, one outcome, one subgroup, one horizon) and under each estimand the
estimates the paper reports of it, with their numbers as printed. You see the record, not the
paper.

For every claim, name the estimates in the record that support it and say how far:
- `estimate_ids`: the ids of the estimates the claim rests on, copied from the record's
  lines "estimate `<id>`" (an estimand's id, E12, is not an estimate's id) — the estimate
  whose number the claim states, or the estimates whose direction and size the claim
  summarises; several when the claim covers several outcomes, subgroups or horizons; empty
  when none does.
- `coverage`: `exact` — a number the claim states (in the abstract or in its introduction
  sentences) is printed in one of the supporting estimates, as the same quantity (an effect,
  a mean, a percentage), not merely the same digits; `consistent` — the claim states no
  number to match and the supporting estimates give its direction and size; `partial` —
  part of the claim is in the record (one of two outcomes; the direction but not the stated
  size; the size, but for another subgroup or horizon); `none` — nothing in the record backs
  the claim.
- `why`: for `partial` and `none`, what the record lacks, or what the claim rests on instead
  (a computed quantity, a comparison the paper does not estimate as such, a qualitative
  statement); for `exact`, the number matched and the estimate it sits in, in a few words;
  else null.

Every claim appears once in your answer, by its id. Never guess a number: a stated number that
no estimate prints makes the claim `partial` or `none`, not `exact`.
