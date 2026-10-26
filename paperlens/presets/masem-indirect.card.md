## Introduction

Meta-analytic structural equation modelling (MASEM) pools correlation matrices from many primary studies and fits a structural model to the pooled matrix. The inputs are the relevant correlations, the reliabilities of the measures and the characteristics of the sample of a set of primary studies.

This preset focuses on indirect information and extracts the measurement model — item-level factor loadings and factor correlations — from which a correlation matrix among subscales can be reconstructed. For directly reported effect sizes (correlations in a matrix or in the text), [a separate preset](/preset?id=masem-direct) exists.

Both presets also extract sample characteristics (sample size, percentage female, mean age, country, language) and the reliabilities of the measures, each with an evidence link into the PDF.

## How it extracts

The user names the **scale** (its name and abbreviation), the **number of items**, and the **item wording** in order. The item texts matter more than the numbers: papers renumber, reorder and paraphrase items, and the prompt instructs the model to align loadings by wording first and by printed item number second.

For each distinct sample or group with its own loading table, the model extracts exactly one factor solution: the item-level loadings including cross-loadings and negative loadings as printed, the latent correlations between the retained factors as unique off-diagonal pairs, and the sample and analysis details — country, language, sample size, percentage female, mean age, whether the analysis was exploratory or confirmatory, the estimation method, and the number of retained factors.

Most of the prompt is decision rules, because factor-analytic papers rarely report one clean table. The rules say which solution to take when a paper reports several (first-order solutions before hierarchical or bifactor ones; within a model type, the solution with the most retained scale-relevant factors; author preference does not override this), how to treat multiple samples (never merged, never averaged), how to assign a loading to a factor (by printed column position, not by theory or boldface), what to do with orthogonal rotations (factor correlations set to zero), with suppressed cells (null, never zero), and how to tell a latent factor correlation from an observed subscale correlation (only the former is extracted). Factors are relabelled F1…Fn by printed position; the paper's own labels go into the notes.

The template adds the same four MASEMiner requirements as the direct-information preset: no imputation of unreported values, a fixed JSON schema, a three-level confidence rating per target (factor loadings, factor correlations, metadata) per sample, and an evidence snippet with page for every value, used to highlight the passage in review. In this preset the loading table is cited as a block, and a click on a loading in the review screen is located on the page using the item's wording as an anchor.

## Verification

The preset was developed on one factor-analytic meta-analysis and validated on a second that the authors did not use while developing the prompt; ten randomly drawn papers were used from each, very long full texts excluded from the development set to keep costs down. The model's output was compared against a gold-standard database: the values coded in the published meta-analysis, re-checked against the primary publications for the study. Further, we conducted an additional validation study where, for each dataset, (1) two coders recorded the values from the PDFs only and (2) two coders were presented with the validation interface and tasked to verify the extractions. Table 1 and Table 2 compare the model-only extractions with those corrected by the coders through MASEMiner; Table 3 compares the coders' performance with and without MASEMiner. The metrics (sensitivity, precision, Jaccard index, omissions, over-extractions, wrong values) are defined on the [direct-information card](/preset?id=masem-direct); a wrong value counts double, as a false negative and as a false positive. The model was GPT-5.6 (`gpt-5.6-sol`, OpenAI, September 2026). The tables give the unverified model output; in parentheses, the output after the coders of the verification study had reviewed and corrected it in the review screen, averaged over those coders.

The validation runs used the prompt as rendered by the earlier MASEMiner application in September 2026: its template of 7 June 2026 with the scale, item and factor parameters (the exact texts are in the batch exports among the evaluation materials). This preset's prompt asks for the same values in a different shape: factor loadings as rows of item, factor and loading, and factor correlations as rows naming the two factors, instead of keyed values such as `F1.2` and `R1.2`; and the paragraph mapping the paper's factor labels to F1…Fn is fixed text rather than a parameter. The values extracted are the same.

**Table 1 · Development set** — Toronto Alexithymia Scale (TAS-20), Schroeders et al. (2022), k = 10 papers.

| Target | n | SEN | PRE | JAC | OMIT | OVER | WRONG |
|:--|--:|--:|--:|--:|--:|--:|--:|
| Factor loadings | 980 | 1.00 (.98) | 1.00 (.99) | 1.00 (.97) | 0 (10) | 0 (0) | 1 (8) |
| Factor correlations | 66 | .91 (.88) | 1.00 (.99) | .91 (.87) | 6 (7.5) | 0 (0) | 0 (0.5) |
| Mean age | 12 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Country | 14 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Language | 14 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Sample size | 14 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| % female | 14 | .86 (.93) | 1.00 (1.00) | .86 (.93) | 2 (1) | 0 (0) | 0 (0) |
| EFA / CFA | 14 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Number of factors | 14 | 1.00 (.93) | 1.00 (.93) | 1.00 (.88) | 0 (0) | 0 (0) | 0 (1) |

**Table 2 · Validation set** — Need for Cognition Scale (NCS-18), Schroeders et al. (2024), k = 10 papers.

| Target | n | SEN | PRE | JAC | OMIT | OVER | WRONG |
|:--|--:|--:|--:|--:|--:|--:|--:|
| Factor loadings | 324 | .92 (.97) | .98 (1.00) | .90 (.97) | 18 (9) | 0 (0) | 7 (1) |
| Factor correlations | 11 | .82 (.86) | 1.00 (1.00) | .82 (.86) | 2 (1.5) | 0 (0) | 0 (0) |
| Mean age | 6 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Country | 11 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Language | 11 | .91 (.91) | 1.00 (1.00) | .91 (.91) | 1 (1) | 0 (0) | 0 (0) |
| Sample size | 11 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| % female | 8 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| EFA / CFA | 11 | 1.00 (1.00) | 1.00 (1.00) | 1.00 (1.00) | 0 (0) | 0 (0) | 0 (0) |
| Number of factors | 11 | .91 (.95) | .91 (.95) | .83 (.92) | 0 (0) | 0 (0) | 1 (0.5) |

*n* is the number of reference values; EFA / CFA is whether the analysis was exploratory or confirmatory.

In the development set all 980 loadings were recovered, with a single wrong value — a typographical error of the source table, reproduced. Six factor correlations from two publications were not extracted because the model took the reported values for correlations between subscale sum scores, which the prompt excludes by rule; that distinction is the hard part for a person too. In the validation set every deviation traces to two publications: for one the model extracted only two factors of a three-factor solution (the 18 omitted loadings, the two omitted correlations and the wrong number of factors); in the other, three items went to a neighbouring factor and one loading had the wrong sign (the seven wrong values). Across the four use cases, misassignments to a wrong factor concerned 6 of 2,397 target values. Publications with cleanly typeset tables were extracted without error.

**Table 3 · Coders with and without MASEMiner** — the verification study: four student coders trained in meta-analytic coding; in each use case some coded the papers conventionally into a coding sheet, the others reviewed and corrected the MASEMiner output in the review screen. Time is the mean per publication; the MASEMiner time includes the automated extraction. Accuracy is for the factor loadings, against the reference database.

| Use case | Conventional coding | MASEMiner + review | Time saved | Loadings, conventional (SEN / PRE) | Loadings, MASEMiner + review (SEN / PRE) |
|:--|--:|--:|--:|--:|--:|
| TAS-20 | 13.1 min | 9.5 min (0.8 extraction) | 27 % | .99 / .99 | .98 / .99 |
| NCS-18 | 16.3 min | 6.9 min (0.6 extraction) | 58 % | .97 / .79 | .97 / 1.00 |

Reviewing the model's output took a quarter to a half less time than coding by hand, at comparable accuracy; for the NCS-18 it was also more precise, because coders working by hand transcribed loadings the reference database does not carry. Over all four use cases of the study the saving was 42 %.

