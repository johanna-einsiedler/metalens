You are extracting a **Human–AI collaboration experiment** for a meta-analysis of when human+AI teams outperform humans or AI alone. Follow this pre-registered data dictionary exactly.

# Structure

Return exactly ONE JSON object with:

- `paper_metadata` — the paper's identification ONLY: `title`, `authors`, `year`, `journal` (the venue's full official name, e.g. "Proceedings of the ACM on Human-Computer Interaction", not an abbreviation), `doi`.
- `records` — an ARRAY. Create **one object per experimental CONDITION × performance metric** reported in the main results (see "Building the records array"). Each object carries the full field set below.
- `evidence` — verbatim snippet support (see Evidence).

If the paper is not a human–AI collaboration experiment (no comparison of human-alone, AI-alone, and human+AI performance), return `"records": []` and one `evidence` note explaining the document type.

# Building the records array (read carefully)

Create one object per distinct experimental CONDITION for which performance is reported in the main results, **crossed with each performance metric**. Treat each AI-assistance variant as its OWN object — each explanation style, each AI-accuracy/reliability level, each task block or participant subgroup, and each distinct performance metric gets a separate object.

Within EACH object, fill all three condition values — `Perf_Human` (human alone), `Perf_AI` (AI alone), `Perf_HumanAI` (human+AI) — for THAT condition. Do NOT collapse different conditions into one object, and do NOT split a single condition's three values across three objects. A typical paper yields about **3–8 objects**; produce one for every condition the main results table distinguishes.

The study-level fields (task, design, AI system, participants) are the SAME for every object of a paper — repeat the identical value on each object. The reviewer automatically collapses identical values into a shared "Study information" panel, so only the measurement fields (the Ns, `Perf_Metric`, the explanation flags, and the `Perf_*`/`Std_*` values) visibly vary between records.

# Fields on EACH record

**Study & task** (study-level — identical on every record)
- `Task_Type` — nature of the task: `"Decide"` (choose/judge/classify/estimate) vs `"Create"` (generate content). One of: `"Create"`, `"Decide"`.
- `Task_Desc` — one short phrase naming what the participant had to determine (e.g. "Determine nutritional content of meals").
- `Task_Domain` — the O*NET domain: one of `"Arts and Humanities"`, `"Business and Management"`, `"Communications"`, `"Education"`, `"Engineering and Technology"`, `"Health Services"`, `"Law and Public Safety"`, `"Manufacturing and Production"`, `"Mathematics and Science"`, `"Transportation"`.
- `Exp_Design` — participant-assignment design. One of: `"Between-Subjects"`, `"Within-Subjects"`, `"Mixed, Between-Subjects"`, `"Mixed, Within-Subjects"`, `"Other"`.
- `Task_Data` — input data modality the task operates on; pick the single best-fitting entry from: `"Audio"`, `"Audio, Text"`, `"Categoric, Image, Numeric"`, `"Categoric, Numeric"`, `"Categoric, Numeric, Text"`, `"Code"`, `"Image"`, `"Image, Numeric, Text"`, `"Image, Text"`, `"Numeric"`, `"Numeric, Text"`, `"Text"`, `"Video"`.
- `Task_Output` — what the participant produces. One of: `"Binary"` (yes/no, two classes), `"Categoric"` (one of >2 classes / a label), `"Numeric"` (a number/estimate), `"Text"`, `"Image"`, `"Audio"`, `"Video"`, `"Code"`.

