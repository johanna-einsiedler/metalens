"""Preset access — the ONE facade over the declarative preset format.

Presets stay the source of truth for what an extraction asks for and how the review UI
lays it out. Two stores, one document shape (``preset_spec``):

  * built-in file presets under ``paperlens/presets/<id>.json`` (+ ``<id>.prompt.md``),
  * personal presets in the ``personal_preset`` table (``spec`` column; rows that predate
    the format are upgraded on read by ``presets_legacy``).

``get()`` returns a *meta* dict every existing caller understands — ``title``, ``prompt``
(rendered with default params), ``template_params`` (the param defaults), ``sub_views`` /
``field_types`` / ``render_hints`` (synthesised from the spec) — plus ``spec`` for code
that reads the new format directly. Resolution is by id only (no principal): it runs in
the worker/persist path; ``is_visible`` is the read wall for the by-id routes.
"""
from __future__ import annotations

import copy
import sys
from typing import Any

from . import preset_spec as ps
from . import presets_legacy as legacy

PRESETS_DIR = ps.PRESETS_DIR
LEGACY_IDS = legacy.LEGACY_IDS
ADAPTER_ONLY_IDS = legacy.ADAPTER_ONLY_IDS
resolve_id = legacy.resolve_id


def load_all() -> dict[str, dict]:
    """Every built-in preset, keyed by id → normalised spec."""
    return ps.load_dir(PRESETS_DIR)


def _meta_from_spec(spec: dict, *, source: str = "file", extra: dict | None = None) -> dict:
    fd = ps.field_defs_for(spec)
    meta = {
        "id": spec["id"],
        "title": spec["meta"].get("title"),
        "tagline": spec["meta"].get("tagline"),
        "description": spec["meta"].get("description"),
        "mode": spec["meta"].get("mode"),
        "landing_hidden": bool(spec["meta"].get("hidden")),
        "prompt": ps.render_prompt(spec),
        "template_params": {n: d.get("default") for n, d in (spec["prompt"].get("params") or {}).items()},
        "sub_views": fd["sub_views"],
        "field_types": fd["field_types"],
        "render_hints": fd["render_hints"],
        "schema_id": fd["schema_id"],
        "label": fd["label"],
        "source": source,
        "legacy": False,
        "spec": spec,
    }
    if extra:
        meta.update(extra)
    return meta


def _meta_from_setup(row: dict, extra: dict) -> dict | None:
    """A saved setup: the live BASE preset with the setup's values as parameter defaults.
    The schema id and label stay the base's (same data contract); only the id, title and
    ``params`` are the setup's own."""
    base = load_all().get(resolve_id(row["base_preset_id"]))
    if base is None:
        return None
    spec = copy.deepcopy(base)
    decl = spec["prompt"].get("params") or {}
    for name, val in (row.get("params") or {}).items():
        if name in decl:
            decl[name]["default"] = val
    spec["meta"]["title"] = row.get("title") or spec["meta"].get("title")
    if row.get("tagline"):
        spec["meta"]["tagline"] = row["tagline"]
    spec["meta"]["hidden"] = False
    return _meta_from_spec(spec, source="personal", extra={
        **extra, "id": row["id"], "setup": True, "base_preset_id": spec["id"],
        "params": row.get("params") or {}})


def _meta_from_row(row: dict) -> dict:
    """A ``personal_preset`` row → meta. A row with a ``spec`` is a format-2 preset; an
    older row keeps its stored prompt + grammar authoritative and gets a best-effort spec."""
    extra = {k: row.get(k) for k in ("owner_user_id", "session_id", "visibility", "created_at")}
    if row.get("base_preset_id"):
        return _meta_from_setup(row, extra)
    if row.get("spec"):
        try:
            return _meta_from_spec(ps.normalize(row["spec"]), source="personal", extra=extra)
        except ps.SpecError as e:
            print(f"[presets] personal preset {row.get('id')!r} has an invalid spec: {e}",
                  file=sys.stderr, flush=True)
    try:
        spec = legacy.upgrade_preset(row)
    except ps.SpecError:
        spec = None
    return {**row, "source": "personal", "legacy": True, "spec": spec,
            "landing_hidden": False}


def get(preset_id: str, conn=None) -> dict | None:
    """Resolve a preset by id: built-in first (legacy ids alias to their new names), then a
    personal preset when ``conn`` is given."""
    if not preset_id:
        return None
    pid = resolve_id(preset_id)
    spec = load_all().get(pid)
    if spec is not None:
        return _meta_from_spec(spec)
    if conn is None:
        return None
    from . import records   # lazy: avoid the records <-> presets import cycle
    row = records.get_personal_preset(conn, pid)
    return _meta_from_row(row) if row else None


def prompt_for(preset_id: str | None, conn=None, params: dict | None = None) -> str | None:
    """The full extraction prompt for a preset (author text + generated sections), with
    ``params`` merged over the declared defaults. None if unknown."""
    if not preset_id:
        return None
    meta = get(preset_id, conn)
    if meta is None:
        return None
    if not meta.get("legacy"):
        return ps.render_prompt(meta["spec"], params) if params else meta["prompt"]
    p = meta.get("prompt")
    return p if isinstance(p, str) and p.strip() else None


def emit_schema_row(preset_id: str, conn=None) -> dict[str, Any] | None:
    """What ``schema.field_defs`` holds for this preset: the spec plus the synthesised
    legacy grammar keys (or, for a pre-format-2 personal preset, its stored grammar
    verbatim plus a best-effort spec). None if the preset is unknown."""
    meta = get(preset_id, conn)
    if meta is None:
        return None
    if meta.get("legacy"):
        row = legacy.legacy_schema_row(meta)
        if meta.get("spec") is not None:
            row["spec"] = meta["spec"]
        row["legacy"] = True
        return row
    return ps.field_defs_for(meta["spec"])


