# The abstract's claims — step 1

You are given the abstract of one paper (a study of generative AI at work), split into
numbered sentences (A1, A2 …), and the sentences of its introduction (I1, I2 …, each with its
page). For every sentence of the abstract say what it is, and for every result sentence point
to the sentences of the introduction that state the same finding.

- `kind`: `result` — a finding of this paper: what was found, an effect, a difference, a
  pattern in the data (a sentence that mixes a finding with its interpretation is a result);
  `method` — what was done: the design, the sample, the treatment, the measures;
  `background` — the motivation, the question, the state of the literature; `implication` —
  what the finding means for policy, practice or theory, a prediction beyond the data;
  `other` — anything else (a sentence about the paper itself: "we contribute …", "this paper
  is the first …").
- `numbers`: the quantities the sentence states, each as printed — digits ("40%", "453",
  "2 times", "26.08%") and number words ("two fewer hours", "twice", "half") alike. For a
  result sentence, the quantities of the finding itself: an effect, a difference, a level, a
  share, a multiple — not the duration of a horizon ("2 weeks after"), the size of a sample
  or a year. Empty when the sentence states none.
- `intro_ids`: for a result sentence, the ids of the introduction sentences that state the
  same finding — what the authors wrote when they restate this result in the introduction,
  with its numbers where they give them; several when the finding is spread over several
  sentences; empty when the introduction does not restate it. For the other kinds, empty.
- `note`: a few words when something is unclear (a sentence stating two findings; a
  restatement that differs from the abstract), else null.

Every sentence of the abstract appears once in your answer, in its order, by its id.