**AI system** (study-level)
- `AI_Type` — `"Shallow"` (classic ML: logistic/trees/SVM), `"Deep"` (neural nets / deep learning), `"Generative"` (LLM / generative model), or `"Wizard of Oz"` (AI outputs simulated by researchers).
- `AI_Data_In` — input modality the AI consumes; one of: `"Audio"`, `"Audio, Text"`, `"Categoric, Image, Numeric"`, `"Categoric, Numeric"`, `"Code"`, `"Image"`, `"Image, Text"`, `"Numeric"`, `"Numeric, Text"`, `"Text"`, `"Video"`.
- `AI_Data_Out` — what the AI outputs; one of: `"Audio"`, `"Binary"`, `"Categorical"`, `"Code"`, `"Continuous"`, `"Image"`, `"Numeric"`, `"Text"`, `"Video"`.
- `LLM_Model` — the named model if the AI is generative / an LLM (e.g. "GPT-4", "Llama-2-70B", "Gemini 1.5 Pro"), else `null`.
- `LLM_Version` — the specific checkpoint / snapshot / date if stated (e.g. "gpt-4-0613"), else `null`.
- `Prompting_Strategy` — how a generative model is prompted / run: one of `"zero-shot"`, `"few-shot"`, `"chain-of-thought"` (prompted step-by-step), `"reasoning / thinking mode"` (the model natively runs an extended-reasoning mode — e.g. o1/o3, DeepSeek-R1, Claude extended thinking, Gemini thinking), `"RAG"`, `"tool-use / agentic"`, or `null` (not generative, or not stated). If several apply, pick the most distinctive one.
- `Multi_Agent` — was more than one AI agent used together? `"Yes"` | `"No"`.
- `Multi_Agent_Count` — number of agents (integer); `null` unless `Multi_Agent` is `"Yes"`.
- `Fine_Tuned` — was the AI fine-tuned / task-adapted (vs used off-the-shelf)? `"Yes"` | `"No"`.

**Collaboration**
- `Final_Decision` — who makes the final decision (the human is in the loop): `"Human"`.
- `Division_Labor` — was there a fixed, pre-assigned split of which items the human vs the AI handles? Usually `"No"`. `"No"` | `"Yes"`.
- `AI_Conf_Incl` — did the AI display a confidence value/score to the participant? `"No"` | `"Yes"`.
- `Interface_Modality` — how the AI's output was presented: one of `"chat"`, `"dashboard"`, `"inline-overlay"`, `"document"`, `"voice"`, or `null` if unclear.
- `Interaction_Turns` — `"single"` (one AI response) or `"multi"` (back-and-forth), or `null` if unclear.

**Participants** (study-level)
- `Participant_Type` — short label for the population. Crowdsourcing platform (MTurk/Prolific/Upwork) → `"Crowdworkers"`; domain experts (radiologists, physicians, clinicians, pathologists, lawyers) → `"Specialists"`; university students → `"Students"`; otherwise a short profession/role label.
- `Participant_Source` — recruitment source/platform. One of: `"Company"`, `"Email Lists"`, `"Email Lists, Social Media"`, `"Email, Twitter"`, `"Hospital"`, `"Invitation"`, `"MTurk"`, `"Not Specified"`, `"Prolific"`, `"Social Media, Snowball Sampling"`, `"University"`, `"Upwork"`, `"Volunteer"`, `"Website"`.

**Measurement** (varies per record — one record per condition × metric)
- `N_Total` — total participants in that experiment (integer).
- `N_Human` — participants in the human-alone condition. In a WITHIN-subjects design (the same people do the unaided and AI-assisted tasks) set `N_Human` = the total number of participants; in a BETWEEN-subjects design use the count assigned to the unaided arm. (integer)
- `N_HumanAI` — participants in the human+AI (AI-assisted) condition. WITHIN-subjects: = total participants; BETWEEN-subjects: the count in the AI-assisted arm. (integer)
- `Perf_Metric` — the performance metric's name as the paper labels it (e.g. Accuracy, F1, AUC, Error rate).
- `AI_Expl_Incl` — did the AI provide an explanation in this comparison's assisted condition? `"No"` | `"Yes"`.
- `AI_Expl_Type` — modality of that explanation. One of: `"Image"`, `"Image, Numeric"`, `"Image, Numeric, Text"`, `"Image, Text"`, `"NA"`, `"Numeric"`, `"Numeric, Text"`, `"Text"` (use `"NA"` if `AI_Expl_Incl` is `"No"`).
- `Perf_Human` — mean performance of humans alone, on the paper's own scale (number).
- `Perf_AI` — mean performance of the AI alone (number).
- `Perf_HumanAI` — mean performance of the human+AI condition (number).
- `Std_Human` — SD of human-alone performance, only if a SD is reported; if only SE/CI/IQR is given, leave `""` (empty string).
- `Std_AI` — SD of AI-alone performance. A single deterministic AI model evaluated on a fixed test set has NO variance across participants — set `Std_AI` = `0` in that case. Otherwise the reported SD, or `""`.
- `Std_HumanAI` — SD of human+AI performance, only if a SD is reported, else `""`.

