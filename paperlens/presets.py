"""Preset access + schema-row emission (plan §3 "schema entity").

Presets remain the SOURCE OF TRUTH. This module is a thin facade over the
vendored ``presets_loader`` (the archive's full template-rendering machinery):

  * ``load_all`` / ``get`` — discover presets with their prompt fully rendered
    from ``<id>.template.md`` + ``template_params`` and their ``sub_views`` resolved.
  * ``prompt_for`` — the rendered extraction prompt (so /api/extract works for
    masem / econ / ai-findings, not just inline-prompt presets like forestplot).
  * ``emit_schema_row`` — serialize the resolved view-grammar into the JSONB
    ``schema.field_defs`` the SPA/validators read (presets never become primary).
"""
from __future__ import annotations

from typing import Any

from . import presets_loader

PRESETS_DIR = presets_loader.PRESETS_DIR


def load_all() -> dict[str, dict]:
    return presets_loader.load_all()


def get(preset_id: str, conn=None) -> dict | None:
    """Resolve a preset by id. File presets (presets/*.json) are the source of truth;
    if ``conn`` is given and the id isn't a file preset, fall back to a DB-backed
    PERSONAL preset. Resolution is by id only (no principal) — it runs in the
    worker/persist path — so any preset id resolves regardless of who owns it."""
    meta = presets_loader.get(preset_id)
    if meta is not None:
        return meta
    if conn is not None:
        from . import records   # lazy: avoid the records <-> presets import cycle
        return records.get_personal_preset(conn, preset_id)
    return None


def prompt_for(preset_id: str | None, conn=None) -> str | None:
    """The fully-rendered extraction prompt for a preset, or None if unknown."""
    if not preset_id:
        return None
    meta = get(preset_id, conn)
    p = meta.get("prompt") if meta else None
    return p if isinstance(p, str) and p.strip() else None


def emit_schema_row(preset_id: str, conn=None) -> dict[str, Any] | None:
    """Serialize a preset's resolved view-grammar into ``schema.field_defs``.

    Returns None if the preset is unknown. Content-hashable so a preset change
    can mint a new immutable schema version. Resolves file OR (with ``conn``) DB presets.
    """
    meta = get(preset_id, conn)
    if meta is None:
        return None
    sub_views = meta.get("sub_views") or []
    data_sources = (meta.get("template_params") or {}).get("data_sources") or []

    evidence_keys = sorted({k for sv in sub_views for k in sv.get("evidence_keys", [])})
    confidence_keys = sorted({k for sv in sub_views for k in sv.get("confidence_keys", [])})
    core_keys = sorted({k for sv in sub_views for k in sv.get("include_keys", [])
                        if k not in ("sample_id", "n")})

    return {
        "preset_id": meta["id"],
        "title": meta.get("title"),
        "tagline": meta.get("tagline"),
        "mode": meta.get("mode"),
        "schema_version": meta.get("schema_version"),
        "data_sources": data_sources,
        "sub_views": sub_views,
        "evidence_keys": evidence_keys,
        "confidence_keys": confidence_keys,
        "core_keys": core_keys,
    }
