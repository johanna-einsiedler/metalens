# From a claimed number to the printed cells

You are given claims that state a number (from a paper's abstract, or from the introduction
or conclusion sentence that restates an abstract claim with a number), each with its unit,
and the estimates the record holds for that unit: every specification, with its numbers as
printed and the cell they were read from. For each claim say where its number comes from:

- `printed_in`: "<estimate_id>.<field>" when the number is printed as it stands in one of the
  estimates' fields (the same quantity, not merely the same digits); `formula` and `inputs`
  then empty.
- otherwise `formula`: an arithmetic expression over named inputs that reproduces the number
  from printed values, for example `(mean_treat - mean_ref) / mean_ref`, `effect / mean_ref`,
  `exp(effect) - 1`, `share_treat / share_ref`, with every name listed in `inputs` as the
  estimate id and field it is read from. Use only the fields given; invent no value. The
  expression may use + - * / and exp, log, abs. Whenever you can say in words how the number
  is derived from printed values — an average over the AI arms, a difference or ratio of two
  printed values, a rounding of such a result — write that derivation as the formula; leave
  `formula` null only when no printed value enters it (a count of tasks, a number about
  another unit), and say so in `why`.
- `estimate_ids`: the estimates the claim rests on (the ones the inputs come from, or the one
  it is printed in).
- `why`: one sentence.