# Numeric & value hygiene (critical)

- Report **RAW reported values only**. `N_*` and `Perf_*` are JSON **numbers**, not strings; `year` and `Multi_Agent_Count` are integers.
- The three SD fields follow the rule above: a reported SD as a number, `0` for a deterministic AI, or `""` when no usable SD is reported (do not substitute SE/CI/IQR).
- Use `null` for any non-SD field the paper does not report; never guess, compute, or interpolate a value.
- Categorical fields: use exactly one of the listed allowed values; `null` only if genuinely indeterminable.
- Do NOT emit ID / enumeration or derived columns (no `Paper_ID`, `Exp_ID`, `Measure_ID`, no `*_Cleaned`, `*_Adj`, `Synergy`).
- Repeat the study-level fields identically on every record — do NOT move them into `paper_metadata` (which holds only title/authors/year/journal/doi).

# OUTPUT SCHEMA (strict)

Return ONLY this JSON object — no markdown fences, no prose before/after.

```
{
  "paper_metadata": {"title": string, "authors": [string], "year": int|null, "journal": string|null, "doi": string|null},
  "records": [
    {
      "Task_Type": string|null, "Task_Desc": string|null, "Task_Domain": string|null,
      "Exp_Design": string|null, "Task_Data": string|null, "Task_Output": string|null,
      "AI_Type": string|null, "AI_Data_In": string|null, "AI_Data_Out": string|null,
      "LLM_Model": string|null, "LLM_Version": string|null, "Prompting_Strategy": string|null,
      "Multi_Agent": "Yes"|"No"|null, "Multi_Agent_Count": int|null, "Fine_Tuned": "Yes"|"No"|null,
      "Final_Decision": string|null, "Division_Labor": "Yes"|"No"|null, "AI_Conf_Incl": "Yes"|"No"|null,
      "Interface_Modality": string|null, "Interaction_Turns": string|null,
      "Participant_Type": string|null, "Participant_Source": string|null,
      "N_Total": int|null, "N_Human": int|null, "N_HumanAI": int|null,
      "Perf_Metric": string|null, "AI_Expl_Incl": "Yes"|"No"|null, "AI_Expl_Type": string|null,
      "Perf_Human": number|null, "Perf_AI": number|null, "Perf_HumanAI": number|null,
      "Std_Human": number|"", "Std_AI": number|"", "Std_HumanAI": number|""
    }
  ],
  "evidence": [
    {"snippet": "verbatim text …", "page": 5, "source": "Table 2", "field": "records[0].Perf_HumanAI"}
  ]
}
```

# Evidence

Provide `evidence` whose `snippet` is the **verbatim** text supporting the key numbers. `field` MUST be `records[i].<Field>` (e.g. `"records[3].Perf_HumanAI"`) — always the `records[i].` prefix with that row's index. Quote character-for-character; never paraphrase. Pages are 1-indexed. Prioritise the performance values (`Perf_Human` / `Perf_AI` / `Perf_HumanAI`), the Ns, and the SDs; omit an evidence entry rather than fabricate a quote.
