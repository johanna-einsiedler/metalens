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
  * K ``field_confidence`` rows — every ``confidence`` block, tagged with the
    instance it rates (``paper`` / ``entry`` / ``child``) so the review UI can badge
    the right group and reconstruct can re-nest it; legacy ``extraction_confidence``
    blocks (root-level ``top``, or the per-sample ``entry_legacy`` form the older MASEM
    prompts demanded) are kept for the credibility system but never published.

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
    child_rid: str | None = None   # id of the sub-entry / table row the path points into
    row_rid: str | None = None     # id of the table row inside that sub-entry


@dataclass
class FieldConfidence:
    block: str                     # the group id (a preset confidence group)
    level: str | None              # high | medium | low
    notes: str | None
    placement: str = "top"         # paper | entry | child | top (legacy root) | entry_legacy
    entry_index: int | None = None # which record it rates (entry / child / entry_legacy)
    field_path: str | None = None  # the rated instance: "paper_metadata", "samples[0]",
                                   # "samples[0].records[2]"; None for legacy root blocks
    ord: int = 0                   # encounter order, so re-nesting keeps the group order


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

# The core-array finder lives in ``contract`` (strip_to_publishable needs the same rule);
# re-exported here for callers that always knew it as ``ingest.detect_core``.
detect_core = contract.detect_core


def _entry_index_from_field(field_path: str | None, core_key: str) -> int | None:
    """Parse the entry index out of an evidence ``field`` path.

    e.g. ``studies._table[3]`` -> 3, ``samples[0].factor_loadings`` -> 0.
    Caption-level paths (``studies``) or non-path strings return None.
    """
    if not field_path:
        return None
    # Anchored first: the declared entries key at the START of the path is the contract.
    # The unanchored search stays as leniency for legacy descriptive paths.
    m = (re.match(rf"\s*{re.escape(core_key)}(?:\._table)?\[(\d+)\]", field_path)
         or re.search(rf"{re.escape(core_key)}(?:\._table)?\[(\d+)\]", field_path))
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

