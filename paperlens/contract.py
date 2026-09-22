"""The frozen canonical-record contract.

Every extraction emits ONE JSON object per paper containing:

  * ``paper_metadata`` — identity plus any declared paper-level fields,
  * ONE top-level per-entry array whose key names the domain unit
    (``samples`` / ``records`` / ``studies._table`` / ``tables`` / ...); entries may hold
    nested sub-entry arrays (``samples[i].records[j]``),
  * a flat ``evidence`` array OR per-entry nested ``evidence`` arrays, each
    item ``{snippet, page, source, field}`` where ``field`` is an absolute JSON-path,
  * ``confidence`` blocks nested IN the instance they rate — ``paper_metadata.confidence``,
    ``<entries>[i].confidence``, ``<entries>[i].<child>[j].confidence`` — each
    ``{<group>: {level, notes}}``. These ARE publishable (normalised, see below).
  * a legacy ``extraction_confidence`` block (root-level, or per entry as the older MASEM
    prompts demanded) — NOT publishable, kept only for the credibility system,
  * tabular data wrapped in ``_table`` markers (legacy).

Spec sources in the archive:
  * web/prompt_builder.py:22-81           (the evidence appendix contract)
  * web/donor.py:72 _PUBLISH_TOP_LEVEL_KEYS (the publishable subset)
  * web/pdf_utils.py:261 _parse_result_json (fence/preamble/truncation repair)
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

# The publishable top-level keys — the de-facto record contract. Anything NOT
# in here (e.g. ``extraction_confidence``, page images) is dropped from the
# public/archive format. Mirrors donor._PUBLISH_TOP_LEVEL_KEYS. The document's core
# per-entry array is ALWAYS publishable whatever its key (see ``publishable_keys``).
PUBLISH_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "paper_metadata",
    "samples",
    "summaries",
    "records",
    "studies",
    "evidence",
    "metric",
    "notes",
    "schema_version",
})

# Publishable keys that are NOT the core per-entry array and NOT evidence —
# carried verbatim as document-level extras through ingest/reconstruct.
_NON_CORE_META_KEYS: frozenset[str] = frozenset({
    "paper_metadata", "evidence", "metric", "notes", "schema_version",
})

# Candidate keys for the core per-entry array, in detection priority order.
CORE_ARRAY_CANDIDATES: tuple[str, ...] = ("samples", "records", "studies", "summaries")

# Where the model's self-assessment lives. ``confidence`` is the declared, publishable,
# per-instance block; ``extraction_confidence`` is the legacy (non-publishable) one.
CONFIDENCE_KEY = "confidence"
# Every sub-entry row and table row carries a permanent id under this key (internal: never
# published, never shown). Evidence refers to rows by it, so deleting or inserting a row
# cannot shift a quote onto its neighbour. Rows of the model's output get "o<original index>"
# (deterministic, so a re-ingest of the same response yields the same ids); rows a reviewer
# adds get a random id from the client.
ROW_ID_KEY = "_rid"


def is_rows(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def assign_row_ids(field_values: dict) -> None:
    """Give every row of every list-of-objects (and of the lists inside those rows) an id, in place."""
    for v in field_values.values():
        if not is_rows(v):
            continue
        for j, row in enumerate(v):
            row.setdefault(ROW_ID_KEY, f"o{j}")
            for vv in row.values():
                if is_rows(vv):
                    for r, inner in enumerate(vv):
                        inner.setdefault(ROW_ID_KEY, f"o{r}")


def strip_row_ids(node):
    """A deep copy of ``node`` without any row id (what leaves the system)."""
    if isinstance(node, dict):
        return {k: strip_row_ids(v) for k, v in node.items() if k != ROW_ID_KEY}
    if isinstance(node, list):
        return [strip_row_ids(x) for x in node]
    return node


_ROW_PATH = re.compile(r"^(?:[^.\[]+(?:\._table)?\[\d+\]\.)?(?P<a>[A-Za-z_]\w*)\[(?P<j>\d+)\]"
                       r"(?:\.(?P<b>[A-Za-z_]\w*)\[(?P<r>\d+)\])?(?P<rest>(?:\..*)?)$")


def row_refs(field_path: str | None, field_values: dict) -> tuple[str | None, str | None]:
    """(level-1 row id, level-2 row id) of the row(s) an evidence path points INTO, by the
    positions the path names; (None, None) for a path that names no row."""
    m = _ROW_PATH.match(field_path or "")
    if not m or not isinstance(field_values, dict):
        return None, None
    rows = field_values.get(m["a"])
    j = int(m["j"])
    if not is_rows(rows) or j >= len(rows):
        return None, None
    first = rows[j].get(ROW_ID_KEY)
    second = None
    if m["b"]:
        inner = rows[j].get(m["b"])
        r = int(m["r"])
        if is_rows(inner) and r < len(inner):
            second = inner[r].get(ROW_ID_KEY)
    return first, second


def live_path(field_path: str | None, child_rid: str | None, row_rid: str | None, field_values: dict) -> str | None:
    """The evidence path with its row positions brought up to date from the row ids: where the
    row sits NOW. A row that no longer exists loses its index (the quote then covers the whole
    list). Paths without ids are returned as stored."""
    m = _ROW_PATH.match(field_path or "")
    if not m or not child_rid or not isinstance(field_values, dict):
        return field_path
    prefix = field_path[:m.start("a")]
    rows = field_values.get(m["a"])
    j = next((k for k, row in enumerate(rows) if row.get(ROW_ID_KEY) == child_rid), None) if is_rows(rows) else None
    if j is None:
        return f"{prefix}{m['a']}"
    out = f"{prefix}{m['a']}[{j}]"
    if m["b"]:
        inner = rows[j].get(m["b"])
        r = (next((k for k, row in enumerate(inner) if row.get(ROW_ID_KEY) == row_rid), None)
             if (row_rid and is_rows(inner)) else (int(m["r"]) if not row_rid else None))
        return f"{out}.{m['b']}" if r is None else f"{out}.{m['b']}[{r}]{m['rest']}"
    return out + m["rest"]
LEGACY_CONFIDENCE_KEY = "extraction_confidence"


def publishable_keys(entries_key: str | None = None) -> frozenset[str]:
    """The publishable top-level set for a document whose core array is ``entries_key``.
    A preset may name its entries anything (``tables``, ``regressions``…); that key is
    part of the contract, not an accident of detection."""
    return PUBLISH_TOP_LEVEL_KEYS | ({entries_key} if entries_key else frozenset())


def detect_core(obj: dict, preferred: str | None = None) -> tuple[str, str, list]:
    """Find the per-entry array. Returns (core_key, shape, entries).

    ``preferred`` is the key the preset DECLARED; it wins when present so a document
    can carry a second array (e.g. a top-level ``records`` beside ``samples``) without
    the candidate order deciding which one is the spine. shape is "list" (a JSON array)
    or "table" (``{"_table": [...]}``). Raises ValueError if no core array is present.
    """
    def _as_core(key: str):
        val = obj.get(key)
        if isinstance(val, list):
            return key, "list", val
        if isinstance(val, dict) and isinstance(val.get("_table"), list):
            return key, "table", val["_table"]
        return None

    if preferred and preferred in obj:
        hit = _as_core(preferred)
        if hit:
            return hit
    for key in CORE_ARRAY_CANDIDATES:
        if key in obj:
            hit = _as_core(key)
            if hit:
                return hit
    # Generic fallback: any non-meta top-level key that looks like an entry array.
    for key in obj:
        if key in _NON_CORE_META_KEYS or key in (CONFIDENCE_KEY, LEGACY_CONFIDENCE_KEY):
            continue
        hit = _as_core(key)
        if hit:
            return hit
    raise ValueError("no core per-entry array found in canonical record")


def parse_result_json(result_text: str | dict | list) -> Any | None:
    """Strip markdown fences / surrounding prose and parse the model's JSON.

    Lean port of the archive's ``_parse_result_json``: handles ```json fences,
    a preamble before the first container, and trailing prose after it. Returns
    the parsed object, or ``None`` if it can't be parsed. Already-parsed
    dict/list inputs are returned unchanged.
    """
    if isinstance(result_text, (dict, list)):
        return result_text
    if not result_text:
        return None

    text = result_text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n?```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Locate the first plausible JSON container start, then shrink from the end
    # to strip trailing prose.
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        return None
    candidate = text[min(starts):]
    for end in range(len(candidate), 0, -1):
        if candidate[end - 1] not in "}]":
            continue
        try:
            return json.loads(candidate[:end])
        except json.JSONDecodeError:
            continue
    return None


# ── confidence blocks ─────────────────────────────────────────────────────────

def looks_like_confidence(v: Any) -> bool:
    """Is ``v`` a confidence block — ``{group: "high"}`` or ``{group: {level, notes}}``?
    An empty dict counts (it is dropped either way). A data field that merely happens to
    be called ``confidence`` (a number, a list, a dict of numbers) is left alone."""
    if not isinstance(v, dict):
        return False
    return all(isinstance(x, str) or (isinstance(x, dict) and ("level" in x or "notes" in x))
               for x in v.values())


def _str_or_none(v: Any) -> str | None:
    return None if v is None else str(v)


def normalize_confidence(block: Any) -> dict[str, dict[str, str | None]] | None:
    """Canonical form of a confidence block: ``{group: {"level": str|None, "notes":
    str|None}}``, group order preserved. Bare strings (``"high"``) become a level with no
    notes. Returns None when nothing usable is in it — the block is then dropped, so a
    document that carries ``confidence: {}`` and one that carries nothing are the same
    publishable document."""
    if not isinstance(block, dict):
        return None
    out: dict[str, dict[str, str | None]] = {}
    for group, val in block.items():
        if isinstance(val, str):
            out[str(group)] = {"level": val, "notes": None}
        elif isinstance(val, dict):
            out[str(group)] = {"level": _str_or_none(val.get("level")),
                               "notes": _str_or_none(val.get("notes", val.get("note")))}
    return out or None


def _normalize_instance(d: dict, *, children: bool) -> None:
    """In place: drop the legacy block, canonicalise ``confidence``, recurse one level
    into sub-entry arrays (lists of objects) when ``children``."""
    d.pop(LEGACY_CONFIDENCE_KEY, None)
    if CONFIDENCE_KEY in d and looks_like_confidence(d[CONFIDENCE_KEY]):
        nc = normalize_confidence(d[CONFIDENCE_KEY])
        if nc is None:
            del d[CONFIDENCE_KEY]
        else:
            d[CONFIDENCE_KEY] = nc
    if not children:
        return
    if isinstance(d.get("evidence"), list):              # per-entry nested evidence
        d["evidence"] = _normalize_evidence(d["evidence"])
    for v in d.values():
        if isinstance(v, list):
            for child in v:
                if isinstance(child, dict):
                    _normalize_instance(child, children=False)


def _normalize_evidence(items: list) -> list:
    """One path per item: a ``field`` LIST (one quote reused for several values) becomes
    consecutive single-field items, in place — the form the spine stores and re-emits."""
    out: list = []
    for it in items:
        fp = it.get("field") if isinstance(it, dict) else None
        if fp is None and isinstance(it, dict) and isinstance(it.get("fields"), list):
            fp = it["fields"]
        if not isinstance(fp, list):
            out.append(it)
            continue
        for f in ([x for x in fp if isinstance(x, str)] or [None]):
            one = {k: v for k, v in it.items() if k != "fields"}
            one["field"] = f
            out.append(one)
    return out


def strip_to_publishable(result: str | dict, entries_key: str | None = None) -> dict | None:
    """Return the publishable subset of a parsed result, in canonical form.

    This is the target of the ingest / reconstruct round-trip invariant:
    ``reconstruct(ingest(x)) == strip_to_publishable(x)``. Beyond the top-level whitelist
    (plus the document's core array, whatever it is called) it canonicalises what the
    spine stores in normalised form: ``confidence`` blocks inside ``paper_metadata``, the
    entries and their sub-entries are normalised, and legacy ``extraction_confidence``
    blocks are removed at every level (they were never publishable; the older MASEM
    prompts put them inside each sample, which silently broke the invariant).
    """
    parsed = parse_result_json(result)
    if not isinstance(parsed, dict):
        return None
    try:
        core_key, core_shape, _entries = detect_core(parsed, preferred=entries_key)
    except ValueError:
        core_key, core_shape = None, None
    keys = publishable_keys(core_key)
    out = {k: strip_row_ids(v) for k, v in parsed.items() if k in keys}   # deep copy, internal row ids dropped

    pm = out.get("paper_metadata")
    if isinstance(pm, dict):
        _normalize_instance(pm, children=False)
    if core_key is not None:
        arr = out[core_key]["_table"] if core_shape == "table" else out[core_key]
        for el in arr:
            if isinstance(el, dict):
                _normalize_instance(el, children=True)
    if isinstance(out.get("evidence"), list):
        out["evidence"] = _normalize_evidence(out["evidence"])
    return out