def schema_id_for(preset_id: str, conn=None) -> str | None:
    """The content-addressed schema id a run of this preset produces."""
    meta = get(preset_id, conn)
    if meta is None:
        return None
    return meta.get("schema_id") or f"{meta['id']}@v1"


def render(preset_id: str, params: dict | None = None, conn=None) -> dict | None:
    """Everything a run needs from a preset: the rendered prompt for these ``params``,
    the schema id, the review grammar and the spec."""
    meta = get(preset_id, conn)
    if meta is None:
        return None
    if meta.get("legacy"):
        return {"prompt": meta.get("prompt") or "", "sub_views": meta.get("sub_views") or [],
                "schema_id": f"{meta['id']}@v1", "spec": meta.get("spec"), "params": {}}
    spec = meta["spec"]
    resolved = ps.resolve_params(spec, params)
    fd = ps.field_defs_for(spec)
    return {"prompt": ps.render_prompt(spec, resolved), "sub_views": fd["sub_views"],
            "schema_id": fd["schema_id"], "spec": spec, "params": resolved}


def is_visible(conn, preset_id: str, principal) -> bool:
    """May this principal read the preset's prompt/detail? Built-in presets are global.
    A personal preset is readable by its owner, or by anyone once it is public —
    the same wall the picker enforces, applied to the by-id routes."""
    if not preset_id:
        return False
    if resolve_id(preset_id) in load_all():
        return True
    if conn is None:
        return False
    from . import records   # lazy: avoid the records <-> presets import cycle
    meta = records.get_personal_preset(conn, preset_id)
    if meta is None:
        return False
    return meta.get("visibility") == "public" or records.is_preset_owner(conn, preset_id, principal)


# ── resolving what a run uses ─────────────────────────────────────────────────

from dataclasses import dataclass, field as _field   # noqa: E402


@dataclass
class RunSpec:
    """Everything an extraction run needs, decided SERVER-SIDE from what the browser sent."""
    prompt: str                       # the exact prompt to send ("" if the caller must supply one)
    schema_id: str | None             # the row the documents will reference
    field_defs: dict | None           # what to store in that row (None → an 'auto' row)
    spec: dict | None                 # the declaration (None for unknown / adapter-only ids)
    entries_key: str | None           # the declared core array, for ingest
    params: dict = _field(default_factory=dict)
    prompt_edited: bool = False       # the caller posted its own prompt text


def _parse_params(params) -> dict:
    if not params:
        return {}
    if isinstance(params, dict):
        return params
    import json
    try:
        val = json.loads(params)
    except (TypeError, ValueError):
        raise ValueError("params must be a JSON object")
    if not isinstance(val, dict):
        raise ValueError("params must be a JSON object")
    return val


def resolve_run(conn, *, preset_id: str | None = None, schema_id: str | None = None,
                params=None, prompt: str = "") -> RunSpec:
    """Turn ``preset_id`` / ``schema_id`` / ``params`` / an optional edited ``prompt`` into
    the run's prompt and schema row. The rules, in order:

    1. ``preset_id`` names a preset → the server renders the prompt for ``params`` and mints
       the content-addressed schema id (a caller-supplied prompt still wins, flagged).
    2. ``schema_id`` names an EXISTING row → keep it exactly (add-papers to a dataset, imports,
       reproductions), with the prompt re-rendered from its preset when none was posted.
    3. ``schema_id`` names no row but its base id is a preset (a stale client posting
       ``<preset>@v1``) → treated as rule 1.
    4. Anything else (``extract@v1`` from a custom prompt, unknown ids) → the id is kept
       verbatim with no grammar (an 'auto' row); the caller must have posted a prompt.
    """
    from . import records   # lazy: avoid the records <-> presets import cycle
    params = _parse_params(params)
    prompt = prompt or ""
    pid = resolve_id(preset_id) if preset_id else None
    if not pid and schema_id and records.get_schema(conn, schema_id) is None:
        base = resolve_id(schema_id.partition("@")[0])
        if get(base, conn) is not None:
            pid = base                                            # rule 3
    if pid:
        out = render(pid, params, conn=conn)
        if out is not None:                                       # rule 1
            rendered = out["prompt"]
            chosen = prompt.strip() or rendered
            return RunSpec(prompt=chosen, schema_id=out["schema_id"],
                           field_defs=emit_schema_row(pid, conn), spec=out.get("spec"),
                           entries_key=(out.get("spec") or {}).get("entries", {}).get("key"),
                           params=out.get("params") or {},
                           prompt_edited=bool(prompt.strip()) and prompt.strip() != rendered.strip())
    if schema_id:
        row = records.get_schema(conn, schema_id)
        if row is not None:                                       # rule 2
            fd = row.get("field_defs") or {}
            spec = fd.get("spec") if isinstance(fd, dict) else None
            base = row.get("preset_id") or schema_id.partition("@")[0]
            rendered = prompt_for(base, conn) or "" if not prompt.strip() else ""
            return RunSpec(prompt=prompt.strip() or rendered, schema_id=schema_id,
                           field_defs=fd or None, spec=spec,
                           entries_key=(spec or {}).get("entries", {}).get("key"),
                           params=params, prompt_edited=bool(prompt.strip()))
    return RunSpec(prompt=prompt.strip(), schema_id=schema_id or None, field_defs=None,
                   spec=None, entries_key=None, params=params,
                   prompt_edited=bool(prompt.strip()))                            # rule 4
