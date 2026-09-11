"""Declarative presets (format 2): load, validate, hash, and render.

ONE document describes an extraction task end to end — what to ask the model for, what
comes back, and how a coder verifies it:

  * ``meta``        — title / tagline / description / mode / hidden          (metadata)
  * ``prompt``      — the author's task instructions (+ typed ``${params}``) and which
                      structural sections the SYSTEM generates from the declaration
  * ``paper``       — fields extracted once per paper  → ``paper_metadata.<name>``
  * ``entries``     — the per-entry array (its key, fields, and one level of ``children``
                      sub-entry arrays)                                       (structure)
  * ``confidence``  — named groups with a scope (paper / entry / child) the model rates
  * ``display``     — tabs, layouts, triage: how the review UI lays the result out

File presets are ``paperlens/presets/<id>.json`` with a sibling ``<id>.prompt.md``
(``prompt.file``); DB presets store the identical *normalised* document (``prompt.file``
inlined into ``prompt.text``). Nothing in here touches the database.

The generated prompt sections and the review UI read the SAME declaration, so they can
no longer disagree — which is the whole point of the format.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import string
import sys
from pathlib import Path
from typing import Any

PRESETS_DIR = Path(__file__).parent / "presets"
FORMAT = 2

FIELD_TYPES = ("string", "text", "integer", "number", "boolean", "enum", "multi", "list", "table")
CODED_TYPES = frozenset({"enum", "multi", "boolean"})   # values the model assigns, not quotes
SCALAR_TYPES = ("string", "text", "integer", "number", "boolean", "enum", "multi", "list")
PARAM_TYPES = ("string", "text", "integer", "number", "boolean", "list", "table")
CONF_SCOPES = ("paper", "entry", "child")
GENERATED_SECTIONS = ("paper_metadata", "output_schema", "evidence", "confidence", "return_format")
EVIDENCE_BY_KIND = {          # what each kind of thing may declare
    "scalar": ("value", "row", "none"),
    "table": ("table", "row", "none"),
    "entries": ("row", "none"),
    "child": ("row", "table", "none"),
}
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
# a field may not be called any of these — they are structural keys of the output
RESERVED_NAMES = frozenset({"evidence", "confidence", "extraction_confidence", "_table", "_value"})
# the entries array may not shadow a top-level structural key
RESERVED_ENTRY_KEYS = frozenset({"paper_metadata", "evidence", "confidence", "extraction_confidence",
                                 "metric", "notes", "schema_version"})
# always present in paper_metadata, whatever the preset declares
IDENTITY_FIELDS = (
    ("title", "string", "the full paper title verbatim (required — never an empty string)"),
    ("doi", "string", "the DOI (e.g. \"10.1037/abc.0000123\") if printed anywhere; else null"),
    ("year", "integer", "publication year as an integer; else null"),
    ("authors", "list", "the author list in printed order, one string per author; else null"),
    ("journal", "string", "the full venue / journal name; else null"),
)

_ALLOWED_KEYS = {
    "root": {"format", "id", "version", "meta", "prompt", "paper", "entries", "confidence", "display"},
    "meta": {"title", "tagline", "description", "mode", "hidden"},
    "prompt": {"file", "text", "params", "generate"},
    "param": {"type", "label", "help", "default", "columns", "numbered", "empty"},
    "paper": {"fields"},
    "entries": {"key", "label", "help", "cardinality", "id_field", "title", "evidence", "fields", "children"},
    "child": {"key", "label", "help", "title", "evidence", "layout", "fields"},
    "field": {"name", "label", "type", "help", "required", "range", "options", "allow_other",
              "columns", "evidence", "confidence"},
    "column": {"name", "label", "type", "help", "required", "range", "options", "allow_other"},
    "confidence": {"levels", "notes", "groups"},
    "group": {"id", "label", "scope", "help"},
    "display": {"tabs", "entries", "grid_rows", "triage", "paper_panel"},
    "tab": {"id", "label", "fields"},
}


class SpecError(ValueError):
    """A preset document that failed validation; ``errors`` lists every problem."""

    def __init__(self, errors: list[str], preset_id: str | None = None):
        self.errors = list(errors)
        self.preset_id = preset_id
        head = f"preset {preset_id!r}: " if preset_id else "preset: "
        super().__init__(head + "; ".join(self.errors[:6]) + (" …" if len(self.errors) > 6 else ""))


# ── small helpers ─────────────────────────────────────────────────────────────

def format_key(name: str) -> str:
    """``factor_loadings`` → ``Factor Loadings`` (the label a field gets when it has none;
    mirrors grammar.js formatKey so the prompt and the UI agree)."""
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), str(name).replace("_", " "))


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _read_sibling(name: str, base_dir: Path) -> str:
    """Read a file next to the preset JSON, refusing anything outside the presets dir."""
    candidate = (base_dir / name).resolve()
    try:
        candidate.relative_to(PRESETS_DIR.resolve())
    except ValueError:
        raise SpecError([f"prompt.file {name!r} is outside the presets directory"])
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError as e:
        raise SpecError([f"prompt.file {name!r} could not be read: {e}"])


def _collapse_blank_lines(text: str) -> str:
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip("\n") + "\n"


# ── loading / normalising ─────────────────────────────────────────────────────

def is_v2(doc: Any) -> bool:
    return isinstance(doc, dict) and doc.get("format") == FORMAT


def load_file(path: Path) -> dict:
    """Parse one ``<id>.json`` into a validated, normalised spec (prompt inlined)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SpecError([f"cannot read {Path(path).name}: {e}"])
    if not is_v2(doc):
        raise SpecError([f"{Path(path).name} is not a format-{FORMAT} preset"])
    return normalize(doc, base_dir=Path(path).parent)


def load_dir(directory: Path = PRESETS_DIR) -> dict[str, dict]:
    """Every valid format-2 preset directly under ``directory`` (sub-folders are not
    globbed). Invalid files are reported on stderr and skipped, never fatal."""
    out: dict[str, dict] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[presets] skipping {path.name}: {e}", file=sys.stderr, flush=True)
            continue
        if not is_v2(raw):
            continue                      # legacy files are the facade's business
        try:
            spec = normalize(raw, base_dir=path.parent)
        except SpecError as e:
            print(f"[presets] skipping {path.name}: {e}", file=sys.stderr, flush=True)
            continue
        if spec["id"] in out:
            print(f"[presets] duplicate id {spec['id']!r} in {path.name}; ignoring",
                  file=sys.stderr, flush=True)
            continue
        out[spec["id"]] = spec
    return out


def _default_evidence(field: dict) -> str:
    return "table" if field.get("type") == "table" else "value"


def _norm_field(f: dict, *, column: bool = False) -> dict:
    out = dict(f)
    out.setdefault("type", "string")
    out.setdefault("label", format_key(out.get("name", "")))
    out.setdefault("required", False)
    if not column:
        out.setdefault("evidence", _default_evidence(out))
        if out["type"] == "table":
            out["columns"] = [_norm_field(c, column=True) for c in (out.get("columns") or [])]
    return out


