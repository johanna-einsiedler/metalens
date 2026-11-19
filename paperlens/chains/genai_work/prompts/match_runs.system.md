# Matching two readings of one paper's estimands

Two independent readings of the same paper each listed what the paper estimates: its tasks (distinct work
by a distinct sample), the arms of each task, its outcomes, and one estimand per task × arm × reference
arm × outcome × subgroup × horizon. Most estimands were paired by a program. You are given the ones it
could not pair: list A from the first reading, list B from the second, each estimand described in words.

Pair an estimand of A with an estimand of B when they are the same estimand: the same task, the same two
arms compared the same way round, the same outcome measure, the same subgroup (the full sample however it
is called is one subgroup) and the same horizon (the post-treatment effect however it is called is one
horizon). Wording differs between the readings; names and ids do not matter, meaning does. Each estimand
is paired at most once. Do not pair when one side pools what the other splits (a subgroup against the full
sample, one horizon against several, two outcomes against one composite), when the arms differ, or when
the outcome differs.

Then, for every estimand of B left unpaired, give the reason: `not_in_primary` when nothing in A estimates
it (the first reading missed it or chose not to list it), `split_or_merge` when A has the same comparison
at another level of pooling, `other` when you cannot tell. One sentence of reason each; be brief.