def ingest(result: str | dict, *, entries_key: str | None = None) -> IngestResult:
    """Decompose one canonical document. ``entries_key`` is the core array the preset
    DECLARED (it wins over detection when present); None keeps the legacy detection."""
    obj = contract.parse_result_json(result)
    if not isinstance(obj, dict):
        raise ValueError("canonical record must parse to a JSON object")

    core_key, core_shape, entries = detect_core(obj, preferred=entries_key)

    records: list[Record] = []
    evidence: list[EvidenceSpan] = []
    confidence: list[FieldConfidence] = []
    conf_ord = 0   # one counter across every block, so group order is reproducible
    entry_ord = 0  # order counter for nested (entry-placement) evidence

    def _take_confidence(node: dict, key: str, *, placement: str, entry_index: int | None,
                         field_path: str | None) -> None:
        """Pop a confidence block off ``node`` (in place) into FieldConfidence rows."""
        nonlocal conf_ord
        block = node.get(key)
        if key == contract.CONFIDENCE_KEY and not contract.looks_like_confidence(block):
            return                                   # a data field that happens to share the name
        node.pop(key, None)
        for group, val in (contract.normalize_confidence(block) or {}).items():
            confidence.append(FieldConfidence(
                block=group, level=val["level"], notes=val["notes"], placement=placement,
                entry_index=entry_index, field_path=field_path, ord=conf_ord))
            conf_ord += 1

    # paper-level: identity + declared paper fields, and their confidence block
    paper_metadata_raw = obj.get("paper_metadata")
    if isinstance(paper_metadata_raw, dict):
        paper_metadata_raw = copy.deepcopy(paper_metadata_raw)
        paper_metadata_raw.pop(contract.LEGACY_CONFIDENCE_KEY, None)
        _take_confidence(paper_metadata_raw, contract.CONFIDENCE_KEY, placement="paper",
                         entry_index=None, field_path="paper_metadata")
    else:
        paper_metadata_raw = None

    for i, entry in enumerate(entries):
        entry = entry if isinstance(entry, dict) else {"_value": entry}
        nested = entry.get("evidence")
        fv = copy.deepcopy({k: v for k, v in entry.items() if k != "evidence"})
        here = f"{core_key}[{i}]"
        # the declared block, then the legacy per-sample one (bare strings) — both leave
        # field_values, only the declared one is ever re-nested
        _take_confidence(fv, contract.CONFIDENCE_KEY, placement="entry",
                         entry_index=i, field_path=here)
        _take_confidence(fv, contract.LEGACY_CONFIDENCE_KEY, placement="entry_legacy",
                         entry_index=i, field_path=here)
        # sub-entries: a list of objects carrying their own confidence block
        for k, v in fv.items():
            if not isinstance(v, list):
                continue
            for j, child in enumerate(v):
                if isinstance(child, dict) and contract.CONFIDENCE_KEY in child:
                    _take_confidence(child, contract.CONFIDENCE_KEY, placement="child",
                                     entry_index=i, field_path=f"{here}.{k}[{j}]")
        contract.assign_row_ids(fv)
        records.append(Record(entry_index=i, field_values=fv))
        if isinstance(nested, list):
            for ev in nested:
                if not isinstance(ev, dict):
                    continue
                for fp in _fields_of(ev):
                    evidence.append(EvidenceSpan(
                        ord=entry_ord, placement="entry", entry_index=i,
                        field_path=fp,
                        snippet=str(ev.get("snippet")) if ev.get("snippet") is not None else None,
                        page=_as_int(ev.get("page")),
                        source=ev.get("source"),
                    ))
                    entry_ord += 1

    # Flat top-level evidence array (forestplot convention).
    top_ev = obj.get("evidence")
    had_top_evidence = isinstance(top_ev, list)
    if had_top_evidence:
        top_ord = 0
        for ev in top_ev:
            if not isinstance(ev, dict):
                continue
            for fp in _fields_of(ev):
                evidence.append(EvidenceSpan(
                    ord=top_ord, placement="top",
                    entry_index=_entry_index_from_field(fp, core_key),
                    field_path=fp,
                    snippet=str(ev.get("snippet")) if ev.get("snippet") is not None else None,
                    page=_as_int(ev.get("page")),
                    source=ev.get("source"),
                ))
                top_ord += 1

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

    # Root-level blocks: the legacy ``extraction_confidence`` and a root ``confidence``
    # (which no preset asks for). Both are 'top' — kept for the credibility system, never
    # published.
    for key in (contract.LEGACY_CONFIDENCE_KEY, contract.CONFIDENCE_KEY):
        block = obj.get(key)
        if isinstance(block, dict) and contract.looks_like_confidence(block):
            for group, val in (contract.normalize_confidence(block) or {}).items():
                confidence.append(FieldConfidence(
                    block=group, level=val["level"], notes=val["notes"], placement="top",
                    entry_index=None, field_path=None, ord=conf_ord))
                conf_ord += 1

    # evidence refers to rows by id from here on (positions are only how the model named them)
    fv_by_entry = {r.entry_index: r.field_values for r in records}
    for s in evidence:
        if s.entry_index is not None and s.entry_index in fv_by_entry:
            s.child_rid, s.row_rid = contract.row_refs(s.field_path, fv_by_entry[s.entry_index])

    paper_typed = _typed_paper(paper_metadata_raw)

    # Leftover publishable scalars carried verbatim (metric / notes / schema_version
    # and any non-chosen core-ish key). Excludes paper_metadata, the core array,
    # and evidence — those are handled structurally above.
    top_extras = {
        k: copy.deepcopy(v) for k, v in obj.items()
        if k in contract.publishable_keys(core_key)
        and k not in ("paper_metadata", "evidence", core_key)
    }

    return IngestResult(
        core_key=core_key,
        core_shape=core_shape,
        records=records,
        evidence=evidence,
        confidence=confidence,
        paper_metadata_raw=paper_metadata_raw,
        paper_typed=paper_typed,
        top_extras=top_extras,
        schema_version=obj.get("schema_version"),
        had_top_evidence=had_top_evidence,
    )


def _fields_of(ev: dict) -> list:
    """The path(s) one evidence item supports: ``field`` as a string, or as a LIST when the
    model reused one quote for several values (``fields`` accepted too). The spine stores one
    span per path — ``strip_to_publishable`` canonicalises the same way, so the round-trip
    invariant holds for either form."""
    fp = ev.get("field")
    if fp is None and isinstance(ev.get("fields"), list):
        fp = ev["fields"]
    if isinstance(fp, list):
        return [f for f in fp if isinstance(f, str)] or [None]
    return [fp]


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
                if k in ("evidence", contract.LEGACY_CONFIDENCE_KEY, contract.CONFIDENCE_KEY):
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
