You are extracting a **Human–AI collaboration experiment** for a meta-analysis of when human+AI teams outperform humans or AI alone. Follow this pre-registered codebook exactly.

# Structure

Return exactly ONE JSON object with:

- `paper_metadata` — the paper's identification ONLY: `title`, `authors` (list), `year`, `journal` (the venue's full official name, not an abbreviation), `doi`.
- `records` — an ARRAY. Create **one object per experimental CONDITION × performance metric** reported in the main results (one row per effect size). A paper may report several experiments and several metrics — produce a record for each. Each object carries the full field set below.
- `evidence` — one verbatim snippet per field (see Evidence).

If the paper is not a human–AI collaboration experiment (no comparison of human-alone, AI-alone, and human+AI performance), return `"records": []` and one `evidence` note explaining the document type.

# Building the records array

One record per distinct experimental condition for which performance is reported, **crossed with each performance metric**. Treat each AI-assistance variant, each explanation style, each participant subgroup, and each distinct metric as its OWN record. A typical paper yields ~3–8 records. The study-level fields (design, task, AI system, participants) repeat identically across a paper's records — the reviewer collapses identical values into a shared "Study information" panel, so only the measurement fields visibly vary.

# Fields on EACH record

Values in **[brackets]** are the ONLY allowed values — use exactly one (or, for **(multi)** fields, a JSON array of one or more). Use `null` when a field is genuinely not reported.

**Design** (study-level)
- `Exp_Design` — participant-assignment design. [Between-Subjects, Within-Subjects, Mixed, Other]
- `Comp_Type` — comparison type for this effect size. [Independent Samples, Dependent Samples]

**Task** (study-level)
- `Task_Desc` — one short phrase for what the participant had to determine (free text).
- `Task_Data` **(multi)** — input data modality/modalities of the task. Array of [Text, Video, Numeric, Image, Categoric, Code, Audio].
- `Task_Output` **(multi)** — required output type(s). Array of [Binary, Categoric, Numeric, Open Response, Image, Audio].
- `Task_Type` — nature of the task: Decide (choose/judge) vs Create (generate). [Create, Decide]

**AI system** (study-level)
- `AI_Type` — type of AI. Shallow = classic ML; Deep = neural nets; Generative = LLM/generative; Wizard of Oz = human-simulated. [Generative, Deep, Shallow, Wizard of Oz]
- `AI_Data_In` **(multi)** — input modality/modalities the AI consumes. Array of [Text, Numeric, Image, Categoric, Code, Video, Audio].
- `AI_Data_Out` — output type the AI produces. [Binary, Categorical, Continuous, Numeric, Image, Text, Code]
- `AI_Expl_Incl` — did the AI provide an explanation? [Yes, No]
- `AI_Conf_Incl` — did the AI display a confidence value? [Yes, No]
- `AI_Expl_Type` **(multi)** — modality of the AI explanation. Array of [Image, Numeric, Text]; use `"NA"` (a plain string, not an array) if `AI_Expl_Incl` is No.
- `Prompting_Strategy` **(multi)** — how a generative model is prompted / run. Array of [zero-shot, few-shot, chain-of-thought, reasoning / thinking mode, RAG, tool-use / agentic]; `null` if not generative or not stated. ("reasoning / thinking mode" = a native extended-reasoning model, e.g. o1/o3, DeepSeek-R1, Claude extended thinking — distinct from prompted chain-of-thought.)
- `Fine_Tuned` — was the AI fine-tuned / task-adapted (vs off-the-shelf)? [Yes, No]
- `Interface_Modality` — how the AI's output was presented. [chat, dashboard, inline-overlay, document, voice]

**Collaboration**
- `Final_Decision` — who makes the final decision (human-in-the-loop). [Human]
- `Division_Labor` — a fixed pre-assigned split of which items the human vs AI handles? Usually No. [Yes, No]
- `Condition_Name` — the paper's own label for this human+AI condition (free text).

**Performance metric**
- `Perf_Metric` — the performance metric. [Accuracy, Error, Error Rate, Quality, Score, Time] — or the paper's exact metric name if none of these fit.
- `Perf_Dir` — Up = higher is better; Down = lower is better (error/time). [Up, Down]

**Sample**
- `N_Exp` — total participants in the experiment (integer).
- `N_Human` — participants in the human-alone condition. WITHIN-subjects → total participants; BETWEEN-subjects → the unaided arm's count. (integer)
- `N_HumanAI` — participants in the human+AI condition. WITHIN-subjects → total participants; BETWEEN-subjects → the assisted arm's count. (integer)

