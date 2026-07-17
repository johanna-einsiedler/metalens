"""The frozen canonical-record contract (v1).

Carried over verbatim from the archived pipeline as *spec*, not inherited code.
Every extraction emits ONE JSON object per paper containing:

  * a top-level per-entry array whose key names the domain unit
    (``samples`` / ``records`` / ``studies._table`` / ``tables`` / ...),
  * a flat ``evidence`` array OR per-entry nested ``evidence`` arrays, each
    item ``{snippet, page, source, field}`` where ``field`` is a JSON-path,
  * an optional ``extraction_confidence`` block (NOT publishable),
  * tabular data wrapped in ``_table`` markers.

Spec sources in the archive:
  * web/prompt_builder.py:22-81           (the evidence appendix contract)
  * web/donor.py:72 _PUBLISH_TOP_LEVEL_KEYS (the publishable subset)
  * web/pdf_utils.py:261 _parse_result_json (fence/preamble/truncation repair)
"""
from __future__ import annotations

import json
import re
from typing import Any

# The publishable top-level keys — the de-facto record contract. Anything NOT
# in here (e.g. ``extraction_confidence``, page images) is dropped from the
# public/archive format. Mirrors donor._PUBLISH_TOP_LEVEL_KEYS.
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


def strip_to_publishable(result: str | dict) -> dict | None:
    """Return the whitelisted publishable subset of a parsed result.

    Mirrors donor._strip_to_publishable. This is the target of the ingest /
    reconstruct round-trip invariant: ``reconstruct(ingest(x)) ==
    strip_to_publishable(x)``.
    """
    parsed = parse_result_json(result)
    if not isinstance(parsed, dict):
        return None
    return {k: v for k, v in parsed.items() if k in PUBLISH_TOP_LEVEL_KEYS}
