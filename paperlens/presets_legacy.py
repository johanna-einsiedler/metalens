"""Format-1 presets and legacy schema rows, seen through the format-2 model.

Nothing here writes anything. Two jobs:

  * ``upgrade_preset(meta)`` — a pre-format-2 preset (an old ``personal_preset`` row, or
    a stray v1 file) becomes a best-effort spec so the rest of the app has ONE shape to
    read. The old grammar keys it carried (``sub_views``, ``field_types``, ``render_hints``)
    are still emitted verbatim by ``legacy_schema_row`` so documents extracted under it
    render exactly as before.
  * ``upgrade_field_defs(old, core_key, core_shape, records)`` — an old ``schema.field_defs``
    row (or a NULL one from a designer run) plus the document's own records becomes a spec
    at read time, so the review UI can be spec-driven for old documents too. The row on
    disk is never touched.

Renamed preset ids live here as well: documents keep the id they were extracted under
forever, so the old names must keep resolving.
"""
from __future__ import annotations

import re
from typing import Any

from . import preset_spec as ps

# Preset ids that were renamed. ``record.schema_id`` still carries the old id (e.g.
# ``masem@v3``), and add-papers re-resolves the prompt by it.
LEGACY_IDS = {
    "masem":       "masem-direct",
    "masem-ncs18": "masem-indirect",
}
# Presets whose files are gone. Their schema rows still exist (published catalogue
# imports, old extractions); they render through the adapter but can't produce a prompt.
ADAPTER_ONLY_IDS = frozenset({"ai-findings", "econ-headline", "forestplot", "hai-screening"})

_CORE_LABELS = {"samples": "Sample", "records": "Record", "studies": "Study", "summaries": "Summary",
                "findings": "Finding", "tables": "Table", "regressions": "Regression"}


def resolve_id(preset_id: str) -> str:
    return LEGACY_IDS.get(preset_id, preset_id)


# ── v1 preset meta → the old schema row (verbatim behaviour) ─────────────────

def legacy_schema_row(meta: dict) -> dict:
    """Exactly what ``presets.emit_schema_row`` produced for a v1 preset, so existing
    documents keep their grammar."""
    sub_views = meta.get("sub_views") or []
    tparams = meta.get("template_params") or {}
    field_types = meta.get("field_types") or tparams.get("field_types") or {}
    render_hints = meta.get("render_hints") or tparams.get("render_hints") or {}
    return {
        "preset_id": meta["id"],
        "title": meta.get("title"),
        "tagline": meta.get("tagline"),
        "mode": meta.get("mode"),
        "schema_version": meta.get("schema_version"),
        "data_sources": tparams.get("data_sources") or [],
        "sub_views": sub_views,
        "field_types": field_types,
        "render_hints": render_hints,
        "evidence_keys": sorted({k for sv in sub_views for k in sv.get("evidence_keys", [])}),
        "confidence_keys": sorted({k for sv in sub_views for k in sv.get("confidence_keys", [])}),
        "core_keys": sorted({k for sv in sub_views for k in sv.get("include_keys", [])
                             if k not in ("sample_id", "n")}),
    }


def _field_from_type(name: str, ft: dict | None) -> dict:
    ft = ft or {}
    t = ft.get("type")
    if t == "select" and ft.get("options"):
        return {"name": name, "type": "enum", "options": list(ft["options"]),
                "allow_other": bool(ft.get("allow_other"))}
    if t == "multiselect" and ft.get("options"):
        return {"name": name, "type": "multi", "options": list(ft["options"])}
    if t == "number":
        return {"name": name, "type": "number"}
    return {"name": name, "type": "string"}


def _guess_entries_key(prompt: str) -> str:
    m = re.search(r'"(samples|records|studies|summaries|tables|findings|regressions)"\s*:', prompt or "")
    return m.group(1) if m else "records"


