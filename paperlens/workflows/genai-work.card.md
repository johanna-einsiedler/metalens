## Introduction

The GenAI-at-work review extracts, from every included paper, what the paper reports about generative AI and work: the study's design, the units of its estimates — tasks, arms, outcomes, subgroups, horizons — the estimates themselves, read from the paper's exhibits, and the claims of its abstract mapped to the estimates that support them. A pipeline outside Metalens does the extraction and runs its own checks; the record it writes for a paper is what the coders validate here.

The validation has three passes, each with a preset of its own, each reading the same PDF with the quoted passages highlighted. This workflow is how the three passes run over one set of papers: one workflow dataset for the study, one linked dataset per pass, the coders added by email, each paper assigned to its coders, the release gated on every verdict.

## Steps

1. **Judgment** (supplied, two coders per paper). The pipeline's judgment file and the PDF are uploaded into the run. A coder decides the screening criteria and the study design from the PDF alone — nothing prefilled, nothing highlighted on that panel — then confirms the units as the extraction listed them, each with its description and its passage and no number shown, giving every task, arm and outcome its category without seeing the model's choice, then judges for each sentence of the abstract whether it is about the unit the extraction matched it to. Two coders code every paper, each in a coding of their own.
2. **Extraction check** (supplied, one coder per paper). One table or figure of the paper, drawn at random by the pipeline among those the estimates cite, shown transcribed: each cited cell as printed with the unit it was mapped to. The coder confirms value and mapping cell by cell, adds the cells the extraction missed, and may flag the paper for a full review.
3. **Routed papers** (supplied, one coder per paper). A paper is routed when its record contradicts an inclusion criterion (every extracted size below 200, no no-AI reference arm, no performance outcome, no identification), when a headline estimate scores high on the pipeline's checks, or when a coder flagged it. Its file lists every flag — the checks' codes on its headline estimates, the runs' disagreements, the criterion, the coder's flag — for a coder to resolve.
4. **Release**. Gated on all three: every assigned coder gave a verdict on every paper of their pass, or the owner released anyway, which the release records.

## Review

Every paper of a pass is reviewed whole (`mode: all`, `unit: paper`). The judgment pass assigns each paper to two of the workflow's coders, the other two passes to one; coders are assigned in turn, so the load is even, and no coder sees another's coding. A coder's verdicts, categories, corrections and notes are their coding; the record as uploaded stays as it was. Agreement between the two judgment coders, and between each coder and the pipeline, is computed from the export, which carries every coding by label.

## Versions

The workflow pins the three presets by their data contract. A change to a preset's fields, to a step, or to the review declaration (the number of coders included) is a new version of the workflow; a change of wording is not.
