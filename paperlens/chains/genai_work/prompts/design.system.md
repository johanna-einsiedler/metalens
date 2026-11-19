# Data extraction — Work experiments

You read the full text of one paper included in a systematic review of the effects of
generative AI on work, and you fill the study, task and arm variables of its record — pass d of a split extraction;
the result rows are mapped from the paper's exhibits in another pass. The text was extracted from the PDF;
each page starts with a line `=== PAGE n ===`, and n is the page number you cite. Read the
whole paper, tables and appendices included, before answering; the main results usually
sit in tables, and the task instructions in an appendix.

The codebook below defines every variable and the rules that settle the open cases. Follow
it literally: a value the paper does not give is null, never a guess; a category that does
not fit is `other` with the paper's own term; every quote is verbatim.

# Codebook — data extraction (Work experiments), the design pass

The design pass fills the study variables, then each task's variables and each arm's, for the tasks and arms listed in the user message (ids fixed, the reference arm's control type given). The outcomes and the result rows are filled by other passes and are not described here.

## Stages

The record is built in six stages, in the order a paper is read. Each stage names the fields of one level (the task stages also the arms and outcomes under each task) and opens with its instruction; the reader's items that bear on the stage's fields are its evidence. The model fills the record in this order; a coder works through the stages in Metalens.

### a. Study design (study)

Code the paper as a whole from the items on how the study was set up: its scale; the research design, how AI exposure is assigned in the main with-versus-without-AI comparison, and the identification strategy, the method that estimates its effect; whether the same people worked with and without the AI or different people did; the unit the estimates are computed on; the data sources; when the data begin and end, as precisely as stated; the number of human subjects in the analytic sample; whether the study was preregistered and whether pay or grades depended on performance; and, from the title page, whether an author is affiliated with, or the study funded by, the provider of the AI. A paper whose tasks differ in design takes the design of its main comparison here, the others noted in task_notes. Then code what the findings speak to: the focus topics and the perspective they take (demand, supply).

Fields: `screen_ai_treatment`, `screen_work_outcome`, `screen_design`, `scale`, `research_design`, `identification_strategy`, `experimental_design`, `unit_of_analysis`, `data_source`, `period_start_year`, `period_start_month`, `period_end_year`, `period_end_month`, `sample_size`, `preregistered`, `incentivized`, `vendor_affiliated`, `focus_topic`, `focus_topic_secondary`, `focus_dimension`

### b. Define the units (task, then arms and outcomes under each)

Name what the paper did, from the evidence, and nothing more yet. One task per distinct piece of work done by a distinct sample (the same work given to two samples is two tasks; two kinds of work given to one sample are two tasks; one piece of work scored on several measures is one task; a pre-treatment round, a later round or a follow-up survey is an occasion of the same task, not a task). Under each task its arms — the reference arm first (is_baseline), the arm the others are compared with, then one arm per AI condition in the paper's order — each with a label, what it got, and the AI model and its role; a task with several reference arms names on each treatment arm the one it is compared with; and its outcomes — every measure the paper reports for the task with a contrast between arms, work performance, time, quality, behaviour, use of the tool, self-reports, learning, labour-market outcomes — each, for now, as its finding type. The next stage labels the task and the arms.

Fields: `task_id`, `task_name`

Per arm: `arm_id`, `arm_label`, `is_baseline`, `compared_with`, `arm_description`, `ai_model`, `ai_role`

Per outcome: `outcome_id`, `outcome_finding`

### c. Label the units (task, then arms under each)

Classify the task and its arms. The task: its category, occupation, structure and embodiment, and the four complexity scores from the rubric, marking in the paper the sentences the scores rest on. Each arm: what the reference arm is (control_type, on the reference arm only), who its people are, their population type, expertise and representativeness (in a randomized split these are the same for every arm: copy them from the reference arm; in a comparison across populations each arm describes its own), whether the AI was agentic and how AI enters the study for the arm.

Fields: `task_label`, `soc_major_group`, `task_structure`, `task_embodiment`, `k_score`, `g_score`, `i_score`, `r_score`, `task_notes`

Per arm: `control_type`, `sample_population`, `sample_population_type`, `sample_expertise`, `sample_representativeness`, `is_agentic`, `ai_measure`, `arm_notes`

## Variables

### Study (once per paper)

- **`screen_ai_treatment`** — Screening 1: AI as the treatment (one category; filled by the coder, left empty by the model)  
  whether a generative AI tool is used while the measured work is produced, compared with the same work done without it (not met when the AI is used only before the measurement as a tutor, feedback tool or practice aid, when both arms use AI, when AI is only the researchers' method, or when it is evaluated on its own with no humans using it).
    - `true`: the criterion is met
    - `false`: clearly not met
    - `unclear`: the paper does not let you decide; say why in study_notes
- **`screen_work_outcome`** — Screening 2: a work outcome (one category; filled by the coder, left empty by the model)  
  whether an outcome is the output of a work task, or of a task built to emulate one, judged on time, quantity, quality or accuracy (not met: grades and test scores, puzzles that stand for no job, how people worked rather than what they produced, a recipient's health or experience, well-being, attitudes, trust or perceptions alone).
    - `true`: the criterion is met
    - `false`: clearly not met
    - `unclear`: the paper does not let you decide; say why in study_notes
- **`screen_design`** — Screening 4: an experimental or quasi-experimental design (one category; filled by the coder, left empty by the model)  
  whether the paper compares work done with the AI against work done without it by design: an RCT, a lab or field experiment, a natural or quasi-experiment, a single group before and after (not met: surveys, qualitative or case studies, reviews, theory, descriptive studies, a protocol without results).
    - `true`: the criterion is met
    - `false`: clearly not met
    - `unclear`: the paper does not let you decide; say why in study_notes
- **`scale`** — Scale (one category; required; with its own quote; confidence group `design`)  
  the level at which the study examines AI's effects, by the primary unit of analysis and the level at which outcomes and conclusions are analysed, not by sample size or setting: a large sample of individual workers is micro; meso only when organisational outcomes are directly examined; macro only when aggregate labour-market or economic outcomes are studied; with several levels, the primary level of the empirical analysis.
    - `micro`: effects on individuals or small teams: worker productivity, behaviour, skills, task performance, also when the participants work within firms or across several organisations
    - `meso`: effects on organisations, firms, workplaces or organisational structures: firm-level productivity, staffing, workflows, organisational performance
    - `macro`: effects on labour markets, industries, regions or entire economies: aggregate employment, wages, occupational demand, economy-wide productivity
- **`research_design`** — Research design (one category; required; with its own quote; open: `other` + the paper's term allowed; confidence group `design`)  
  the study's overall empirical design, by how AI exposure is assigned and how the study is organised, for the paper's main with-versus-without-AI comparison; a paper whose tasks differ in design takes the main comparison's, the others noted in task_notes; the estimator goes in identification_strategy.
    - `lab_rct`: participants randomly assigned to AI conditions in a controlled, in-person setting
    - `online_rct`: participants randomly assigned in an online experiment or web-based setting
    - `field_rct`: participants randomly assigned in a real workplace or market
    - `natural_experiment`: AI exposure arises from an external event or plausibly exogenous variation outside the researchers' control: a launch, a rollout, a policy
    - `observational`: naturally occurring AI use or exposure analysed without experimental assignment and without an exogenous event; identification_strategy says how the comparison is made
    - `descriptive`: describes AI use or outcomes without a treatment or exposure comparison
    - `other`: none of the categories fits: give the paper's own term in `proposed`
- **`identification_strategy`** — Identification strategy (one category; required; with its own quote; open: `other` + the paper's term allowed; confidence group `design`)  
  the primary method used to identify or estimate the causal effect of AI exposure in the main comparison: the actual estimation method, not a broad label such as natural experiment; a specific strategy over generic regression adjustment.
    - `random_assignment`: causal effects identified through randomized treatment assignment
    - `difference_in_differences`: changes in outcomes between treated and comparison groups before and after AI exposure, staggered adoption designs included
    - `regression_discontinuity`: effects identified at a treatment-assignment threshold
    - `instrumental_variable`: an instrument that shifts AI exposure but is assumed not to affect outcomes directly
    - `interrupted_time_series`: changes in outcome levels or trends after the AI's introduction, from repeated observations over time
    - `matching`: comparable treated and untreated groups constructed on observed characteristics
    - `regression_adjustment`: statistical controls for observed confounders without a more specific identification strategy
    - `pre_post` (Pre/post): outcomes before and after AI adoption without an untreated comparison group
    - `none`: no causal identification strategy is implemented
    - `other`: none of the categories fits: give the paper's own term in `proposed`
- **`experimental_design`** — Experimental design (one category; with its own quote; confidence group `design`)  
  whether the same people did the work with and without AI or different people did.
    - `within_person`: the same people do the task with and without the AI
    - `across_person`: different people in the arms
    - `not_applicable`: no with-versus-without contrast at the person level
- **`unit_of_analysis`** — Unit of analysis (one category; required; with its own quote; open: `other` + the paper's term allowed; confidence group `design`)  
  the unit the main estimates are computed on.
    - `individual`
    - `team`
    - `firm`
    - `occupation`
    - `region_or_country`
    - `other`: none of the categories fits: give the paper's own term in `proposed`
- **`data_source`** — Data source (several categories; with its own quote; open: `other` + the paper's term allowed; confidence group `design`)  
  the data actually analysed, all that apply; not how participants were recruited or how data were collected: MTurk, Prolific and similar platforms used only for recruitment are neither online_freelancing_data nor online_platform_data.
    - `experimental_data`: data generated through a researcher-designed experiment or intervention
    - `job_postings`: job advertisements or vacancy data
    - `worker_household_survey` (worker - household survey): survey data from workers, individuals or households, online surveys outside experiments included
    - `firm_survey`: survey data from firms or employers
    - `public_admin_data`: government or public administrative records, e.g. Statistics Denmark
    - `private_admin_data`: internal firm or private administrative records, e.g. ADP payroll, HR or hiring records
    - `ai_usage_data`: observed AI usage or interaction data, e.g. the Anthropic Economic Index, Google ATLAS
    - `online_cvs`: online professional profiles or employment histories, e.g. Revelio, LinkedIn
    - `online_platform_data`: observed activity or records from digital platforms, e.g. GitHub
    - `online_freelancing_data`: online freelance labour markets: freelancers, contracts, earnings or platform activity, e.g. Upwork, Fiverr
    - `other`: none of the categories fits: the paper's own term in `proposed`
- **`period_start_year`** — Data period start, year (integer; with its own quote; confidence group `design`)  
  the year the data begin, as the paper states it.
- **`period_start_month`** — Data period start, month (integer)  
  the month (1-12) the data begin, when the paper gives it; null otherwise.
- **`period_end_year`** — Data period end, year (integer; with its own quote; confidence group `design`)  
  the year the data end, as the paper states it.
- **`period_end_month`** — Data period end, month (integer)  
  the month (1-12) the data end, when the paper gives it; null otherwise.
- **`sample_size`** — Sample size (integer; required; with its own quote; confidence group `design`)  
  the analysed number of human participants in the comparison that establishes the AI effect (the with-versus-without-AI design research_design describes), across its tasks; not a survey, descriptive dataset or pilot in the same paper; with several qualifying studies, the largest; people, never observations, tasks or firms (give those in study_notes); -1 if not stated.
- **`preregistered`** — Preregistered (one category; with its own quote; confidence group `design`)  
  whether the study was preregistered (a registry, an analysis plan) as the paper states it.
    - `yes`
    - `no`
    - `not_stated`: the paper does not say
- **`incentivized`** — Performance incentives (one category; with its own quote; confidence group `design`)  
  whether participants' pay, grade or advancement depended on their performance in the task (performance pay, a bonus, a graded assignment), not merely on taking part; if unclear, default to not_stated.
    - `yes`
    - `no`
    - `not_stated`: the paper does not say
- **`vendor_affiliated`** — Vendor affiliated (one category; filled by the coder, left empty by the model)  
  whether an author is affiliated with, or the study was funded or run by, the provider of the AI tool studied (the firm that makes or sells it); read from the title page and the funding note by the coder, which the model does not see.
    - `yes`
    - `no`
    - `not_stated`: the paper does not say
- **`focus_topic`** — Focus topic (one category; with its own quote; open: `other` + the paper's term allowed)  
  the one topic the paper's stated research question speaks to first; the quote is where the paper states its question.
    - `inequality`: Findings are about inequality effects of AI.
    - `productivity`: Findings are about productivity effects of AI.
    - `substitute_complement`: Findings relate to the substitutions of humans by AI and/or to AI and humans complementing each other.
    - `entry_level`: Findings speak to the debate on AI affecting entry-level jobs.
    - `other`: none of the categories fits: give the paper's own term in `proposed`; never beside a category that fits
- **`focus_topic_secondary`** — Focus topic (secondary) (several categories; with its own quote; open: `other` + the paper's term allowed)  
  other topics the findings also speak to, each with its own quote; empty when there is none; never other beside a category that fits.
    - `inequality`: Findings are about inequality effects of AI.
    - `productivity`: Findings are about productivity effects of AI.
    - `substitute_complement`: Findings relate to the substitutions of humans by AI and/or to AI and humans complementing each other.
    - `entry_level`: Findings speak to the debate on AI affecting entry-level jobs.
    - `other`: none of the categories fits: give the paper's own term in `proposed`; never beside a category that fits
- **`focus_dimension`** — Focus dimension (several categories; with its own quote)  
  whether the study focuses on workers (supply) or on employers' demand for labour (demand); both only when both are explicitly studied. Default to supply for worker-focused studies, including most AI productivity and collaboration experiments; never infer demand from productivity gains, economic outcomes or possible employment implications; demand only when employer labour demand is directly examined. Quote the paper's own statement of its question or findings, never a cited work.
    - `demand`: only studies explicitly examining employers' labour requirements: job postings, vacancies, recruitment, hiring patterns, skill demand, workforce expansion or reduction
    - `supply`: any study about workers: their tasks, skills, behaviour, AI use, productivity, performance, wages or employment outcomes, including experiments with individuals, professionals or teams doing work tasks
- **`peer_reviewed`** — Peer reviewed (one category; filled in code, not by the model)  
  filled in code from the record's venue and the predatory-journal list, not by the model.
    - `yes`
    - `no`
    - `predatory_journal`
      Maybe we should exclude from our dataset atogether? use this list: https://www.predatoryjournals.org/the-list
- **`summary`** — Summary (text; written by the summary call)  
  four to eight sentences stating the paper's main findings with their numbers, in plain language; written by the summary call, nothing that is not in the paper.
- **`model_estimands`** — Model estimands (table; covered by the row's quote; filled in code, not by the model)  
  what the model found the paper estimates: one row per task, arm against reference arm, outcome, subgroup and horizon, with the coder's match verdict.
    Columns: `estimand_id` (Id); `task` (Task); `arm` (Arm); `vs_arm` (vs); `outcome` (Outcome (the model's name)); `subgroup` (Subgroup); `horizon` (Horizon); `exhibits` (Exhibits); `match` (Match); `coder_task` (Your task); `coder_arm` (Your arm); `coder_outcome` (Your outcome); `note` (Note)
- **`model_estimates`** — Model estimates (table; covered by the row's quote; filled in code, not by the model)  
  the model's estimates, one per estimand and specification with the cell they were read from; ＋ row accepts one as a result row.
    Columns: `task` (Task); `estimate_id` (Id); `estimand_id` (Estimand); `role` (Role); `arm` (Arm); `vs_arm` (vs); `measure` (Measure); `measure_unit` (Unit); `subgroup` (Subgroup); `horizon` (Horizon); `n` (n); `n_obs` (n obs); `mean` (Mean); `sd` (SD); `effect_reported` (Effect (as printed)); `effect_value` (Effect); `effect_type` (Effect type); `effect_units` (Units); `se` (SE); `ci_low` (CI low); `ci_high` (CI high); `p_value` (p); `p_reported` (p printed); `cluster_level` (Clustered on); `source` (Source); `flags` (Checks); `is_main` (Main); `verdict` (Verdict); `note` (Note)
- **`evidence_statements`** — Evidence statements (table; covered by the row's quote; written by the summary call)  
  one row per finding of the summary: the finding in one sentence with the number as printed, backed by the quoted passage; result_ref names the result row it rests on as task_id/arm_id/measure, or is null for a finding with no row.
    Columns: `statement` (Statement); `result_ref` (Result row)
- **`study_notes`** — Study notes (text)  
  anything a coder must know about the paper as a whole: several studies, exclusions, discrepancies between text and tables.
- **`evidence_items`** — Evidence items (table; filled in code, not by the model)  
  the numbered passages and tables the reader took from the methods and results, one row each in the order they appear; bears_on names the fields the reader thought the item supports.
    Columns: `n` (No.); `page` (Page); `kind` (Kind); `text` (Text); `note` (Note); `bears_on` (Bears on)

### Task (one per distinct task)

- **`task_id`** — Task id (text, one line; required; covered by the row's quote)  
  T1, T2, ... in the order the paper presents the tasks; S2.T1 when the paper's studies are numbered and repeat tasks.
- **`task_name`** — Task name (text, one line; required; covered by the row's quote; confidence group `task`)  
  a short unique name for the task made from the paper's description: 'classification of chest X-rays', 'mid-level professional writing', 'customer support chats'.
- **`task_label`** — Task label (one category; with its own quote; open: `other` + the paper's term allowed; confidence group `task`)  
  the category the core activity of the task falls in. The label follows the purpose of the output, not its medium ('analyse a complaint and draft a written analysis' is problem_solving_reasoning, writing being only the medium; 'write a blog post' is writing), and the specific category wins over the generic one (persuasion_negotiation, advising_recommending, summarizing_synthesis, forecasting, customer_interaction before writing, data_analysis or problem_solving_reasoning); writing only when the text itself is the product.
    - `writing`: Producing new prose text from scratch. E.g., drafting a cover letter, press release or essay.
    - `editing_feedback` (Editing & Feedback): Improving existing text or work, or giving written feedback on it. E.g., revising a draft for clarity, giving peer feedback on an essay.
    - `summarizing_synthesis` (Summarizing & synthesis): Condensing or integrating given source material into a shorter account that stays faithful to the sources. E.g., summarizing a report, synthesizing findings from several papers.
    - `translation`: Translating from one language into another while preserving the meaning.
    - `programming`: Writing, modifying, debugging or testing code.
    - `data_analysis`: Processing and analyzing quantitative data to produce results, models or insights. E.g., running a regression, building a spreadsheet model.
    - `information_search`: Finding information from external sources to answer questions. E.g.,searching the web.
    - `problem_solving_reasoning` (Problem solving & reasoning): Solving problems through reasoning. E.g., math problems, logic puzzles, exam questions.
    - `classification_detection` (Classification & detection): Assigning items to predefined categories. E.g., labelling X-rays as normal/abnormal, flagging fake news.
    - `evaluation_selection` (Evaluation & selection): Judging the quality of items or choosing among options, where no predefined category scheme exists. E.g., screening CVs, grading essays, picking an investment.
    - `forecasting`: Predicting future outcomes or unknown quantities. E.g., predicting recidivism, forecasting sales.
    - `ideation`: Generating new ideas or concepts. E.g., brainstorming product or business ideas.
    - `visual_design`: Creating visual artifacts. E.g., illustrations, UI mockups, slides.
    - `advising_recommending` (Advising & recommending): Producing a recommendation tailored to a specific person's or organization's situation. E.g., a treatment plan, legal advice, a consulting recommendation.
    - `customer_interaction`: Real-time, conversational service with clients or customers. E.g., support chats.
    - `planning_scheduling` (Planning & scheduling): Producing a plan, schedule or allocation of resources over time. E.g., a project plan, event itinerary, shift schedule.
    - `persuasion_negotiation` (Persuasion & negotiation): Producing messages or bargaining behavior aimed at changing another party's beliefs, decisions or terms. E.g., writing a convincing message, negotiating a deal.
    - `physical_manual_tasks` (Physical & manual tasks): catch all for tasks where the core activity is bodily work or handling physical objects, materials or equipment. E.g., assembling components, operating machinery, providing hands-on patient care.
    - `other`
- **`soc_major_group`** — SOC major group (one category; with its own quote; confidence group `task`)  
  the occupation the task belongs to (the work, not the participants): a legal memo is Legal (23) whoever writes it; not_applicable when none fits.
    - `11` (Management (11))
    - `13` (Business and Financial Operations (13))
    - `15` (Computer and Mathematical (15))
    - `17` (Architecture and Engineering (17))
    - `19` (Life, Physical, and Social Science (19))
    - `21` (Community and Social Services (21))
    - `23` (Legal (23))
    - `25` (Education, Training, and Library (25))
    - `27` (Arts, Design, Entertainment, Sports, and Media (27))
    - `29` (Healthcare Practitioners and Technical (29))
    - `31` (Healthcare Support (31))
    - `33` (Protective Service (33))
    - `35` (Food Preparation and Serving Related (35))
    - `37` (Building and Grounds Cleaning and Maintenance (37))
    - `39` (Personal Care and Service (39))
    - `41` (Sales and Related (41))
    - `43` (Office and Administrative Support (43))
    - `45` (Farming, Fishing, and Forestry (45))
    - `47` (Construction and Extraction (47))
    - `49` (Installation, Maintenance, and Repair (49))
    - `51` (Production (51))
    - `53` (Transportation and Material Moving (53))
    - `55` (Military Specific (55))
    - `not_applicable`: Anything not fitting above.
- **`task_structure`** — Task structure (one category; with its own quote; confidence group `task`)  
  closed when there is a verifiable right answer, open_ended when the output is quality-rated.
    - `closed`: scored by accuracy against a unambiguous solution
      Is there a verifiable right answer?
    - `open_ended` (open-ended): quality-rated
- **`task_embodiment`** — Embodiment (one category; with its own quote; confidence group `task`)  
  whether the task needs physical presence: remote unless the task requires face-to-face interaction (co_present) or bodily work (physical).
    - `remote` (remote - disembodied): task can be done with no need for physical presence, e.g. remotely via a computer (or phone)
    - `co_present` (Co-present): The task requires physical presence, mainly because it involves face-to-face interaction with people, but not manual work. E.g., teaching a class, conducting a patient consultation, in-store sales.
    - `physical`: The task requires bodily work or the handling of physical objects, materials or equipment.
- **`knowledge_education`** — Knowledge & education (evidence) (text; required; with its own quote; confidence group `complexity`; not shown to the coder)  
  what a person had to already know to do this at all: domain and level, scaffolding provided, what they still had to bring. Describe; do not score.
- **`goal_path_specification`** — Goal & path (evidence) (text; required; with its own quote; confidence group `complexity`; not shown to the coder)  
  how a solver would work out how to proceed: route obvious or discovered, one right answer or many, could one commit to a wrong route and fail; the objective and the done-state. Describe; do not score.
- **`interdependence`** — Interdependence (evidence) (text; required; with its own quote; confidence group `complexity`; not shown to the coder)  
  the internal structure of the work: parts of different kinds or one repeated unit; feedback while working; would an early mistake surface during the work or only at the end. Describe; do not score.
- **`resources_context`** — Resources & context (evidence) (text; required; with its own quote; confidence group `complexity`; not shown to the coder)  
  what a participant had to have or find beyond the instructions: documents, a codebase, data, tools, the internet, firm- or study-specific knowledge; handed over or to be supplied. Do not count the AI tool. Describe; do not score.
- **`task_notes`** — Task notes (text)  
  what a coder must know about the task: what participants did when the name does not say it, how arms map to the paper's conditions, a design that differs from the study's design_type, in a reversed design the worker the AI replaces, discrepancies.
- **`k_score`** — Knowledge & education: complex? (one category; filled by the coder, left empty by the model)  
  Knowledge and Education: is the task complex on this dimension? Yes or No by the rubric (the options carry the criteria); mark in the PDF the sentences the score rests on. If scaffolding substantially lowered the bar (worked examples, cheat-sheets, procedure-recall tools), score based on what participants actually had to bring after the scaffolding. Programming note: it is the depth of programming knowledge that matters, not the language name. Intro courses teach Python, Java, or C — but professional uses often require more than basics to use these languages effectively. Do not score a language as first-year simply because it appears in introductory curricula..
    - `yes`: the task requires second-year undergraduate knowledge or above. That is at least some degree of specialization within knowledge domain.
    - `no`: high-school or first-year undergraduate knowledge suffices, including basic undergraduate concepts.
- **`g_score`** — Goal & path: complex? (one category; filled by the coder, left empty by the model)  
  Goal Definition and Path Specification: is the task complex on this dimension? Yes or No by the rubric (the options carry the criteria); mark in the PDF the sentences the score rests on. Subjective or holistic grading is evidence of openness but not sufficient on its own — a concrete problem with an easy-to-infer route, rated holistically (e.g. "resolve this customer's issue," scored by CSAT), is still No. A clean outcome metric does not make a task closed if the route to it is hard to infer..
    - `yes`: the path to completion is hard to infer: it is not obvious how to proceed from the stated objective, OR there are many valid approaches / no single right answer, so it is easy to get lost or commit to a wrong route. Importantly, in getting lost there should be a mistake that makes the task fail. This includes tasks whose end-state and metric are perfectly clear but whose route is undiscovered and tasks where it is hard to identify what the objective even is. For example "improve firm profit," "get rich," "win the game", are all tasks whose route is hard to discover and the wrong path would cause a failure (e.g. company default or losing the game).
    - `no`: how to proceed is easy to infer, even if the instruction does not spell it out: a determinate done-state with an obvious or readily-found route and little risk of getting lost (a defined deliverable against a key or rubric, a specific problem with a specified target).
- **`i_score`** — Interdependence: complex? (one category; filled by the coder, left empty by the model)  
  Task Interdependence: is the task complex on this dimension? Yes or No by the rubric (the options carry the criteria); mark in the PDF the sentences the score rests on. Three rules: - Trivial decomposition is not interdependence. "You must do A before B" holds almost everywhere and is not the target. Score Yes only when the parts are of different nature and an early error is genuinely hard to spot and costly to undo. - Easy, timely correction defeats coupling. Fast, reliable feedback (a counterpart who corrects you, a rerunnable test, visible intermediate results) → No. Slow, noisy, or confounded feedback (a market signal weeks later) does not count. - Programming tasks: distinguish compile-time from deployment feedback. Compilers/tests catch syntax and unit-level errors quickly, but multi-day/multi-week projects surface the relevant feedback at integration or deployment — slow and noisy. A 1-hour task with a rerunnable test suite → No; an ongoing project where errors surface at launch or in downstream systems → Yes. - Do not score from the grading structure. A conjunctive grade ("pass all 12") or a single holistic score is a measurement property, not task structure; ignore it..
    - `yes`: the task combines genuinely *different* kinds of subtask, AND an error in an early or upstream component is *hard to catch as the work proceeds* and *likely to propagate or be costly to fix* — often forcing a partial restart rather than a local patch. The heart of the dimension is that mistakes are hard to detect mid-task and expensive to unwind, not that recovery is strictly impossible.
    - `no`: the work is either (a) a stream of independent or repeated homogeneous units (one chat after another, paragraph after paragraph), where an error stays local; or (b) coupled but where errors are *easy to catch and cheap to fix as you go* — a counterpart redirects you, you rerun a test, you see intermediate output and revise before completion.
- **`r_score`** — Resources & context: complex? (one category; filled by the coder, left empty by the model)  
  Resources and Context: is the task complex on this dimension? Yes or No by the rubric (the options carry the criteria); mark in the PDF the sentences the score rests on. Do not count the AI tool being studied as a resource — it is the treatment..
    - `yes`: the task requires substantial resources beyond what is handed to the participant: extensive materials to search or synthesise across a large context (e.g. a corpus of documents, multiple studies to browse, a large codebase, browsing the internet for at least an hour), specialist data sources or tools, or substantial context-specific knowledge — whether firm-specific (e.g. tacit knowledge), study-specific, procedural, or otherwise — that the participant must bring or that would need to be supplied to a model.
    - `no`: the task is self-contained: a few materials sitting directly in front of the participant suffice, and it draws only on general knowledge or basic everyday tacit knowledge.

### Arm (one per condition of a task, the reference arm included)

- **`arm_id`** — Arm id (text, one line; required; covered by the row's quote)  
  C for the reference arm, T1, T2, ... for the treatment arms of this task, in the paper's order.
- **`arm_label`** — Arm label (text, one line; required; covered by the row's quote)  
  the paper's own name for the condition ('Control', 'ChatGPT access', 'GPT-4 + overview').
- **`is_baseline`** — Reference arm (true / false; required; covered by the row's quote)  
  true for the one arm of the task the others are compared with: usually the people doing the work without the AI, or before getting it; control_type says what the comparison is.
- **`compared_with`** — Compared with (text, one line; filled by the coder, left empty by the model)  
  the arm_id of the reference arm this arm is compared with; empty when the task has one reference arm (then every other arm is compared with it).
- **`control_type`** — Control type (one category; with its own quote; open: `other` + the paper's term allowed; confidence group `arm`)  
  what the reference arm is, on the reference arm only (null on the treatment arms).
    - `no_ai_concurrent` (no AI, concurrent group): a concurrent group or condition doing the task without the AI, drawn from the same pool (randomized or assigned; design_type says which)
    - `no_ai_prior` (no AI, the same units before): the same people or units before access: a pre-period, an own baseline, the not-yet-treated state of a rollout
    - `no_ai_after` (no AI, the same units after): the same people or units after the AI was taken away (a ban, an outage, the end of access): the time or group with the AI is the treatment arm, the one without it the reference arm, whatever the paper calls the treatment
    - `placebo_tool` (another, non-AI tool): the comparison condition got a non-AI aid instead: a search engine, documentation, templates, a checklist, non-generative software
    - `other_ai` (another AI): a different AI system or configuration is the comparison (an older model, no retrieval, a different prompt or interface, non-agentic); every arm uses AI
    - `expert_benchmark`: unassisted experts or incumbents whose performance is the reference for AI-assisted others: a different population, not an assigned condition
    - `ai_alone` (the AI alone): the AI's own output with no human; recorded only when the paper's contrast is against it, with n_arm null
    - `external_norm`: a published norm, a historical rate or a market benchmark; no participants
    - `none`: no comparison: a single arm, descriptive
    - `other`: none of the categories fits: give the paper's own term in `proposed`
- **`sample_population`** — Sample population (text, one line; required; with its own quote; confidence group `arm`)  
  who the people in this arm are, in the paper's own words: 'BCG consultants', 'third-year law students', 'customer support agents of a Fortune 500 firm'.
- **`sample_population_type`** — Population type (one category; with its own quote; open: `other` + the paper's term allowed; confidence group `arm`)  
  the kind of people the outcomes are measured on: the actual population whose outcomes are measured, not everyone the study mentions. The specific category wins over the generic one; recruitment through an online platform or panel takes precedence over general_population whatever the age, nationality or demographic restrictions; never other merely because a sample is heterogeneous or distinctively described.
    - `students`: university or school students
    - `crowdworkers_online_panel` (crowdworkers/online panel): Prolific, MTurk, similar platforms
    - `general_population`: members of the public or of sub-populations: convenience samples, job applicants, mixed groups of adults without a shared task-relevant occupation
    - `professionals_in_domain` (Professionals, in-domain): People working in the occupation the task comes from such as consultants on consulting tasks, radiologists on X-ray
    - `professionals_out_of_domain` (Professionals, out-of-domain): Professionals doing a task outside their field
    - `recipients` (recipients of the work): the people the work is for, when the outcomes are measured on them: patients counselled, customers served, students taught (a reversed design, see the rules)
    - `other`: only when none of the categories fits: the paper's own term in `proposed`
- **`sample_expertise`** — Expertise (one category; with its own quote; confidence group `arm`)  
  the arm's training and experience in the domain of the task, decided in this order: (1) people employed in the occupation the task belongs to are expert, whatever their seniority and whatever spread of skill or experience the paper reports among them, unless the paper itself calls them junior, then intermediate; (2) students in the field and trainees are intermediate; (3) people with no training in the domain (the general public, crowdworkers, job applicants, students outside the field) are novice; (4) mixed only when the arm deliberately combines people from different levels in this sense (students with professionals, laypeople with experts), never for a reported skill spread within one occupational sample; (5) not_applicable only for a task with no domain anyone could be trained in (a hidden-profile game, a generic survey); (6) not_stated when the paper says nothing about who the people are.
    - `novice`: no training in the task domain: the general public, crowdworkers, job applicants, students outside the field; a sample described as needing no specific industry or contextual knowledge
    - `intermediate`: students in the field, trainees, and staff the paper itself calls junior
    - `expert`: employed in the occupation the task belongs to, whatever the seniority or the spread of skill the paper reports, unless the paper itself calls them junior
    - `mixed`: the arm deliberately combines people from different levels (students with professionals, laypeople with experts); a reported spread of skill or experience within one occupational sample is not mixed
    - `not_applicable`: the task has no domain anyone could be trained in
    - `not_stated`: the paper gives no information on who the people are [added in batch 2026-11-03]
- **`sample_representativeness`** — Representativeness (one category; with its own quote; confidence group `arm`)  
  how the arm's sample relates to a population.
    - `population_wide` (Population-wide): e.g. register data in Denmark
    - `representative_sample`: e.g. household or firm survey
    - `non_representative_sample` (non-representative sample): e.g. small experiment
- **`countries`** — Countries (list; with its own quote; confidence group `arm`)  
  where the arm's people or data come from, not the authors; 'online, unspecified' when the paper does not say.
- **`n_arm`** — People in the arm (integer; with its own quote; confidence group `arm`)  
  human subjects who did this task in this arm; null if not stated.
- **`arm_description`** — What this arm got (text; required; with its own quote; confidence group `arm`)  
  tool, access, training, prompts, restrictions: what made this condition what it was.
- **`arm_notes`** — Arm notes (text)  
  anything a coder must know about the arm.
- **`ai_model`** — AI model (text, one line; with its own quote; confidence group `arm`)  
  the exact system this arm used, with version and access route ('GPT-4 via ChatGPT Plus', 'GitHub Copilot', 'Claude Sonnet 4'); null for a no-AI reference arm or if not stated.
- **`ai_role`** — AI role (one category; with its own quote; open: `other` + the paper's term allowed; confidence group `arm`)  
  how the AI took part in the work in this arm; none for a no-AI reference arm.
    - `open_ended_assistance` (Open-ended assistance): the human has open-ended access and uses it however they like; a general-purpose chatbot or agent is open_ended_assistance
      How was AI used to solve the task?
    - `ai_review`: The human produces the work and AI provides feedback.
    - `ai_advice`: AI gives a recommendation like a prediction or a label but human makes decision. E.g. radiology image classification or algorithmic hiring
    - `human_review`: AI produces the work and the human reviews it.
    - `ai_only` (AI-only): Full delegation with human limited to prompting, guiding and giving feedback.
    - `none`: the arm did not use AI (a no-AI reference arm)
    - `other`
- **`is_agentic`** — Agentic tool (true / false; with its own quote; confidence group `arm`)  
  whether the AI autonomously executes a multi-step task or workflow rather than merely responding or assisting: true when it independently determines and executes successive actions toward a goal (conducting an interview, navigating software, using tools, modifying files, completing a workflow with limited human intervention); false when it responds to individual prompts, generates content, recommends or assists without executing a sequence of actions on its own; null for a no-AI reference arm.
- **`ai_measure`** — How AI enters (one category; with its own quote; confidence group `arm`)  
  how AI exposure or use is operationalised in this arm; null for a no-AI reference arm.
    - `exposure`: potential exposure to AI measured through an index, score or proxy based on tasks, occupations or capabilities, e.g. Eloundou et al.
    - `adoption`: actual AI adoption or deployment by workers, firms or organisations, naturally occurring rollouts included, where adoption is not assigned by the researchers
    - `observed_usage`: actual intensity, frequency or patterns of AI use measured through usage logs, interactions or other behavioural records
    - `experimental_access`: the researchers assign, provide or withhold access to AI as part of an experimental intervention, whether or not the participants use it

# Decision rules

These rules settle what the variable definitions leave open. They are read verbatim by the
model, shown to the coders, and are the only place a rule lives: a disagreement is resolved
by editing this file, never by a private convention. The order in which the record is built,
and the instruction for each stage, is the Stages section of the codebook (generated from
`taxonomy.STAGES`); these rules are the detail behind it.

## Units

A paper is extracted at four levels. The **study** is the paper (one record). A **task** is a
distinct piece of work the participants performed, done by a distinct sample: the same work
given to two samples in two studies of one paper is two tasks (`S1.T1`, `S2.T1`); two pieces of
work given to one sample are two tasks (`T1`, `T2`); one piece of work scored on three
measures is one task with three result rows per arm. An **arm** is a condition of a task:
what a group of participants was given to do the work with, and who those participants are
(its sample). A **result row** is one number the paper reports for one arm, one outcome
measure, one subgroup and one horizon.

A paper with several studies that differ only in the sample (a replication) gets one task per
study. A labour-market paper (no participants doing a task) is extracted with one entry per
population an estimate is reported for, no arms, and the result rows carry the estimates; its
fields and rules are added when that tier is extracted.

## Study design

- **`research_design`** is the setting and the identification design of the paper's main
  with-versus-without-AI comparison in one field (lab, online or field RCT; pre/post with one
  group; staggered rollout; natural experiment; regression discontinuity; instrumental
  variable; observational comparison; descriptive), and **`experimental_design`** says whether
  the same people (`within_person`) or different people (`across_person`) did the work with
  and without AI. Both are coded once per paper. A paper whose tasks differ in design
  takes the design of its main comparison; the other designs are noted in the tasks'
  `task_notes`, and their result rows carry their own `contrast_type` and `estimand_type`.
- **`unit_of_analysis`** is the unit the main estimates are computed on, usually the
  individual in an experiment.
- **`sample_size`** is the analysed number of human participants in the comparison that
  establishes the AI effect, the with-versus-without-AI design `research_design` describes,
  across its tasks: not a survey, descriptive dataset or pilot reported in the same paper;
  with several qualifying studies, the largest. People, never observations, tasks or firms
  (those go in `study_notes`). `n_arm` and `n` give the arm- and row-level counts.
- **`period_start_year`**, **`period_start_month`**, **`period_end_year`** and
  **`period_end_month`** are when the data begin and end, as precisely as the paper states
  it: the year always, the month (1-12) when the paper gives it; the time of the experiment
  for a lab study.
- **`data_source`**: experimental_data = outcomes measured in the study's own experiment;
  job_postings = vacancy or posting data; worker_household_survey = a survey of workers or
  households; firm_survey = a survey of firms; public_admin_data = registers and statistics
  of a public body; private_admin_data = a firm's internal records (payroll, tickets, logs);
  ai_usage_data = logs of AI use from a provider; online_cvs = résumé or profile data;
  online_platform_data = activity on a platform (GitHub, Stack Overflow); online_freelancing_data
  = freelancing marketplaces; any other source is typed as the paper's own term.
- **`preregistered`** and **`incentivized`** are read from what the paper states (a
  registration; the payment scheme); `not_stated` when it does not say. Performance
  incentives means pay, a grade or an advancement that depended on how well the task was
  done, not a flat fee for taking part; for employees, a bonus or evaluation tied to the
  measured performance. **`vendor_affiliated`** is read by the coder from the title page
  and the funding note, which the model does not see: an author employed by, or the study
  funded or run by, the firm that makes or sells the AI tool studied.
- **`focus_topic`** and **`focus_dimension`** are what the paper's findings speak to, not
  what it mentions; the quote is the passage where the paper states its question or reads
  its findings in those terms (in the methods or results, since the introduction is withheld).

## Tasks

- **One task per distinct work × distinct sample.** Variants of the material (different
  essay prompts, different tickets) are one task. Different kinds of work (writing a memo,
  then negotiating) are separate tasks even when done by the same people in one session.
- **`task_name`** is a short, unique, descriptive name from the paper's own account
  ("drafting a legal memo", "resolving customer-support chats"), never the paper's label
  ("Task 1") alone; what participants did, when the name does not say it, goes in
  `task_notes`. The task's **`outcomes`** table lists every measure the paper reports for it with a
  contrast between arms, and its `how_measured` column says who or what evaluated the output and against what
  standard.
- **`task_label`** classifies the core activity, what the output is judged on, not the
  domain: writing a medical note is writing; diagnosing from an X-ray is classification;
  solving a coding problem is programming; answering exam questions is problem solving.
- **`soc_major_group`** is the occupation the work belongs to, not the participants'
  occupation: students drafting a legal memo give Legal (23); crowdworkers writing marketing
  copy give Arts, Design, Entertainment, Sports, and Media (27); a laboratory task with no
  occupation (abstract puzzles, word games) gives `not_applicable`.
- **The four complexity fields** (model only; not shown to coders) describe; they never
  score. Write what a person had to know, how a solver would find the route, how the parts
  of the work hang together and what feedback came during the work, and what had to be
  found or brought beyond the instructions. The model's scores are given later, blind, from
  these descriptions; a coder scores the four dimensions from the paper with the rubric and
  marks the sentences each score rests on.

## Arms

- **Every task has exactly one reference arm** (`is_baseline = true`, `arm_id = C`), the arm
  the others are compared with, unless `control_type = none` (a single arm, nothing to
  compare with); a task with several reference arms (two control groups, say) names on each
  treatment arm, in **`compared_with`**, the reference arm it is compared with, empty when
  there is one. **`control_type`** says what the reference arm is, and is set on the
  reference arm only:
  - `no_ai_concurrent`: a concurrent group or condition doing the task without the AI,
    drawn from the same pool, randomized or assigned (`research_design` says which). In a
    within-person design it is the no-AI condition of the same people.
  - `no_ai_prior`: the same people or units before access: a pre-period, an own baseline,
    the not-yet-treated state of a staggered rollout.
  - `no_ai_after`: the same people or units after the AI was taken away (a ban, an outage,
    the end of access): the time or group with the AI is the treatment arm and the one
    without it the reference arm, whatever the paper calls the treatment.
  - `placebo_tool`: the comparison condition got a non-AI aid instead: a search engine,
    documentation, templates, a checklist, non-generative software.
  - `other_ai`: a different AI system or configuration is the comparison (an older model,
    no retrieval, a different prompt or interface, a non-agentic version); every arm uses AI.
  - `expert_benchmark`: unassisted experts or incumbents whose performance is the reference
    for AI-assisted others; a different population, not an assigned condition.
  - `ai_alone`: the AI's own output with no human, recorded as an arm only when the paper's
    contrast is against it; its `n_arm` is null.
  - `external_norm`: a published norm, a historical rate or a market benchmark; no
    participants.
  - Anything else is typed as the paper's own term.
- A no-AI reference arm (`no_ai_concurrent`, `no_ai_prior`, `no_ai_after`, `placebo_tool`) has `ai_role =
  none`, `ai_model = null`, `is_agentic = null`, `ai_measure = null`. A reference arm that is
  another AI or the AI alone is coded like a treatment arm in the AI stage.
- **One treatment arm per AI condition**, `T1`, `T2`, … in the paper's order: a second tool,
  a second model, a variant with a feature or prompt changed, a higher dose, each is its own
  arm. The paper's own name goes in `arm_label`.
- **Each arm has its sample**: `sample_population` in the paper's words, its
  `sample_population_type`, `sample_expertise`, `sample_representativeness`, `countries`
  and `n_arm`. In a randomized split the sample fields are the same for every arm: code
  them on the reference arm and copy them to the others. In a comparison across populations
  (unassisted experts against assisted novices, adopters against non-adopters, two firms)
  each arm describes its own people. When the paper's tasks share their arms, the arms are
  coded once and copied to the other tasks.
- **Novices and experts.** Heterogeneity of the AI effect by experience within one condition
  is a `subgroup` of the result rows, not an arm. Novices with the AI compared with experts
  without it is a reference arm of type `expert_benchmark`.
- **Subgroups are not arms.** Juniors and seniors in the same condition are one arm; their
  separate estimates are result rows with a `subgroup`. Measurement occasions are not arms;
  they are `horizon`s.
- **`n_arm`** counts the people who did this task in this arm, as stated for the analysis
  sample; null when the paper gives only a total. In a staggered rollout the reference arm's
  `n_arm` is the never-treated people when stated, else null, and the treatment arm's the
  people ever treated; the total goes in `sample_size`.
- **AI-vs-AI designs.** When every arm uses AI, the comparison arm the paper measures
  against is the reference arm with `control_type = other_ai`; the rows are `ai_vs_ai` or
  `ai_variant` contrasts, and `meets_criteria` depends on whether another task of the paper
  has a no-AI comparison.
- **Removal designs.** When the AI is taken away (a ban, an outage, the end of a trial or of
  access), the time or group with the AI is the treatment arm and the one without it the
  reference arm (`control_type = no_ai_after`), whatever the paper calls the treatment. An
  effect the paper reports as the effect of removal enters with its sign reversed, and
  `result_notes` says so.
- **Reversed designs.** When the AI does the task in place of the worker and nobody prompts
  or supervises it while the work is delivered (a chatbot counselling patients before a
  scan, an AI answering support tickets on its own), the treatment arm is `ai_role =
  ai_only`, the reference arm is the worker doing the same task without the tool, and
  `task_notes` names the worker the AI replaces. The outcomes are then usually measured on the people
  the work is for: the arms' sample fields describe them (`sample_population_type =
  recipients`, `n_arm` their number), the task's `soc_major_group` is still the
  occupation the work belongs to, and every result row says on whom it is measured
  (`measured_on`, below). The worker's own time or throughput in such a design is measured
  on the worker.

## AI as treatment

- **`ai_model`** is the exact system with its version and access route, as the paper
  states it; **`ai_role`** how it took part in the work; **`is_agentic`** whether it acted
  on its own across files and software; **`ai_measure`** how AI enters the study for this
  arm: `experimental_access` when the study gives or withholds the tool, `exposure`,
  `adoption` and `observed_usage` for designs that measure AI in the field. All four are
  coded per treatment arm; a no-AI reference arm takes `none` for the role and null for the
  rest.

## Open categories

Every category field marked open may take `other` with the paper's own term in `proposed`
and a few words on what it means. Use it when no category fits, not when one fits
imperfectly. The terms are collected after each batch and unified against the taxonomy;
nothing is lost by proposing one.

## Quoting

Every value that has a quote is backed by a verbatim passage: copy the text exactly as it
appears, one contiguous span of 10 to 60 words from a single page, with that page's number
(the pages are marked `=== PAGE n ===` in the text given). Do not paraphrase, correct,
translate or join passages with ellipses. A table cell is quoted as the row of the table it
sits in, as the text layer prints it. The number in a numeric field must appear in its quote.
A result row has two quotes: one for its arm statistics (`n`, `mean`, `sd`) and one for its
effect (`effect_reported`, `effect_value`, `se`, `ci_low`, `ci_high`, `p_value`, `p_reported`,
`n_obs`); when a value has no printed source, the field is null, never guessed.

## Confidence

Each confidence group is rated high, medium or low with a short note: high when the paper
states the value plainly and the quote shows it; medium when it is inferred from several
passages or the paper is ambiguous; low when the value is a guess, the paper does not
report it, or the quote only partly supports it. A low rating sends the paper to a human.

## How to answer

The tasks and arms are listed in the user message with their ids: fill the study variables,
then for each listed task (in that order, `task_id` as given) its task variables and for each
of its listed arms (in that order, `arm_id` as given) its arm variables. Add no task or arm,
drop none. The result rows are filled by another pass: no numbers here beyond what a variable
asks for.

- A field with its own quote is an object `{"value": ..., "quote": "...", "page": n}`; a
  field marked open adds `"proposed"`: the paper's own term and a few words on what it means
  when `value` is `other`, null otherwise. Every other field is a plain value (or null).
- `confidence` rates each group (`design` on the study; `task`, `complexity` on a task; `arm`
  on an arm) as high, medium or low with a short note, as the codebook's Confidence section
  says.
- A category field takes one of the codes listed in the codebook, exactly as written, or
  `other` where the field is open. A value the paper does not give is null with an empty
  quote, never a guess and never a placeholder word ("not stated", "unclear", "n/a") written
  into a field; every quote is verbatim, from one page.
- The reference arm's `control_type` is given in the user message: copy it.
