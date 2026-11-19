# Claims in the discussion or conclusion about units the abstract does not cover

You are given the numbered sentences of one paper's discussion or conclusion, the units of
the paper's record the abstract says nothing about (outcome category x task, with the measures
under each), and the numbered sentences of the introduction. For each of these units, find the
sentences of the discussion or conclusion that state a finding about it, and give one atomic
claim per finding: `sentence_id` the discussion sentence, `statement` the finding in the
authors' words, `unit_id`, `number` as printed when the sentence gives one for this unit (else
null), `direction` (`better`, `worse`, `null_effect`, `mixed`, `unclear`), the subgroup or
horizon if any, `intro_ids` the introduction sentences that state the same finding, and
`conclusion_ids` the other discussion sentences that repeat it. Ids d1, d2 … A unit no sentence
speaks about gets no claim.
