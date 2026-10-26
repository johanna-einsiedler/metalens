## Introduction

Meta-analytic structural equation modelling (MASEM) pools correlation matrices from many primary studies and fits a structural model to the pooled matrix. The inputs are the relevant correlations, the reliabilities of the measures and the characteristics of the sample of a set of primary studies.

This preset focuses on direct information and extracts the effect sizes a paper reports, such as correlations in a matrix or in the text, and related bivariate statistics. For indirect information (item-level factor loadings and factor correlations), [a separate preset](/preset?id=masem-indirect) exists.

Both presets also extract sample characteristics (sample size, percentage female, mean age, country, language) and the reliabilities of the measures, each with an evidence link into the PDF.

## How it extracts

The user supplies the **canonical variable labels**, i.e. the constructs the meta-analysis is about, each with a short definition and the synonyms a paper might use, and the **effect-size codes** to extract (by default Pearson correlations and odds ratios). The prompt instructs the model to map each eligible measure in the paper to exactly one canonical label, to report only zero-order associations that are explicitly printed, and to ignore adjusted effects, regression coefficients, SEM paths, partial correlations and anything model-based. Matrix cells are read by row and column label. Statistics are extracted in the metric the paper prints them in.

If a paper reports on multiple samples, each sample is treated as an analytically distinct group of participants — an independent cohort, a subgroup, a wave — and linked to its own effect sizes and its own metadata. For every effect size the model also records the sample size it was computed on, the reliability of each measure in that sample (alpha preferred, then omega, ICC, kappa), and the instrument names.

Two optional parameters refine the task for a particular meta-analysis: **eligibility rules** replace the default rule that only the canonical variables are extracted, for instance to restrict pairs to those involving a focal construct, and **mapping guidance** adds a paragraph on typical informants, instruments and things to ignore. The parameters and the rendered prompt are shown below.

## Validation interface

Every extracted value is checked against the paper in one screen. The left panel lists the papers of the dataset; the centre shows the extracted values of one sample — the effect sizes as rows, the sample characteristics above them, every cell editable; the right panel shows the page of the PDF the value was read from. A click on a value scrolls the page to its quotation and highlights it, so a coder confirms or corrects each number against the source without leaving the screen. Edits are recorded as human corrections with the original value, the person and the time; a sample is marked verified or flagged at the end of its card, and the dataset's badge counts how much of it a person has checked.

```figure review-panel-direct
The review panel on Grydeland et al. (2012): the extracted effect sizes of the HEIA sample on the left, Table 2 of the paper on the right with the quoted cells highlighted; the selected odds ratio, 1.43, and its row in the table share the accent.
```

