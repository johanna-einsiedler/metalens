# TASK

Extract, from a PDF reporting one or more experiments, everything needed to compare the
performance of humans alone, an AI system alone, and humans working with that AI on the
same task.

The coding follows the extraction protocol of Vaccaro, Almaatouq & Malone (2024, Nature
Human Behaviour, "When combinations of humans and AI are useful") and extends it to
generative AI (large language models and other generative systems) on decision tasks.

Return EXACTLY ONE valid JSON object following the required schema.

# UNITS

Experiment → human–AI condition → performance measure.

- An EXPERIMENT is a study with its own participant sample and task. Papers often report
  several (Study 1, Experiment 2, a replication, a pilot analysed separately). A new task
  or a new sample is a new experiment; a robustness or sensitivity re-analysis of the same
  data is not.
- A CONDITION is an arm in which humans worked WITH the AI. Human-alone arms and AI-alone
  runs are reference arms, not conditions: their values go into every condition's measure
  rows. Create one condition per human–AI arm that has its own reported numbers —
  explanation vs. no explanation, different models, different delegation rules, different
  amounts of AI information. Participant subgroups (experts vs. novices, older vs. younger)
  become separate conditions only when the paper reports human-alone AND human–AI values
  for each subgroup.
- A MEASURE is one performance metric reported for that condition, with the matching
  human-alone and AI-alone values on the same scale.

# ELIGIBILITY — RECORD, DO NOT FILTER

The review keeps experiments in which (1) humans alone, (2) the AI alone and (3) humans
working with the AI were all evaluated on the same task with the same metric, and the task
is a decision task (Task_Type = Decide). Extract the experiment even when a reference arm
is missing or the task is Create: leave the missing values null and explain in Exp_Notes
so that the coder can decide eligibility. Never skip an experiment because it looks
ineligible.

# CODING RULES

## Design

- Exp_Design describes how the human-alone vs. human–AI comparison was assigned.
  Between-Subjects: different participants in each arm. Within-Subjects: the same
  participants did both. Mixed, Between-Subjects / Mixed, Within-Subjects: the study has
  both kinds of factor — pick the variant that applies to the human vs. human–AI
  contrast. Other: team-level units, simulated pairings, or anything that fits none.
- Comp_Type follows from that contrast: Independent Samples when the two values come from
  different people, Dependent Samples when from the same people. Designs in which the
  same individuals' own answers are later fused with the AI's are Dependent Samples.
- N_Exp is the total number of human participants analysed in the experiment.

## Task

- Task_Desc is one verb-first phrase naming what participants had to do.
- Task_Type = Decide when the answer is scored against a reference answer or an expert
  rubric: classification, detection, prediction, estimation, diagnosis, structured
  clinical or legal reasoning, appraisal against a checklist. Task_Type = Create when the
  output is open-ended and judged on quality: writing, summarising, code, designs,
  conversations.
- Task_Data is the modality of the material the human judges; Task_Output the form of the
  answer. AI_Data_In and AI_Data_Out describe the AI's own input and output, which may
  differ from the human's.

## Participants

- Participant_Type is the paper's own description. Participant_Type_2 is the coded group;
  Participant_Expert is true when the participants had domain expertise for the task.
- Participant_Source is the recruitment channel as stated (MTurk, Prolific, Upwork,
  Invitation, Email Lists, University, Hospital, Company, Volunteer, Not Specified).

## AI system and its presentation (per condition)

- AI_Type: Generative for large language models and other generative models; Deep for
  non-generative neural networks; Shallow for classical machine learning; Wizard of Oz
  when the "AI" output was scripted, simulated or fixed by the experimenters.
- LLM_Model (beyond the original codebook): the specific system with version and access
  route — "GPT-4 via ChatGPT Plus", "GPT-4o (gpt-4o-2024-08-06) via API in a custom app",
  "Claude-3-Opus". Null if the paper does not say.