def normalize(doc: dict, base_dir: Path | None = None) -> dict:
    """Deep-copy, inline ``prompt.file``, fill every default, validate. Raises SpecError.
    The result is the ONE shape every other function in this module reads."""
    spec = copy.deepcopy(doc)
    spec.setdefault("version", 1)
    spec.setdefault("meta", {})
    spec["meta"].setdefault("mode", "extraction")
    spec["meta"].setdefault("hidden", False)
    prompt = spec.setdefault("prompt", {})
    if prompt.get("file") and not prompt.get("text"):
        if base_dir is None:
            raise SpecError([f"prompt.file {prompt['file']!r} given but no base directory to read it from"],
                            spec.get("id"))
        prompt["text"] = _read_sibling(prompt["file"], Path(base_dir))
    prompt.pop("file", None)
    prompt.setdefault("params", {})
    for pname, pdecl in list(prompt["params"].items()):
        if not isinstance(pdecl, dict):             # shorthand: a bare default value
            pdecl = {"default": pdecl}
        pdecl.setdefault("type", _param_type_from_default(pdecl.get("default")))
        pdecl.setdefault("label", format_key(pname))
        pdecl.setdefault("default", None)
        prompt["params"][pname] = pdecl
    prompt.setdefault("generate", list(GENERATED_SECTIONS))
    paper = spec.setdefault("paper", {})
    paper["fields"] = [_norm_field(f) for f in (paper.get("fields") or [])]
    entries = spec.setdefault("entries", {})
    if entries:
        entries.setdefault("label", format_key(entries.get("key", "entry")).rstrip("s") or "Entry")
        entries.setdefault("cardinality", "many")
        entries.setdefault("evidence", "row")
        entries["fields"] = [_norm_field(f) for f in (entries.get("fields") or [])]
        children = []
        for ch in entries.get("children") or []:
            ch = dict(ch)
            ch.setdefault("label", format_key(ch.get("key", "item")).rstrip("s") or "Item")
            ch.setdefault("evidence", "row")
            ch.setdefault("layout", "table")
            ch["fields"] = [_norm_field(f) for f in (ch.get("fields") or [])]
            children.append(ch)
        entries["children"] = children
    conf = spec.setdefault("confidence", {})
    conf.setdefault("levels", ["high", "medium", "low"])
    conf.setdefault("notes", True)
    conf["groups"] = [dict({"scope": "entry"}, **g) for g in (conf.get("groups") or [])]
    for g in conf["groups"]:
        g.setdefault("label", format_key(g.get("id", "")))
    display = spec.setdefault("display", {})
    display.setdefault("entries", "cards")
    display.setdefault("grid_rows", "entries")
    display.setdefault("triage", "declaration")
    display.setdefault("paper_panel", "open")
    errors, _warnings = validate(spec)
    if errors:
        raise SpecError(errors, spec.get("id"))
    return spec


def _param_type_from_default(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, list):
        return "table" if v and all(isinstance(x, dict) for x in v) else "list"
    return "string"


# ── validation ────────────────────────────────────────────────────────────────

