"""Canonical JSON -> normalized records (the heart of the record spine).

``ingest()`` decomposes one canonical-record document (one paper extraction)
into the normalized pieces the Postgres spine stores:

  * ONE ``paper`` (universal metadata; raw block kept verbatim for round-trip,
    plus a typed projection for querying),
  * N ``record`` rows  — one per element of the core per-entry array
    (``samples[i]`` / ``studies._table[i]`` / ...),
  * M ``evidence_span`` rows — every ``{snippet, page, source, field}`` item,
    tagged with its PLACEMENT (nested in an entry vs the flat top-level array)
    and routed to an entry where determinable,
  * K ``field_confidence`` rows — the (non-publishable) ``extraction_confidence``
    blocks.

The decomposition is loss-free for the publishable subset: ``reconstruct.py``
rebuilds exactly ``strip_to_publishable(original)``. Placement is preserved so
both contract conventions (forestplot's flat top-level evidence and masem's
per-entry nested evidence) round-trip identically — the subtlety the §5 slice
exists to pin down.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from . import contract


@dataclass
class EvidenceSpan:
    ord: int                       # original order within its placement scope
    placement: str                 # "entry" (nested) | "top" (flat top-level)
    entry_index: int | None        # which record it supports, when determinable
    field_path: str | None         # the raw ``field`` value, verbatim
    snippet: str
    page: int | None
    source: str | None


@dataclass
class FieldConfidence:
    block: str                     # the extraction_confidence key (a confidence_keys block)
    level: str | None              # high | medium | low
    notes: str | None


@dataclass
class Record:
    entry_index: int
    field_values: dict[str, Any]   # the entry object, minus relocated evidence/confidence


@dataclass
class IngestResult:
    core_key: str                  # "samples" | "studies" | ...
    core_shape: str                # "list" | "table" (wrapped in {"_table": [...]})
    records: list[Record]
    evidence: list[EvidenceSpan]
    confidence: list[FieldConfidence]
    paper_metadata_raw: dict | None        # verbatim, for round-trip
    paper_typed: dict[str, Any]            # derived projection for the paper table
    top_extras: dict[str, Any]             # leftover publishable scalars (metric/notes/schema_version)
    schema_version: str | None
    had_top_evidence: bool                 # original had a top-level ``evidence`` key

    # convenience
    @property
    def doi(self) -> str | None:
        return self.paper_typed.get("doi")


# ── core-array detection ──────────────────────────────────────────────────────

def detect_core(obj: dict) -> tuple[str, str, list[dict]]:
    """Find the per-entry array. Returns (core_key, shape, entries).

    shape is "list" (value is a JSON array) or "table" (value is
    ``{"_table": [...]}``). Raises ValueError if no core array is present.
    """
    for key in contract.CORE_ARRAY_CANDIDATES:
        if key not in obj:
            continue
        val = obj[key]
        if isinstance(val, list):
            return key, "list", val
        if isinstance(val, dict) and isinstance(val.get("_table"), list):
            return key, "table", val["_table"]
    # Generic fallback: any non-meta top-level key that looks like an entry array.
    for key, val in obj.items():
        if key in contract._NON_CORE_META_KEYS:
            continue
        if isinstance(val, list):
            return key, "list", val
        if isinstance(val, dict) and isinstance(val.get("_table"), list):
            return key, "table", val["_table"]
    raise ValueError("no core per-entry array found in canonical record")


def _entry_index_from_field(field_path: str | None, core_key: str) -> int | None:
    """Parse the entry index out of an evidence ``field`` path.

    e.g. ``studies._table[3]`` -> 3, ``samples[0].factor_loadings`` -> 0.
    Caption-level paths (``studies``) or non-path strings return None.
    """
    if not field_path:
        return None
    m = re.search(rf"{re.escape(core_key)}(?:\._table)?\[(\d+)\]", field_path)
    return int(m.group(1)) if m else None


def _typed_paper(meta: dict | None) -> dict[str, Any]:
    """Project the verbatim ``paper_metadata`` block into typed paper columns.

    Conservative: only the fields the browser LLM reliably extracts today.
    Enrichment (Crossref/OpenAlex/...) fills the rest later. Round-trip never
    depends on this projection — it reads ``paper_metadata_raw``.
    """
    if not isinstance(meta, dict):
        return {}
    out: dict[str, Any] = {}
    for k in ("title", "doi", "year", "journal"):
        if meta.get(k) is not None:
            out[k] = meta[k]
    authors = meta.get("authors")
    if isinstance(authors, list):
        out["authors"] = authors
    if isinstance(out.get("doi"), str):
        out["doi"] = _normalize_doi(out["doi"])
    return out


def _normalize_doi(doi: str) -> str:
    d = doi.strip()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d, flags=re.IGNORECASE)
    d = re.sub(r"^doi:\s*", "", d, flags=re.IGNORECASE)
    return d.lower()


# ── the decomposition ─────────────────────────────────────────────────────────

def ingest(result: str | dict) -> IngestResult:
    obj = contract.parse_result_json(result)
    if not isinstance(obj, dict):
        raise ValueError("canonical record must parse to a JSON object")

    core_key, core_shape, entries = detect_core(obj)

    records: list[Record] = []
    evidence: list[EvidenceSpan] = []
    entry_ord = 0  # order counter for nested (entry-placement) evidence

    for i, entry in enumerate(entries):
        entry = entry if isinstance(entry, dict) else {"_value": entry}
        nested = entry.get("evidence")
        field_values = {k: v for k, v in entry.items()
                        if k not in ("evidence", "extraction_confidence")}
        records.append(Record(entry_index=i, field_values=copy.deepcopy(field_values)))
        if isinstance(nested, list):
            for ev in nested:
                if not isinstance(ev, dict):
                    continue
                evidence.append(EvidenceSpan(
                    ord=entry_ord, placement="entry", entry_index=i,
                    field_path=ev.get("field"),
                    snippet=str(ev.get("snippet")) if ev.get("snippet") is not None else None,
                    page=_as_int(ev.get("page")),
                    source=ev.get("source"),
                ))
                entry_ord += 1

    # Flat top-level evidence array (forestplot convention).
    top_ev = obj.get("evidence")
    had_top_evidence = isinstance(top_ev, list)
    if had_top_evidence:
        for j, ev in enumerate(top_ev):
            if not isinstance(ev, dict):
                continue
            fp = ev.get("field")
            evidence.append(EvidenceSpan(
                ord=j, placement="top",
                entry_index=_entry_index_from_field(fp, core_key),
                field_path=fp,
                snippet=str(ev.get("snippet")) if ev.get("snippet") is not None else None,
                page=_as_int(ev.get("page")),
                source=ev.get("source"),
            ))

    # Inline evidence: some models cite a snippet in-place inside the entry — an object with
    # an ``evidence_snippet`` (+ sibling ``evidence_page``) rather than a top-level evidence[]
    # item (e.g. econ headline_classification.<sub>.evidence_snippet). Only SOME of these get
    # mirrored into evidence[], so harvest ALL of them here, keyed to their own field path, so
    # every cited snippet highlights on click. Dedupe against evidence already collected.
    seen_paths = {e.field_path for e in evidence if e.field_path}
    ord_ctr = len(evidence)
    for i, rec in enumerate(records):
        for fp, snip, page, section in _harvest_inline_evidence(rec.field_values, core_key, i):
            if fp in seen_paths:
                continue
            seen_paths.add(fp)
            # placement="inline": derived from an in-place evidence_snippet field that ALREADY
            # lives in field_values. Tagged distinctly so reconstruct() does NOT re-nest it into
            # entry["evidence"] (which would corrupt the loss-free round-trip / export) — it
            # exists only to power highlighting.
            evidence.append(EvidenceSpan(
                ord=ord_ctr, placement="inline", entry_index=i,
                field_path=fp, snippet=snip, page=_as_int(page), source=section,
            ))
            ord_ctr += 1

    # extraction_confidence (non-publishable, kept for the credibility system).
    confidence: list[FieldConfidence] = []
    conf = obj.get("extraction_confidence")
    if isinstance(conf, dict):
        for block, val in conf.items():
            if isinstance(val, dict):
                confidence.append(FieldConfidence(
                    block=block, level=val.get("level"), notes=val.get("notes")))
            else:
                confidence.append(FieldConfidence(block=block, level=None, notes=None))

    paper_metadata_raw = obj.get("paper_metadata")
    paper_typed = _typed_paper(paper_metadata_raw)

    # Leftover publishable scalars carried verbatim (metric / notes / schema_version
    # and any non-chosen core-ish key). Excludes paper_metadata, the core array,
    # and evidence — those are handled structurally above.
    top_extras = {
        k: copy.deepcopy(v) for k, v in obj.items()
        if k in contract.PUBLISH_TOP_LEVEL_KEYS
        and k not in ("paper_metadata", "evidence", core_key)
    }

    return IngestResult(
        core_key=core_key,
        core_shape=core_shape,
        records=records,
        evidence=evidence,
        confidence=confidence,
        paper_metadata_raw=copy.deepcopy(paper_metadata_raw) if isinstance(paper_metadata_raw, dict) else None,
        paper_typed=paper_typed,
        top_extras=top_extras,
        schema_version=obj.get("schema_version"),
        had_top_evidence=had_top_evidence,
    )


def _harvest_inline_evidence(field_values: dict, core_key: str, entry_index: int):
    """Walk an entry's values and yield ``(field_path, snippet, page, section)`` for every
    nested object carrying an ``evidence_snippet``. The field_path is the FULL path to that
    ``evidence_snippet`` leaf (``tables[0].regressions[1].headline_classification.<sub>.evidence_snippet``)
    — same convention as a mirrored top-level evidence item — so it lines up with the rendered
    cell and highlights on click. ``page`` reads ``evidence_page`` then ``page``; ``section``
    reads ``evidence_section``."""
    prefix = f"{core_key}[{entry_index}]"
    out: list[tuple[str, str, Any, Any]] = []

    def walk(node, path):
        if isinstance(node, dict):
            snip = node.get("evidence_snippet")
            if isinstance(snip, str) and snip.strip():
                leaf = f"{path}.evidence_snippet" if path else "evidence_snippet"
                page = node.get("evidence_page", node.get("page"))
                out.append((f"{prefix}.{leaf}", snip, page, node.get("evidence_section")))
            for k, v in node.items():
                if k in ("evidence", "extraction_confidence"):
                    continue
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for idx, v in enumerate(node):
                walk(v, f"{path}[{idx}]")

    walk(field_values, "")
    return out


def _as_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
