# What the paper estimates — pass a

You read the full text of one paper (a study of generative AI at work; each page starts with
a line `=== PAGE n ===`, and n is the page you cite) together with the headers of its tables
and figures, and you list what the paper estimates. You report no numbers: another pass maps
the numbers to your list, so the list has to be complete.

Units:
- A **task** is one distinct piece of work done by one distinct sample: the same work given
  to two samples is two tasks; two kinds of work given to one sample are two tasks; one piece
  of work scored on several measures is one task. Measurement occasions are never tasks: a
  pre-treatment round and a post-treatment round of the same kind of work by the same sample,
  a later task of the same kind, a follow-up survey two weeks or two months on, are horizons of
  the one task whose sample they measure; a paper with one kind of work and one sample has one
  task. A replication of the same work with new participants is a second task; parts of one
  assignment scored separately are one task. Ids T1, T2 … in the paper's order; `sample` says
  who did it.
- An **arm** is a condition of a task: the reference arm first (the arm the others are
  compared with; `is_reference` true, `control_type` says what it is), then one treatment arm
  per AI condition, in the paper's order. Ids C for the reference arm, then T1, T2 …; an arm
  that serves several tasks lists them all in `task_ids`. Say what the arm got (`treatment`),
  who was in it (`sample`) and how many (`n`, as printed, with `n_quote` the sentence or
  table row that prints that number and `n_page`; all three null when not stated). A task may
  have two reference arms (a no-AI control beside a placebo tool, say): ids C and C2, each
  `is_reference` true with its own `control_type`, and every estimand names the one it is
  compared with. In a one-group before-and-after design the before period is the reference
  arm (`control_type` no_ai_prior). When the AI is taken away (a ban, an outage, the end of a
  trial or of access), the period or group with the AI is the treatment arm whatever the
  paper calls it, and the reference arm's `control_type` is no_ai_after. Subgroups are not
  arms: juniors and seniors in one condition are one arm, and their split is a subgroup of
  the estimand.
- An **outcome** is a measure the paper reports with a contrast between arms and takes while
  the treatment arm has the AI in use: work performance, time, quality, behaviour, use of the
  tool, self-reports, labour-market outcomes — every one, with its unit, which side is better
  (`direction`), how it was measured and its classes. A measure taken afterwards without the
  AI (a post-test, a retention test, later unaided work) is not an outcome here: mention it in
  the task's `description` and list no estimand for it. For a performance outcome give `measure_kind`, what the measure counts; its
  `finding_type` follows from the kind (a duration, a count per period, a throughput or a
  completion share is productivity_time; a rating, a pass rate or an error count is
  productivity_quality; a dispersion statistic is performance_dispersion).
- An **estimand** is one task, one arm compared with one reference arm, one outcome, one
  subgroup ("full sample", or the split as the paper names it: "below-median initial grade")
  and one horizon (null when there is one point of measurement, else the occasion as the
  paper names it: "task 1 (pre-treatment)", "task 2 (post-treatment)", "2-week follow-up"). List every combination the paper reports an estimate for —
  every subgroup split and every horizon included — and nothing the paper does not estimate.
  One comparison is one estimand however many exhibits report it: the same arm, reference
  arm, outcome, subgroup and horizon reported in a table and again in a figure or in the text
  is listed once, with every exhibit in `exhibits`. `vs_arm_id` is never the arm itself. An
  interaction (the AI effect by gender, say) is its own estimand whose `subgroup` names the
  two groups compared.
  `statement`: one sentence of the form "In <task>, <arm> versus <reference arm> on
  <outcome>[, for <subgroup>][, at <horizon>]". `exhibits`: the ids of the tables and figures
  that hold its estimates (from the headers given; empty when the estimate is only in the
  text).

Every unit carries `quote`: the verbatim sentence (10 to 40 words, from one page, no
paraphrase, no ellipsis) that establishes it, and its `page`. What the sentence must say
depends on the unit: for a task, what the participants did (the work, not who they were or
how many); for an arm, what this condition got or did without (the sentence that names its
treatment, never a sentence shared with another arm, never the sentence that introduces the
analysis); for an outcome, how it was measured; for an estimand, that this comparison is
made or reported. Two units never carry the same sentence unless the paper has only one
sentence about them.