Table 2 on page 6 of Grydeland, M., Bergh, I. H., Bjelland, M., et al. (2012). Correlates of weight status among Norwegian 11-year-olds: The HEIA study. *BMC Public Health, 12*, 1053 ([doi:10.1186/1471-2458-12-1053](https://doi.org/10.1186/1471-2458-12-1053)), an open-access article under the Creative Commons Attribution licence (CC BY 2.0). The values are the model's unverified extraction of this paper from the validation study.

## Verification

The preset was developed on one meta-analysis and validated on a second that the authors did not use while developing the prompt; ten randomly drawn papers were used from each. The model's output was compared against a gold-standard database: the values coded in the published meta-analysis, re-checked against the primary publications for the study. Further, we conducted an additional validation study where, for each dataset, (1) two coders recorded the values from the PDFs only and (2) two coders were presented with the validation interface and tasked to verify the extractions. Table 1 and Table 2 compare the model-only extractions with those corrected by the coders through MASEMiner; Table 3 compares the coders' performance with and without MASEMiner. We report:

- **Sensitivity** (SEN = TP / (TP + FN)): the share of reference values the model recovered.
- **Precision** (PRE = TP / (TP + FP)): the share of the model's values that were correct.
- **Jaccard index** (JAC = TP / (TP + FN + FP)): sensitivity and precision in one number.
- **Omissions** (OMIT): a reference value the model did not report.
- **Over-extractions** (OVER): a value for which no reference value exists, such as a correlation for a pair or a sample the meta-analysis did not target.
- **Wrong values** (WRONG): a reference value reported with a different number.

A wrong value counts double for precision, recall and the Jaccard index: it is recorded both as a false negative, because the reference value was not recovered, and as a false positive, because a wrong number was returned (FN = OMIT + WRONG, FP = OVER + WRONG). This is a deliberate choice, since an omission or a surplus row in this particular setup is easy to spot for a reviewer, a wrong number is not. The model was GPT-5.6 (`gpt-5.6-sol`, OpenAI, September 2026). The tables give the unverified model output; in parentheses, the output after the coders of the verification study had reviewed and corrected it in this review screen, averaged over those coders.

The validation runs used the prompt as rendered by the earlier MASEMiner application on 25 September 2026: its template of 7 June 2026 with the variable definitions and, for the self-other set, the eligibility rules pasted into the prompt text (the exact texts are part of the evaluation materials). This preset's prompt is that text with four changes: the eligibility rules and the mapping guidance are parameters instead of edits to the text; evidence is required for every non-null sample metadata value, with rules for values obtained by inference; every evidence field must carry its `samples[i]` prefix; and the paper metadata include the journal. The extracted fields and the schema id are the same.

**Table 1 · Development set** — Gnambs (2013), self- and other-rated Big Five personality traits, k = 10 papers.

| Target | n | SEN | PRE | JAC | OMIT | OVER | WRONG |
|:--|--:|--:|--:|--:|--:|--:|--:|
| Effect size | 340 | 1.00 (1.00) | .54 (.54) | .54 (.54) | 0 (0) | 285 (285) | 0 (0.5) |
| Sample size | 295 | .98 (1.00) | .67 (.68) | .67 (.68) | 5 (0) | 140 (140) | 0 (0) |
| Reliability | 90 | .89 (.90) | .86 (.86) | .78 (.79) | 10 (9) | 13 (13) | 0 (0) |
| Mean age | 9 | .78 (.78) | 1.00 (1.00) | .78 (.78) | 2 (2) | 0 (0) | 0 (0) |
| Country | 12 | .75 (.92) | .90 (.92) | .69 (.85) | 2 (0) | 0 (0) | 1 (1) |
| % female | 11 | .27 (.41) | .75 (.70) | .25 (.35) | 7 (5) | 0 (0.5) | 1 (1.5) |

**Table 2 · Validation set** — Marker et al. (2022), video gaming and body mass, k = 10 papers.

| Target | n | SEN | PRE | JAC | OMIT | OVER | WRONG |
|:--|--:|--:|--:|--:|--:|--:|--:|
| Effect size | 34 | 1.00 (1.00) | .69 (.69) | .69 (.69) | 0 (0) | 15 (15) | 0 (0) |
| Sample size | 28 | 1.00 (.96) | .93 (.90) | .93 (.87) | 0 (0) | 2 (2) | 0 (1) |
| Reliability | – | – | – | – | – | – | – |
| Mean age | 8 | 1.00 (1.00) | 1.00 (.94) | 1.00 (.94) | 0 (0) | 0 (0.5) | 0 (0) |
| Country | 12 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| % female | 12 | .67 (.83) | .80 (.86) | .57 (.74) | 2 (0.5) | 0 (0) | 2 (1.5) |

*n* is the number of reference values; sample sizes are those attached to each effect size. Reliabilities were not coded in the reference database of the video-gaming meta-analysis.

Every target effect size was recovered in both sets, without a single wrong value. Precision is modest because the model also returns correlations the meta-analysis did not target: 285 in the development set, mostly correlations among informant ratings or among self-ratings and almost all from four publications; in the validation set, 12 of the 15 over-extractions come from one publication. These rows are visible as extra entries in the review screen and are deleted there; they are also the reason the eligibility-rules parameter exists. Reliabilities and sample sizes were otherwise recovered almost completely; the omissions were confined to one publication each. Descriptive information was less complete, the percentage of females in particular, mostly because the reference database and the model computed on different bases, e.g. the sex ratio before or after exclusions.

**Table 3 · Coders with and without MASEMiner** — the verification study: four student coders trained in meta-analytic coding; in each use case some coded the papers conventionally into a coding sheet, the others reviewed and corrected the MASEMiner output in this screen. Time is the mean per publication; the MASEMiner time includes the automated extraction. Accuracy is for the effect sizes, against the reference database.

| Use case | Conventional coding | MASEMiner + review | Time saved | Effect sizes, conventional (SEN / PRE) | Effect sizes, MASEMiner + review (SEN / PRE) |
|:--|--:|--:|--:|--:|--:|
| Self–other correlations | 30.6 min | 20.1 min (3.6 extraction) | 34 % | .99 / .65 | 1.00 / .54 |
| Video gaming and body mass | 17.2 min | 7.9 min (1.9 extraction) | 54 % | .91 / .91 | 1.00 / .69 |

Reviewing the model's output took a third to a half of the time of coding by hand, at comparable accuracy. The reviewed output recovers every effect size (coding by hand missed some), while its precision stays where the model left it — the coders rarely deleted the surplus correlations, and wrong values that needed the source table were corrected only in part.

The figures below are read live from the public datasets built with this preset on this server, as coders verify them.

### The prompts

Folded below: the prompt of the self–other runs exactly as sent on 25 September 2026; the prompt of the video-gaming runs, reconstructed by rendering the same template with that study's parameters (the self–other prompt confirms the method: the template rendered by the application's builder, with the eligibility rules pasted in by hand where the template says to extract only the canonical variables; the video-gaming study had no such rules); and, for comparison, this preset's prompt rendered with the self–other parameters on 26 October 2026. The prompt shown under "How it extracts" is the live one with the preset's own defaults. At run time the application appended one sentence announcing the page images and asking for sequential PDF page numbers in the evidence.

**Self–other correlations (Gnambs, 2013) — as sent, 25 September 2026**

````
# TASK

Extract eligible effect sizes and metadata from a PDF reporting an empirical study.

Return EXACTLY ONE valid JSON object following the required schema.

# DOMAIN CONFIGURATION

## EFFECT SIZES

Canonical effect size labels:
- "r" = Correlation

Extract ONLY these canonical effect sizes.

## VARIABLES

Canonical variable labels:
- **os** — Openness self-rating
- **cs** — Conscientiousness self-rating
- **es** — Extraversion self-rating
- **as** — Agreeableness self-rating
- **ns** — Neuroticism self-rating
- **op** — Openness other-rating
- **cp** — Conscientiousness other-rating
- **ep** — Extraversion other-rating
- **ap** — Agreeableness other-rating
- **np** — Neuroticism other-rating
Extract effect sizes ONLY for:
- Big Five personality traits,
- measured for the SAME target person,
- using self-ratings and/or other-ratings.

The correlated variables may:
- involve the same or different traits,
- involve the same or different raters,
provided both ratings refer to the same individual target.

Eligible correlations inlcude:
- self-self,
- self-other,
- other-other,
for:
- SAME trait,
- DIFFERENT traits.

Extract ALL eligible pairwise correlations between canonical variables appearing in the reported matrices, including:
- monotrait correlations,
- heterotrait correlations,
- monomethod correlations,
- heteromethod correlations.

Eligible Big Five domains are:
- Openness
- Conscientiousness
- Extraversion
- Agreeableness
- Neuroticism

Map:
- emotional stability
- emotional instability
to neuroticism.

Do NOT create a separate emotional stability variable.

Map all eligible measures to ONE canonical variable category.

Typical eligible informants include:
- peer,
- family,
- friend,
- acquaintance,
- spouses,
- romantic partner,
- sibling,
- parent,
- teacher,
- coworker,
- supervisor,
- observer,
- informant.

Code all non-self ratings as other-ratings.

IGNORE:
- facet-level correlations unless explicitly mapped to a Big Five domain,
- aggregate personality scores,
- personality pathology measures,
- non-Big-Five constructs.

Typical eligible instruments include:
- NEO Personality Inventory (NEO-PI / NEO-PI-R)
- NEO Five-Factor Inventory (NEO-FFI)
- Big Five Inventory (BFI)
- International Personality Item Pool (IPIP) inventories (IPIP-NEO)
- Ten-Item Personality Inventory (TIPI)
- Big Five Aspect Scales (BFAS)
- Goldberg Big Five Markers
- Five-Factor Personality Inventory (FFPI)

Examples:
- Self-report Goldberg Extraversion -> "es"
- Peer-rated Goldberg Extraversion -> "ep"
- Parent-rated Goldberg Extraversion -> "ep"
- Self-report Emotional Stability -> "ns"
- Spouse-rated Emotional Stability -> "np"

Use ONLY canonical variable labels in:
- "var1"
- "var2"

Study-specific operationalizations belong in:
- "desc1"
- "desc2"

# EXTRACTION RULES

Extract ONLY:
- eligible variables,
- eligible effect sizes,
- zero-order associations,
- explicitly reported statistics.

Eligible statistics may appear in:
- tables,
- text,
- figures,
- supplements embedded in the PDF.

Correlation matrices should be interpreted using standard row-column table structure.

A reported matrix cell constitutes an explicitly reported effect size when:
- both variables are identifiable from the matrix labels,
- and the numeric value appears in the table.

Extract correlations from:
- upper triangles,
- lower triangles,
- multitrait-multimethod matrices,
when explicitly displayed.

Do NOT infer missing matrix cells from symmetry.

Extract only directly reported statistics.
Do NOT:
- infer,
- estimate,
- derive,
- impute,
- reconstruct,
- combine,
- transform statistics into alternative effect size metrics.

Ignore:
- adjusted effects,
- regression coefficients,
- SEM paths,
- mediation effects,
- multilevel estimates,
- partial correlations,
- model-based estimates.

Treat analytically distinct groups as separate samples when:
- independent cohorts,
- subgroup analyses,
- separate waves,
- separate samples,
are reported independently.

Do not create separate samples for:
- robustness checks,
- sensitivity analyses,
- repeated mentions of the same participants.

# FIELD EXTRACTION RULES

For each effect size extract:
- sample size,
- reliability,
- instrument name,
when explicitly reported.

Use null when unavailable.

## Reliability hierarchy

Use reliabilities reported for the analyzed sample only.

Preferred hierarchy:
1. alpha
2. omega
3. icc
4. kappa
5. other

If multiple reliabilities are reported:
- select the highest-priority type,
- report others in notes.

## Record alignment rules

- Reorder variables alphabetically only in the final JSON output.
- All fields ending in "1" correspond to "var1".
- All fields ending in "2" correspond to "var2".

## Numeric constraints

Numeric fields must contain JSON numbers only.

Do NOT use:
- strings,
- percentages,
- ranges,
- textual qualifiers.

Use null when unavailable.

# SAMPLE METADATA

Extract when explicitly available:
- publication year,
- country,
- continent,
- language of respondents,
- publication type,
- percentage female,
- mean age,
- clinical status.

Use null when unavailable.

## Publication type coding

- 1 = journal article
- 2 = book
- 3 = thesis/dissertation
- 4 = proceedings/presentation
- 5 = other

## Clinical coding

- 0 = nonclinical sample
- 1 = clinical sample
- 2 = mixed clinical/nonclinical sample

# EVIDENCE RULES

Each extracted sample/group must include evidence for:
- sample identification,
- effect size source,
- sample size.

Each evidence entry must contain EXACTLY:
- "snippet"
- "page"
- "field"

Evidence snippets must:
- quote verbatim PDF text,
- support the referenced field,
- correspond to the same sample/group,
- avoid paraphrasing or reconstruction.

# SELF-ASSESS EXTRACTION CONFIDENCE

For EACH extracted sample/group, return an ``extraction_confidence`` object with one rating per high-level extraction target.

Required keys (all MUST be present):

- ``effect_sizes``: confidence in the per-pair effect-size values (the ``records`` array) for this sample — including correct var1/var2 mapping and correct numeric values.
- ``reliabilities``: confidence in the reliability fields within records (rel1, rel2, rel1_type, rel2_type) for this sample.
- ``metadata``: confidence in the sample metadata block (pubyear, country, continent, lang, pubtype, female, age, clinical) for this sample.

Each rating MUST be one of EXACTLY these three strings (lower-case):

- ``"high"``: the relevant numeric values / metadata are clearly stated in the paper, the table layout was unambiguous, no major OCR or interpretation issues, and the values were extracted directly without inference.
- ``"medium"``: values were extractable but the source had at least one of: ambiguous variable labels requiring careful mapping to canonical short names, partial OCR artifacts, suppressed correlation values, sparse metadata, or a non-trivial reconciliation between matrix labels and prose names.
- ``"low"``: substantial ambiguity remained — e.g. heavily damaged OCR, conflicting matrices, missing or unclear variable identities, large fractions of unreported cells, or significant guesswork required.

Calibration:

- If a category was not extractable at all (no reliability reported, no metadata reported) — still emit a rating (``"low"``) AND explain in ``notes``.
- The confidence rating reflects how reliably the values match the paper, NOT how complete or theoretically pleasing the dataset is.
- Be conservative: prefer ``"medium"`` over ``"high"`` when in doubt; prefer ``"low"`` over ``"medium"`` when in doubt.

# PAPER METADATA

In addition to the per-sample blocks, extract paper-level identifying metadata from the PDF front matter / header / footer. These fields identify the source paper itself and are used to generate citations for downstream datasets.

- title:    the full paper title verbatim. Required — fall back to the best-effort title if the front matter is mangled, but never emit an empty string.
- doi:      the DOI string (e.g. "10.1037/abc.0000123") if present anywhere in the front matter, header/footer, references, or copyright block. Use null if no DOI is reported.
- year:     publication year as a JSON integer (e.g. 2021). Use null if you cannot determine the year.
- authors:  the author list as an array of strings, one per author, in the order printed (e.g. ["Smith J", "Jones K"]). Use null only if no authors are listed.

# OUTPUT SCHEMA

{
  "paper_metadata": {
    "title":   "string",
    "doi":     null,
    "year":    null,
    "authors": null
  },

  "samples": [
    {
      "sample_id": "string",

      "records": [
        {
          "var1": null,
          "var2": null,
          "desc1": null,
          "desc2": null,
          "es": null,
          "type": null,
          "n": null,
          "rel1": null,
          "rel2": null,
          "rel1_type": null,
          "rel2_type": null,
          "instr1": null,
          "instr2": null
        }
      ],

      "pubyear": null,
      "country": null,
      "continent": null,
      "lang": null,
      "pubtype": null,
      "female": null,
      "age": null,
      "clinical": null,

      "extraction_confidence": {
        "effect_sizes":  "medium",
        "reliabilities": "medium",
        "metadata":      "medium"
      },

      "notes": ""
    }
  ],

  "evidence": [
    {
      "snippet": "",
      "page": null,
      "field": ""
    }
  ]

}

# VALIDATION RULES

Before returning output, validate:
- all effect sizes are within valid numeric ranges,
- all evidence fields reference existing JSON paths,
- every sample contains at least one effect size,
- all required fields are present,
- every sample has an extraction_confidence object with all three required keys,
- paper_metadata.title is populated,
- output is valid JSON parseable by json.loads.

Return:
- EXACTLY ONE top-level JSON object,
- JSON only,
- no markdown,
- no explanations,
- no comments,
- no code fences.
````

**Video gaming and body mass (Marker et al., 2022) — reconstructed, 25 September 2026**

````
# TASK

Extract eligible effect sizes and metadata from a PDF reporting an empirical study.

Return EXACTLY ONE valid JSON object following the required schema.

# DOMAIN CONFIGURATION

## EFFECT SIZES

Canonical effect size labels:
- "r" = Correlation
- "or" = Odds ratios

Extract ONLY these canonical effect sizes.

## VARIABLES

Canonical variable labels:
- **bm** — A measure of body mass such as (1) self-reported or objective body mass index (BMI), (2) percent fat mass through skinfold thickness, (3) bioelectrical impedance analysis, or (4) waist circumference.
- **vg** — A measure of video game use such as (1) time for video gaming absolute (i.e., hours/day), (2) subjective general intensity, or (3) frequency of gaming (i.e., number of sessions)
- **pa** — A measure of physical activitiy such as (1) length of exercise, (2) frequency of exercise, (3) exercise duration, or (4) exercise participation.

Extract ONLY these canonical variables.

IGNORE all other variables.

Map all eligible measures to ONE canonical variable category.

Use ONLY canonical variable labels in:
- "var1"
- "var2"

Study-specific operationalizations belong in:
- "desc1"
- "desc2"

# EXTRACTION RULES

Extract ONLY:
- eligible variables,
- eligible effect sizes,
- zero-order associations,
- explicitly reported statistics.

Eligible statistics may appear in:
- tables,
- text,
- figures,
- supplements embedded in the PDF.

Correlation matrices should be interpreted using standard row-column table structure.

A reported matrix cell constitutes an explicitly reported effect size when:
- both variables are identifiable from the matrix labels,
- and the numeric value appears in the table.

Extract correlations from:
- upper triangles,
- lower triangles,
- multitrait-multimethod matrices,
when explicitly displayed.

Do NOT infer missing matrix cells from symmetry.

Extract only directly reported statistics.
Do NOT:
- infer,
- estimate,
- derive,
- impute,
- reconstruct,
- combine,
- transform statistics into alternative effect size metrics.

Ignore:
- adjusted effects,
- regression coefficients,
- SEM paths,
- mediation effects,
- multilevel estimates,
- partial correlations,
- model-based estimates.

Treat analytically distinct groups as separate samples when:
- independent cohorts,
- subgroup analyses,
- separate waves,
- separate samples,
are reported independently.

Do not create separate samples for:
- robustness checks,
- sensitivity analyses,
- repeated mentions of the same participants.

# FIELD EXTRACTION RULES

For each effect size extract:
- sample size,
- reliability,
- instrument name,
when explicitly reported.

Use null when unavailable.

## Reliability hierarchy

Use reliabilities reported for the analyzed sample only.

Preferred hierarchy:
1. alpha
2. omega
3. icc
4. kappa
5. other

If multiple reliabilities are reported:
- select the highest-priority type,
- report others in notes.

## Record alignment rules

- Reorder variables alphabetically only in the final JSON output.
- All fields ending in "1" correspond to "var1".
- All fields ending in "2" correspond to "var2".

## Numeric constraints

Numeric fields must contain JSON numbers only.

Do NOT use:
- strings,
- percentages,
- ranges,
- textual qualifiers.

Use null when unavailable.

# SAMPLE METADATA

Extract when explicitly available:
- publication year,
- country,
- continent,
- language of respondents,
- publication type,
- percentage female,
- mean age,
- clinical status.

Use null when unavailable.

## Publication type coding

- 1 = journal article
- 2 = book
- 3 = thesis/dissertation
- 4 = proceedings/presentation
- 5 = other

## Clinical coding

- 0 = nonclinical sample
- 1 = clinical sample
- 2 = mixed clinical/nonclinical sample

# EVIDENCE RULES

Each extracted sample/group must include evidence for:
- sample identification,
- effect size source,
- sample size.

Each evidence entry must contain EXACTLY:
- "snippet"
- "page"
- "field"

Evidence snippets must:
- quote verbatim PDF text,
- support the referenced field,
- correspond to the same sample/group,
- avoid paraphrasing or reconstruction.

# SELF-ASSESS EXTRACTION CONFIDENCE

For EACH extracted sample/group, return an ``extraction_confidence`` object with one rating per high-level extraction target.

Required keys (all MUST be present):

- ``effect_sizes``: confidence in the per-pair effect-size values (the ``records`` array) for this sample — including correct var1/var2 mapping and correct numeric values.
- ``reliabilities``: confidence in the reliability fields within records (rel1, rel2, rel1_type, rel2_type) for this sample.
- ``metadata``: confidence in the sample metadata block (pubyear, country, continent, lang, pubtype, female, age, clinical) for this sample.

Each rating MUST be one of EXACTLY these three strings (lower-case):

- ``"high"``: the relevant numeric values / metadata are clearly stated in the paper, the table layout was unambiguous, no major OCR or interpretation issues, and the values were extracted directly without inference.
- ``"medium"``: values were extractable but the source had at least one of: ambiguous variable labels requiring careful mapping to canonical short names, partial OCR artifacts, suppressed correlation values, sparse metadata, or a non-trivial reconciliation between matrix labels and prose names.
- ``"low"``: substantial ambiguity remained — e.g. heavily damaged OCR, conflicting matrices, missing or unclear variable identities, large fractions of unreported cells, or significant guesswork required.

Calibration:

- If a category was not extractable at all (no reliability reported, no metadata reported) — still emit a rating (``"low"``) AND explain in ``notes``.
- The confidence rating reflects how reliably the values match the paper, NOT how complete or theoretically pleasing the dataset is.
- Be conservative: prefer ``"medium"`` over ``"high"`` when in doubt; prefer ``"low"`` over ``"medium"`` when in doubt.

# PAPER METADATA

In addition to the per-sample blocks, extract paper-level identifying metadata from the PDF front matter / header / footer. These fields identify the source paper itself and are used to generate citations for downstream datasets.

- title:    the full paper title verbatim. Required — fall back to the best-effort title if the front matter is mangled, but never emit an empty string.
- doi:      the DOI string (e.g. "10.1037/abc.0000123") if present anywhere in the front matter, header/footer, references, or copyright block. Use null if no DOI is reported.
- year:     publication year as a JSON integer (e.g. 2021). Use null if you cannot determine the year.
- authors:  the author list as an array of strings, one per author, in the order printed (e.g. ["Smith J", "Jones K"]). Use null only if no authors are listed.

# OUTPUT SCHEMA

{
  "paper_metadata": {
    "title":   "string",
    "doi":     null,
    "year":    null,
    "authors": null
  },

  "samples": [
    {
      "sample_id": "string",

      "records": [
        {
          "var1": null,
          "var2": null,
          "desc1": null,
          "desc2": null,
          "es": null,
          "type": null,
          "n": null,
          "rel1": null,
          "rel2": null,
          "rel1_type": null,
          "rel2_type": null,
          "instr1": null,
          "instr2": null
        }
      ],

      "pubyear": null,
      "country": null,
      "continent": null,
      "lang": null,
      "pubtype": null,
      "female": null,
      "age": null,
      "clinical": null,

      "extraction_confidence": {
        "effect_sizes":  "medium",
        "reliabilities": "medium",
        "metadata":      "medium"
      },

      "notes": ""
    }
  ],

  "evidence": [
    {
      "snippet": "",
      "page": null,
      "field": ""
    }
  ]

}

# VALIDATION RULES

Before returning output, validate:
- all effect sizes are within valid numeric ranges,
- all evidence fields reference existing JSON paths,
- every sample contains at least one effect size,
- all required fields are present,
- every sample has an extraction_confidence object with all three required keys,
- paper_metadata.title is populated,
- output is valid JSON parseable by json.loads.

Return:
- EXACTLY ONE top-level JSON object,
- JSON only,
- no markdown,
- no explanations,
- no comments,
- no code fences.
````

**This preset with the self–other parameters — rendered 26 October 2026**

````
# TASK

Extract eligible effect sizes and metadata from a PDF reporting an empirical study.

Return EXACTLY ONE valid JSON object following the required schema.

# DOMAIN CONFIGURATION

## EFFECT SIZES

Canonical effect size labels:
- "r" = Correlation

Extract ONLY these canonical effect sizes.

## VARIABLES

Canonical variable labels:
- "os" = Openness self-rating
- "cs" = Conscientiousness self-rating
- "es" = Extraversion self-rating
- "as" = Agreeableness self-rating
- "ns" = Neuroticism self-rating
- "op" = Openness other-rating
- "cp" = Conscientiousness other-rating
- "ep" = Extraversion other-rating
- "ap" = Agreeableness other-rating
- "np" = Neuroticism other-rating

Extract effect sizes ONLY for:
- Big Five personality traits,
- measured for the SAME target person,
- using self-ratings and/or other-ratings.

The correlated variables may:
- involve the same or different traits,
- involve the same or different raters,
provided both ratings refer to the same individual target.

Eligible correlations inlcude:
- self-self,
- self-other,
- other-other,
for:
- SAME trait,
- DIFFERENT traits.

Extract ALL eligible pairwise correlations between canonical variables appearing in the reported matrices, including:
- monotrait correlations,
- heterotrait correlations,
- monomethod correlations,
- heteromethod correlations.

Eligible Big Five domains are:
- Openness
- Conscientiousness
- Extraversion
- Agreeableness
- Neuroticism

Map:
- emotional stability
- emotional instability
to neuroticism.

Do NOT create a separate emotional stability variable.

Map all eligible measures to ONE canonical variable category.

Typical eligible informants include:
- peer,
- family,
- friend,
- acquaintance,
- spouses,
- romantic partner,
- sibling,
- parent,
- teacher,
- coworker,
- supervisor,
- observer,
- informant.

Code all non-self ratings as other-ratings.

IGNORE:
- facet-level correlations unless explicitly mapped to a Big Five domain,
- aggregate personality scores,
- personality pathology measures,
- non-Big-Five constructs.

Typical eligible instruments include:
- NEO Personality Inventory (NEO-PI / NEO-PI-R)
- NEO Five-Factor Inventory (NEO-FFI)
- Big Five Inventory (BFI)
- International Personality Item Pool (IPIP) inventories (IPIP-NEO)
- Ten-Item Personality Inventory (TIPI)
- Big Five Aspect Scales (BFAS)
- Goldberg Big Five Markers
- Five-Factor Personality Inventory (FFPI)

Examples:
- Self-report Goldberg Extraversion -> "es"
- Peer-rated Goldberg Extraversion -> "ep"
- Parent-rated Goldberg Extraversion -> "ep"
- Self-report Emotional Stability -> "ns"
- Spouse-rated Emotional Stability -> "np"

Use ONLY canonical variable labels in:
- "var1"
- "var2"

Study-specific operationalizations belong in:
- "desc1"
- "desc2"

# EXTRACTION RULES

Extract ONLY:
- eligible variables,
- eligible effect sizes,
- zero-order associations,
- explicitly reported statistics.

Eligible statistics may appear in:
- tables,
- text,
- figures,
- supplements embedded in the PDF.

Correlation matrices should be interpreted using standard row-column table structure.

A reported matrix cell constitutes an explicitly reported effect size when:
- both variables are identifiable from the matrix labels,
- and the numeric value appears in the table.

Extract correlations from:
- upper triangles,
- lower triangles,
- multitrait-multimethod matrices,
when explicitly displayed.

Do NOT infer missing matrix cells from symmetry.

Extract only directly reported statistics.
Do NOT:
- infer,
- estimate,
- derive,
- impute,
- reconstruct,
- combine,
- transform statistics into alternative effect size metrics.

Ignore:
- adjusted effects,
- regression coefficients,
- SEM paths,
- mediation effects,
- multilevel estimates,
- partial correlations,
- model-based estimates.

Treat analytically distinct groups as separate samples when:
- independent cohorts,
- subgroup analyses,
- separate waves,
- separate samples,
are reported independently.

Do not create separate samples for:
- robustness checks,
- sensitivity analyses,
- repeated mentions of the same participants.

# FIELD EXTRACTION RULES

For each effect size extract:
- sample size,
- reliability,
- instrument name,
when explicitly reported.

Use null when unavailable.

## Reliability hierarchy

Use reliabilities reported for the analyzed sample only.

Preferred hierarchy:
1. alpha
2. omega
3. icc
4. kappa
5. other

If multiple reliabilities are reported:
- select the highest-priority type,
- report others in notes.

## Record alignment rules

- Reorder variables alphabetically only in the final JSON output.
- All fields ending in "1" correspond to "var1".
- All fields ending in "2" correspond to "var2".

## Numeric constraints

Numeric fields must contain JSON numbers only.

Do NOT use:
- strings,
- percentages,
- ranges,
- textual qualifiers.

Use null when unavailable.

# SAMPLE METADATA

Extract when explicitly available:
- publication year,
- country,
- continent,
- language of respondents,
- publication type,
- percentage female,
- mean age,
- clinical status.

Use null when unavailable.

## Publication type coding

- 1 = journal article
- 2 = book
- 3 = thesis/dissertation
- 4 = proceedings/presentation
- 5 = other

## Clinical coding

- 0 = nonclinical sample
- 1 = clinical sample
- 2 = mixed clinical/nonclinical sample

# EVIDENCE RULES

Each extracted sample/group must include evidence for:
- sample identification,
- effect size source,
- sample size,
- every non-null sample metadata value (pubyear, country, lang, female, age, clinical), each with its own evidence entry with field "samples[i].<name>".

This includes values obtained through inference (for example lang from the country of recruitment, country from author affiliations, female computed from counts).
For an inferred value, quote verbatim the passage the inference rests on and state the inference in notes.
If no passage supports a value, set the value to null instead of reporting it without evidence.

Each evidence entry must contain EXACTLY:
- "snippet"
- "page"
- "field"

The "field" value MUST be a JSON path into the emitted structure, ALWAYS prefixed
with the sample index `samples[i]`, so each evidence entry maps back to its sample
(and, when it supports a specific pair, the `records[j]` index). Use these forms:
- samples[0].sample_id
- samples[0].records            (the effect-size table/source for that sample)
- samples[0].records[2].es      (a specific effect-size value)
- samples[0].records[0].rel1    (a specific reliability)
- samples[0].n
- samples[0].female
- samples[0].country
Never omit the `samples[i]` prefix — an unindexed field (e.g. just "records") cannot
be linked to the value it supports.

Evidence snippets must:
- quote verbatim PDF text,
- support the referenced field,
- correspond to the same sample/group,
- avoid paraphrasing or reconstruction.

# SELF-ASSESS EXTRACTION CONFIDENCE

For EACH extracted sample/group, return an ``extraction_confidence`` object with one rating per high-level extraction target.

Required keys (all MUST be present):

- ``effect_sizes``: confidence in the per-pair effect-size values (the ``records`` array) for this sample — including correct var1/var2 mapping and correct numeric values.
- ``reliabilities``: confidence in the reliability fields within records (rel1, rel2, rel1_type, rel2_type) for this sample.
- ``metadata``: confidence in the sample metadata block (pubyear, country, continent, lang, pubtype, female, age, clinical) for this sample.

Each rating MUST be one of EXACTLY these three strings (lower-case):

- ``"high"``: the relevant numeric values / metadata are clearly stated in the paper, the table layout was unambiguous, no major OCR or interpretation issues, and the values were extracted directly without inference.
- ``"medium"``: values were extractable but the source had at least one of: ambiguous variable labels requiring careful mapping to canonical short names, partial OCR artifacts, suppressed correlation values, sparse metadata, or a non-trivial reconciliation between matrix labels and prose names.
- ``"low"``: substantial ambiguity remained — e.g. heavily damaged OCR, conflicting matrices, missing or unclear variable identities, large fractions of unreported cells, or significant guesswork required.

Calibration:

- If a category was not extractable at all (no reliability reported, no metadata reported) — still emit a rating (``"low"``) AND explain in ``notes``.
- The confidence rating reflects how reliably the values match the paper, NOT how complete or theoretically pleasing the dataset is.
- Be conservative: prefer ``"medium"`` over ``"high"`` when in doubt; prefer ``"low"`` over ``"medium"`` when in doubt.

# PAPER METADATA

In addition to the per-sample blocks, extract paper-level identifying metadata from the PDF front matter / header / footer. These fields identify the source paper itself and are used to generate citations for downstream datasets.

- title:    the full paper title verbatim. Required — fall back to the best-effort title if the front matter is mangled, but never emit an empty string.
- doi:      the DOI string (e.g. "10.1037/abc.0000123") if present anywhere in the front matter, header/footer, references, or copyright block. Use null if no DOI is reported.
- year:     publication year as a JSON integer (e.g. 2021). Use null if you cannot determine the year.
- authors:  the author list as an array of strings, one per author, in the order printed (e.g. ["Smith J", "Jones K"]). Use null only if no authors are listed.
- journal:  the journal or venue name as printed. Use null if it is not shown.

# OUTPUT SCHEMA

{
  "paper_metadata": {
    "title":   "string",
    "doi":     null,
    "year":    null,
    "authors": null,
    "journal": null
  },

  "samples": [
    {
      "sample_id": "string",

      "records": [
        {
          "var1": null,
          "var2": null,
          "desc1": null,
          "desc2": null,
          "es": null,
          "type": null,
          "n": null,
          "rel1": null,
          "rel2": null,
          "rel1_type": null,
          "rel2_type": null,
          "instr1": null,
          "instr2": null
        }
      ],

      "pubyear": null,
      "country": null,
      "continent": null,
      "lang": null,
      "pubtype": null,
      "female": null,
      "age": null,
      "clinical": null,

      "extraction_confidence": {
        "effect_sizes":  "medium",
        "reliabilities": "medium",
        "metadata":      "medium"
      },

      "notes": ""
    }
  ],

  "evidence": [
    {
      "snippet": "",
      "page": null,
      "field": ""
    }
  ]

}

# VALIDATION RULES

Before returning output, validate:
- all effect sizes are within valid numeric ranges,
- all evidence fields reference existing JSON paths,
- every sample contains at least one effect size,
- all required fields are present,
- every sample has an extraction_confidence object with all three required keys,
- paper_metadata.title is populated,
- output is valid JSON parseable by json.loads.

Return:
- EXACTLY ONE top-level JSON object,
- JSON only,
- no markdown,
- no explanations,
- no comments,
- no code fences.
````

## Limitations

The prompt is fairly generic and might not work well for a MASEM with unusual eligibility rules, e.g. only longitudinal correlations or only specific informant pairs.

One model was tested — the most recent OpenAI model at the time of validation. The schema can be extended to further statistics (means, standard deviations) and study characteristics (education, assessment mode); however, none of those has been evaluated.

## Related work

- Schroeders, U., Gnambs, T., & Einsiedler, J. (2026). *MASEMiner – AI-assisted data extraction for meta-analytic structural equation modeling.* Earlier version as a preprint: [doi:10.31234/osf.io/dceqs_v1](https://doi.org/10.31234/osf.io/dceqs_v1).
- Evaluation materials — prompts, codebooks, human-coded reference databases, MASEMiner exports (JSON) and analysis scripts: Harvard Dataverse, DOI to be added on publication.
- Source code: [github.com/johanna-einsiedler/metalens](https://github.com/johanna-einsiedler/metalens) (engine) and [github.com/johanna-einsiedler/maseminer](https://github.com/johanna-einsiedler/maseminer) (local install); hosted at [metalens.tech/maseminer](https://metalens.tech/maseminer).
- Gnambs, T. (2013). The elusive general factor of personality: The acquaintance effect. *European Journal of Personality, 27*(5), 507–520. [doi:10.1002/per.1933](https://doi.org/10.1002/per.1933) — the development set.
- Marker, C., Gnambs, T., & Appel, M. (2022). Exploring the myth of the chubby gamer: A meta-analysis on sedentary video gaming and body mass. *Social Science & Medicine, 301*, 112325. [doi:10.1016/j.socscimed.2019.05.030](https://doi.org/10.1016/j.socscimed.2019.05.030) — the validation set.
- Schroeders, U., Breuer, J., Einsiedler, J., et al. (2026). *Seven guiding questions for reproducible AI-assisted research in the social and behavioral sciences.* PsyArXiv. [doi:10.31234/osf.io/xdtqh_v1](https://doi.org/10.31234/osf.io/xdtqh_v1) — why the export keeps the model output next to the human revisions.
- Companion preset: [MASEMiner — indirect information](/preset?id=masem-indirect).
- Funding: Deutsche Forschungsgemeinschaft (DFG), project 546292646, Priority Programme SPP 2317 META-REP; University of Copenhagen Lighthouse grant "Scaling Scientific Synthesis" (129339).
