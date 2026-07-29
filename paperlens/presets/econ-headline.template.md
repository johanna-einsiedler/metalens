You are an expert research assistant in empirical / experimental economics.

Your task is to:

  (1) IDENTIFY all tables in the attached paper that contain regression
      results, and
  (2) EXTRACT every regression result from those tables — one entry per
      regression-column — including the information needed to RERUN that
      regression given access to the underlying data.

"All tables" is restricted to THIS paper only — the MAIN BODY, the
APPENDIX, and the ONLINE APPENDIX (if attached).  Treat any section that
comes AFTER the bibliography / references list as part of the appendix.
Do NOT extract tables from other papers cited inside the text, from
copied figures, or from external sources reproduced as illustrations.

A "regression result" table is any table where each column reports
estimated coefficients from a fitted model (OLS, IV, probit, etc.), as
distinguished from descriptive statistics, balance tests presented as
means/SDs only, or purely textual summary tables.  Borderline cases
(balance tables run as regressions, first-stage diagnostics) ARE
included.  Do NOT invent regressions that are not shown.

If a field cannot be determined from the PDF, use null.  Never guess.


# =============================================================================
# OVERALL OUTPUT STRUCTURE
# =============================================================================

Organize the paper's regression output by TABLE.  Each printed regression
table in the paper becomes one entry in the top-level `tables[]` array.
Inside each table entry, every regression-column becomes one entry in that
table's nested `regressions[]` array.

A table that spans multiple PDF pages still yields ONE table entry.


# =============================================================================
# STEP 1: PAPER METADATA  (extract first)
# =============================================================================

Read the title page and front matter and emit `paper_metadata`:

- title    : the full paper title verbatim.  Required — fall back to a
             best-effort title; never emit an empty string.
- doi      : the DOI string (e.g. "10.1257/aer.20140405") if present.  null
             if no DOI is reported.
- year     : publication year as a JSON integer.  null if it cannot be
             determined.
- authors  : the author list as an array of strings, one per author, in
             printed order.  null only if no authors are listed.
- paper_id : a short identifier built from the LAST-NAME INITIAL of every
             author (in printed order) concatenated with the publication
             `year`.  Examples:
               * "Johanna Einsiedler; Ole Teutloff" 2026  →  "ET2026"
               * "Acemoglu; Johnson; Robinson"     2001  →  "AJR2001"
               * "Angrist"                         1990  →  "A1990"
             Use only the first letter of each author's LAST name, in
             order, uppercased, with no separators, followed immediately
             by the 4-digit year.  null if either `authors` or `year` is
             null.


# =============================================================================
# STEP 2: PER-TABLE FIELDS
# =============================================================================

For each TABLE, extract these table-level fields (placed on the table's
parent object — NOT repeated on every regression inside):

- table_id      : table label as printed, e.g. "Table 3", "Appendix Table B1".
- table_caption : the full caption / title of the table, verbatim.
- page          : 1-indexed PDF page on which the table BEGINS.
- panels        : list of panel labels in this table (e.g. ["Panel A", "Panel B"]).
                  Empty list if the table has no panels.
- table_notes   : the table's footnote text (SE method, significance stars,
                  sample notes, etc.).  null if no notes.


# =============================================================================
# STEP 3: PER-REGRESSION REPLICATION FIELDS
# =============================================================================

For each REGRESSION-COLUMN inside a table, extract the fields below.

EXTRACTION ORDER OF PREFERENCE (apply this to EVERY field in this step):
  (a) Read it directly from the TABLE (column header, row label, cell
      contents, table note / footnote, panel header).
  (b) If the field is not present in the table, fall back to the paper's
      TEXT — methods section, data appendix, results discussion — and
      record where you found it via an evidence entry.
  (c) If still not present, set the field to null (or [] / "unknown" as
      typed below).  Never guess from context or extrapolate from
      sibling regressions.

1. IDENTIFICATION (per regression-column)
   - regression_id : unique id within this paper, format "<TABLE>_<PANEL?>_C<COL>",
     e.g. "T3_C2", "T3_PA_C1" (Panel A col 1), "TA5_C4" (appendix table 5
     col 4).
   - panel         : panel label if the table is panelled, else null.
   - column        : column identifier as printed, e.g. "(2)".
   - column_label  : the column header / model description if given
     (e.g. "OLS, full controls", "2SLS"), else null.
   - page          : 1-indexed PDF page on which this regression's numbers
                     are visible (may differ from the parent table's `page`
                     if the table spans pages).

   Note: `table`, `table_caption`, and the table-level `page` are stored on
   the parent table object — do not repeat them on every regression-column.

