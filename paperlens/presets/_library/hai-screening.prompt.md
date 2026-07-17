You are screening a research paper for **eligibility in a meta-analysis of human–AI collaboration**. Apply the pre-registered inclusion/exclusion criteria below and return a single structured verdict with per-criterion answers and verbatim evidence.

# Goal

Return exactly ONE JSON object with:
- `paper_metadata` — `{"title": ..., "doi": ..., "year": ..., "authors": [...], "journal": ...}` (`title` required; others `null` if not determinable).
- `records` — an ARRAY with **exactly ONE object**: the screening verdict for this paper (the fields below).
- `evidence` — verbatim snippets supporting the key criterion answers (see Evidence).

# The criteria (answer each, then decide overall eligibility)

**1. Original human–AI collaboration experiment**
- `Is_Original_Experiment` — `"Yes"` if the paper presents an ORIGINAL experiment that evaluates an instance where a **human and an AI system work together** to perform a task; else `"No"`.
- `Study_Type` — classify the paper: one of `"experiment"`, `"meta-analysis"`, `"literature-review"`, `"theoretical"`, `"qualitative"`, `"commentary"`, `"opinion"`, `"simulation"`, `"other"`.
- `Excluded_By_Type` — `"Yes"` if `Study_Type` is anything OTHER than `"experiment"` (meta-analyses, reviews, theoretical/qualitative work, commentaries, opinions, and simulations are all excluded); else `"No"`.

**2. Reports all three performances, quantitatively** (the core requirement)
- `Reports_Human_Alone` — `"Yes"` if it reports the performance of the **human alone** on the task; else `"No"`.
- `Reports_AI_Alone` — `"Yes"` if it reports the performance of the **AI alone**; else `"No"`.
- `Reports_HumanAI` — `"Yes"` if it reports the performance of the **human–AI system** (the team); else `"No"`.
- `Quantitative_Measure` — `"Yes"` if those performances are reported with a **quantitative measure** (accuracy, F1, error rate, time, etc.); else `"No"`.
- (A paper reporting human-alone but NOT AI-alone — or AI-alone but NOT human-alone — fails this block and is excluded.)

**3. Sufficient statistics to compute an effect size**
- `Reports_Experimental_Design` — `"Yes"` if the experimental design is described (between/within-subjects, conditions); else `"No"`.
- `Reports_N_Per_Condition` — `"Yes"` if the **number of participants in each condition** is given; else `"No"`.
- `Reports_SD_Per_Condition` — `"Yes"` if the **standard deviation of the outcome in each condition** is reported; `"Calculable"` if not reported directly but derivable from other reported quantities (SE, CI, test statistics); `"No"` otherwise.

**4. Language**
- `Is_English` — `"Yes"` if the paper is written in English; else `"No"`.

# Overall verdict

- `Eligible` — set to `"Yes"` ONLY if ALL of these hold, else `"No"`:
  `Is_Original_Experiment="Yes"` AND `Excluded_By_Type="No"` AND `Reports_Human_Alone="Yes"` AND `Reports_AI_Alone="Yes"` AND `Reports_HumanAI="Yes"` AND `Quantitative_Measure="Yes"` AND `Reports_Experimental_Design="Yes"` AND `Reports_N_Per_Condition="Yes"` AND `Reports_SD_Per_Condition` in {`"Yes"`,`"Calculable"`} AND `Is_English="Yes"`.
- `Exclusion_Reasons` — a short list of the criteria that FAILED (e.g. `["AI-alone performance not reported", "no SD per condition"]`); empty list `[]` if `Eligible="Yes"`.
- `Screening_Notes` — one or two sentences summarising the judgement (what the paper is, and the decisive factor).

Be conservative and evidence-driven: if a required quantity is genuinely absent from the paper, answer `"No"` — do not assume it exists. Judge only from the paper's content.

# OUTPUT SCHEMA (strict)

Return ONLY this JSON object — no markdown fences, no prose before/after.

```
{
  "paper_metadata": {"title": string, "doi": string|null, "year": int|null, "authors": [string], "journal": string|null},
  "records": [
    {
      "Eligible": "Yes"|"No",
      "Is_Original_Experiment": "Yes"|"No",
      "Study_Type": "experiment"|"meta-analysis"|"literature-review"|"theoretical"|"qualitative"|"commentary"|"opinion"|"simulation"|"other",
      "Excluded_By_Type": "Yes"|"No",
      "Reports_Human_Alone": "Yes"|"No",
      "Reports_AI_Alone": "Yes"|"No",
      "Reports_HumanAI": "Yes"|"No",
      "Quantitative_Measure": "Yes"|"No",
      "Reports_Experimental_Design": "Yes"|"No",
      "Reports_N_Per_Condition": "Yes"|"No",
      "Reports_SD_Per_Condition": "Yes"|"Calculable"|"No",
      "Is_English": "Yes"|"No",
      "Exclusion_Reasons": [string],
      "Screening_Notes": string
    }
  ],
  "evidence": [
    {"snippet": "verbatim text …", "page": 5, "source": "Results", "field": "records[0].Reports_AI_Alone"}
  ]
}
```

# Evidence

Provide `evidence` entries whose `snippet` is the **verbatim** text supporting the decisive criterion answers — especially the human-alone / AI-alone / human–AI performance statements, the N-per-condition, and the SD (or the statistics that make it calculable). Rules:
- `field` MUST be `records[0].<Column>` (e.g. `"records[0].Reports_SD_Per_Condition"`) — always the `records[0].` prefix.
- Quote character-for-character; never paraphrase. Pages are 1-indexed PDF pages.
- You need not cover every field — cover the criteria that decide eligibility. Omit an evidence entry rather than fabricate a quote.