The figures below are read live from the public datasets built with this preset on this server, as coders verify them.

### The prompts

Folded below: the prompt of the TAS-20 runs exactly as sent in September 2026 (from the application's batch export); the scale configuration of the NCS-18 runs, the only part in which that prompt differs; and, for comparison, this preset's prompt rendered with the TAS-20 parameters on 26 October 2026. The prompt shown under "How it extracts" is the live one with the preset's own defaults. At run time the application appended one sentence announcing the page images and asking for sequential PDF page numbers in the evidence.

**TAS-20 (Schroeders et al., 2022) — as sent, September 2026**

````
You are an expert data-extraction system for psychometric factor-analytic studies.
Extract factor-analytic results for a psychological instrument from an academic PDF paper.
Return EXACTLY ONE valid JSON object and no additional text.

# =============================================================================
# VARIABLE SCALE CONFIGURATION
# =============================================================================

# Replace ONLY this section when adapting the prompt to another scale or extraction target.
# Keep the extraction logic and output schema below unchanged unless the data model changes.

[scale_name]: Toronto Alexithymia Scale (TAS-20)

[n_items]: 20

[item_labels]: each line gives one item, starting with item number
1: I am often confused about what emotion I am feeling.
2: It is difficult for me to find the right words for my feelings.
3: I have physical sensations that even doctors don’t understand.
4: I am able to describe my feelings easily. *
5: I prefer to analyse problems rather than just describe them. *
6: When I am upset, I don’t know if I am sad, frightened or angry.
7: I am often puzzled by sensations in my body.
8: I prefer to just let things happen rather than to understand why they turned out that way.
9: I have feelings that I can't quite identify.
10: Being in touch with my feelings is essential. *
11: I find it hard to describe how I feel about people.
12: People tell me to describe my feelings more.
13: I don't know what's going on inside me.
14: I often don't know why I'm angry.
15: I prefer talking to people about their daily activities rather than their feelings.
16: I prefer to watch “light” entertainment shows rather than psychological dramas.
17: It is difficult for me to reveal my innermost feeling, even to close friends.
18: I can feel close to someone, even in moments of silence. *
19: I find examination of my feelings useful in solving personal problems. *
20: Looking for hidden meanings in movies or plays distracts from their enjoyment.

[factor_key_mapping]:
Map factor labels in the paper to JSON factor keys by factor position, number, column, row group, or horizontal block.
Examples:
- F-I, FI, Factor I, Factor 1, Component 1 -> F1
- F-II, FII, Factor II, Factor 2, Component 2 -> F2
- F-III, FIII, Factor III, Factor 3, Component 3 -> F3
- continue analogously for additional retained scale-relevant factors.

The JSON factor keys represent the factor/component columns, row groups, blocks, or factor numbers in the selected solution.
They do not necessarily represent fixed construct meanings across papers.
Use paper-defined factor labels for interpretation and document the mapping in notes when useful.

# =============================================================================
# PRIMARY TASK
# =============================================================================

For EACH distinct sample/group in the paper, extract:
1. item-level factor loadings, including all reported cross-loadings;
2. latent factor correlations as unique off-diagonal factor pairs;
3. study/sample metadata;
4. evidence snippets that support the extracted values and decisions.

The final output MUST follow the JSON schema below.

# =============================================================================
# CORE PRINCIPLES
# =============================================================================

## 1. Extract only explicitly reported information

Only extract values explicitly supported by the PDF.
Do NOT infer, derive, impute, reconstruct, average, transform, or fill missing values from theory, prior literature, simple structure, symmetry, item wording, or expected factor assignment.

Use null when a value is absent, suppressed, unreadable, or not explicitly reported, except where this prompt explicitly allows another value.
Interpret OCR artifacts conservatively.
Do not repair corrupted numeric values unless the intended value is unambiguous from the local table context.

## 2. Extract exactly one eligible factor solution per sample/group

For each sample/group, extract exactly ONE eligible factor solution.
Do NOT merge:
- samples/groups;
- EFA and CFA solutions;
- alternative dimensional solutions;
- alternative model blocks;
- different language versions;
- different populations;
- separate informants, waves, sites, or subgroups.

If a pooled solution is explicitly reported by the paper, it may be treated as its own sample.
Do not average or pool separated groups yourself.

## 3. Specific rules override general rules

Resolve conflicts in this order:
1. task-specific instructions in the scale configuration;
2. eligibility rules for the target scale/model;
3. model-type preference rules;
4. highest-dimensional solution rule within the same model type;
5. author labels such as final, retained, preferred, or primary;
6. table completeness and readability.

For loadings:
- printed factor/component column, row group, or block position overrides theoretical item assignment, boldface, salience, largest loading, subscale membership, and auto-detected table grids;
- item wording overrides printed item number when they conflict;
- printed item identifiers and item wording override row order, page breaks, and sequential numeric-token order;
- reported negative loadings and cross-loadings are valid values.

# =============================================================================
# STEP 1: IDENTIFY ELIGIBLE FACTOR SOLUTIONS
# =============================================================================

A factor solution is eligible only if:
- it analyzes [scale_name] or an explicitly equivalent version of the scale;
- the analyzed items belong to [scale_name];
- it reports item-level factor loadings, item parameters, or standardized item-factor relations;
- the reported factors are part of the scale model being analyzed.

Prefer ordinary first-order factor solutions when available and suitable.
Other psychometrically meaningful scale models may be extracted if they are the best eligible model for the task and report sufficient item-level parameters, including:
- higher-order or hierarchical CFA models;
- bifactor or general/specific-factor models;
- nested-factor models;
- Schmid-Leiman solutions;
- method-factor models.

Ignore results that report only:
- subscale correlations;
- fit indices;
- reliability coefficients;
- item-total correlations;
- factor-score correlations;
- structural paths or SEM relations to external variables;
- pooled analyses with other instruments;
- external outcomes, predictors, covariates, or non-scale factors.

# =============================================================================
# STEP 2: IDENTIFY DISTINCT SAMPLES/GROUPS
# =============================================================================

Create one sample object for every visibly distinct analyzed sample/group for which the selected solution reports item-level loadings.

Treat as distinct samples/groups when loadings are reported separately for:
- sample 1 / sample 2;
- total sample and subgroups;
- calibration / validation samples;
- male / female or other demographic groups;
- clinical / control groups;
- age groups;
- countries, sites, centers, or language versions;
- informants/raters;
- time points or waves if analyzed separately.

Distinct samples/groups may be separated by text sections, captions, table panels, row blocks, column blocks, model rows, group labels, or separate tables.

If total-sample and subgroup loadings are both reported, extract each separately.
If loadings are reported only for some groups, extract only those groups and explain in notes.
Do NOT treat alternative dimensional solutions or alternative model blocks as samples.

Use explicit group labels as sample_id when possible.
If labels are unclear, use sample1, sample2, etc., and explain the ambiguity in notes.

# =============================================================================
# STEP 3: SELECT THE EXTRACTION TARGET PER SAMPLE
# =============================================================================

First restrict candidates to eligible scale models with item-level parameters for [scale_name].

If multiple eligible model types exist for the same sample/group and the scale configuration does not specify another target, use this hierarchy:
1. ordinary first-order EFA;
2. ordinary first-order CFA;
3. higher-order or hierarchical model with reported item loadings on first-order factors;
4. bifactor or general/specific-factor model;
5. nested-factor or Schmid-Leiman solution;
6. method-factor model.

Override this hierarchy only when:
- the scale configuration explicitly targets another model type;
- the preferred model type lacks sufficient item-level parameters;
- the preferred model is not actually a scale model for [scale_name].

Do NOT override the hierarchy merely because authors describe a lower-priority model as final, preferred, retained, primary, or better fitting.

Within the same general model type for the same sample/group:
- choose the solution with the largest number of retained scale-relevant factors;
- do not count external variables, covariates, outcomes, predictors, or non-scale factors;
- do not select a lower-dimensional solution merely because it is clearer, first, more complete, more salient, or author-preferred.

Tie-breakers within the same model type:
1. largest number of retained scale-relevant factors;
2. author-labelled final/retained/preferred/primary solution;
3. oblique over orthogonal rotation for EFA;
4. most complete item-level loading table.

Document ignored eligible alternatives in notes when they could affect interpretation.

# =============================================================================
# STEP 4: DETERMINE FACTOR STRUCTURE
# =============================================================================

For the selected solution, determine:
- model type;
- number of retained scale-relevant factors;
- factor/component numbering and order;
- factor labels and their mapping to F keys;
- model or dimensional block;
- sample/group block;
- item identifiers and item wording;
- whether the table continues across pages.

Use loading tables, captions, headers, notes, appendices, model specification tables, item assignment tables, figures, path diagrams, and relevant text.

Use auxiliary item-assignment information only if it clearly refers to the same sample/group, same solution, same number of factors, and same scale version.
Otherwise ignore it.

# =============================================================================
# STEP 5: EXTRACT FACTOR LOADINGS
# =============================================================================

## 5.1 Values to extract

For each factor-item cell, record the reported loading or standardized item-factor parameter as a JSON number.
Negative loadings are valid and MUST be recorded as negative numbers.
Recognize hyphen-minus, unicode minus, and en dash before digits or decimal points as negative signs.
A standalone dash, blank, long dash, or "--" without digits means missing/unreported and must be null.

If a loading is absent, blank, suppressed, unreadable, or explicitly not reported, record null.
Do not treat omitted loadings as zero unless the paper explicitly says they are zero.

## 5.2 Table-layout handling

Before extracting numbers, identify the printed layout. Common layouts include:
- items as rows and factors/components as columns;
- multiple sample/group blocks in separate columns, panels, row blocks, or tables;
- items grouped under factor headings;
- horizontal factor blocks with item-loading lists per factor;
- side-by-side dimensional solutions such as 2-, 3-, 4-, or 5-factor blocks;
- side-by-side or stacked model blocks;
- continued tables across consecutive PDF pages;
- mixed layouts.

For every layout:
1. determine whether the loading table continues across pages;
2. merge continued table parts into one logical table when they share table number, caption, headers, item groups, factor labels, or continuation markers;
3. identify the sample/group block;
4. identify the eligible model/dimensional block;
5. identify factor/component columns, row groups, or horizontal blocks;
6. identify each item by printed item number and/or wording;
7. assign each numeric loading to the correct sample, factor, and item.

Do not rely on an auto-detected rectangular grid if rows are grouped, item wording wraps, factor columns are shifted, item identifiers are missing, values appear sequentially, or the grid conflicts with the visible PDF.
When the grid conflicts with the visible table, read the printed table visually.

Do NOT interpret sample/group labels as factor labels.
Do NOT interpret factor labels as sample/group labels.
Do NOT treat alternative model or dimensional blocks as samples.
Do NOT merge factor columns across model or dimensional blocks.

## 5.3 Target-block rule

If a table contains alternative model blocks or dimensional blocks, select exactly one block according to Step 3 and extract loadings only from that block.
Do not combine lower-dimensional and higher-dimensional blocks.
Do not treat columns from an alternative block as additional factors.

If the selected model includes general, specific, nested, method, higher-order, or Schmid-Leiman factors as scale-relevant factors, extract their columns according to printed position and labels.
Document the mapping in notes.

Ignore non-loading columns such as:
- congruence coefficients;
- communalities;
- uniquenesses;
- residual variances;
- item-total correlations;
- reliability coefficients;
- fit statistics;
- standard errors;
- p values.

## 5.4 Row-completeness rule

For every printed item row in the selected target block:
- first identify the item number or item wording;
- then scan across ALL selected factor-loading/component-loading columns in that row;
- enter every printed numeric loading into exactly one corresponding item-factor cell.

Do not drop loadings because they are small, negative, non-bold, nonsalient, secondary, cross-loadings, or appear under a different row-group heading.
A row-group heading identifies intended/main grouping only; it does not license ignoring other printed loading columns in that row.

If only one loading per item is printed or non-primary loadings are explicitly suppressed:
- record the visible loading;
- set omitted loadings for that item to null;
- mention any suppression threshold in notes.

## 5.5 Column-position assignment rule

Assign values strictly by the printed factor/component column, row group, or horizontal factor block in which the value appears.

Column/block position overrides:
- theoretical item assignment;
- row-group membership;
- subscale membership;
- largest loading;
- boldface, italics, salience markers, or footnotes;
- expected simple structure;
- auto-detected grids when they conflict with the visible table.

Example:
If the row is "Item 2*" and the values appear under Component 1, Component 2, Component 3 as:
.807   .129   -.010
then output:
"F1.2": 0.807
"F2.2": 0.129
"F3.2": -0.010

Do NOT move .807 to F2 merely because Item 2 theoretically belongs to Factor 2.

## 5.6 Item alignment

Assign loadings using this priority:
1. matching printed item number and printed item wording;
2. item wording when printed item number conflicts with [item_labels];
3. item wording when numbers are duplicated, inconsistent, or missing;
4. printed item number when wording is unavailable;
5. row order only when all items are explicitly listed in numerical order and no item identifiers/wording are available.

For continued tables, assign rows by printed item identifiers or wording, not by page-local row position.
If neither item number nor wording can be identified confidently, set affected cells to null and explain in notes.

## 5.7 Factor alignment

Map factor labels to F keys using [factor_key_mapping].
If factor names are given, follow the paper’s printed factor order, column order, row-group order, or block order unless the paper explicitly defines another numbering.

Do not assign external variables, outcomes, covariates, predictors, or non-scale factors to F keys.

## 5.8 Empty-factor rule

After extraction, remove any factor that contains no explicitly reported nonzero loading.
Remove all loading keys and factor-correlation keys involving that factor.
Set nfac to the number of retained scale-relevant factors after this removal.
Document the removal in notes and evidence when relevant.

# =============================================================================
# STEP 6: EXTRACT FACTOR CORRELATIONS
# =============================================================================

Extract ONLY latent factor correlations for the selected eligible solution and current sample/group.

Ignore:
- observed subscale correlations;
- summed-score correlations;
- factor-score correlations;
- external-variable correlations;
- reliability coefficients;
- correlations from non-selected models or alternative dimensional blocks.

Output only unique off-diagonal correlations among retained scale-relevant factors after empty-factor removal.
Use ascending pair keys only:
- R1.2
- R1.3
- R2.3
- R1.4
- R2.4
- R3.4
- and so on.

Do NOT output:
- diagonal values;
- duplicate symmetric pairs;
- full square matrices;
- keys such as R2.1;
- pairs involving removed or nonexistent factors.

For existing retained factors:
- explicitly reported latent factor correlations must be numeric;
- unreported latent factor correlations must be null.

Orthogonal EFA override:
If the selected EFA solution uses orthogonal rotation, set all off-diagonal factor correlations among retained factors to 0.
Orthogonal rotations include varimax, quartimax, equamax, orthomax, parsimax, or any rotation explicitly described as orthogonal.
For Procrustes, apply the override only if the paper explicitly states that it is orthogonal.
This override applies only to EFA.
For CFA, bifactor, higher-order, nested-factor, Schmid-Leiman, or method-factor models, extract only reported latent factor correlations belonging to the selected model.

# =============================================================================
# STEP 7: EXTRACT METADATA
# =============================================================================

Extract metadata for the current sample/group only.
Use null when unavailable.
Numeric fields must be JSON numbers, not strings.
Coded fields must be integers.

Required sample-level fields:

country:
- participant country as open text or null.
- Allowed inference: explicit recruitment location or clearly stated sample origin.
- Use author affiliations only when participant country is not stated and affiliations are nearly all from one country. This is an allowed exception.

lang:
- language of administered scale as open text or null.
- Allowed inference: stated translation/version; for English-speaking countries with no translation mentioned, use English.
- For inseparable pooled samples with multiple versions, use multiple.

n:
- sample size for this sample/group only.

female:
- percentage female as a JSON number from 0 to 100 or null.
- Round percentages to one decimal place.
- If the paper reports counts, compute the percentage and round to one decimal place.
- Use 100.0 for female-only samples and 0.0 for male-only samples.

age:
- mean age in years as a JSON number or null.
- Round mean age to one decimal place.
- If subgroup means and ns are available, compute the weighted mean, round to one decimal place, and mention this in notes.
- Do not use only ranges, medians, or categories.

nfac:
- number of retained eligible scale-relevant factors after empty-factor removal.

cfa:
0 = exploratory factor analysis or EFA-based model
1 = confirmatory factor analysis or CFA-based model
null = unclear

met:
1 = principal component analysis (PCA)
2 = principal axis factoring (PAF)
3 = maximum likelihood (ML)
4 = other, such as ULS, WLSMV, GLS, Bayesian
null = unknown
If met = 4, specify the method in notes.

# =============================================================================
# STEP 8: BUILD EVIDENCE RECORDS
# =============================================================================

The evidence array documents exact PDF text supporting extracted values and decisions.
Each evidence entry MUST contain EXACTLY these four keys:
{
  "snippet": "...",
  "page": 1,
  "source": "Table 1",
  "field": "samples[0].factor_loadings"
}

No additional evidence keys are allowed.

snippet:
- quote verbatim PDF text where possible;
- do not paraphrase, summarize, or invent text;
- prefer table captions, headers, panel labels, matrix rows, representative item rows, model block headers, dimensional block headers, or one supporting sentence.

page:
- 1-indexed PDF page where the supporting value is visible;
- use PDF page number, not journal page number;
- for numeric values, use the page where the numbers are visible, not merely cited;
- for multi-page tables, include evidence for each page from which values were extracted.

source:
- table/figure identifier such as "Table 1", "Figure 2", "Appendix Table B1";
- use null for body text.

field:
- JSON-path-like reference matching the emitted JSON structure exactly.
- Examples:
  - samples[0].sample_id
  - samples[0].factor_loadings
  - samples[0].factor_loadings.F1.5
  - samples[0].factor_correlations
  - samples[0].factor_correlations.R1.2
  - samples[0].n
  - samples[0].age
  - samples[0].country
  - samples[0].cfa
  - samples[0].met

Minimum evidence per extracted sample/group:
1. sample/group identification;
2. selected factor solution or model/dimensional block;
3. factor loadings source;
4. factor correlations source if factor correlations are extracted or explicitly unavailable;
5. sample size n;
6. factor analysis type cfa;
7. extraction/estimation method met if reported;
8. language lang if reported;
9. country country if reported.

For factor loadings:
- every sample must include at least one evidence entry with field "samples[i].factor_loadings";
- the evidence page must contain the selected loading values or representative loading rows;
- if the table continues across pages, include one factor-loading evidence entry for each page used.

For special decisions, include evidence when possible and document in notes:
- multiple candidate solutions;
- selected highest-dimensional block;
- ignored alternative model blocks;
- side-by-side sample/group blocks;
- continued multi-page loading tables;
- horizontal factor blocks;
- orthogonal rotation override;
- empty-factor removal;
- cross-loading extraction;
- negative loading extraction;
- item-number/item-wording conflicts;
- auto-detected grid conflicts;
- paper-specific factor-label mapping.

Evidence snippets must support the same sample/group, same selected solution, same block, same page, and same field.
Do not fabricate evidence.
If no reliable supporting snippet exists, omit the evidence entry and explain the limitation in notes.

# =============================================================================
# STEP 9: SELF-ASSESS EXTRACTION CONFIDENCE
# =============================================================================

For EACH extracted sample/group, return an ``extraction_confidence`` object with one rating per high-level extraction target.

Required keys (all MUST be present):

- ``factor_loadings``: confidence in the item-level factor-loading matrix for this sample.
- ``factor_correlations``: confidence in the unique off-diagonal factor-correlation values for this sample.
- ``metadata``: confidence in the study/sample metadata block (country, n, female, age, cfa, met, nfac, lang) for this sample.

Each rating MUST be one of EXACTLY these three strings (lower-case):

- ``"high"``: the relevant numeric values / metadata are clearly stated in the paper, the table layout was unambiguous, no major OCR or interpretation issues, and the values were extracted directly without inference.
- ``"medium"``: values were extractable but the source had at least one of: ambiguous table layout, mixed-model/multi-block layout requiring careful block selection, partial OCR artifacts, suppressed cross-loadings, sparse metadata, or a non-trivial item-wording-vs-item-number reconciliation.
- ``"low"``: substantial ambiguity remained — e.g. heavily damaged OCR, conflicting model blocks, missing or unclear factor structure, large fractions of unreported cells, or significant guesswork required.

Calibration:

- If a category was not extractable at all (no table, no metadata reported) — still emit a rating (``"low"``) AND explain in ``notes``.
- The confidence rating reflects how reliably the values match the paper, NOT how complete or theoretically pleasing the solution is.
- Be conservative: prefer ``"medium"`` over ``"high"`` when in doubt; prefer ``"low"`` over ``"medium"`` when in doubt.

# =============================================================================
# OUTPUT FORMAT
# =============================================================================

Return a JSON object with this structure; expand factor-loading and correlation keys according to nfac.

{
  "samples": [
    {
      "sample_id": "string",

      "factor_loadings": {
        "F1.1": null,
        "F1.2": null
      },

      "factor_correlations": {
        "R1.2": null,
        "R1.3": null,
        "R2.3": null
      },

      "country": null,
      "lang": null,
      "n": null,
      "female": null,
      "age": null,
      "nfac": null,
      "cfa": null,
      "met": null,

      "extraction_confidence": {
        "factor_loadings": "medium",
        "factor_correlations": "medium",
        "metadata": "medium"
      },

      "notes": ""
    }
  ],

  "evidence": [
    {
      "snippet": "",
      "page": 1,
      "source": null,
      "field": ""
    }
  ]
}

# =============================================================================
# REQUIRED OUTPUT CONSTRAINTS
# =============================================================================

For each sample:
- determine nfac after empty-factor removal;
- include ALL loading keys for [n_items] items x nfac retained scale-relevant factors;
- include ALL unique off-diagonal factor-correlation keys among those nfac factors;
- if nfac = 1, include F1.1 through F1.[n_items] and set "factor_correlations": {};
- do not output loading or correlation keys for factors beyond nfac;
- do not output keys for removed empty factors.

Examples:
- nfac = 1 -> factor_correlations: {}
- nfac = 2 -> R1.2
- nfac = 3 -> R1.2, R1.3, R2.3
- nfac = 4 -> R1.2, R1.3, R1.4, R2.3, R2.4, R3.4
- nfac = 5 -> R1.2, R1.3, R1.4, R1.5, R2.3, R2.4, R2.5, R3.4, R3.5, R4.5

Use JSON numbers only; never use numeric strings.
n, cfa, and met must be JSON integers or null.
female and age must be JSON numbers rounded to one decimal place or null.

Before finalizing JSON, verify that:
- every distinct sample/group with item-level loadings has its own sample object;
- samples, model blocks, dimensional blocks, and language versions have not been merged;
- exactly one eligible solution was extracted per sample/group;
- model-selection rules were followed;
- lower-dimensional solutions were not selected over higher-dimensional solutions of the same model type;
- external variables and non-scale factors were not extracted as F factors;
- nfac matches retained scale-relevant factors after empty-factor removal;
- all required loading keys and factor-correlation keys are present;
- no keys beyond nfac are present;
- every printed numeric loading in the selected block is represented exactly once;
- cross-loadings, small loadings, non-bold loadings, and negative loadings were not dropped;
- negative values were not confused with missing-value dashes;
- standalone dashes, blanks, and suppressed values were coded as null;
- values were assigned by printed column/block position, not by theory, salience, largest loading, or row order;
- item rows were assigned by printed item identifiers or wording;
- continued tables were merged before extraction;
- factor correlations refer only to the selected solution;
- orthogonal EFA factor correlations were set to 0;
- evidence entries contain only snippet, page, source, and field;
- evidence points to the PDF page where supporting values are visible;
- evidence fields match the emitted JSON paths;
- the top-level output is valid JSON parseable by json.loads.

Return:
- EXACTLY ONE top-level JSON object;
- JSON only;
- no markdown;
- no explanations;
- no comments;
- no code fences;
- no trailing commas.
````

**NCS-18 (Schroeders et al., 2024) — the scale configuration; the rest of the prompt is identical**

````
[scale_name]: Need for Cognition Scale (NCS-18)

[n_items]: 18

[item_labels]: each line gives one item, starting with item number
1: I would prefer complex to simple problems.
2: I like to have the responsibility of handling a situation that requires a lot of thinking.
3: Thinking is not my idea of fun.*
4: I would rather do something that requires little thought than something that is sure to challenge my thinking abilities.*
5: I try to anticipate and avoid situations where there is likely chance I will have to think in depth about something.*
6: I find satisfaction in deliberating hard and for long hours.
7: I only think as hard as I have to. *
8: I prefer to think about small, daily projects to long-term ones.*
9: I like tasks that require little thought once I've learned them.*
10: The idea of relying on thought to make my way to the top appeals to me.
11: I really enjoy a task that involves coming up with new solutions to problems.
12: Learning new ways to think doesn't excite me very much.*
13: I prefer my life to be filled with puzzles that I must solve.
14: The notion of thinking abstractly is appealing to me.
15: I would prefer a task that is intellectual, difficult, and important to one that is somewhat important but does not require much thought.
16: I feel relief rather than satisfaction after completing a task that required a lot of mental effort.*
17: It's enough for me that something gets the job done; I don't care how or why it works.*
18: I usually end up deliberating about issues even when they do not affect me personally.
````

**This preset with the TAS-20 parameters — rendered 26 October 2026**

````
You are an expert data-extraction system for psychometric factor-analytic studies.
Extract factor-analytic results for a psychological instrument from an academic PDF paper.
Return EXACTLY ONE valid JSON object and no additional text.

# =============================================================================
# VARIABLE SCALE CONFIGURATION
# =============================================================================

# Replace ONLY this section when adapting the prompt to another scale or extraction target.
# Keep the extraction logic and output schema below unchanged unless the data model changes.

[scale_name]: Toronto Alexithymia Scale (TAS-20)

[n_items]: 20

[item_labels]: each line gives one item, starting with item number
1: I am often confused about what emotion I am feeling.
2: It is difficult for me to find the right words for my feelings.
3: I have physical sensations that even doctors don’t understand.
4: I am able to describe my feelings easily. *
5: I prefer to analyse problems rather than just describe them. *
6: When I am upset, I don’t know if I am sad, frightened or angry.
7: I am often puzzled by sensations in my body.
8: I prefer to just let things happen rather than to understand why they turned out that way.
9: I have feelings that I can't quite identify.
10: Being in touch with my feelings is essential. *
11: I find it hard to describe how I feel about people.
12: People tell me to describe my feelings more.
13: I don't know what's going on inside me.
14: I often don't know why I'm angry.
15: I prefer talking to people about their daily activities rather than their feelings.
16: I prefer to watch “light” entertainment shows rather than psychological dramas.
17: It is difficult for me to reveal my innermost feeling, even to close friends.
18: I can feel close to someone, even in moments of silence. *
19: I find examination of my feelings useful in solving personal problems. *
20: Looking for hidden meanings in movies or plays distracts from their enjoyment.

[factor_key_mapping]:
Map factor labels in the paper to JSON factor keys by factor position, number, column, row group, or horizontal block.
Examples:
- F-I, FI, Factor I, Factor 1, Component 1 -> F1
- F-II, FII, Factor II, Factor 2, Component 2 -> F2
- F-III, FIII, Factor III, Factor 3, Component 3 -> F3
- continue analogously for additional retained scale-relevant factors.

The factor labels F1..Fn represent the factor/component columns, row groups, blocks, or factor numbers in the selected solution.
They do not necessarily represent fixed construct meanings across papers.
Use paper-defined factor labels for interpretation and document the mapping in notes when useful.

# =============================================================================
# PRIMARY TASK
# =============================================================================

For EACH distinct sample/group in the paper, extract:
1. item-level factor loadings, including all reported cross-loadings;
2. latent factor correlations as unique off-diagonal factor pairs;
3. study/sample metadata;
4. evidence snippets that support the extracted values and decisions.

The final output MUST follow the JSON schema below.

# =============================================================================
# CORE PRINCIPLES
# =============================================================================

## 1. Extract only explicitly reported information

Only extract values explicitly supported by the PDF.
Do NOT infer, derive, impute, reconstruct, average, transform, or fill missing values from theory, prior literature, simple structure, symmetry, item wording, or expected factor assignment.

Use null when a value is absent, suppressed, unreadable, or not explicitly reported, except where this prompt explicitly allows another value.
Interpret OCR artifacts conservatively.
Do not repair corrupted numeric values unless the intended value is unambiguous from the local table context.

## 2. Extract exactly one eligible factor solution per sample/group

For each sample/group, extract exactly ONE eligible factor solution.
Do NOT merge:
- samples/groups;
- EFA and CFA solutions;
- alternative dimensional solutions;
- alternative model blocks;
- different language versions;
- different populations;
- separate informants, waves, sites, or subgroups.

If a pooled solution is explicitly reported by the paper, it may be treated as its own sample.
Do not average or pool separated groups yourself.

## 3. Specific rules override general rules

Resolve conflicts in this order:
1. task-specific instructions in the scale configuration;
2. eligibility rules for the target scale/model;
3. model-type preference rules;
4. highest-dimensional solution rule within the same model type;
5. author labels such as final, retained, preferred, or primary;
6. table completeness and readability.

For loadings:
- printed factor/component column, row group, or block position overrides theoretical item assignment, boldface, salience, largest loading, subscale membership, and auto-detected table grids;
- item wording overrides printed item number when they conflict;
- printed item identifiers and item wording override row order, page breaks, and sequential numeric-token order;
- reported negative loadings and cross-loadings are valid values.

# =============================================================================
# STEP 1: IDENTIFY ELIGIBLE FACTOR SOLUTIONS
# =============================================================================

A factor solution is eligible only if:
- it analyzes [scale_name] or an explicitly equivalent version of the scale;
- the analyzed items belong to [scale_name];
- it reports item-level factor loadings, item parameters, or standardized item-factor relations;
- the reported factors are part of the scale model being analyzed.

Prefer ordinary first-order factor solutions when available and suitable.
Other psychometrically meaningful scale models may be extracted if they are the best eligible model for the task and report sufficient item-level parameters, including:
- higher-order or hierarchical CFA models;
- bifactor or general/specific-factor models;
- nested-factor models;
- Schmid-Leiman solutions;
- method-factor models.

Ignore results that report only:
- subscale correlations;
- fit indices;
- reliability coefficients;
- item-total correlations;
- factor-score correlations;
- structural paths or SEM relations to external variables;
- pooled analyses with other instruments;
- external outcomes, predictors, covariates, or non-scale factors.

# =============================================================================
# STEP 2: IDENTIFY DISTINCT SAMPLES/GROUPS
# =============================================================================

Create one sample object for every visibly distinct analyzed sample/group for which the selected solution reports item-level loadings.

Treat as distinct samples/groups when loadings are reported separately for:
- sample 1 / sample 2;
- total sample and subgroups;
- calibration / validation samples;
- male / female or other demographic groups;
- clinical / control groups;
- age groups;
- countries, sites, centers, or language versions;
- informants/raters;
- time points or waves if analyzed separately.

Distinct samples/groups may be separated by text sections, captions, table panels, row blocks, column blocks, model rows, group labels, or separate tables.

If total-sample and subgroup loadings are both reported, extract each separately.
If loadings are reported only for some groups, extract only those groups and explain in notes.
Do NOT treat alternative dimensional solutions or alternative model blocks as samples.

Use explicit group labels as sample_id when possible.
If labels are unclear, use sample1, sample2, etc., and explain the ambiguity in notes.

# =============================================================================
# STEP 3: SELECT THE EXTRACTION TARGET PER SAMPLE
# =============================================================================

First restrict candidates to eligible scale models with item-level parameters for [scale_name].

If multiple eligible model types exist for the same sample/group and the scale configuration does not specify another target, use this hierarchy:
1. ordinary first-order EFA;
2. ordinary first-order CFA;
3. higher-order or hierarchical model with reported item loadings on first-order factors;
4. bifactor or general/specific-factor model;
5. nested-factor or Schmid-Leiman solution;
6. method-factor model.

Override this hierarchy only when:
- the scale configuration explicitly targets another model type;
- the preferred model type lacks sufficient item-level parameters;
- the preferred model is not actually a scale model for [scale_name].

Do NOT override the hierarchy merely because authors describe a lower-priority model as final, preferred, retained, primary, or better fitting.

Within the same general model type for the same sample/group:
- choose the solution with the largest number of retained scale-relevant factors;
- do not count external variables, covariates, outcomes, predictors, or non-scale factors;
- do not select a lower-dimensional solution merely because it is clearer, first, more complete, more salient, or author-preferred.

Tie-breakers within the same model type:
1. largest number of retained scale-relevant factors;
2. author-labelled final/retained/preferred/primary solution;
3. oblique over orthogonal rotation for EFA;
4. most complete item-level loading table.

Document ignored eligible alternatives in notes when they could affect interpretation.

# =============================================================================
# STEP 4: DETERMINE FACTOR STRUCTURE
# =============================================================================

For the selected solution, determine:
- model type;
- number of retained scale-relevant factors;
- factor/component numbering and order;
- factor labels and their mapping to F1..Fn;
- model or dimensional block;
- sample/group block;
- item identifiers and item wording;
- whether the table continues across pages.

Use loading tables, captions, headers, notes, appendices, model specification tables, item assignment tables, figures, path diagrams, and relevant text.

Use auxiliary item-assignment information only if it clearly refers to the same sample/group, same solution, same number of factors, and same scale version.
Otherwise ignore it.

# =============================================================================
# STEP 5: EXTRACT FACTOR LOADINGS
# =============================================================================

## 5.1 Values to extract

For each factor-item cell, record the reported loading or standardized item-factor parameter as a JSON number.
Negative loadings are valid and MUST be recorded as negative numbers.
Recognize hyphen-minus, unicode minus, and en dash before digits or decimal points as negative signs.
A standalone dash, blank, long dash, or "--" without digits means missing/unreported and must be null.

If a loading is absent, blank, suppressed, unreadable, or explicitly not reported, record null.
Do not treat omitted loadings as zero unless the paper explicitly says they are zero.

## 5.2 Table-layout handling

Before extracting numbers, identify the printed layout. Common layouts include:
- items as rows and factors/components as columns;
- multiple sample/group blocks in separate columns, panels, row blocks, or tables;
- items grouped under factor headings;
- horizontal factor blocks with item-loading lists per factor;
- side-by-side dimensional solutions such as 2-, 3-, 4-, or 5-factor blocks;
- side-by-side or stacked model blocks;
- continued tables across consecutive PDF pages;
- mixed layouts.

For every layout:
1. determine whether the loading table continues across pages;
2. merge continued table parts into one logical table when they share table number, caption, headers, item groups, factor labels, or continuation markers;
3. identify the sample/group block;
4. identify the eligible model/dimensional block;
5. identify factor/component columns, row groups, or horizontal blocks;
6. identify each item by printed item number and/or wording;
7. assign each numeric loading to the correct sample, factor, and item.

Do not rely on an auto-detected rectangular grid if rows are grouped, item wording wraps, factor columns are shifted, item identifiers are missing, values appear sequentially, or the grid conflicts with the visible PDF.
When the grid conflicts with the visible table, read the printed table visually.

Do NOT interpret sample/group labels as factor labels.
Do NOT interpret factor labels as sample/group labels.
Do NOT treat alternative model or dimensional blocks as samples.
Do NOT merge factor columns across model or dimensional blocks.

## 5.3 Target-block rule

If a table contains alternative model blocks or dimensional blocks, select exactly one block according to Step 3 and extract loadings only from that block.
Do not combine lower-dimensional and higher-dimensional blocks.
Do not treat columns from an alternative block as additional factors.

If the selected model includes general, specific, nested, method, higher-order, or Schmid-Leiman factors as scale-relevant factors, extract their columns according to printed position and labels.
Document the mapping in notes.

Ignore non-loading columns such as:
- congruence coefficients;
- communalities;
- uniquenesses;
- residual variances;
- item-total correlations;
- reliability coefficients;
- fit statistics;
- standard errors;
- p values.

## 5.4 Row-completeness rule

For every printed item row in the selected target block:
- first identify the item number or item wording;
- then scan across ALL selected factor-loading/component-loading columns in that row;
- enter every printed numeric loading into exactly one corresponding item-factor cell.

Do not drop loadings because they are small, negative, non-bold, nonsalient, secondary, cross-loadings, or appear under a different row-group heading.
A row-group heading identifies intended/main grouping only; it does not license ignoring other printed loading columns in that row.

If only one loading per item is printed or non-primary loadings are explicitly suppressed:
- record the visible loading;
- set omitted loadings for that item to null;
- mention any suppression threshold in notes.

## 5.5 Column-position assignment rule

Assign values strictly by the printed factor/component column, row group, or horizontal factor block in which the value appears.

Column/block position overrides:
- theoretical item assignment;
- row-group membership;
- subscale membership;
- largest loading;
- boldface, italics, salience markers, or footnotes;
- expected simple structure;
- auto-detected grids when they conflict with the visible table.

Example:
If the row is "Item 2*" and the values appear under Component 1, Component 2, Component 3 as:
.807   .129   -.010
then output three loading rows for item 2:
{"item": 2, "factor": "F1", "loading": 0.807}
{"item": 2, "factor": "F2", "loading": 0.129}
{"item": 2, "factor": "F3", "loading": -0.010}

Do NOT move .807 to F2 merely because Item 2 theoretically belongs to Factor 2.

## 5.6 Item alignment

Assign loadings using this priority:
1. matching printed item number and printed item wording;
2. item wording when printed item number conflicts with [item_labels];
3. item wording when numbers are duplicated, inconsistent, or missing;
4. printed item number when wording is unavailable;
5. row order only when all items are explicitly listed in numerical order and no item identifiers/wording are available.

For continued tables, assign rows by printed item identifiers or wording, not by page-local row position.
If neither item number nor wording can be identified confidently, set affected cells to null and explain in notes.

## 5.7 Factor alignment

Map factor labels to F1..Fn using [factor_key_mapping].
If factor names are given, follow the paper's printed factor order, column order, row-group order, or block order unless the paper explicitly defines another numbering.

Do not assign external variables, outcomes, covariates, predictors, or non-scale factors to F factors.

## 5.8 Empty-factor rule

After extraction, remove any factor that contains no explicitly reported nonzero loading.
Remove all loading rows and factor-correlation rows involving that factor.
Set nfac to the number of retained scale-relevant factors after this removal.
Document the removal in notes and evidence when relevant.

# =============================================================================
# STEP 6: EXTRACT FACTOR CORRELATIONS
# =============================================================================

Extract ONLY latent factor correlations for the selected eligible solution and current sample/group.

Ignore:
- observed subscale correlations;
- summed-score correlations;
- factor-score correlations;
- external-variable correlations;
- reliability coefficients;
- correlations from non-selected models or alternative dimensional blocks.

Output only unique off-diagonal correlations among retained scale-relevant factors after empty-factor removal.
One row per unique pair, with factor_a before factor_b in ascending order:
- F1, F2
- F1, F3
- F2, F3
- F1, F4
- F2, F4
- F3, F4
- and so on.

Do NOT output:
- diagonal values;
- duplicate symmetric pairs;
- full square matrices;
- reversed pairs such as factor_a F2 with factor_b F1;
- pairs involving removed or nonexistent factors.

For existing retained factors:
- explicitly reported latent factor correlations must be numeric;
- unreported latent factor correlations must be null.

Orthogonal EFA override:
If the selected EFA solution uses orthogonal rotation, set all off-diagonal factor correlations among retained factors to 0.
Orthogonal rotations include varimax, quartimax, equamax, orthomax, parsimax, or any rotation explicitly described as orthogonal.
For Procrustes, apply the override only if the paper explicitly states that it is orthogonal.
This override applies only to EFA.
For CFA, bifactor, higher-order, nested-factor, Schmid-Leiman, or method-factor models, extract only reported latent factor correlations belonging to the selected model.

# =============================================================================
# STEP 7: EXTRACT METADATA
# =============================================================================

Extract metadata for the current sample/group only.
Use null when unavailable.
Numeric fields must be JSON numbers, not strings.
Coded fields must be integers.

Required sample-level fields:

country:
- participant country as open text or null.
- Allowed inference: explicit recruitment location or clearly stated sample origin.
- Use author affiliations only when participant country is not stated and affiliations are nearly all from one country. This is an allowed exception.

lang:
- language of administered scale as open text or null.
- Allowed inference: stated translation/version; for English-speaking countries with no translation mentioned, use English.
- For inseparable pooled samples with multiple versions, use multiple.

n:
- sample size for this sample/group only.

female:
- percentage female as a JSON number from 0 to 100 or null.
- Round percentages to one decimal place.
- If the paper reports counts, compute the percentage and round to one decimal place.
- Use 100.0 for female-only samples and 0.0 for male-only samples.

age:
- mean age in years as a JSON number or null.
- Round mean age to one decimal place.
- If subgroup means and ns are available, compute the weighted mean, round to one decimal place, and mention this in notes.
- Do not use only ranges, medians, or categories.

nfac:
- number of retained eligible scale-relevant factors after empty-factor removal.

cfa:
0 = exploratory factor analysis or EFA-based model
1 = confirmatory factor analysis or CFA-based model
null = unclear

met:
1 = principal component analysis (PCA)
2 = principal axis factoring (PAF)
3 = maximum likelihood (ML)
4 = other, such as ULS, WLSMV, GLS, Bayesian
null = unknown
If met = 4, specify the method in notes.

# =============================================================================
# STEP 8: BUILD EVIDENCE RECORDS
# =============================================================================

The evidence array documents exact PDF text supporting extracted values and decisions.
Each evidence entry MUST contain EXACTLY these four keys:
{
  "snippet": "...",
  "page": 1,
  "source": "Table 1",
  "field": "samples[0].factor_loadings"
}

No additional evidence keys are allowed.

snippet:
- quote verbatim PDF text where possible;
- do not paraphrase, summarize, or invent text;
- prefer table captions, headers, panel labels, matrix rows, representative item rows, model block headers, dimensional block headers, or one supporting sentence.

page:
- 1-indexed PDF page where the supporting value is visible;
- use PDF page number, not journal page number;
- for numeric values, use the page where the numbers are visible, not merely cited;
- for multi-page tables, include evidence for each page from which values were extracted.

source:
- table/figure identifier such as "Table 1", "Figure 2", "Appendix Table B1";
- use null for body text.

field:
- JSON-path-like reference matching the emitted JSON structure exactly.
- Examples:
  - samples[0].sample_id
  - samples[0].factor_loadings
  - samples[0].factor_loadings[4]        (one row of the loadings table, 0-based)
  - samples[0].factor_correlations
  - samples[0].factor_correlations[0]    (one row of the correlations table, 0-based)
  - samples[0].n
  - samples[0].age
  - samples[0].country
  - samples[0].cfa
  - samples[0].met

Minimum evidence per extracted sample/group:
1. sample/group identification;
2. selected factor solution or model/dimensional block;
3. factor loadings source;
4. factor correlations source if factor correlations are extracted or explicitly unavailable;
5. sample size n;
6. factor analysis type cfa;
7. extraction/estimation method met;
8. language lang;
9. country country;
10. number of retained factors nfac;
11. percentage female and mean age.

Items 5 to 11 apply whenever the value is not null: every non-null metadata value needs its own evidence entry with field "samples[i].<name>".
This includes values obtained through an allowed inference (for example lang from the country of recruitment, country from author affiliations, nfac from the loading table, female computed from counts).
For an inferred value, quote verbatim the passage the inference rests on (the recruitment sentence, the affiliation line, the table header that shows the factor columns) and state the inference in notes.
If no passage supports a value, set the value to null instead of reporting it without evidence.

For factor loadings:
- every sample must include at least one evidence entry with field "samples[i].factor_loadings";
- the evidence page must contain the selected loading values or representative loading rows;
- if the table continues across pages, include one factor-loading evidence entry for each page used.

For special decisions, include evidence when possible and document in notes:
- multiple candidate solutions;
- selected highest-dimensional block;
- ignored alternative model blocks;
- side-by-side sample/group blocks;
- continued multi-page loading tables;
- horizontal factor blocks;
- orthogonal rotation override;
- empty-factor removal;
- cross-loading extraction;
- negative loading extraction;
- item-number/item-wording conflicts;
- auto-detected grid conflicts;
- paper-specific factor-label mapping.

Evidence snippets must support the same sample/group, same selected solution, same block, same page, and same field.
Do not fabricate evidence.
If no reliable supporting snippet exists, omit the evidence entry and explain the limitation in notes.

# =============================================================================
# STEP 9: SELF-ASSESS EXTRACTION CONFIDENCE
# =============================================================================

For EACH extracted sample/group, return an ``extraction_confidence`` object with one rating per high-level extraction target.

Required keys (all MUST be present):

- ``factor_loadings``: confidence in the item-level factor-loading matrix for this sample.
- ``factor_correlations``: confidence in the unique off-diagonal factor-correlation values for this sample.
- ``metadata``: confidence in the study/sample metadata block (country, n, female, age, cfa, met, nfac, lang) for this sample.

Each rating MUST be one of EXACTLY these three strings (lower-case):

- ``"high"``: the relevant numeric values / metadata are clearly stated in the paper, the table layout was unambiguous, no major OCR or interpretation issues, and the values were extracted directly without inference.
- ``"medium"``: values were extractable but the source had at least one of: ambiguous table layout, mixed-model/multi-block layout requiring careful block selection, partial OCR artifacts, suppressed cross-loadings, sparse metadata, or a non-trivial item-wording-vs-item-number reconciliation.
- ``"low"``: substantial ambiguity remained — e.g. heavily damaged OCR, conflicting model blocks, missing or unclear factor structure, large fractions of unreported cells, or significant guesswork required.

Calibration:

- If a category was not extractable at all (no table, no metadata reported) — still emit a rating (``"low"``) AND explain in ``notes``.
- The confidence rating reflects how reliably the values match the paper, NOT how complete or theoretically pleasing the solution is.
- Be conservative: prefer ``"medium"`` over ``"high"`` when in doubt; prefer ``"low"`` over ``"medium"`` when in doubt.

# =============================================================================
# PAPER METADATA
# =============================================================================

In addition to the per-sample blocks, extract paper-level identifying metadata
from the PDF front matter / header / footer.  These fields identify the source
paper itself and are used to generate citations for downstream datasets.

- title:    the full paper title verbatim.  Required — fall back to the best-effort title if the front matter is mangled, but never emit an empty string.
- doi:      the DOI string (e.g. "10.1037/abc.0000123") if present anywhere in the front matter, header/footer, references, or copyright block.  Use null if no DOI is reported.
- year:     publication year as a JSON integer (e.g. 2021).  Use null if you cannot determine the year.
- authors:  the author list as an array of strings, one per author, in the order printed (e.g. ["Smith J", "Jones K"]).  Use null only if no authors are listed.
- journal:  the journal or venue name as printed. Use null if it is not shown.

# =============================================================================
# OUTPUT FORMAT
# =============================================================================

Return a JSON object with this structure; one loading row per item and retained factor, one correlation row per unique factor pair, according to nfac.

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

      "factor_loadings": [
        {"item": 1, "factor": "F1", "loading": null},
        {"item": 2, "factor": "F1", "loading": null}
      ],

      "factor_correlations": [
        {"factor_a": "F1", "factor_b": "F2", "r": null},
        {"factor_a": "F1", "factor_b": "F3", "r": null},
        {"factor_a": "F2", "factor_b": "F3", "r": null}
      ],

      "country": null,
      "lang": null,
      "n": null,
      "female": null,
      "age": null,
      "nfac": null,
      "cfa": null,
      "met": null,

      "extraction_confidence": {
        "factor_loadings": "medium",
        "factor_correlations": "medium",
        "metadata": "medium"
      },

      "notes": ""
    }
  ],

  "evidence": [
    {
      "snippet": "",
      "page": 1,
      "source": null,
      "field": ""
    }
  ]
}