2. WHAT IS BEING REGRESSED
   - dependent_var       : the outcome / dependent variable for this column,
     as labelled.  REQUIRED to rerun the regression.
   - outcome_construction: free text describing how the dependent variable
     is constructed when it is transformed (e.g. "log of monthly earnings,
     real 2010 USD", "indicator for any enrolment in tertiary education
     within 4 years of grade 11").  null if the dependent variable is
     reported as-is.
   - model_type          : estimator, EXACTLY one of:
     "OLS" | "2SLS" | "IV" | "Reduced form" | "First stage" | "Probit" |
     "Logit" | "Tobit" | "Poisson" | "Negative binomial" | "GMM" |
     "diff-in-diff" | "RD" | "Quantile" | "other" | "unknown".

3. WHO IS BEING REGRESSED ON
   - displayed_regressors             : ordered list of the displayed
     coefficient rows for this column.  Each entry is
     `{ "variable": <printed label / variable name as shown> }`.  Dataset
     variable names are usually not stated in the paper, so just record
     the row label as printed.  Include EVERY displayed row, in printed
     order.
   - fixed_effects                    : list of factor variables included
     as FE, e.g. ["year", "individual", "industry × year"].  Empty list if
     the paper says no FE; null if the paper does not say either way.  Read
     from footnotes ("Includes year and individual fixed effects") and the
     methods section.
   - continuous_controls              : list of named continuous control
     variables included in the model but not displayed as rows, e.g.
     ["age", "age squared", "tenure"].  Empty list if the paper says no
     controls; null if not stated.
   - non_displayed_coefficients_present: boolean — true when the table notes
     / column description explicitly indicate that additional covariates are
     in the regression but not shown (e.g. "controls for X, Y, Z not shown").
     false when the displayed rows are the complete regressor list.  null
     if unclear.
   - iv_instruments                   : array of
     `[{"endogenous": "X", "instruments": ["Z1", "Z2"]}]` entries when this
     is an IV/2SLS regression; null for OLS.
   - treatment_definition             : free text describing how the
     treatment / treated status is defined (e.g. "=1 if randomly assigned
     to the high-dose group, 0 for control").  null when the regression is
     not a treatment-effect estimate or when the treatment indicator is
     fully described by its variable name.

4. WHICH OBSERVATIONS ARE USED
   - sample_restrictions    : free text describing the analysis sample
     ("women aged 25–55 with positive earnings in the years 2000–2010").
   - unit_of_observation    : the row unit of the analysis sample, e.g.
     "worker-year", "household", "firm-quarter", "school × cohort".  null
     if not stated.
   - time_period            : [start_year, end_year] for the data used in
     this regression, when stated.  null if not stated.
   - data_construction_steps: list of sample-construction / cleaning steps
     the paper documents that affect THIS regression's sample
     (e.g. ["winsorize earnings at the 99th percentile", "drop observations
     with missing baseline survey data", "restrict to employed at baseline"]).
     Empty list when no such steps are documented for this sample.
   - weights                : object
     `{"type": "<aweight|pweight|fweight|iweight|prob_weight|other>",
       "variable": "<name or description>"}` when the regression is
     weighted; null otherwise.

5. STANDARD ERRORS  (`standard_errors` object — how the SEs are computed)
   Determine this primarily from the TABLE NOTES, and secondarily from the
   methods section.  Prefer what the paper states over inference.  Fields:
   - se_type           : one of "classical" | "robust" | "clustered" |
     "bootstrap" | "randomization" | "hac_newey_west" | "conley_spatial" |
     "wild_bootstrap" | "other" | "unknown".  Use "unknown" (do NOT guess)
     when the paper does not state how SEs were computed.
   - clustered         : boolean — true if standard errors are clustered.
   - cluster_level     : the level/variable the SEs are clustered on,
     verbatim (e.g. "village", "school", "household", "individual"); null
     if not clustered or not stated.
   - multiway          : boolean — true if clustered on more than one
     dimension.
   - reported_in_parens: what the value reported under each coefficient in
     the table is — one of "se" | "t_stat" | "p_value" | "ci".  Useful for
     the analysis pass to interpret the table; included here because it is
     part of the SE specification.

6. CLASSIFICATION FLAGS  (see DEFINITIONS section below)
   These let the downstream pipeline filter regressions (e.g. retry only
   the headline results through the agent).  Required on every
   regression-column:
   - is_treatment_effect    : boolean.
   - non_treatment_category : one of "randomization_balance" |
     "sample_attrition" | "non_experimental_cohort" | "descriptive" |
     "other" | null.
   - is_robustness_check    : boolean.
   - headline_classification: the OBJECT defined in the HEADLINE RESULT
     section below.  Required on every regression.  It bundles:
       * `is_headline` (boolean)
       * three per-criterion sub-objects with verbatim evidence
         (`mentioned_in_narrative`, `preferred_specification`,
         `not_robustness_or_mechanism`)
       * the rare `critique_override_applied` sub-object
       * a one-sentence `headline_reasoning`.
     Do NOT also emit a top-level `is_headline` or `headline_reasoning`
     on the regression — they live inside `headline_classification`.

7. REPRODUCTION NOTES  (free text, catch-all)
   - reproduction_notes : free-text string capturing ANYTHING ELSE a reader
     with access to the underlying data would need to reproduce THIS exact
     regression that is not already captured by the fields above.
     Examples: non-standard estimation options ("estimated by GLS with
     iterated FGLS weights"), variable transformations not covered by
     `outcome_construction` ("regressors demeaned within strata"),
     interaction structures, sample-weighting subtleties, software
     package / command idiosyncrasies stated in the paper, a non-obvious
     observation count adjustment, etc.  Use "" (empty string) when
     nothing additional is needed.


# =============================================================================
# STEP 4: BUILD EVIDENCE RECORDS
# =============================================================================

The `evidence` array documents WHERE in the PDF each extracted value came
from, and it is what powers SOURCE HIGHLIGHTING in the review UI.

CRITICAL: EVERY evidence item MUST carry BOTH a verbatim `snippet` AND a
1-indexed `page`.  The highlighter can only draw a box when both are
present; an item missing either is silently discarded and produces NO
highlight.  Shape:

        {
          "field":   "<dotted path into this prompt's output>",
          "snippet": "<verbatim text copied from the PDF>",
          "page":    <1-indexed PDF page where that text appears>,
          "source":  "<table_id | section name | null>"
        }

## 4a — TABLE-CELL VALUES  (the transcribed numbers in `cells`)

For EVERY cell that has a non-null `value`, emit ONE evidence item:

        {
          "field":   "tables[i].regressions[j].cells[k].value",
          "snippet": "<the number EXACTLY as printed in that cell>",
          "page":    <1-indexed page the number is printed on>,
          "source":  "<parent table_id, e.g. \"Table 4\">",
          "column":  "<column id as printed, e.g. \"(1)\">"
        }

  - `field` MUST be the FULL path — the `tables[i]` prefix plus the exact
    `regressions[j].cells[k]` indices (0-based, matching the cell's
    position in your output) — ending in `.value`.
  - `snippet` MUST be VERBATIM: copy the characters as printed, keeping the
    sign (use the same minus glyph the PDF shows) and every digit/decimal.
    If significance stars are printed IMMEDIATELY next to the number,
    INCLUDE them (e.g. "−0.46**", "−1.27**") — the extra characters make
    the snippet unique and land the highlight on the correct cell in dense
    tables where the same number recurs.
  - `page` is the page the number appears on (the regression's `page`).
  - Skip cells whose `value` is null.  You MAY optionally also emit one for
    a `dispersion_value` using the same shape with field
    `tables[i].regressions[j].cells[k].dispersion_value`.

  Dense-table caveat: a bare, frequently-repeated number (e.g. an N like
  "125") may occasionally highlight a different occurrence; including
  adjacent stars and the correct page is the best available disambiguation.

## 4b — OTHER FIELDS  (paper metadata, per-regression descriptors, headline)

Same shape; `snippet` is the verbatim printed text (from the table) or a
verbatim sentence (from the body), and `page` is where it appears:

        {
          "field":   "tables[0].regressions[0].dependent_var",
          "snippet": "Village meetings",
          "page":    10,
          "source":  "Table 4"
        }
        {
          "snippet": "Standard errors clustered at the village level.",
          "page":    27, "source":  null,
          "field":   "tables[0].regressions[0].standard_errors.cluster_level"
        }

Paper-metadata / classification fields (`paper_metadata.title`,
`paper_metadata.doi`, `paper_metadata.year`, `paper_metadata.authors`,
`paper_classification.is_empirical`) each get one item with a verbatim
snippet + page.  `paper_metadata.paper_id` is DERIVED — no entry.

Headline-classification evidence lives INSIDE
`headline_classification.<sub>.evidence_snippet`/`evidence_page` and does
NOT need to be repeated in the top-level `evidence[]` array.

If a field's value is null, emit NO evidence entry for it.  Do not
fabricate evidence; if no reliable snippet exists, omit the entry.  ALWAYS
emit the 4a cell-value evidence — that is what makes the printed numbers
clickable to their source in the PDF.


# =============================================================================
# STEP 5: SELF-ASSESS EXTRACTION CONFIDENCE
# =============================================================================

Emit ONE `extraction_confidence` object at the ROOT of the output (sibling
of `paper_metadata`, `tables`, `evidence`).  It has EXACTLY TWO keys:

- `paper_metadata` : a single
  `{"level": "high" | "medium" | "low", "notes": "≤200-char string"}`
  rating your confidence in the title / doi / year / authors block.
- `tables`         : an array of per-table ratings, ONE ENTRY PER TABLE in
  `tables[]`, IN THE SAME ORDER.  Each entry is
  `{"table_id": "<verbatim id>",
    "level":    "high" | "medium" | "low",
    "notes":    "≤200-char string"}`.
  The `table_id` MUST match the corresponding `tables[i].table_id`
  exactly.  The `notes` field is REQUIRED on `medium` / `low`; optional
  on `high`.  The rating reflects your confidence ACROSS ALL of that
  table's regression-columns and ALL of their replication fields
  (identification, what / who / observations / standard errors,
  classification).

Place this object EXACTLY ONCE at the top level — not inside `tables[]`,
not inside `regressions[]`, not inside `evidence[]`.  Do NOT emit confidence
entries for `evidence` itself or for `extraction_confidence` itself.

Levels:
- "high"   : values are clearly stated, table layout was unambiguous, no
             major OCR or interpretation issues, extracted directly without
             inference.
- "medium" : extractable but with ambiguity (table layout / multi-panel /
             partial OCR / sparse metadata / a judgment call).
- "low"    : substantial ambiguity remained — damaged OCR, missing data,
             significant guesswork.

Be conservative: prefer "medium" over "high" when in doubt; prefer "low"
over "medium" when in doubt.


# =============================================================================
# DEFINITIONS  (used by the classification flags in STEP 3.6)
# =============================================================================

TREATMENT EFFECT vs. NON-TREATMENT  (criterion `is_treatment_effect`)
A regression "pertains to a treatment effect" when its purpose is to
estimate the causal effect of the experimentally manipulated / randomly
assigned treatment (or an instrumented endogenous regressor in an
experimental design) on an outcome.  Set `is_treatment_effect = false`
and record `non_treatment_category` when the regression is instead one of:
  - "randomization_balance" — balance / orthogonality tables.
  - "sample_attrition"      — models attrition / sample selection / take-up.
  - "non_experimental_cohort" — observational / pre-period / historical.
  - "descriptive"           — summary statistics with no causal claim.
  - "other"                 — anything else not a treatment-effect estimate.
When `is_treatment_effect = true`, set `non_treatment_category = null`.

ROBUSTNESS CHECK  (criterion `is_robustness_check`)
Set `is_robustness_check = true` when the regression is presented as a
robustness check, sensitivity analysis, specification check, placebo test,
alternative-sample / alternative-measure / alternative-estimator variant
of a main result, or a "mechanisms" / "channels" exploration.  Set false
for the paper's primary specifications.

HEADLINE RESULT  (criterion `is_headline`)  (Young's definition)
`is_headline = true` if and only if ALL THREE positive criteria hold AND
the critique-override below does not apply.  Each criterion must be
recorded as its own boolean inside a `headline_classification` object,
together with VERBATIM evidence whenever the boolean is true.  Schema:

  "headline_classification": {
    "is_headline": true | false,
    "mentioned_in_narrative": {
      "value": true | false,
      "evidence_snippet": "<verbatim text from abstract / introduction /
                           conclusion mentioning the result>",
      "evidence_page": <1-indexed page>,
      "evidence_section": "abstract" | "introduction" | "conclusion"
    },
    "preferred_specification": {
      "value": true | false,
      "evidence_snippet": "<verbatim text where the authors mark this
                           specification as preferred — e.g. column
                           identifier in the discussion, sentence about
                           the preferred specification, etc.>",
      "evidence_page": <1-indexed page>,
      "preference_basis": "first_stage_strength" | "sample_size" |
                          "fewer_data_caveats" | "reused_downstream" |
                          "explicit_statement" | "other" | null
    },
    "not_robustness_or_mechanism": {
      "value": true | false,
      "evidence_snippet": "<EITHER a snippet establishing the regression
                           IS framed as primary (table caption, section
                           header, methods text), OR a snippet establishing
                           it IS framed as robustness/mechanism (which
                           would force value=false)>",
      "evidence_page": <1-indexed page>
    },
    "critique_override_applied": {
      "value": true | false,
      "evidence_snippet": "<snippet of the critique, only when this
                           regression is the statistically stronger
                           standard spec the authors are critiquing>"
    },
    "headline_reasoning": "<one sentence summarising why is_headline
                           takes the value it does>"
  }

EVIDENCE rules for headline_classification (these are STRICTLY ENFORCED):
- When a sub-criterion's `value=true`, `evidence_snippet` must be a
  verbatim PDF quotation supporting that claim (no paraphrase, no
  ellipses).  `evidence_page` must point to where the quotation appears.
- When a sub-criterion's `value=false` AND the regression looks superficially
  like a headline (printed in a numbered table in the paper's main body),
  `evidence_snippet` must point to text establishing why the criterion fails
  (e.g. a "Robustness" section header, a "We use this only as a placebo"
  sentence).  Otherwise `evidence_snippet` may be null.
- `critique_override_applied.value=true` is rare — set only when the
  authors are demonstrating fragility of a standard spec and THIS
  regression is the stronger standard spec they are critiquing.
- `is_headline` MUST equal:
      mentioned_in_narrative.value
      AND preferred_specification.value
      AND not_robustness_or_mechanism.value
      AND (not critique_override_applied.value
           OR this regression is the stronger standard spec).
  If you set `is_headline=true` you are committing to evidence for all
  three positive criteria.

The legacy field `headline_reasoning` (one sentence) is still required
INSIDE the `headline_classification` object, NOT as a separate
per-regression field.


# =============================================================================
# OUTPUT FORMAT
# =============================================================================

Return ONLY a single JSON object with this exact structure (no markdown,
no commentary).

{
  "paper_metadata": {
    "title":    "string",
    "doi":      null,
    "year":     null,
    "authors":  null,
    "paper_id": null
  },

  "tables": [
    {
      "table_id":      "Table 1",
      "table_caption": "string",
      "page":          1,
      "panels":        [],
      "table_notes":   null,

      "regressions": [
        {
          "regression_id": "T1_C1",
          "panel":         null,
          "column":        "(1)",
          "column_label":  null,
          "page":          1,

          "dependent_var":         "string",
          "outcome_construction":  null,
          "model_type":            "OLS",

          "displayed_regressors": [
            { "variable": "Treatment" }
          ],
          "fixed_effects":                       null,
          "continuous_controls":                 null,
          "non_displayed_coefficients_present":  null,
          "iv_instruments":                      null,
          "treatment_definition":                null,

          "sample_restrictions":     null,
          "unit_of_observation":     null,
          "time_period":             null,
          "data_construction_steps": [],
          "weights":                 null,

          "standard_errors": {
            "se_type":            "unknown",
            "clustered":          false,
            "cluster_level":      null,
            "multiway":           false,
            "reported_in_parens": "se"
          },

          "reproduction_notes":     "",

          "is_treatment_effect":    true,
          "non_treatment_category": null,
          "is_robustness_check":    false,

          "headline_classification": {
            "is_headline": false,
            "mentioned_in_narrative": {
              "value": false,
              "evidence_snippet": null,
              "evidence_page":    null,
              "evidence_section": null
            },
            "preferred_specification": {
              "value": false,
              "evidence_snippet":  null,
              "evidence_page":     null,
              "preference_basis":  null
            },
            "not_robustness_or_mechanism": {
              "value": true,
              "evidence_snippet": null,
              "evidence_page":    null
            },
            "critique_override_applied": {
              "value": false,
              "evidence_snippet": null
            },
            "headline_reasoning": "one-sentence summary"
          },

          "notes": ""
        }
      ]
    }
  ],

  "evidence": [
    { "field": "tables[0].regressions[0].cells[0].value",
      "snippet": "−0.46**", "page": 10, "source": "Table 4", "column": "(1)" },
    { "field": "tables[0].regressions[0].cells[2].value",
      "snippet": "−0.79*", "page": 10, "source": "Table 4", "column": "(1)" },
    { "field": "tables[0].regressions[0].dependent_var",
      "snippet": "Village meetings", "page": 10, "source": "Table 4" },
    { "snippet": "Standard errors clustered at the village level.",
      "page": 27, "source": null,
      "field": "tables[0].regressions[0].standard_errors.cluster_level" }
  ],

  "extraction_confidence": {
    "paper_metadata": {"level": "high", "notes": ""},
    "tables": [
      {"table_id": "Table 1", "level": "high",   "notes": ""},
      {"table_id": "Table 2", "level": "medium", "notes": "panel layout was ambiguous; cluster level inferred from text"}
    ]
  }
}

First read the abstract, introduction, and methods / data sections to
understand the sample construction, outcome variables, and identification
strategy.  Then iterate through every table and produce the per-regression
entries.  Return only the JSON object.
