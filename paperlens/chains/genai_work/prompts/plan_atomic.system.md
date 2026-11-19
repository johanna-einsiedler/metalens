# The abstract's claims, one per outcome x task unit

You are given the result sentences of one paper's abstract, the units the paper's record has
(outcome category x task x arm against reference arm, with the measures under each), and the
numbered sentences of the introduction and of the discussion or conclusion.

Split every abstract sentence into its atomic claims: one claim per empirical relationship it
states, that is one per unit it speaks about. A unit is an outcome category of one task; which
arms are compared is not part of it, so a claim that pools several AI arms or names no arm
still has a unit. For each claim give the unit it belongs to (`unit_id` from the list; null
only when no outcome category or task fits, with the statement saying what it is about),
the number the claim states for THAT unit as printed ("12.2%", "19%", "two times"; null when
the sentence gives no number for this unit, even if it gives one for another), the direction
(`better`, `worse`, `null_effect`, `mixed`, `unclear`), the subgroup or horizon it refers to
if any, and the ids of the introduction and conclusion sentences that restate this claim
(with their numbers where they give them). Ids C1, C2 … in the abstract's order.

The `statement` is the atomic claim itself, in your words: what the sentence says about THAT
unit, naming the outcome, the task and the direction, with the number if the sentence gives one
for it ("time taken on the email task: lower with the AI", "answer quality on the comprehension
task: higher, by 19%"). Never copy the sentence as the statement; when one sentence covers
several units, each unit's statement is different and says only its own part.
