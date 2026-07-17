"""Normalized records -> canonical JSON (the round-trip proof).

``reconstruct_publishable()`` rebuilds exactly the publishable subset of the
original document from an ``IngestResult``:

    reconstruct_publishable(ingest(x)) == strip_to_publishable(x)

This guarantees the contract -> normalized decomposition loses nothing the
public/archive format carries, across BOTH evidence-placement conventions.
``extraction_confidence`` is intentionally absent (it is not a publishable key);
it is preserved separately as ``field_confidence`` for the credibility system.
"""
from __future__ import annotations

import copy
from typing import Any

from .ingest import EvidenceSpan, IngestResult


def _span_to_item(span: EvidenceSpan) -> dict[str, Any]:
    # The contract mandates exactly these four keys per evidence item
    # (prompt_builder.py:27). Reconstruct them verbatim.
    return {
        "snippet": span.snippet,
        "page": span.page,
        "source": span.source,
        "field": span.field_path,
    }


def reconstruct_publishable(res: IngestResult) -> dict[str, Any]:
    out: dict[str, Any] = {}

    if res.paper_metadata_raw is not None:
        out["paper_metadata"] = copy.deepcopy(res.paper_metadata_raw)

    entries: list[dict[str, Any]] = [
        copy.deepcopy(r.field_values)
        for r in sorted(res.records, key=lambda r: r.entry_index)
    ]

    # Re-nest entry-placement evidence into its entry, preserving original order.
    nested = [s for s in res.evidence if s.placement == "entry"]
    by_entry: dict[int, list[EvidenceSpan]] = {}
    for s in nested:
        if s.entry_index is None:
            continue
        by_entry.setdefault(s.entry_index, []).append(s)
    for idx, spans in by_entry.items():
        spans.sort(key=lambda s: s.ord)
        entries[idx]["evidence"] = [_span_to_item(s) for s in spans]

    if res.core_shape == "table":
        out[res.core_key] = {"_table": entries}
    else:
        out[res.core_key] = entries

    # Flat top-level evidence, preserving original order.
    if res.had_top_evidence:
        top = sorted((s for s in res.evidence if s.placement == "top"),
                     key=lambda s: s.ord)
        out["evidence"] = [_span_to_item(s) for s in top]

    # Leftover publishable scalars carried verbatim (metric / notes / schema_version).
    out.update(copy.deepcopy(res.top_extras))

    return out
