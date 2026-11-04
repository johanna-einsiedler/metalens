# Harmonise the causal claims of “${dataset}” into corpus edges

Each row below is one causal claim an article states: the cause and the effect as the abstract names them, the broader construct each is an indicator of when the article says so, the sign, and the statement. Rows marked [NEW] were added since the committed edges below were made.

Map every claim's cause and effect to a concept in one shared vocabulary of short snake_case concept names (`minimum_wage`, `employment`, `earnings`, `mental_health`), so that claims about the same relation meet across articles. Prefer the stated construct over the variable; keep the claim's orientation (its cause stays the cause); do not merge distinct concepts because they sound alike, and do not split one concept over spellings. Carry the sign as stated. Return ONE ROW PER CLAIM — not per concept pair; the rows are merged into edges afterwards — and cite the claim's row id in `sources`.

## The committed edges (keep their concept names unless a NEW claim shows them wrong)

${existing}

## The claims (${new_count} new)

${rows}