def upgrade_preset(meta: dict) -> dict:
    """A v1 preset dict (already prompt-inlined) → a validated format-2 spec. Best effort:
    the field list is whatever the old grammar named; anything else the model returned
    still shows in the review UI under "Other"."""
    sub_views = meta.get("sub_views") or []
    tparams = meta.get("template_params") or {}
    field_types = meta.get("field_types") or tparams.get("field_types") or {}
    names: list[str] = []
    for sv in sub_views:
        for k in sv.get("include_keys") or []:
            if k not in names:
                names.append(k)
    for k in field_types:
        leaf = k.split(".")[-1]
        if leaf not in names:
            names.append(leaf)
    fields = [_field_from_type(n, field_types.get(n)) for n in names
              if ps.NAME_RE.match(n) and n not in ps.RESERVED_NAMES]
    groups = []
    for sv in sub_views:
        for g in sv.get("confidence_keys") or []:
            if ps.NAME_RE.match(g) and g not in {x["id"] for x in groups}:
                groups.append({"id": g, "label": ps.format_key(g), "scope": "entry"})
    known = {f["name"] for f in fields}
    tabs = []
    for sv in sub_views:
        tid = sv.get("id") or f"tab{len(tabs) + 1}"
        if not ps.NAME_RE.match(tid) or tid in {t["id"] for t in tabs}:
            continue
        inc = [k for k in (sv.get("include_keys") or []) if k in known]
        if sv.get("exclude_keys") and not inc:
            inc = [k for k in known if k not in sv["exclude_keys"]
                   and k not in {x for t in tabs for x in t["fields"]}]
        tabs.append({"id": tid, "label": sv.get("label") or ps.format_key(tid), "fields": inc})
    key = _guess_entries_key(meta.get("prompt") or "")
    doc = {
        "format": ps.FORMAT, "id": meta["id"], "version": 1,
        "meta": {"title": meta.get("title") or meta["id"], "tagline": meta.get("tagline") or "",
                 "description": meta.get("description") or "",
                 "mode": meta.get("mode") if meta.get("mode") in ("extraction", "summarize") else "extraction",
                 "hidden": bool(meta.get("landing_hidden"))},
        # the whole old prompt is author text; it already carries its own schema sections
        "prompt": {"text": meta.get("prompt") or "(no prompt)", "params": {}, "generate": []},
        "paper": {"fields": []},
        "entries": {"key": key, "label": _CORE_LABELS.get(key, ps.format_key(key).rstrip("s") or "Entry"),
                    "fields": fields, "children": []},
        "confidence": {"groups": groups},
        "display": {"tabs": tabs} if tabs else {},
    }
    return ps.normalize(doc)


# ── an old schema row + its data → a spec, at read time ──────────────────────

def _infer_type(values: list[Any]) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "string"
    if all(isinstance(v, bool) for v in vals):
        return "boolean"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "integer" if all(isinstance(v, int) for v in vals) else "number"
    if all(isinstance(v, str) for v in vals):
        return "text" if any("\n" in v or len(v) > 160 for v in vals) else "string"
    return "string"


def _rows_of(v: Any) -> list[dict] | None:
    """The row objects of an array-of-objects or a ``{"_table": [...]}`` value."""
    if isinstance(v, dict) and isinstance(v.get("_table"), list):
        v = v["_table"]
    if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
        return v
    return None


def _column_defs(rows: list[dict]) -> list[dict]:
    names: list[str] = []
    for r in rows:
        for k in r:
            if k not in names and k not in ps.RESERVED_NAMES and ps.NAME_RE.match(k):
                names.append(k)
    return [{"name": n, "type": t if t != "text" else "string"}
            for n in names for t in [_infer_type([r.get(n) for r in rows])]
            if not isinstance(next((r.get(n) for r in rows if r.get(n) is not None), None), (dict, list))]