**Participants** (study-level)
- `Participant_Type` — participant population. [Crowdworkers, Specialists (Expert), Specialists (Non-Expert), Students, Physicians, Volunteers, Working Professionals] — or the paper's own short label if none fit.
- `Participant_Source` — recruitment source/platform. [MTurk, Prolific, Upwork, Hospital, University, Company, Invitation, Email Lists, Social Media, Website, Volunteer, Not Specified]
- `Participant_Expert` — were participants domain experts? [Yes, No]
- `Participant_Crowdworker` — were participants crowdworkers? [Yes, No]

**Performance — raw means** (in the paper's own metric/scale, NOT re-oriented)
- `Avg_Perf_Human` · `Avg_Perf_AI` · `Avg_Perf_HumanAI` — mean performance of human-alone, AI-alone, and human+AI (numbers).

**Performance — SD**
- `Sd_Perf_Human` · `Sd_Perf_AI` · `Sd_Perf_HumanAI` — SDs, only if a SD is reported (not SE/CI/IQR). A single deterministic AI on a fixed test set has no participant-level variance → `Sd_Perf_AI` = 0. Use `null` when no usable SD is reported.

**Effect size**
- `Est_ES` — were the effect-size inputs estimated (digitized from a figure / imputed) rather than reported directly? [Yes, No]

**Notes**
- `Notes` — free-text coder notes (or `null`).
- `Notes_2` — any additional caveat (or `null`).

# Numeric & value hygiene (critical)

- Report **RAW reported values only**. `N_*`, `Avg_Perf_*`, `Sd_Perf_*`, `year` are JSON **numbers**, not strings. `null` for anything not reported — never guess, compute, or interpolate.
- Categorical fields: use exactly the allowed value(s); **(multi)** fields are JSON arrays. `null` only if genuinely indeterminable.
- Do **NOT** emit any derived/enumeration column: no `Paper_ID`/`Exp_ID`/`Treatment_ID`/`Measure_ID`/`ES_ID`, no `*_Cleaned`, no `*_Adj`, no `Task_Data_Is*` flags, no `Baseline`, no `Synergy`, no `Participant_Type_2`. Those are computed downstream.
- Repeat the study-level fields identically on every record; keep `paper_metadata` to title/authors/year/journal/doi only.

# OUTPUT SCHEMA (strict)

Return ONLY this JSON object — no markdown fences, no prose before/after.

```
{
  "paper_metadata": {"title": string, "authors": [string], "year": int|null, "journal": string|null, "doi": string|null},
  "records": [
    {
      "Exp_Design": string|null, "Comp_Type": string|null,
      "Task_Desc": string|null, "Task_Data": [string], "Task_Output": [string], "Task_Type": string|null,
      "AI_Type": string|null, "AI_Data_In": [string], "AI_Data_Out": string|null,
      "AI_Expl_Incl": "Yes"|"No"|null, "AI_Conf_Incl": "Yes"|"No"|null, "AI_Expl_Type": [string]|"NA"|null,
      "Prompting_Strategy": [string]|null, "Fine_Tuned": "Yes"|"No"|null, "Interface_Modality": string|null,
      "Final_Decision": "Human"|null, "Division_Labor": "Yes"|"No"|null, "Condition_Name": string|null,
      "Perf_Metric": string|null, "Perf_Dir": "Up"|"Down"|null,
      "N_Exp": int|null, "N_Human": int|null, "N_HumanAI": int|null,
      "Participant_Type": string|null, "Participant_Source": string|null,
      "Participant_Expert": "Yes"|"No"|null, "Participant_Crowdworker": "Yes"|"No"|null,
      "Avg_Perf_Human": number|null, "Avg_Perf_AI": number|null, "Avg_Perf_HumanAI": number|null,
      "Sd_Perf_Human": number|null, "Sd_Perf_AI": number|null, "Sd_Perf_HumanAI": number|null,
      "Est_ES": "Yes"|"No"|null, "Notes": string|null, "Notes_2": string|null
    }
  ],
  "evidence": [
    {"snippet": "verbatim text …", "page": 5, "source": "Table 2", "field": "records[0].Avg_Perf_HumanAI"}
  ]
}
```

# Evidence

Provide an `evidence` item for **every field you can support with a quote**, one per field: `{ "field": "records[i].<Field>", "snippet": "<verbatim>", "page": N, "source": "..." }`. `field` MUST be `records[i].<Field>` with that row's index. Quote **character-for-character**; never paraphrase; pages are 1-indexed. When one quote supports several fields, **repeat the same snippet** as a separate item for each field. Prioritise the performance values (`Avg_Perf_*`), the Ns, the SDs, and the categorical design/task/AI fields. Omit a field's evidence item rather than fabricate a quote.
