You are extracting a **Human–AI collaboration experiment** for a meta-analysis of when human+AI teams outperform humans or AI alone. Follow the pre-registered data dictionary below.

# Structure

Return exactly ONE JSON object with:

- `paper_metadata` — ONLY the paper's identification: `title`, `authors`, `year`, `journal` (venue), `doi`.
- `records` — an ARRAY, **one object per experimental condition / treatment**. Put EVERY other variable (below) on EACH record. If a variable is the same for all conditions of the experiment (e.g. the task, the AI system, the human-alone / AI-alone baselines), repeat that same value on every record — the reviewer collapses identical values into a shared "study information" panel automatically. A study with 4 collaboration conditions → 4 record objects, each carrying the full field set.
- `evidence` — verbatim snippet support (see Evidence).

If the paper is not a human–AI collaboration experiment (no comparison of human, AI, and human+AI performance), return `"records": []` and one `evidence` note explaining the document type.

# Fields on EACH record

**Experimental information**
- `Pre_Registration` — `"Yes"` | `"No"`.
- `Research_Design` — `"Between-Subjects"` | `"Within-Subjects"` | `"Mixed"` | `"Other"`.

**Task**
- `Task_Desc` — one-sentence description of the task.
- `Task_Domain` — the O*NET domain: one of `"Arts and Humanities"`, `"Business and Management"`, `"Communications"`, `"Education"`, `"Engineering and Technology"`, `"Health Services"`, `"Law and Public Safety"`, `"Manufacturing and Production"`, `"Mathematics and Science"`, `"Transportation"`.
- `Task_Data` — a LIST of input data types, each one of `"Numeric"`, `"Image"`, `"Text"`, `"Audio"`, `"Video"` (e.g. `["Text"]`). Include every type that applies.
- `Task_Output` — one of `"Binary Value"`, `"Discrete Value"`, `"Continuous Value"`, `"Categorical Option"`, `"List"`, `"Text"`, `"Image"`, `"Audio"`, `"Video"`, `"Other"`.
- `Task_Type` — one of `"Create"`, `"Decide"`, `"Sense"`, `"Remember"`, `"Learn"`.

**AI system**
- `AI_Type` — one of `"Generative AI"`, `"Deep Learning"`, `"Shallow"`, `"Wizard of Oz"`, `"Other"`.
- `AI_Data_In` — one of `"Tabular"`, `"Image"`, `"Text"`, `"Audio"`, `"Video"`.
- `AI_Data_Out` — one of `"Binary Value"`, `"Discrete Value"`, `"Continuous Value"`, `"Categorical Option"`, `"List"`, `"Text"`, `"Image"`, `"Audio"`, `"Video"`, `"Other"`.
- `LLM_Model` (named model if generative/LLM, else null) · `LLM_Version` (checkpoint/date, else null) · `Prompting_Strategy` (`"zero-shot"`/`"few-shot"`/`"chain-of-thought"`/`"RAG"`/null).
- `Multi_Agent` (`"Yes"`/`"No"`) · `Multi_Agent_Count` (int, null unless Yes) · `Fine_Tuned` (`"Yes"`/`"No"`).

**Collaboration design** (usually the thing that varies between conditions)
- `Condition_Name` — the paper's label for this condition (`"HAI = Guidelines"`, `"AI + explanation"`).
- `Final_Decision` — `"Human"` | `"AI"`.
- `Division_Labor` — `"Yes"` | `"No"`.
- `AI_Expl_Incl` — `"Yes"` | `"No"` · `AI_Conf_Incl` — `"Yes"` | `"No"`.
- `AI_Expl_Type` — one of `"Text"`, `"Image"`, `"Audio"`, `"Video"`, `"Other"` (null if no explanation).
- `Interface_Modality` (`"chat"`/`"dashboard"`/`"inline-overlay"`/`"document"`, null if unclear) · `Interaction_Turns` (`"single"`/`"multi"`/integer, null if unclear).

**Evaluation**
- `Perf_Metric` — the performance measure (`"Accuracy"`, `"F1"`, …).
- `Perf_Dir` — `"Up"` if higher is better, `"Down"` if lower is better.
- `Tail_Performance` — `"Yes"` | `"No"` (are tail/worst-case metrics reported).
- `Effect_Size_Measure` — the effect-size metric if reported (`"Cohen's d"`, `"Hedges' g"`), else null.
- `Significance_Test` — the significance test (`"t-test"`, `"ANOVA"`), else null.

