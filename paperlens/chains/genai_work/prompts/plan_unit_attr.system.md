# The level each unit is analysed at, and what the appendix may hold

You are given the units of one paper's record (an outcome category of one task), the passages
describing each task, the study's unit of analysis as coded, and the sentences of the paper
that point to an appendix or supplementary material.

1. For each unit: `level`, the unit the outcome is measured and analysed on — `individual`
   (a person, possibly over time), `team` (a group doing the work together, a project, a
   repository), `firm`, `occupation`, `region_or_country`, `other`; `unit_size` for a team or
   a firm: its size or the range of sizes as the paper states it ("teams of 3 to 5", "firms
   with 50 to 2,000 employees"), null when not stated or not a team or firm; `quote` the
   sentence that establishes the level (and size), with its `page`; null when the paper only
   implies it.
2. For each pointer sentence: the ids of the units whose results, measures, tasks or samples
   the appendix it points to may hold more about (empty when it concerns none, as for a
   survey instrument or a proof), and `may_hold`: what it says is there, in a few words.