def validate(spec: dict) -> tuple[list[str], list[str]]:
    """Every problem at once, path-prefixed. Returns (errors, warnings); errors block
    save and run, warnings are advice for the author."""
    errs: list[str] = []
    warns: list[str] = []
    E, W = errs.append, warns.append

    def unknown(obj: dict, kind: str, at: str) -> None:
        for k in obj:
            if k not in _ALLOWED_KEYS[kind]:
                E(f"{at}: unknown key {k!r}")

    if not isinstance(spec, dict):
        return ["preset must be a JSON object"], []
    unknown(spec, "root", "$")
    if spec.get("format") != FORMAT:
        E(f"$.format must be {FORMAT}")
    pid = spec.get("id")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        E("$.id must match ^[a-z][a-z0-9-]{1,63}$ (no '@')")
    if not isinstance(spec.get("version", 1), int) or spec.get("version", 1) < 1:
        E("$.version must be a positive integer")

    meta = spec.get("meta")
    if not isinstance(meta, dict):
        E("$.meta must be an object"); meta = {}
    else:
        unknown(meta, "meta", "$.meta")
        if not (isinstance(meta.get("title"), str) and meta["title"].strip()):
            E("$.meta.title is required")
        if meta.get("mode") not in ("extraction", "summarize"):
            E("$.meta.mode must be 'extraction' or 'summarize'")
        if not isinstance(meta.get("hidden", False), bool):
            E("$.meta.hidden must be a boolean")

    prompt = spec.get("prompt")
    params: dict = {}
    if not isinstance(prompt, dict):
        E("$.prompt must be an object"); prompt = {}
    else:
        unknown(prompt, "prompt", "$.prompt")
        if not (isinstance(prompt.get("text"), str) and prompt["text"].strip()):
            E("$.prompt.text (or prompt.file) is required and must not be empty")
        params = prompt.get("params") or {}
        if not isinstance(params, dict):
            E("$.prompt.params must be an object"); params = {}
        for pname, pdecl in params.items():
            at = f"$.prompt.params.{pname}"
            if not NAME_RE.match(str(pname)):
                E(f"{at}: parameter names must match ^[A-Za-z_][A-Za-z0-9_]*$")
            if not isinstance(pdecl, dict):
                E(f"{at}: must be an object"); continue
            unknown(pdecl, "param", at)
            if pdecl.get("type") not in PARAM_TYPES:
                E(f"{at}.type must be one of {', '.join(PARAM_TYPES)}")
            if pdecl.get("type") == "table":
                cols = pdecl.get("columns")
                if not (isinstance(cols, list) and cols and all(isinstance(c, str) for c in cols)):
                    E(f"{at}.columns is required for a table parameter")
        gen = prompt.get("generate", list(GENERATED_SECTIONS))
        if not isinstance(gen, list) or any(g not in GENERATED_SECTIONS for g in gen):
            E(f"$.prompt.generate may only contain {', '.join(GENERATED_SECTIONS)}")
        elif "output_schema" not in gen:
            W("$.prompt.generate: the output schema is not generated — the prompt text must spell out "
              "the JSON structure itself, and only a person keeps it in step with the declaration")
        # every ${x} in the author text must be a param or a generated section
        if isinstance(prompt.get("text"), str):
            for ident in _placeholders(prompt["text"]):
                if ident not in params and ident not in GENERATED_SECTIONS:
                    E(f"$.prompt.text references ${{{ident}}}, which is neither a declared "
                      f"parameter nor a generated section")

    # fields ------------------------------------------------------------------
    seen: dict[str, str] = {}   # name -> where

    def check_field(f: Any, at: str, *, kind: str, column: bool = False) -> None:
        if not isinstance(f, dict):
            E(f"{at}: must be an object"); return
        unknown(f, "column" if column else "field", at)
        name = f.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            E(f"{at}.name must match ^[A-Za-z_][A-Za-z0-9_]*$ (no '.', '[' or spaces)")
            name = None
        elif name in RESERVED_NAMES:
            E(f"{at}.name {name!r} is reserved")
        ftype = f.get("type", "string")
        if ftype not in FIELD_TYPES:
            E(f"{at}.type must be one of {', '.join(FIELD_TYPES)}")
        if column and ftype == "table":
            E(f"{at}: a table column cannot itself be a table")
        opts = f.get("options")
        if ftype in ("enum", "multi"):
            if isinstance(opts, str):
                m = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", opts)
                if not m or m.group(1) not in params:
                    E(f"{at}.options: '${{param}}' must name a declared parameter")
                elif params[m.group(1)].get("type") not in ("list", "table"):
                    E(f"{at}.options: parameter {m.group(1)!r} must be a list or table")
            elif not (isinstance(opts, list) and opts):
                E(f"{at}.options is required (non-empty) for type {ftype}")
            else:
                vals = [o.get("value") if isinstance(o, dict) else o for o in opts]
                if any(isinstance(v, (dict, list)) or v is None for v in vals):
                    E(f"{at}.options entries must be scalars or {{value, label}} objects")
                if len({json.dumps(v) for v in vals}) != len(vals):
                    E(f"{at}.options must be unique")
        elif opts is not None:
            E(f"{at}.options only applies to enum / multi fields")
        if f.get("allow_other") and ftype != "enum":
            E(f"{at}.allow_other only applies to enum fields")
        rng = f.get("range")
        if rng is not None:
            if ftype not in ("integer", "number"):
                E(f"{at}.range only applies to integer / number fields")
            elif not (isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, (int, float)) for x in rng)
                      and rng[0] < rng[1]):
                E(f"{at}.range must be [min, max] with min < max")
        if ftype == "table":
            cols = f.get("columns")
            if not (isinstance(cols, list) and cols):
                E(f"{at}.columns is required (non-empty) for a table field")
            else:
                cnames: set[str] = set()
                for ci, c in enumerate(cols):
                    check_field(c, f"{at}.columns[{ci}]", kind="scalar", column=True)
                    if isinstance(c, dict) and isinstance(c.get("name"), str):
                        if c["name"] in cnames:
                            E(f"{at}.columns: duplicate column {c['name']!r}")
                        cnames.add(c["name"])
        elif f.get("columns") is not None:
            E(f"{at}.columns only applies to table fields")
        if not column:
            ev = f.get("evidence", _default_evidence(f))
            allowed = EVIDENCE_BY_KIND["table" if ftype == "table" else "scalar"]
            if ev not in allowed:
                E(f"{at}.evidence must be one of {', '.join(allowed)} for a {ftype} field")
            if f.get("required") and ev == "none":
                W(f"{at}: required but evidence 'none' — a coder cannot verify it")
            if name and name not in seen:
                seen[name] = at
            elif name:
                E(f"{at}.name {name!r} is already used at {seen[name]}")
            if "required" in f and not isinstance(f["required"], bool):
                E(f"{at}.required must be a boolean")

    paper = spec.get("paper") or {}
    if not isinstance(paper, dict):
        E("$.paper must be an object"); paper = {}
    else:
        unknown(paper, "paper", "$.paper")
    for i, f in enumerate(paper.get("fields") or []):
        check_field(f, f"$.paper.fields[{i}]", kind="scalar")
        if isinstance(f, dict) and f.get("name") in {n for n, _t, _h in IDENTITY_FIELDS}:
            E(f"$.paper.fields[{i}]: {f['name']!r} is always present in paper_metadata — do not redeclare it")

    entries = spec.get("entries") or {}
    child_keys: list[str] = []
    child_tables: dict[str, set[str]] = {}   # child key -> its table fields (grid_rows targets)
    entry_field_names: list[str] = []
    table_fields: set[str] = set()
    if meta.get("mode") in ("extraction", "summarize") and not entries:
        E("$.entries is required")
    if entries:
        if not isinstance(entries, dict):
            E("$.entries must be an object"); entries = {}
        else:
            unknown(entries, "entries", "$.entries")
            key = entries.get("key")
            if not isinstance(key, str) or not NAME_RE.match(key):
                E("$.entries.key must match ^[A-Za-z_][A-Za-z0-9_]*$")
            elif key in RESERVED_ENTRY_KEYS:
                E(f"$.entries.key {key!r} is a reserved top-level key")
            if entries.get("cardinality", "many") not in ("many", "one"):
                E("$.entries.cardinality must be 'many' or 'one'")
            if entries.get("evidence", "row") not in EVIDENCE_BY_KIND["entries"]:
                E("$.entries.evidence must be 'row' or 'none'")
            for i, f in enumerate(entries.get("fields") or []):
                check_field(f, f"$.entries.fields[{i}]", kind="scalar")
                if isinstance(f, dict) and isinstance(f.get("name"), str):
                    entry_field_names.append(f["name"])
                    if f.get("type") == "table":
                        table_fields.add(f["name"])
            for ci, ch in enumerate(entries.get("children") or []):
                at = f"$.entries.children[{ci}]"
                if not isinstance(ch, dict):
                    E(f"{at}: must be an object"); continue
                unknown(ch, "child", at)
                ckey = ch.get("key")
                if not isinstance(ckey, str) or not NAME_RE.match(ckey):
                    E(f"{at}.key must match ^[A-Za-z_][A-Za-z0-9_]*$"); ckey = None
                elif ckey in RESERVED_NAMES:
                    E(f"{at}.key {ckey!r} is reserved")
                elif ckey in seen:
                    E(f"{at}.key {ckey!r} collides with a field declared at {seen[ckey]}")
                else:
                    seen[ckey] = at; child_keys.append(ckey)
                if ch.get("evidence", "row") not in EVIDENCE_BY_KIND["child"]:
                    E(f"{at}.evidence must be one of row, table, none")
                if ch.get("layout", "table") not in ("table", "cards"):
                    E(f"{at}.layout must be 'table' or 'cards'")
                if "children" in ch:
                    E(f"{at}: children may not nest (one level only)")
                if not ch.get("fields"):
                    E(f"{at}.fields must not be empty")
                for i, f in enumerate(ch.get("fields") or []):
                    check_field(f, f"{at}.fields[{i}]", kind="scalar")
                    if isinstance(f, dict) and f.get("type") == "table":
                        table_fields.add(f["name"])
                        if ckey:
                            child_tables.setdefault(ckey, set()).add(f["name"])
                if ch.get("title"):
                    for ph in _title_placeholders(ch["title"]):
                        if ph != "#" and ph not in {f.get("name") for f in ch.get("fields") or [] if isinstance(f, dict)}:
                            E(f"{at}.title references {{{ph}}}, not a field of this child")
            if entries.get("id_field") is not None:
                idf = entries["id_field"]
                match = next((f for f in entries.get("fields") or []
                              if isinstance(f, dict) and f.get("name") == idf), None)
                if match is None:
                    E("$.entries.id_field must name a declared entry field")
                elif match.get("type") not in ("string", "integer"):
                    E("$.entries.id_field must be a string or integer field")
            if entries.get("title"):
                for ph in _title_placeholders(entries["title"]):
                    if ph != "#" and ph not in entry_field_names:
                        E(f"$.entries.title references {{{ph}}}, not an entry field")

    # confidence ---------------------------------------------------------------
    conf = spec.get("confidence") or {}
    group_scope: dict[str, str] = {}
    if not isinstance(conf, dict):
        E("$.confidence must be an object"); conf = {}
    else:
        unknown(conf, "confidence", "$.confidence")
        levels = conf.get("levels", ["high", "medium", "low"])
        if not (isinstance(levels, list) and len(levels) >= 2 and all(isinstance(x, str) for x in levels)
                and len(set(levels)) == len(levels)):
            E("$.confidence.levels must be a list of at least two distinct strings")
        if not isinstance(conf.get("notes", True), bool):
            E("$.confidence.notes must be a boolean")
        for gi, g in enumerate(conf.get("groups") or []):
            at = f"$.confidence.groups[{gi}]"
            if not isinstance(g, dict):
                E(f"{at}: must be an object"); continue
            unknown(g, "group", at)
            gid = g.get("id")
            if not isinstance(gid, str) or not NAME_RE.match(gid):
                E(f"{at}.id must match ^[A-Za-z_][A-Za-z0-9_]*$"); continue
            if gid in group_scope:
                E(f"{at}.id {gid!r} is duplicated")
            scope = g.get("scope", "entry")
            if scope not in CONF_SCOPES:
                E(f"{at}.scope must be one of {', '.join(CONF_SCOPES)}")
            group_scope[gid] = scope

    # field -> group references must exist, and the group must be rated at or ABOVE the
    # field's level: a per-entry rating may cover that entry's sub-entry fields (MASEMiner
    # rates "effect sizes" once per sample across its records); a per-sub-entry rating
    # cannot cover an entry field, and a paper-level rating covers paper fields only.
    _may_cover = {"paper": {"paper"}, "entry": {"entry", "child"}, "child": {"child"}}

    def check_refs(fields: list, at: str, level: str, child_key: str | None = None) -> None:
        for i, f in enumerate(fields or []):
            if not isinstance(f, dict) or f.get("confidence") is None:
                continue
            gid = f["confidence"]
            if gid not in group_scope:
                E(f"{at}[{i}].confidence {gid!r} is not a declared group"); continue
            if level not in _may_cover.get(group_scope[gid], set()):
                E(f"{at}[{i}].confidence {gid!r} has scope {group_scope[gid]!r}, which cannot "
                  f"cover a {level}-level field")
            if group_scope[gid] == "child":
                child_of_group.setdefault(gid, set()).add(child_key)

    child_of_group: dict[str, set] = {}
    check_refs(paper.get("fields") or [], "$.paper.fields", "paper")
    check_refs(entries.get("fields") or [] if isinstance(entries, dict) else [], "$.entries.fields", "entry")
    for ci, ch in enumerate((entries.get("children") or []) if isinstance(entries, dict) else []):
        if isinstance(ch, dict):
            check_refs(ch.get("fields") or [], f"$.entries.children[{ci}].fields", "child", ch.get("key"))
    for gid, chs in child_of_group.items():
        if len(chs) > 1:
            E(f"$.confidence.groups: child-scope group {gid!r} is referenced from several children")
    referenced = {f.get("confidence") for fs in ([paper.get("fields") or []]
                  + [entries.get("fields") or []] + [ch.get("fields") or [] for ch in (entries.get("children") or [])])
                  for f in fs if isinstance(f, dict)} if isinstance(entries, dict) else set()
    for gid in group_scope:
        if gid not in referenced:
            W(f"$.confidence.groups: group {gid!r} is not referenced by any field")
    if entries and not group_scope:
        W("$.confidence: no groups declared — the model will not rate its own extraction")

    # display ------------------------------------------------------------------
    display = spec.get("display") or {}
    if not isinstance(display, dict):
        E("$.display must be an object"); display = {}
    else:
        unknown(display, "display", "$.display")
        if display.get("entries", "cards") not in ("cards", "table"):
            E("$.display.entries must be 'cards' or 'table'")
        if display.get("triage", "declaration") not in ("declaration", "low_confidence_first"):
            E("$.display.triage must be 'declaration' or 'low_confidence_first'")
        if display.get("paper_panel", "open") not in ("open", "collapsed"):
            E("$.display.paper_panel must be 'open' or 'collapsed'")
        gr = display.get("grid_rows", "entries")
        gck, _, gtk = str(gr).partition(".")
        if gr != "entries" and (gck not in child_keys or (gtk and gtk not in child_tables.get(gck, set()))):
            E("$.display.grid_rows must be 'entries', a child key, or '<child>.<table field>'")
        placeable = set(entry_field_names) | set(child_keys)
        placed: dict[str, str] = {}
        tab_ids: set[str] = set()
        for ti, t in enumerate(display.get("tabs") or []):
            at = f"$.display.tabs[{ti}]"
            if not isinstance(t, dict):
                E(f"{at}: must be an object"); continue
            unknown(t, "tab", at)
            tid = t.get("id")
            if not isinstance(tid, str) or not NAME_RE.match(tid):
                E(f"{at}.id must match ^[A-Za-z_][A-Za-z0-9_]*$")
            elif tid in tab_ids:
                E(f"{at}.id {tid!r} is duplicated")
            else:
                tab_ids.add(tid)
            if not (isinstance(t.get("label"), str) and t["label"].strip()):
                E(f"{at}.label is required")
            for fname in t.get("fields") or []:
                if fname not in placeable:
                    E(f"{at}.fields: {fname!r} is not an entry field or child key")
                elif fname in placed:
                    W(f"{at}.fields: {fname!r} is also in tab {placed[fname]!r}")
                else:
                    placed[fname] = tid or "?"
        if display.get("tabs"):
            for fname in placeable - set(placed):
                W(f"$.display.tabs: {fname!r} is in no tab (it will show under 'Other')")
    return errs, warns


