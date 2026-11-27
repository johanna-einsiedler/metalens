"""The five model calls of the paper-reading half, as Metalens passes (to be ported).

| pass | pipeline stage | prompt | output schema | reads |
|---|---|---|---|---|
| classify | s05 `classify.py` | `classify.md` | `PaperClassification` | the PDF |
| inventory | s06 `extract.py` (per-table mode) | `inventory.md` | `TableInventory` | the PDF |
| extract_table | s06 `extract.py` (per-table mode) | `extract_table.md` | `TableExtraction` | the PDF, one table |
| extract_session | s06 `session.py` (the mode run 3 used) | `extract_session.md` | `SessionManifest` + one `TableExtraction` per table | the PDF, with the Write tool |
| stated_claims | s09 `stated.py` | `stated_claims_v2.md` + its adapter | `StatedClaims` | the PDF |
| claim_map | s10 `mapping.py` | `claim_map_v1.md` + its addendum | `ClaimMap` | the PDF, `stated_claims`, the extraction |
| leak_judge | s14 `leak_judge.py` | `leak_judge.md` | `LeakBatch` | the flagged matches of `computes.blind` |

The pipeline runs these through the Claude Code runtime with the PDF as a file the agent reads
(``common.llm.parse_with_pdf_agent_sdk``); Metalens's ``ctx.call(system, user, schema)`` takes
the page-marked text of the PDF. The passes are ported once the engine has a PDF-input route for
passes (plan of 2026-11-27, step 5); until then ``registrations()`` is empty and the pipeline's
stage scripts remain the implementation. The ``_demo`` chain in the Metalens repository shows the
shape a pass function takes: ``def classify(ctx) -> dict`` calling ``ctx.call(ctx.prompt("system"),
user_text, ctx.schema(), max_tokens=ctx.max_tokens)`` and returning the JSON output.
"""
from __future__ import annotations


def registrations() -> dict[str, dict]:
    return {}