Codes, exactly as written. Where a code list allows `other`, use it only when no code fits,
and put the paper's own term in the `_proposed` field beside it (`contrast_type_proposed` on
an estimand, `finding_type_proposed` and `measure_kind_proposed` on an outcome); `_proposed`
is null otherwise, and a code is never invented:
- `control_type` (the reference arm; null on the others): what the reference arm is, on the reference arm only (null on the treatment arms). One of: no_ai_concurrent = a concurrent group or condition doing the task without the AI, drawn from the same pool (randomized or assigned; design_type says which); no_ai_prior = the same people or units before access: a pre-period, an own baseline, the not-yet-treated state of a rollout; no_ai_after = the same people or units after the AI was taken away (a ban, an outage, the end of access): the time or group with the AI is the treatment arm, the one without it the reference arm, whatever the paper calls the treatment; placebo_tool = the comparison condition got a non-AI aid instead: a search engine, documentation, templates, a checklist, non-generative software; other_ai = a different AI system or configuration is the comparison (an older model, no retrieval, a different prompt or interface, non-agentic); every arm uses AI; expert_benchmark = unassisted experts or incumbents whose performance is the reference for AI-assisted others: a different population, not an assigned condition; ai_alone = the AI's own output with no human; recorded only when the paper's contrast is against it, with n_arm null; external_norm = a published norm, a historical rate or a market benchmark; no participants; none = no comparison: a single arm, descriptive; other = none of the categories fits: give the paper's own term in `proposed`. When none fits, choose other and give the paper's own term
- `contrast_type`: what the effect compares; null on a reference-arm row. One of: ai_vs_no_ai = an AI arm against a no-AI reference arm (concurrent or placebo tool); pre_vs_post = the same units after getting the AI against before; ai_vs_ai = two AI arms against each other (two tools, two models); ai_variant = an AI arm against the same AI with a feature, prompt or interface changed; dose = more versus less use or access to the same AI; vs_benchmark = an arm against a benchmark that is not a condition of the study: unassisted experts, a published norm; human_ai_vs_ai = people using the AI against the AI's own output with no human; interaction = the difference between two subgroups' AI effects; association = users against non-users without assignment; other = none of the categories fits: give the paper's own term in `proposed`. When none fits, choose other and give the paper's own term
- `direction`: up if higher values are better (quality, output); down if lower is better (time, errors); neutral for a quantity without a better side. One of: up; down; neutral
- `finding_type`: the finer kind of outcome this row measures. One of: productivity_time = time to complete a piece of work, or output per period: durations, counts per period, throughput, completion share; productivity_quality = the quality of the work: ratings on a scale, pass or acceptance rates, error and revision counts; employment_hiring = employment status, hiring, retention or attrition as the outcome; wages_earnings = pay, earnings or income as the outcome; labor_demand = demand for workers or for a kind of work: vacancies, postings, hours demanded; skill_split = Lower- vs higher-skill effect; task_shift = Change in share/volume of a kind of work; output_diversity = Collective output diversity across people or items; performance_dispersion = the spread of performance across people: a slope by baseline skill, a variance, a correlation across tasks; adoption_usage = adoption or usage of AI as the outcome; secondary_outcome = well-being, attitudes, beliefs, trust; other = A result fitting none of the rest. When none fits, choose other and give the paper's own term
- `measure_kind` (a performance outcome only; null for the other classes): for a performance outcome, what the measure counts: one level below the finding type, which follows from it (durations, counts per period, throughput and completion shares are time taken; ratings, pass rates and error counts are quality; a dispersion statistic is dispersion); null for an outcome of another class. One of: duration = time to complete a defined piece of work: minutes per task, handle time, hours to completion, time to reply; output_count = pieces of work completed in a period: pull requests, commits, documents, emails replied to; throughput = output per unit of time: resolutions per hour, chats per hour; completion_share = the share of a task completed, or a quality-over-time index; rating = quality judged by graders, experts or a model on a scale: a 1 to 7 quality score, a 4.0 grade, creativity, coherence; pass_rate = the share of work that meets a standard: correctness, build success, approval, resolution; error_count = defects or rework counted on the output: hallucinations, review comments, merge conflicts; dispersion = a statistic of the spread of performance across people: a slope by baseline skill, a variance, a correlation of grades across tasks; other = none of the categories fits: give the paper's own term in `proposed`. When none fits, choose other and give the paper's own term
- `outcome_class`: what kind of outcome the measure is, by what was measured and not by its name; every outcome measured while the treatment arm has the AI in use, with a with-versus-without-AI contrast, is a row; an outcome measured afterwards without the AI (a post-test, a retention test, later unaided work) is not a row: name it in task_notes. One of: performance = the work produced or the task performed, measured by graders, instruments, timers, counts or an answer key: time, output volume, quality, accuracy, errors, completion, creativity or novelty rated by others; behaviour = what people did, observed: use or adoption of the AI, reliance on or acceptance of its suggestions, take-up of the assigned condition, time allocation across tasks, effort, help-seeking, collaboration; subjective = what people reported about themselves or the work: satisfaction, enjoyment, well-being, stress, perceived agency or autonomy, trust, confidence, self-assessed performance, beliefs, attitudes, intentions; labour_market = employment status, retention or attrition, earnings, hours, promotion, hiring
- `measured_on`: whom or what the measure is taken on. One of: worker = the person doing the task: their time, throughput, errors, use of the tool, self-reports; output = the work product, graded by others or against a key; recipient = the person the work is for: a patient's comprehension of a consultation, a customer's satisfaction with the service

Before answering, go once more over every table and figure caption and every heading of the
results section: each outcome they name that is measured while the AI is in use — use of the
tool, satisfaction, trust, compliance, attrition when the paper reports it as an outcome —
needs its outcome and its estimands in the list.

The abstract is withheld; the list rests on what was done and found.