def _placeholders(text: str) -> list[str]:
    return sorted({m.group(1) or m.group(2) for m in
                   re.finditer(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", text)})


def _title_placeholders(tpl: str) -> list[str]:
    return re.findall(r"\{([A-Za-z_#][A-Za-z0-9_]*)\}", tpl or "")


# ── indexes over a normalised spec ────────────────────────────────────────────

def entries_key(spec: dict) -> str | None:
    return (spec.get("entries") or {}).get("key")


def field_index(spec: dict) -> dict[str, dict]:
    """name → {"scope": paper|entry|child, "field": {...}, "child": key|None}; child keys
    map to {"scope": "child_key", "child": {...}}."""
    idx: dict[str, dict] = {}
    for f in (spec.get("paper") or {}).get("fields") or []:
        idx[f["name"]] = {"scope": "paper", "field": f, "child": None}
    ent = spec.get("entries") or {}
    for f in ent.get("fields") or []:
        idx[f["name"]] = {"scope": "entry", "field": f, "child": None}
    for ch in ent.get("children") or []:
        idx[ch["key"]] = {"scope": "child_key", "field": None, "child": ch}
        for f in ch.get("fields") or []:
            idx[f["name"]] = {"scope": "child", "field": f, "child": ch["key"]}
    return idx


def groups_by_scope(spec: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {s: [] for s in CONF_SCOPES}
    for g in (spec.get("confidence") or {}).get("groups") or []:
        out[g.get("scope", "entry")].append(g)
    return out


def fields_of_group(spec: dict, gid: str) -> list[str]:
    names = []
    idx = field_index(spec)
    for name, info in idx.items():
        f = info.get("field")
        if f is not None and f.get("confidence") == gid:
            names.append(name if info["scope"] != "child" else f"{info['child']}[].{name}")
    return names


# ── paths ─────────────────────────────────────────────────────────────────────

_SEG = re.compile(r"\.?([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?")


def parse_path(path: str | None, spec: dict | None = None, key: str | None = None) -> dict | None:
    """Understand an absolute evidence / confidence path.

    Returns ``{"scope", "entry_index", "child_key", "child_index", "table_key", "row_index",
    "field", "valid"}`` or None for a non-path string. ``scope`` is one of ``paper``
    (``paper_metadata[.field]``), ``entries`` (the whole array), ``entry`` (``key[i]…``),
    ``child`` (``key[i].child[j]…``). Legacy ``key._table[i]`` is accepted. A spec makes
    the child-vs-table distinction exact; without one a bracketed segment after the entry
    is read as a child when it carries a further field, else as a table row.
    """
    if not isinstance(path, str) or not path.strip():
        return None
    p = path.strip().replace("._table[", "[")
    segs = []
    pos = 0
    while pos < len(p):
        m = _SEG.match(p, pos)
        if not m:
            return None
        segs.append((m.group(1), int(m.group(2)) if m.group(2) is not None else None))
        pos = m.end()
    if not segs:
        return None
    idx = field_index(spec) if spec else {}
    ekey = key or (entries_key(spec) if spec else None)
    out = {"scope": None, "entry_index": None, "child_key": None, "child_index": None,
           "table_key": None, "row_index": None, "field": None, "valid": True}
    head, hidx = segs[0]
    if head == "paper_metadata":
        out["scope"] = "paper"
        if len(segs) > 1:
            out["field"] = segs[1][0]
        return out
    if ekey and head != ekey:
        out["valid"] = False
    if hidx is None:
        out["scope"] = "entries"
        out["valid"] = out["valid"] and len(segs) == 1
        return out
    out["scope"] = "entry"
    out["entry_index"] = hidx
    rest = segs[1:]
    if not rest:
        return out
    name, nidx = rest[0]
    rest = rest[1:]
    info = idx.get(name)
    is_child = (info or {}).get("scope") == "child_key" if info else (
        nidx is not None and len(rest) > 0)
    if is_child:
        out["scope"] = "child"; out["child_key"] = name; out["child_index"] = nidx
        if not rest:
            return out
        name, nidx = rest[0]
        rest = rest[1:]
        info = idx.get(name)
    if nidx is not None or ((info or {}).get("field") or {}).get("type") == "table":
        out["table_key"] = name; out["row_index"] = nidx
        if rest:
            out["valid"] = False   # nothing may follow a table row
        return out
    out["field"] = name
    if rest:
        out["valid"] = False       # deeper than the grammar allows
    return out


# ── hashing / identity ────────────────────────────────────────────────────────

_WORDING_KEYS = ("label", "help", "title")


def data_contract(spec: dict) -> dict:
    """The part of a preset that decides what the DATA looks like: paper / entries /
    confidence — minus wording (labels, help, titles), which a preset may polish without
    changing the shape of what it extracts."""
    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in _WORDING_KEYS}
        if isinstance(node, list):
            return [strip(x) for x in node]
        return node
    return {"paper": strip(spec.get("paper") or {}),
            "entries": strip(spec.get("entries") or {}),
            "confidence": strip(spec.get("confidence") or {})}


def content_hash(spec: dict) -> str:
    return hashlib.sha256(canonical_json(data_contract(spec)).encode("utf-8")).hexdigest()


def schema_id(spec: dict) -> str:
    return f"{spec['id']}@{content_hash(spec)[:8]}"


def label(spec: dict) -> str:
    return f"{spec['meta']['title']} v{spec.get('version', 1)} ({content_hash(spec)[:8]})"


# ── parameters ────────────────────────────────────────────────────────────────

def resolve_params(spec: dict, overrides: dict | None = None) -> dict:
    """Declared defaults with the run's values merged over them; undeclared names are
    ignored (the MASEM builder sends a few historical ones)."""
    decl = (spec.get("prompt") or {}).get("params") or {}
    out = {name: copy.deepcopy(d.get("default")) for name, d in decl.items()}
    for name, val in (overrides or {}).items():
        if name in decl and val is not None and val != "" and val != []:
            out[name] = val
    return out


_DEFAULT_CODE_LABELS = {"r": "Correlation", "or": "Odds ratios", "rr": "Risk ratios",
                        "hr": "Hazard ratios", "smd": "Standardised mean difference",
                        "d": "Cohen's d", "g": "Hedges' g"}


def _param_codes(decl: dict, value: Any) -> list[str]:
    """The option codes a list/table parameter contributes to an ``options: "${p}"`` field."""
    if not isinstance(value, list):
        return []
    if decl.get("type") == "table":
        col = (decl.get("columns") or ["code"])[0]
        return [str(r.get(col)) for r in value if isinstance(r, dict) and r.get(col) not in (None, "")]
    return [str(x) for x in value if x not in (None, "")]


def render_param(decl: dict, value: Any) -> str:
    """Typed rendering of one parameter for substitution into the author text; ``empty``
    is the text shown when the value renders to nothing."""
    out = _render_param(decl, value)
    return out if out else str(decl.get("empty") or "")


def _render_param(decl: dict, value: Any) -> str:
    ptype = decl.get("type", "string")
    if value is None:
        return ""
    if ptype == "list":
        items = [str(x).strip() for x in (value if isinstance(value, list) else [value]) if str(x).strip()]
        if decl.get("numbered"):
            return "\n".join(f"{i}: {t}" for i, t in enumerate(items, 1))
        return "\n".join(f"- {t}" for t in items)
    if ptype == "table":
        cols = decl.get("columns") or ["code", "label"]
        lines = []
        for row in (value if isinstance(value, list) else []):
            if isinstance(row, str):                       # a bare code
                lines.append(f'- "{row}" = {_DEFAULT_CODE_LABELS.get(row.lower(), row)}')
                continue
            if not isinstance(row, dict):
                continue
            head = str(row.get(cols[0], "")).strip()
            if not head:
                continue
            second = str(row.get(cols[1], "") or "").strip() if len(cols) > 1 else ""
            if not second and len(cols) > 1:
                second = _DEFAULT_CODE_LABELS.get(head.lower(), "")
            line = f'- "{head}"' + (f" = {second}" if second else "")
            for extra in cols[2:]:
                v = row.get(extra)
                if isinstance(v, list) and v:
                    line += f"  ({extra}: " + ", ".join(f'"{x}"' for x in v) + ")"
                elif v not in (None, "", []):
                    line += f" — {v}"
            lines.append(line)
        return "\n".join(lines)
    if ptype == "boolean":
        return "yes" if value else "no"
    return str(value)


# ── the generated prompt ──────────────────────────────────────────────────────

def _opt_values(f: dict, params: dict, decl: dict) -> list[Any] | None:
    opts = f.get("options")
    if isinstance(opts, str):
        m = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", opts)
        if not m:
            return None
        codes = _param_codes(decl.get(m.group(1), {}), params.get(m.group(1)))
        return codes or None
    if isinstance(opts, list):
        return [o.get("value") if isinstance(o, dict) else o for o in opts]
    return None


def _opt_lines(f: dict, params: dict, decl: dict) -> str:
    """``1 = journal article, 2 = book`` for labelled options; ``a | b | c`` otherwise."""
    opts = f.get("options")
    if isinstance(opts, list) and any(isinstance(o, dict) for o in opts):
        return ", ".join(f"{o.get('value')} = {o.get('label', o.get('value'))}" if isinstance(o, dict) else str(o)
                         for o in opts)
    vals = _opt_values(f, params, decl)
    if vals is None:
        return "the codes listed in the task instructions"
    return " | ".join(str(v) for v in vals)


def _token(f: dict, params: dict, decl: dict) -> Any:
    """The placeholder that stands for a field's value in the output skeleton."""
    t = f.get("type", "string")
    nullable = "" if f.get("required") else "|null"
    if t == "string":
        return "string" + nullable
    if t == "text":
        return "markdown string" + nullable
    if t == "integer":
        rng = f.get("range")
        return "integer" + (f" [{rng[0]}, {rng[1]}]" if rng else "") + nullable
    if t == "number":
        rng = f.get("range")
        return "number" + (f" [{rng[0]}, {rng[1]}]" if rng else "") + nullable
    if t == "boolean":
        return "true|false" + nullable
    if t == "enum":
        vals = _opt_values(f, params, decl)
        core = "|".join(str(v) for v in vals) if vals else "code"
        return core + nullable
    if t == "multi":
        vals = _opt_values(f, params, decl)
        return ["|".join(str(v) for v in vals) if vals else "code"]
    if t == "list":
        return ["string"]
    if t == "table":
        return [{c["name"]: _token(c, params, decl) for c in f.get("columns") or []}]
    return "string" + nullable


def derive_output_schema(spec: dict, params: dict | None = None) -> dict:
    """The JSON skeleton (type tokens in place of values) the model must return."""
    params = params if params is not None else resolve_params(spec)
    decl = (spec.get("prompt") or {}).get("params") or {}
    pm: dict[str, Any] = {}
    for name, ftype, _help in IDENTITY_FIELDS:
        pm[name] = "string" if name == "title" else ("integer|null" if ftype == "integer" else
                                                     (["string"] if ftype == "list" else "string|null"))
    for f in (spec.get("paper") or {}).get("fields") or []:
        pm[f["name"]] = _token(f, params, decl)
    pgroups = groups_by_scope(spec)["paper"]
    if pgroups:
        pm["confidence"] = _conf_token(spec, pgroups)
    out: dict[str, Any] = {"paper_metadata": pm}
    ent = spec.get("entries") or {}
    if ent:
        el: dict[str, Any] = {}
        for f in ent.get("fields") or []:
            el[f["name"]] = _token(f, params, decl)
        cgroups = groups_by_scope(spec)["child"]
        for ch in ent.get("children") or []:
            row = {f["name"]: _token(f, params, decl) for f in ch.get("fields") or []}
            mine = [g for g in cgroups if ch["key"] in {n.split("[]")[0] for n in fields_of_group(spec, g["id"])}]
            if mine:
                row["confidence"] = _conf_token(spec, mine)
            el[ch["key"]] = [row]
        egroups = groups_by_scope(spec)["entry"]
        if egroups:
            el["confidence"] = _conf_token(spec, egroups)
        out[ent["key"]] = [el]
    out["evidence"] = [{"snippet": "verbatim text", "page": 1, "source": "Table 2 or null",
                        "field": _example_path(spec)}]
    return out


def _conf_token(spec: dict, groups: list[dict]) -> dict:
    lv = "|".join((spec.get("confidence") or {}).get("levels") or [])
    return {g["id"]: {"level": lv, "notes": "string"} for g in groups}


def _example_path(spec: dict) -> str:
    ent = spec.get("entries") or {}
    key = ent.get("key", "records")
    for ch in ent.get("children") or []:
        for f in ch.get("fields") or []:
            if f.get("evidence") == "value":
                return f"{key}[0].{ch['key']}[0].{f['name']}"
    for f in ent.get("fields") or []:
        if f.get("evidence") == "value":
            return f"{key}[0].{f['name']}"
    return f"{key}[0]"


def _field_line(f: dict, params: dict, decl: dict) -> str:
    t = f.get("type", "string")
    if t in ("enum", "multi"):
        kind = ("one of " if t == "enum" else "any of ") + _opt_lines(f, params, decl)
        if f.get("allow_other"):
            kind += ", or the paper's own term"
        if t == "multi":
            kind = "array; " + kind
    elif t == "table":
        kind = "table: one object per row, with the columns listed underneath"
    elif t == "list":
        kind = "array of strings"
    elif t == "text":
        kind = "markdown text"
    else:
        kind = t
        if f.get("range"):
            kind += f" in [{f['range'][0]}, {f['range'][1]}]"
    if f.get("required"):
        kind += ", required"
    elif t not in ("multi", "list", "table"):
        kind += " or null"
    desc = f.get("label") or format_key(f["name"])
    if f.get("help"):
        desc += f" — {f['help']}"
    line = f"- {f['name']} ({kind}): {desc}"
    if t == "table":                      # a column carries the same label / help / options as a field
        line += "".join("\n    " + _field_line(c, params, decl).replace("\n", "\n    ")
                        for c in f.get("columns") or [])
    return line


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def generated_sections(spec: dict, params: dict | None = None) -> dict[str, str]:
    """Each generated section's text, keyed by section name (whether or not it is
    enabled — the caller picks)."""
    params = params if params is not None else resolve_params(spec)
    decl = (spec.get("prompt") or {}).get("params") or {}
    ent = spec.get("entries") or {}
    key = ent.get("key", "records")
    elabel = ent.get("label", "entry")
    gb = groups_by_scope(spec)
    out: dict[str, str] = {}

    # PAPER METADATA ---------------------------------------------------------
    lines = ["# PAPER METADATA (generated)", "",
             'Populate "paper_metadata" from the title page / front matter / running header:']
    for name, ftype, help_ in IDENTITY_FIELDS:
        lines.append(f"- {name}: {help_}")
    pf = (spec.get("paper") or {}).get("fields") or []
    if pf:
        lines += ["", "Also extract these paper-level fields (once per paper):"]
        lines += [_field_line(f, params, decl) for f in pf]
    out["paper_metadata"] = "\n".join(lines)

    # OUTPUT SCHEMA -----------------------------------------------------------
    skel = derive_output_schema(spec, params)
    top_keys = ", ".join(_q(k) for k in skel)
    lines = ["# OUTPUT SCHEMA (generated — follow exactly)", "",
             f"Return exactly ONE JSON object with these top-level keys and no others: {top_keys}.",
             "Types are shown as placeholders; \"a|b\" means one of those values.", "",
             json.dumps(skel, indent=2, ensure_ascii=False), ""]
    if ent:
        if ent.get("cardinality") == "one":
            lines.append(f'"{key}" holds exactly ONE element — the {elabel}'
                         + (f" ({ent['help']})" if ent.get("help") else "") + ".")
        else:
            lines.append(f'One element of "{key}" per {elabel}'
                         + (f" — {ent['help']}" if ent.get("help") else "")
                         + f'. Return "{key}": [] if the paper reports none.')
        if ent.get("id_field"):
            lines.append(f'"{ent["id_field"]}" identifies each {elabel} and must be unique within the paper.')
        for ch in ent.get("children") or []:
            lines.append(f'"{key}[].{ch["key"]}" holds one element per {ch.get("label", ch["key"])}'
                         + (f" — {ch['help']}" if ch.get("help") else "") + "; [] when none.")
        lines += ["", "FIELDS"]
        if ent.get("fields"):
            lines.append(f"{key}[]:")
            lines += [_field_line(f, params, decl) for f in ent["fields"]]
        for ch in ent.get("children") or []:
            lines.append(f"{key}[].{ch['key']}[]:")
            lines += [_field_line(f, params, decl) for f in ch.get("fields") or []]
    lines += ["", "Use JSON numbers for integer/number fields (never strings); use null when a "
              "value is not reported. Allowed values are exact and case-sensitive."]
    out["output_schema"] = "\n".join(lines)

    # EVIDENCE ----------------------------------------------------------------
    lines = ["# EVIDENCE (generated)", "",
             'Provide a top-level "evidence" array. Each item has EXACTLY these four keys:',
             '  "snippet" — verbatim text from the PDF, character-for-character (no paraphrase, no ellipses)',
             '  "page"    — the 1-indexed PDF page number as an INTEGER (never a printed journal page)',
             '  "source"  — the table / figure / section id (e.g. "Table 2") or null',
             '  "field"   — an absolute JSON path into the object above (see PATHS); when ONE quote',
             '              supports SEVERAL values, give a LIST of paths here instead of repeating the item', "",
             "PATHS (i, j, r are 0-based indices)",
             "  paper_metadata.<field>"]
    if ent:
        lines += [f"  {key}[i]                         the {elabel} as a whole (identification)",
                  f"  {key}[i].<field>"]
        for ch in ent.get("children") or []:
            lines += [f"  {key}[i].{ch['key']}[j]               one {ch.get('label', ch['key'])}",
                      f"  {key}[i].{ch['key']}[j].<field>"]
        lines.append(f"  {key}[i].<table field>[r]        one row of a table field")
        if any(f.get("type") == "table" for ch in ent.get("children") or [] for f in ch.get("fields") or []):
            lines.append(f"  {key}[i].<child>[j].<table field>[r]   one row of a table field inside a {', '.join(ch.get('label', ch['key']) for ch in ent.get('children') or [])}")
        lines.append(f"Never omit the {key}[i] prefix — an unindexed path cannot be linked to its value.")
    lines += ["", "REQUIRED COVERAGE"]
    idx = field_index(spec)
    val_paths, row_lines, table_paths, none_paths = [], [], [], []
    table_row_paths: list[str] = []      # table fields cited per row (evidence: "row")
    ftype_of: dict[str, str] = {}      # path -> field type (coded fields get a different rule)
    for f in pf:
        p = f"paper_metadata.{f['name']}"
        ftype_of[p] = f.get("type", "string")
        (val_paths if f.get("evidence") == "value" else table_paths if f.get("evidence") == "table"
         else none_paths).append(p)
        if f.get("type") == "table" and f.get("evidence") == "row":
            table_row_paths.append(p + "[r]")
    if ent:
        if ent.get("evidence") == "row":
            row_lines.append(f'- one item identifying each {elabel}: field "{key}[i]"')
        for f in ent.get("fields") or []:
            p = f"{key}[i].{f['name']}"
            ftype_of[p] = f.get("type", "string")
            ev = f.get("evidence")
            (val_paths if ev == "value" else table_paths if ev == "table"
             else none_paths if ev == "none" else []).append(p)
            if f.get("type") == "table" and f.get("evidence") == "row":
                table_row_paths.append(p + "[r]")
        for ch in ent.get("children") or []:
            cp = f"{key}[i].{ch['key']}[j]"
            if ch.get("evidence") == "row":
                row_lines.append(f'- one item per {ch.get("label", ch["key"])}: field "{cp}"')
            elif ch.get("evidence") == "table":
                table_paths.append(f"{key}[i].{ch['key']}")
            for f in ch.get("fields") or []:
                p = f"{cp}.{f['name']}"
                ftype_of[p] = f.get("type", "string")
                ev = f.get("evidence")
                (val_paths if ev == "value" else table_paths if ev == "table"
                 else none_paths if ev == "none" else []).append(p)
                if f.get("type") == "table" and f.get("evidence") == "row":
                    table_row_paths.append(p + "[r]")
    lines += row_lines
    # A number or a name can be quoted verbatim; a coded category (enum / multi / boolean) is
    # the model's judgement, so the citation is the passage that justifies the code.
    literal = [p for p in val_paths if ftype_of.get(p) not in CODED_TYPES]
    coded = [p for p in val_paths if ftype_of.get(p) in CODED_TYPES]
    if literal:
        lines.append("- one item per non-null value of: " + ", ".join(literal)
                     + " — the snippet must contain that value verbatim")
    if coded:
        lines.append("- one item per non-null value of: " + ", ".join(coded)
                     + " — coded categories: quote the passage that justifies the code "
                       "(the snippet need not contain the code itself)")
    if table_row_paths:
        lines.append("- one item per row of: " + ", ".join(table_row_paths)
                     + " — the line of the source table (or the sentence) that carries that row's values")
    if table_paths:
        lines.append("- one item per table or block (its caption or header row) for: " + ", ".join(table_paths))
    if none_paths:
        lines.append("- no item is needed for: " + ", ".join(none_paths))
    inst = []
    if ent:
        inst.append(f"every {elabel} i")
        inst += [f"every {ch.get('label', ch['key'])} j" for ch in ent.get("children") or []]
    if table_row_paths:
        inst.append("every row r")
    if inst:
        lines.append("Coverage is per instance — " + ", ".join(inst)
                     + " — the same rules for the last one as for the first; never stop after the first instance.")
    lines += ['When one quote supports several values, cite it ONCE with all their paths in "field" (a list); '
              "never skip a path because its quote was already used. Omit an item rather than invent a "
              "quote; never cite a path that does not exist in your output."]
    out["evidence"] = "\n".join(lines)

    # CONFIDENCE --------------------------------------------------------------
    levels = (spec.get("confidence") or {}).get("levels") or []
    lv = " | ".join(f'"{x}"' for x in levels)
    lines = ["# CONFIDENCE (generated)", ""]
    def _group_lines(groups: list[dict]) -> list[str]:
        gl = []
        for g in groups:
            covers = ", ".join(fields_of_group(spec, g["id"])) or "the fields of this group"
            gl.append(f'  "{g["id"]}" — {g.get("label", g["id"])}: covers {covers}'
                      + (f"; {g['help']}" if g.get("help") else ""))
        return gl
    if gb["paper"]:
        lines.append('Add a "confidence" object to "paper_metadata" with EXACTLY these keys:')
        lines += _group_lines(gb["paper"])
    if gb["entry"]:
        lines.append(f'For EACH element of "{key}", add a "confidence" object with EXACTLY these keys:')
        lines += _group_lines(gb["entry"])
    if gb["child"]:
        by_child: dict[str, list[dict]] = {}
        for g in gb["child"]:
            ck = next(iter({n.split("[]")[0] for n in fields_of_group(spec, g["id"])}), None)
            by_child.setdefault(ck or "?", []).append(g)
        for ck, gs in by_child.items():
            lines.append(f'For EACH element of "{key}[].{ck}", add a "confidence" object with EXACTLY these keys:')
            lines += _group_lines(gs)
    notes = (spec.get("confidence") or {}).get("notes", True)
    lines += [f'Each value is {{"level": <one of {lv}>, "notes": {"string" if notes else "string or null"}}}.',
              '"level" reflects how reliably the values match the paper, not how complete the data is. '
              'Be conservative: prefer the next-lower level when in doubt.',
              'Emit every key even when a category was not extractable (then the lowest level, and say why in "notes").']
    out["confidence"] = "\n".join(lines)

    # RETURN FORMAT -----------------------------------------------------------
    out["return_format"] = "\n".join([
        "# RETURN FORMAT (generated)", "",
        "Return EXACTLY ONE top-level JSON object — JSON only: no markdown fences, no prose before or "
        "after, no comments, no trailing commas. It must parse with json.loads.",
        "Before returning, check: every evidence \"field\" path exists in your output; required fields "
        "are non-null; allowed values come from the listed set; numbers are JSON numbers."])
    return out


def render_prompt(spec: dict, params: dict | None = None) -> str:
    """The final prompt: the author's text with ``${params}`` substituted, plus the enabled
    generated sections (substituted in place where the text says ``${section}``, else
    appended in the fixed order)."""
    params = resolve_params(spec, params) if params is not None else resolve_params(spec)
    prompt = spec.get("prompt") or {}
    decl = prompt.get("params") or {}
    enabled = [s for s in GENERATED_SECTIONS if s in (prompt.get("generate") or [])]
    if any(gb for gb in groups_by_scope(spec).values()) is False:
        enabled = [s for s in enabled if s != "confidence"]
    if not any(groups_by_scope(spec).values()):
        enabled = [s for s in enabled if s != "confidence"]
    sections = generated_sections(spec, params)
    text = prompt.get("text") or ""
    used = set(_placeholders(text))
    sub = {name: render_param(decl[name], params.get(name)) for name in decl}
    for s in GENERATED_SECTIONS:
        sub[s] = sections[s] if s in enabled else ""
    body = string.Template(text).safe_substitute(sub)
    tail = [sections[s] for s in enabled if s not in used]
    if tail:
        body = body.rstrip() + "\n\n---\n\n" + "\n\n---\n\n".join(tail)
    return _collapse_blank_lines(body)


# ── post-extraction validation ────────────────────────────────────────────────

def validate_result(obj: dict, spec: dict, params: dict | None = None) -> list[dict]:
    """What the model got structurally wrong, for triage — never blocks persistence.
    Each issue: ``{"path", "code", "message"}``."""
    issues: list[dict] = []
    if not isinstance(obj, dict):
        return [{"path": "$", "code": "not_object", "message": "result is not a JSON object"}]
    params = params if params is not None else resolve_params(spec)
    decl = (spec.get("prompt") or {}).get("params") or {}
    idx = field_index(spec)
    ent = spec.get("entries") or {}
    key = ent.get("key")
    gen = (spec.get("prompt") or {}).get("generate")
    gen = list(GENERATED_SECTIONS) if gen is None else gen
    # A frozen prompt (no generated sections) asks for confidence the old way — a per-entry
    # ``extraction_confidence`` block keyed by the same group names — and states its own
    # evidence rules, so per-value coverage is not something it was asked for.
    legacy_conf_ok = "confidence" not in gen
    coverage = "evidence" in gen

    def rated(node: dict, gid: str) -> bool:
        if isinstance(node.get("confidence"), dict) and gid in node["confidence"]:
            return True
        return legacy_conf_ok and isinstance(node.get("extraction_confidence"), dict) and gid in node["extraction_confidence"]

    def check_fields(node: dict, fields: list[dict], at: str) -> None:
        for f in fields:
            p = f"{at}.{f['name']}"
            v = node.get(f["name"])
            if v is None:
                if f.get("required"):
                    issues.append({"path": p, "code": "missing_required", "message": "required field is null or absent"})
                continue
            t = f.get("type")
            if t in ("integer", "number") and not (isinstance(v, (int, float)) and not isinstance(v, bool)):
                issues.append({"path": p, "code": "not_number", "message": f"expected a JSON number, got {type(v).__name__}"})
            elif t in ("integer", "number") and f.get("range") and not (f["range"][0] <= v <= f["range"][1]):
                issues.append({"path": p, "code": "out_of_range", "message": f"{v} outside [{f['range'][0]}, {f['range'][1]}]"})
            elif t == "enum" and not f.get("allow_other"):
                vals = _opt_values(f, params, decl)
                if vals and v not in vals and str(v) not in [str(x) for x in vals]:
                    issues.append({"path": p, "code": "not_in_options", "message": f"{v!r} is not an allowed value"})
            elif t == "multi":
                vals = _opt_values(f, params, decl)
                if not isinstance(v, list):
                    issues.append({"path": p, "code": "not_list", "message": "expected an array"})
                elif vals:
                    for x in v:
                        if x not in vals and str(x) not in [str(o) for o in vals]:
                            issues.append({"path": p, "code": "not_in_options", "message": f"{x!r} is not an allowed value"})
            elif t == "table" and not isinstance(v, list):
                issues.append({"path": p, "code": "not_table", "message": "expected an array of row objects"})

    pm = obj.get("paper_metadata")
    if isinstance(pm, dict):
        check_fields(pm, (spec.get("paper") or {}).get("fields") or [], "paper_metadata")
        if not pm.get("title"):
            issues.append({"path": "paper_metadata.title", "code": "missing_required", "message": "title is empty"})
    else:
        issues.append({"path": "paper_metadata", "code": "missing", "message": "no paper_metadata object"})
    for gid in [g["id"] for g in groups_by_scope(spec)["paper"]]:
        if not (isinstance(pm, dict) and isinstance(pm.get("confidence"), dict) and gid in pm["confidence"]):
            issues.append({"path": "paper_metadata.confidence." + gid, "code": "missing_confidence", "message": "no rating"})

    arr = obj.get(key) if key else None
    if key and not isinstance(arr, list):
        issues.append({"path": key, "code": "missing", "message": f'no "{key}" array'})
        arr = []
    egroups = [g["id"] for g in groups_by_scope(spec)["entry"]]
    for i, el in enumerate(arr or []):
        at = f"{key}[{i}]"
        if not isinstance(el, dict):
            issues.append({"path": at, "code": "not_object", "message": "entry is not an object"}); continue
        check_fields(el, ent.get("fields") or [], at)
        for gid in egroups:
            if not rated(el, gid):
                issues.append({"path": f"{at}.confidence.{gid}", "code": "missing_confidence", "message": "no rating"})
        for ch in ent.get("children") or []:
            rows = el.get(ch["key"])
            if rows is None:
                continue
            if not isinstance(rows, list):
                issues.append({"path": f"{at}.{ch['key']}", "code": "not_list", "message": "expected an array"}); continue
            cgroups = [g["id"] for g in groups_by_scope(spec)["child"]
                       if ch["key"] in {n.split("[]")[0] for n in fields_of_group(spec, g["id"])}]
            for j, row in enumerate(rows):
                cat = f"{at}.{ch['key']}[{j}]"
                if not isinstance(row, dict):
                    issues.append({"path": cat, "code": "not_object", "message": "sub-entry is not an object"}); continue
                check_fields(row, ch.get("fields") or [], cat)
                for gid in cgroups:
                    if not rated(row, gid):
                        issues.append({"path": f"{cat}.confidence.{gid}", "code": "missing_confidence", "message": "no rating"})

    # evidence: every path must parse and exist; every 'value' field wants an item
    cited: set[str] = set()
    ev = obj.get("evidence")
    if isinstance(ev, list):
        for n, item in enumerate(ev):
            if not isinstance(item, dict):
                continue
            fps = item.get("field")
            if fps is None and isinstance(item.get("fields"), list):
                fps = item["fields"]
            for fp in (fps if isinstance(fps, list) else [fps]):   # a list = one quote reused for several values
                info = parse_path(fp, spec)
                if info is None or not info["valid"]:
                    issues.append({"path": f"evidence[{n}].field", "code": "bad_path", "message": f"{fp!r} is not a valid path"})
                    continue
                if _path_exists(obj, fp, key):
                    cited.add(_norm_path(fp))
                else:
                    issues.append({"path": f"evidence[{n}].field", "code": "dangling_path", "message": f"{fp!r} does not exist in the output"})
            if not isinstance(item.get("page"), int):
                issues.append({"path": f"evidence[{n}].page", "code": "bad_page", "message": "page must be an integer"})
    if coverage:
        for p in _value_paths(obj, spec):
            if _norm_path(p) not in cited:
                issues.append({"path": p, "code": "uncited_value", "message": "value has no evidence item"})
        for p, what in _row_paths(obj, spec):
            if _norm_path(p) not in cited:
                issues.append({"path": p, "code": "uncited_row", "message": f"{what} has no evidence item"})
    return issues


def _row_paths(obj: dict, spec: dict) -> list[tuple[str, str]]:
    """Every present instance that declares ``evidence: row`` — the entry itself, a sub-entry,
    a table row — with a word for what it is. These want one identifying item each."""
    out: list[tuple[str, str]] = []

    def table_rows(node: dict, fields: list[dict], at: str) -> None:
        for f in fields:
            if f.get("type") == "table" and f.get("evidence") == "row" and isinstance(node.get(f["name"]), list):
                out.extend((f"{at}.{f['name']}[{r}]", f"{f.get('label') or f['name']} row") for r in range(len(node[f["name"]])))

    pm = obj.get("paper_metadata")
    if isinstance(pm, dict):
        table_rows(pm, (spec.get("paper") or {}).get("fields") or [], "paper_metadata")
    ent = spec.get("entries") or {}
    key = ent.get("key")
    arr = obj.get(key) if key else None
    if isinstance(arr, dict):
        arr = arr.get("_table")
    for i, el in enumerate(arr or []):
        if not isinstance(el, dict):
            continue
        at = f"{key}[{i}]"
        if ent.get("evidence") == "row":
            out.append((at, ent.get("label") or key))
        table_rows(el, ent.get("fields") or [], at)
        for ch in ent.get("children") or []:
            for j, row in enumerate(el.get(ch["key"]) or []):
                if not isinstance(row, dict):
                    continue
                cat = f"{at}.{ch['key']}[{j}]"
                if ch.get("evidence") == "row":
                    out.append((cat, ch.get("label") or ch["key"]))
                table_rows(row, ch.get("fields") or [], cat)
    return out


def _norm_path(p: str) -> str:
    return (p or "").strip().replace("._table[", "[")


def _path_exists(obj: dict, path: str, key: str | None) -> bool:
    p = _norm_path(path)
    node: Any = obj
    pos = 0
    while pos < len(p):
        m = _SEG.match(p, pos)
        if not m:
            return False
        name, i = m.group(1), m.group(2)
        if not isinstance(node, dict) or name not in node:
            return False
        node = node[name]
        if isinstance(node, dict) and isinstance(node.get("_table"), list):
            node = node["_table"]
        if i is not None:
            if not isinstance(node, list) or int(i) >= len(node):
                return False
            node = node[int(i)]
        pos = m.end()
    return True


def _value_paths(obj: dict, spec: dict) -> list[str]:
    """Every present, non-null value whose field declares ``evidence: value``."""
    out: list[str] = []
    pm = obj.get("paper_metadata")
    if isinstance(pm, dict):
        for f in (spec.get("paper") or {}).get("fields") or []:
            if f.get("evidence") == "value" and pm.get(f["name"]) is not None:
                out.append(f"paper_metadata.{f['name']}")
    ent = spec.get("entries") or {}
    key = ent.get("key")
    arr = obj.get(key) if key else None
    if isinstance(arr, dict):
        arr = arr.get("_table")
    for i, el in enumerate(arr or []):
        if not isinstance(el, dict):
            continue
        for f in ent.get("fields") or []:
            if f.get("evidence") == "value" and el.get(f["name"]) is not None:
                out.append(f"{key}[{i}].{f['name']}")
        for ch in ent.get("children") or []:
            for j, row in enumerate(el.get(ch["key"]) or []):
                if not isinstance(row, dict):
                    continue
                for f in ch.get("fields") or []:
                    if f.get("evidence") == "value" and row.get(f["name"]) is not None:
                        out.append(f"{key}[{i}].{ch['key']}[{j}].{f['name']}")
    return out


# ── the schema row ────────────────────────────────────────────────────────────

def field_defs_for(spec: dict) -> dict:
    """What goes into ``schema.field_defs`` for a format-2 preset: the spec itself plus
    the legacy keys the current review UI still reads (``sub_views``, ``field_types``,
    ``render_hints``, …) synthesised from it — so old and new front-ends both work."""
    ent = spec.get("entries") or {}
    entry_names = [f["name"] for f in ent.get("fields") or []]
    child_keys = [ch["key"] for ch in ent.get("children") or []]
    placeable = entry_names + child_keys
    tabs = (spec.get("display") or {}).get("tabs") or []
    if not tabs:
        tabs = [{"id": "details", "label": "Details", "fields": placeable}]
    placed = {n for t in tabs for n in t.get("fields") or []}
    rest = [n for n in placeable if n not in placed]
    sub_views = []
    for t in tabs:
        names = list(t.get("fields") or [])
        groups = sorted({(f.get("confidence")) for f in ent.get("fields") or []
                         if f["name"] in names and f.get("confidence")}
                        | {f.get("confidence") for ch in ent.get("children") or [] if ch["key"] in names
                           for f in ch.get("fields") or [] if f.get("confidence")})
        sub_views.append({"id": t["id"], "label": t["label"], "include_keys": names,
                          "evidence_keys": [n for n in names if n in entry_names or n in child_keys],
                          "confidence_keys": groups})
    if rest:
        sub_views.append({"id": "_other", "label": "Other", "include_keys": rest,
                          "evidence_keys": rest, "confidence_keys": []})
    field_types: dict[str, dict] = {}
    for f in ent.get("fields") or []:
        if f.get("type") in ("enum", "multi") and isinstance(f.get("options"), list):
            field_types[f["name"]] = {
                "type": "select" if f["type"] == "enum" else "multiselect",
                "options": [o.get("value") if isinstance(o, dict) else o for o in f["options"]],
                "allow_other": bool(f.get("allow_other"))}
        elif f.get("type") in ("integer", "number"):
            field_types[f["name"]] = {"type": "number"}
    render_hints: dict[str, dict] = {}
    for ch in ent.get("children") or []:
        if ch.get("layout") == "cards":
            scalars = [f["name"] for f in ch.get("fields") or [] if f.get("type") in SCALAR_TYPES]
            tables = [f["name"] for f in ch.get("fields") or [] if f.get("type") == "table"]
            render_hints[ch["key"]] = {"as": "cards", "header": scalars[:2], "table": tables[0] if tables else None}
    groups = [g["id"] for g in (spec.get("confidence") or {}).get("groups") or []]
    return {
        "format": FORMAT,
        "preset_id": spec["id"],
        "title": spec["meta"].get("title"),
        "tagline": spec["meta"].get("tagline"),
        "mode": spec["meta"].get("mode"),
        "schema_version": content_hash(spec)[:8],
        "hash": content_hash(spec),
        "schema_id": schema_id(spec),
        "label": label(spec),
        "data_sources": [],
        "sub_views": sub_views,
        "field_types": field_types,
        "render_hints": render_hints,
        "evidence_keys": sorted(set(placeable)),
        "confidence_keys": groups,
        "core_keys": placeable,
        "spec": spec,
    }
