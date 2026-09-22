"""Normalized records -> canonical JSON (the round-trip proof).

``reconstruct_publishable()`` rebuilds exactly the publishable subset of the
original document from an ``IngestResult``:

    reconstruct_publishable(ingest(x)) == strip_to_publishable(x)

This guarantees the contract -> normalized decomposition loses nothing the
public/archive format carries, across BOTH evidence-placement conventions.
Declared ``confidence`` blocks are re-nested into the instance they rate (paper /
entry / sub-entry) in canonical ``{group: {level, notes}}`` form. Legacy
``extraction_confidence`` (root-level or per entry) is intentionally absent — it is not
a publishable key; it is preserved separately as ``field_confidence`` rows.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from . import contract
from .ingest import EvidenceSpan, FieldConfidence, IngestResult

_CHILD_PATH = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?:\._table)?\[(?P<i>\d+)\]"
                         r"\.(?P<child>[A-Za-z_][A-Za-z0-9_]*)\[(?P<j>\d+)\]$")


def _span_to_item(span: EvidenceSpan) -> dict[str, Any]:
    # The contract mandates exactly these four keys per evidence item
    # (prompt_builder.py:27). Reconstruct them verbatim.
    return {
        "snippet": span.snippet,
        "page": span.page,
        "source": span.source,
        "field": span.field_path,
    }


def _nest(rows: list[FieldConfidence]) -> dict[str, dict[str, str | None]]:
    """A confidence block in canonical form, original group order (by ``ord``)."""
    return {c.block: {"level": c.level, "notes": c.notes}
            for c in sorted(rows, key=lambda c: c.ord)}


def reconstruct_publishable(res: IngestResult) -> dict[str, Any]:
    out: dict[str, Any] = {}

    if res.paper_metadata_raw is not None:
        out["paper_metadata"] = copy.deepcopy(res.paper_metadata_raw)
        paper_conf = [c for c in res.confidence if c.placement == "paper"]
        if paper_conf:
            out["paper_metadata"][contract.CONFIDENCE_KEY] = _nest(paper_conf)

    entries: list[dict[str, Any]] = [
        contract.strip_row_ids(r.field_values)             # a deep copy without the internal row ids
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

    # Re-nest declared confidence: per entry, and per sub-entry (routed by its path).
    entry_conf: dict[int, list[FieldConfidence]] = {}
    child_conf: dict[tuple[int, str, int], list[FieldConfidence]] = {}
    for c in res.confidence:
        if c.placement == "entry" and c.entry_index is not None:
            entry_conf.setdefault(c.entry_index, []).append(c)
        elif c.placement == "child" and c.field_path:
            m = _CHILD_PATH.match(c.field_path)
            if m:
                child_conf.setdefault((int(m["i"]), m["child"], int(m["j"])), []).append(c)
    for idx, rows in entry_conf.items():
        if 0 <= idx < len(entries):
            entries[idx][contract.CONFIDENCE_KEY] = _nest(rows)
    for (i, child, j), rows in child_conf.items():
        if not (0 <= i < len(entries)):
            continue
        arr = entries[i].get(child)
        if isinstance(arr, list) and 0 <= j < len(arr) and isinstance(arr[j], dict):
            arr[j][contract.CONFIDENCE_KEY] = _nest(rows)

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