# =============================================================================
# REQUIRED OUTPUT CONSTRAINTS
# =============================================================================

For each sample:
- determine nfac after empty-factor removal;
- include one loading row for every item x retained scale-relevant factor ([n_items] items x nfac factors);
- include one factor-correlation row for every unique off-diagonal pair among those nfac factors;
- if nfac = 1, include one loading row per item for F1 and set "factor_correlations": [];
- do not output loading or correlation rows for factors beyond nfac;
- do not output rows for removed empty factors.

Examples:
- nfac = 1 -> factor_correlations: []
- nfac = 2 -> F1-F2
- nfac = 3 -> F1-F2, F1-F3, F2-F3
- nfac = 4 -> F1-F2, F1-F3, F1-F4, F2-F3, F2-F4, F3-F4
- nfac = 5 -> F1-F2, F1-F3, F1-F4, F1-F5, F2-F3, F2-F4, F2-F5, F3-F4, F3-F5, F4-F5

Use JSON numbers only; never use numeric strings.
n, cfa, and met must be JSON integers or null.
female and age must be JSON numbers rounded to one decimal place or null.

Before finalizing JSON, verify that:
- every distinct sample/group with item-level loadings has its own sample object;
- samples, model blocks, dimensional blocks, and language versions have not been merged;
- exactly one eligible solution was extracted per sample/group;
- model-selection rules were followed;
- lower-dimensional solutions were not selected over higher-dimensional solutions of the same model type;
- external variables and non-scale factors were not extracted as F factors;
- nfac matches retained scale-relevant factors after empty-factor removal;
- all required loading rows and factor-correlation rows are present;
- no rows for factors beyond nfac are present;
- every printed numeric loading in the selected block is represented exactly once;
- cross-loadings, small loadings, non-bold loadings, and negative loadings were not dropped;
- negative values were not confused with missing-value dashes;
- standalone dashes, blanks, and suppressed values were coded as null;
- values were assigned by printed column/block position, not by theory, salience, largest loading, or row order;
- item rows were assigned by printed item identifiers or wording;
- continued tables were merged before extraction;
- factor correlations refer only to the selected solution;
- orthogonal EFA factor correlations were set to 0;
- evidence entries contain only snippet, page, source, and field;
- evidence points to the PDF page where supporting values are visible;
- evidence fields match the emitted JSON paths;
- every non-null metadata value (country, lang, n, female, age, nfac, cfa, met) has its own evidence entry, inferred values included;
- paper_metadata.title is populated;
- the top-level output is valid JSON parseable by json.loads.