def upgrade_field_defs(old: dict | None, core_key: str, core_shape: str,
                       records: list[dict]) -> dict:
    """A legacy ``schema.field_defs`` (may be None) + the document's records → a format-2
    ``field_defs`` dict: the old keys preserved verbatim (the old UI path) plus ``spec``
    inferred from the data and ``legacy: True`` so the new UI keeps the data-driven
    behaviours (constant hoisting, shape-driven cards) only for these documents."""
    old = old or {}
    field_types = old.get("field_types") or {}
    render_hints = old.get("render_hints") or {}
    sub_views = old.get("sub_views") or []
    fvs = [r.get("field_values") if isinstance(r, dict) and "field_values" in r else r
           for r in records or []]
    fvs = [fv for fv in fvs if isinstance(fv, dict)]
    names: list[str] = []
    for fv in fvs:
        for k in fv:
            if k not in names:
                names.append(k)
    for sv in sub_views:
        for k in sv.get("include_keys") or []:
            if k not in names:
                names.append(k)
    fields: list[dict] = []
    children: list[dict] = []
    for n in names:
        if n in ps.RESERVED_NAMES or not ps.NAME_RE.match(n):
            continue
        values = [fv.get(n) for fv in fvs]
        rows = next((r for r in (_rows_of(v) for v in values) if r), None)
        if rows is not None:
            cols = _column_defs(rows)
            nested = any(_rows_of(x) for r in rows for x in r.values())
            hint = render_hints.get(n) if isinstance(render_hints.get(n), dict) else None
            if (hint and hint.get("as") == "cards") or nested:
                children.append({"key": n, "label": ps.format_key(n).rstrip("s") or n,
                                 "layout": "cards", "evidence": "row",
                                 "fields": cols or [{"name": "value", "type": "string"}]})
            else:
                fields.append({"name": n, "type": "table", "evidence": "table",
                               "columns": cols or [{"name": "value", "type": "string"}]})
            continue
        sample = next((v for v in values if v is not None), None)
        if isinstance(sample, dict):
            # legacy dotted-key objects (factor_loadings: {"F1.2": …}) — shown read-only
            fields.append({"name": n, "type": "table", "evidence": "table",
                           "columns": [{"name": "key", "type": "string"}, {"name": "value", "type": "string"}]})
            continue
        if isinstance(sample, list):
            fields.append({"name": n, "type": "list", "evidence": "none"})
            continue
        ft = field_types.get(n)
        if ft:
            fields.append(_field_from_type(n, ft))
        else:
            fields.append({"name": n, "type": _infer_type(values)})
    known = {f["name"] for f in fields} | {c["key"] for c in children}
    tabs = []
    for sv in sub_views:
        tid = sv.get("id") or f"tab{len(tabs) + 1}"
        if not ps.NAME_RE.match(tid) or tid in {t["id"] for t in tabs}:
            continue
        inc = [k for k in (sv.get("include_keys") or []) if k in known]
        if sv.get("exclude_keys") and not inc:
            inc = [k for k in known if k not in sv["exclude_keys"]
                   and k not in {x for t in tabs for x in t["fields"]}]
        tabs.append({"id": tid, "label": sv.get("label") or ps.format_key(tid), "fields": inc})
    groups = []
    for sv in sub_views:
        for g in sv.get("confidence_keys") or []:
            if ps.NAME_RE.match(g) and g not in {x["id"] for x in groups}:
                groups.append({"id": g, "label": ps.format_key(g), "scope": "entry"})
    pid = old.get("preset_id") or core_key
    if not ps.ID_RE.match(str(pid)):
        pid = "legacy"
    title_tpl = next((f"{{{n}}}" for n in ("sample_id", "table_id", "title", "id", "study_id") if n in known), None)
    doc = {
        "format": ps.FORMAT, "id": pid, "version": 1,
        "meta": {"title": old.get("title") or ps.format_key(pid), "tagline": old.get("tagline") or "",
                 "mode": old.get("mode") if old.get("mode") in ("extraction", "summarize") else "extraction"},
        "prompt": {"text": "(legacy document — prompt not recorded)", "params": {}, "generate": []},
        "paper": {"fields": []},
        "entries": {"key": core_key if ps.NAME_RE.match(core_key or "") else "records",
                    "label": _CORE_LABELS.get(core_key, ps.format_key(core_key or "record").rstrip("s") or "Entry"),
                    **({"title": title_tpl} if title_tpl else {}),
                    "fields": fields, "children": children},
        "confidence": {"groups": groups},
        "display": {"tabs": tabs} if tabs else {},
    }
    try:
        spec = ps.normalize(doc)
    except ps.SpecError:
        doc["entries"]["fields"] = [f for f in fields if f.get("type") != "table"]
        doc["entries"]["children"] = []
        doc["display"] = {}
        spec = ps.normalize(doc)
    return {**old, "format": ps.FORMAT, "legacy": True, "spec": spec}