**Participants**
- `Participant_Type` — one of `"Crowdworker"`, `"Student"`, `"Expert"`, `"Other"`.
- `Participant_Source` — recruitment source (`"MTurk"`, `"Prolific"`, `"University"`).
- `N_Human` — participants in the human-alone condition (int) · `N_HumanAI` — participants in this human+AI condition (int).

**Results**
- `Avg_Perf_Human` · `Sd_Perf_Human` — mean & SD of humans alone (on `Perf_Metric`).
- `Avg_Perf_AI` · `Sd_Perf_AI` — mean & SD of the AI alone.
- `Avg_Perf_HumanAI` · `Sd_Perf_HumanAI` — mean & SD of the human+AI team FOR THIS CONDITION.

**Additional outcomes** (report if measured; value/direction if stated, else `"No"`)
- `Time_On_Task` (with units) · `Trust_Measured` (`"Yes"`/`"No"`) · `Reliance` · `Cognitive_Load` · `Usability_Satisfaction` · `HumanAI_Agreement`.
- `Data_Contamination_Checked` — `"Yes"` | `"No"` | `"NA"`.
- `Notes` — anything the analyst should know about this condition.

# Numeric & value hygiene (critical)

- Report **RAW reported values only**. Numeric fields (`N_*`, `Avg_Perf_*`, `Sd_Perf_*`, `year`, `Multi_Agent_Count`) must be JSON **numbers**, not strings. Use `null` for anything the paper does not report; never guess, compute, or interpolate.
- Categorical fields: use exactly one of the listed options; `null` only if genuinely indeterminable.
- **Do NOT** emit enumeration or derived columns (no `Paper_ID`, `Exp_ID`, `Treatment_ID`, `Measure_ID`, no `*_Cleaned`, `*_Adj`, `Synergy`, `ES_ID`).
- Repeat study-constant fields (task, AI, baselines, metric) identically on every record — do NOT put them in `paper_metadata`.

# OUTPUT SCHEMA (strict)

Return ONLY this JSON object — no markdown fences, no prose before/after.

```
{
  "paper_metadata": {"title": string, "authors": [string], "year": int|null, "journal": string|null, "doi": string|null},
  "records": [
    {
      "Pre_Registration": "Yes"|"No"|null, "Research_Design": string|null,
      "Task_Desc": string|null, "Task_Domain": string|null, "Task_Data": [string], "Task_Output": string|null, "Task_Type": string|null,
      "AI_Type": string|null, "AI_Data_In": string|null, "AI_Data_Out": string|null,
      "LLM_Model": string|null, "LLM_Version": string|null, "Prompting_Strategy": string|null,
      "Multi_Agent": "Yes"|"No"|null, "Multi_Agent_Count": int|null, "Fine_Tuned": "Yes"|"No"|null,
      "Condition_Name": string, "Final_Decision": string|null, "Division_Labor": "Yes"|"No"|null,
      "AI_Expl_Incl": "Yes"|"No"|null, "AI_Conf_Incl": "Yes"|"No"|null, "AI_Expl_Type": string|null,
      "Interface_Modality": string|null, "Interaction_Turns": string|null,
      "Perf_Metric": string|null, "Perf_Dir": "Up"|"Down"|null, "Tail_Performance": "Yes"|"No"|null,
      "Effect_Size_Measure": string|null, "Significance_Test": string|null,
      "Participant_Type": string|null, "Participant_Source": string|null, "N_Human": int|null, "N_HumanAI": int|null,
      "Avg_Perf_Human": number|null, "Sd_Perf_Human": number|null,
      "Avg_Perf_AI": number|null, "Sd_Perf_AI": number|null,
      "Avg_Perf_HumanAI": number|null, "Sd_Perf_HumanAI": number|null,
      "Time_On_Task": string|null, "Trust_Measured": "Yes"|"No"|null, "Reliance": string|null,
      "Cognitive_Load": string|null, "Usability_Satisfaction": string|null, "HumanAI_Agreement": string|null,
      "Data_Contamination_Checked": "Yes"|"No"|"NA"|null, "Notes": string|null
    }
  ],
  "evidence": [
    {"snippet": "verbatim text …", "page": 5, "source": "Table 2", "field": "records[0].Avg_Perf_HumanAI"}
  ]
}
```

# Evidence

Provide `evidence` whose `snippet` is the **verbatim** text supporting the key numbers. `field` MUST be `records[i].<Field>` (e.g. `"records[3].Avg_Perf_HumanAI"`) — always the `records[i].` prefix with the row's index. Quote character-for-character; never paraphrase. Pages are 1-indexed. Cover the performance values, Ns and SDs; omit an evidence entry rather than fabricate a quote.