- AI_Expl_Incl / AI_Expl_Type: whether the AI's output came with an explanation and in
  which modality. AI_Conf_Incl: whether confidence, probability or uncertainty was shown.
- Final_Decision: Human when the human produces the scored answer after seeing, consulting
  or chatting with the AI; AI when the AI's output is the final answer (the human's input
  feeds the AI, or a fixed rule fuses independent human and AI answers).
- Division_Labor: true when items were split between human and AI (delegation, the AI
  handles some cases alone, only a selected subset goes to humans).
- Interaction_Mode (beyond the original codebook): advice_first when the AI output is
  shown before or while the human decides; human_first when the human decides first and
  may revise after seeing the AI; free_use when the human uses the AI freely as a tool
  (chat, queries, drafting); fusion when independent human and AI answers are combined by
  a rule and neither sees the other; review when the human verifies or edits AI-produced
  answers; other for anything else.

## Numbers (per measure row)

- Record means, SDs and Ns exactly as printed. Never rescale: keep proportions as
  proportions and percentages as percentages, but the three means in one row must be on
  the same scale. Put the unit in Perf_Metric when it is not obvious.
- One row per metric. Accuracy, sensitivity, specificity, time per case … are separate
  rows. Sub-scores (domains of a rubric) are separate rows only when the paper reports
  all three arms for them.
- When several human–AI conditions are compared against ONE human-alone arm and ONE
  AI-alone run, repeat those reference values in every condition's rows. When a reference
  arm is missing, leave its values null and say so in Exp_Notes.
- N_Human and N_HumanAI count human PARTICIPANTS (people), exactly as in the original
  codebook — never cases, trials, items or ratings, even when the printed mean and SD are
  computed over those units (two raters scoring 3,024 items is N_Human = 1 per rater arm,
  not 3,024). Give the number of units the statistic covers in Notes instead.
- For within-subjects designs N_Human = N_HumanAI (the same people).
- Perf_Dir is Down for errors, error rates and time. Do not negate values.
- SD: the reported SD. If only an SE or a CI is given you may compute SD (SE × √n; CI
  half-width × √n / 1.96) — then set Est_ES = true and show the computation in Notes. A
  deterministic AI reported as a single value gets Sd_Perf_AI = 0, as in the original
  codebook.
- N_AI is the number of independent AI RUNS: 1 for a single deterministic pass over the
  material (however many items it scored), 5 when "the model was prompted five times per
  case" — never the number of cases or items. Over repeated runs Avg_Perf_AI is the mean
  and Sd_Perf_AI the SD across runs; give runs × cases in Notes.
- Several AI CONFIGURATIONS evaluated alone (different prompts, models or settings) against
  one human–AI arm are separate results, not repeated runs: never average across them. Give
  each configuration its own measure row — same human-alone and human–AI values, its own
  AI-alone value, the configuration named at the end of Perf_Metric ("… — AI alone: basic
  prompt") — and say in Notes which configuration is closest to what participants used.
- Values that appear only in a figure: estimate only when the figure prints the numbers;
  otherwise leave null and describe what the figure shows in Notes. Any estimated or
  computed value makes Est_ES true.
- Prefer the primary analysis population. When the abstract and a table disagree, use
  the table and note the discrepancy.

# CONFIDENCE CALIBRATION

- "high": the values are printed in a table or in the text, the arm and metric they belong
  to are unambiguous, and nothing was derived.
- "medium": the values were extractable but at least one of: the arms had to be mapped to
  conditions with some judgement, a reference value is shared across conditions or
  reported elsewhere in the paper, a code (design, task type, interaction mode) needed
  interpretation, or one value was computed from an SE or CI.
- "low": substantial ambiguity remained — values read from figures, unclear which arm a
  number belongs to, inconsistent numbers across the paper, or a missing reference arm.

Before returning, check that every condition has at least one measure row, every measure
row has Perf_Dir, and the human-alone and AI-alone values in a row refer to the same
metric and scale as the human–AI value.