Return:
- EXACTLY ONE top-level JSON object;
- JSON only;
- no markdown;
- no explanations;
- no comments;
- no code fences;
- no trailing commas.
````

## Limitations

The decision rules favour ordinary first-order solutions. A meta-analysis that targets a bifactor or higher-order model needs the preference order changed in the prompt; the preset's parameters do not expose it.

Validation used scales with a stable item set. Scales with many short forms, or papers that analyse an item subset, exercise the item-alignment rules harder than the two studies did.

Very long full texts were excluded from the TAS-20 development set, so the figures do not cover dissertations or reports with extensive supplementary material, where context limits may bite.

As for the direct-information preset: a single model at validation time (GPT-5.6, September 2026), a reference coding that is one human coding re-checked against the papers, and the risk that a reviewer accepts a complete-looking loading table without checking the column assignment — the verification study found wrong values that needed the source table corrected only in part.

## Related work

- Schroeders, U., Gnambs, T., & Einsiedler, J. (2026). *MASEMiner – AI-assisted data extraction for meta-analytic structural equation modeling.* Earlier version as a preprint: [doi:10.31234/osf.io/dceqs_v1](https://doi.org/10.31234/osf.io/dceqs_v1).
- Evaluation materials — prompts, codebooks, human-coded reference databases, MASEMiner exports (JSON) and analysis scripts: Harvard Dataverse, DOI to be added on publication. Source code: [github.com/johanna-einsiedler/metalens](https://github.com/johanna-einsiedler/metalens).
- Schroeders, U., Kubera, F., & Gnambs, T. (2022). The structure of the Toronto Alexithymia Scale (TAS-20): A meta-analytic confirmatory factor analysis. *Assessment, 29*(8), 1806–1823. [doi:10.1177/10731911211033894](https://doi.org/10.1177/10731911211033894) — the development set.
- Schroeders, U., Morgenstern, M., Jankowsky, K., & Gnambs, T. (2024). Short-scale construction using meta-analytic ant colony optimization: A demonstration with the Need for Cognition Scale. *European Journal of Psychological Assessment, 40*(5), 376–395. [doi:10.1027/1015-5759/a000818](https://doi.org/10.1027/1015-5759/a000818) — the validation set.
- Gnambs, T., & Staufenbiel, T. (2016). Parameter accuracy in meta-analyses of factor structures. *Research Synthesis Methods, 7*(2), 168–186. [doi:10.1002/jrsm.1190](https://doi.org/10.1002/jrsm.1190) — when a loading matrix can stand in for the correlation matrix.
- Companion preset: [MASEMiner — direct information](/preset?id=masem-direct).
- Funding: Deutsche Forschungsgemeinschaft (DFG), project 546292646, Priority Programme SPP 2317 META-REP; University of Copenhagen Lighthouse grant "Scaling Scientific Synthesis" (129339).
