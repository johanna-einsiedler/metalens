"""REVIEW TEMPLATES: how a person checks records of one data contract. A preset keeps the
contract and the prompt; a template says what the review asks — in STAGES of BLOCKS over UNITS:
the paper, the entries, a child's rows, a paper table's rows, or the distinct values of a
column — each block naming the fields shown (editable into the coder's coding), marks on a
row, masks, what stays blind, and the QUESTIONS the coder answers per unit. Answers live in the
coding (``record_coding.answers`` / ``document_coding.answers``, keyed template → unit → question),
never on the record. A preset's ``display.review`` keeps working as its implicit template
(``from_preset``), without questions.

  * ``for``       — the preset (base id) whose records the template reviews
  * ``blind``     — fields whose model values never reach the coder's payload (``fields``), and
                    whether every paper field no block shows is dropped (``paper_fields: "unshown"``)
  * ``stages``    — ordered; each ``{id, label, help, evidence, blocks}``
  * ``blocks``    — ``{unit, label, help, show, title, marks, group_by_entry, where, only_sampled,
                    mask_numbers, stack, questions, add, sections, inline, layout, columns, grid,
                    confirm}`` — ``layout: "rows"`` draws the units as one table (a row each, the
                    questions as columns; ``columns`` groups the shown fields), ``grid`` draws the
                    exhibit the units sit in as printed above it (its transcription in the ``items``
                    table); ``layout: "flags"`` makes the grid where the coder works — a click flags
                    a value (``grid.flag``) or a mapping (``grid.mapping``, consolidated onto the
                    table) or adds a missed cell, the flagged units listed as rows beneath — and
                    ``confirm`` answers every unit still open in one click
  * ``questions`` — field declarations the coder answers: ``{name, type, label, help, options |
                    options_from, allow_other, required, required_when}``

Unit keys (how an answer names its unit; also the DOM ids): the paper ``""``; an entry ``""``
in its record; a child's row ``"<child>#<_rid>"``; a paper table's row ``"<table>#<_rid>"``;
a distinct value ``"<path>.<field>=<value>"``; a unit the coder added ``"<unit>#+<client id>"``.

``template_id`` = ``<id>@<sha8>`` over what the template asks (units, where, the questions'
names, types, options and requiredness) — wording is left out.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import contract
from . import preset_spec as ps

TEMPLATES_DIR = Path(__file__).parent / "templates"
FORMAT = 1
QUESTION_TYPES = ("string", "text", "integer", "number", "boolean", "enum", "multi", "list")
WHERE_OPS = ("eq", "in", "not_in", "not_null", "null", "gt", "gte", "lt", "lte", "sampled")
IDENTITY = ("title", "doi", "year", "authors", "journal")           # the paper's identity: always a paper unit's fields

_ALLOWED = {
    "root": {"format", "id", "version", "meta", "for", "layout", "items", "verify", "blind", "stages"},
    "meta": {"title", "tagline", "description", "hidden", "brands"},
    "blind": {"fields", "paper_fields"},
    "stage": {"id", "label", "help", "evidence", "blocks"},
    "block": {"unit", "label", "help", "show", "title", "marks", "group_by_entry", "where", "only_sampled", "mask_numbers",
              "stack", "questions", "add", "sections", "inline", "layout", "columns", "grid", "confirm"},
    "column": {"label", "fields", "wide"},
    "grid": {"of", "panel", "row", "column", "tip", "value", "flag", "mapping"},
    "grid_flag": {"question", "answer"},
    "grid_mapping": {"field", "sep", "question", "answer", "note"},
    "confirm": {"label", "answers", "paper"},
    "question": {"name", "type", "label", "help", "options", "options_from", "allow_other", "required", "required_when", "range"},
    "options_from": {"field", "prefix", "label_prefix"},
    "mark": {"when", "when_nonempty", "text"},
    "add": {"label", "fields"},
    "add_field": {"name", "type", "label", "help", "options", "required"},
}


class TemplateError(ValueError):
    def __init__(self, errors: list[str], template_id: str | None = None):
        self.errors = list(errors)
        self.template_id = template_id
        head = f"template {template_id!r}: " if template_id else "template: "
        super().__init__(head + "; ".join(self.errors[:6]) + (" …" if len(self.errors) > 6 else ""))


def is_v1(doc: Any) -> bool:
    return isinstance(doc, dict) and doc.get("format") == FORMAT and isinstance(doc.get("stages"), list) and isinstance(doc.get("for"), str)


# ── loading ───────────────────────────────────────────────────────────────────

def load_dir(directory: Path = TEMPLATES_DIR) -> dict[str, dict]:
    """Every valid template file directly under ``directory``; a broken file is reported on
    stderr and skipped, never fatal."""
    out: dict[str, dict] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[templates] skipping {path.name}: {e}", file=sys.stderr, flush=True)
            continue
        if not is_v1(raw):
            continue
        try:
            tpl = normalize(raw)
        except TemplateError as e:
            print(f"[templates] skipping {path.name}: {e}", file=sys.stderr, flush=True)
            continue
        if tpl["id"] in out:
            print(f"[templates] duplicate id {tpl['id']!r} in {path.name}; ignoring", file=sys.stderr, flush=True)
            continue
        out[tpl["id"]] = tpl
    return out


def load_all() -> dict[str, dict]:
    """The bundled templates plus those in ``PAPERLENS_TEMPLATE_DIRS``; bundled ids win."""
    out = load_dir(TEMPLATES_DIR)
    for d in (os.environ.get("PAPERLENS_TEMPLATE_DIRS") or "").split(os.pathsep):
        if d.strip():
            for tid, tpl in load_dir(Path(d.strip())).items():
                out.setdefault(tid, tpl)
    return out


def get(template_id: str | None) -> dict | None:
    if not template_id:
        return None
    return load_all().get(str(template_id).partition("@")[0])


# ── normalisation ─────────────────────────────────────────────────────────────

def normalize(doc: dict) -> dict:
    if not isinstance(doc, dict):
        raise TemplateError(["template must be a JSON object"])
    tpl = json.loads(json.dumps(doc))
    tpl.setdefault("format", FORMAT)
    tpl.setdefault("version", 1)
    tpl.setdefault("meta", {})
    tpl.setdefault("layout", "stages")
    tpl.setdefault("verify", "paper")
    blind = tpl.get("blind")
    tpl["blind"] = {"fields": list((blind or {}).get("fields") or []), **({"paper_fields": blind["paper_fields"]} if isinstance(blind, dict) and blind.get("paper_fields") else {})} \
        if isinstance(blind, dict) or blind is None else blind
    for st in tpl.get("stages") or []:
        if not isinstance(st, dict):
            continue
        if isinstance(st.get("id"), str) and not st.get("label"):
            st["label"] = ps.format_key(st["id"])
        st.setdefault("evidence", "items")
        for bl in st.get("blocks") or []:
            if not isinstance(bl, dict):
                continue
            bl.setdefault("show", [])
            bl.setdefault("questions", [])
            bl.setdefault("mask_numbers", False)
            bl.setdefault("group_by_entry", False)
            bl.setdefault("only_sampled", False)
            bl.setdefault("stack", False)
            for q in bl["questions"] if isinstance(bl["questions"], list) else []:
                if isinstance(q, dict):
                    q.setdefault("type", "string")
                    if isinstance(q.get("name"), str):
                        q.setdefault("label", ps.format_key(q["name"]))
                    q.setdefault("required", False)
            add = bl.get("add")
            if isinstance(add, dict):
                for f in add.get("fields") or []:
                    if isinstance(f, dict):
                        f.setdefault("type", "string")
                        if isinstance(f.get("name"), str):
                            f.setdefault("label", ps.format_key(f["name"]))
    return tpl


# ── the units of a preset ─────────────────────────────────────────────────────

def unit_kind(unit: Any, pspec: dict) -> tuple[str, str | None, str | None] | None:
    """``(kind, key, field)``: ("paper", None, None) | ("entries", None, None) | ("child", key, None)
    | ("table", table, None) | ("distinct", child or table, field); None for a unit the preset has not."""
    kids = {c.get("key") for c in ((pspec.get("entries") or {}).get("children") or []) if isinstance(c, dict)}
    tables = {f.get("name") for f in ((pspec.get("paper") or {}).get("fields") or []) if isinstance(f, dict) and f.get("type") == "table"}
    if unit == "paper":
        return "paper", None, None
    if unit == "entries":
        return "entries", None, None
    if isinstance(unit, str) and unit in kids:
        return "child", unit, None
    if isinstance(unit, str) and unit.startswith("paper_metadata.") and unit[len("paper_metadata."):] in tables:
        return "table", unit[len("paper_metadata."):], None
    if isinstance(unit, dict) and set(unit) == {"distinct"} and isinstance(unit["distinct"], str) and unit["distinct"].count(".") == 1:
        x, field = unit["distinct"].split(".")
        if (x in kids or x in tables) and field in unit_fields(pspec, "child" if x in kids else "table", x):
            return "distinct", x, field
    return None


def unit_fields(pspec: dict, kind: str, key: str | None, field: str | None = None) -> dict[str, dict]:
    """name → declaration of the fields a unit of that kind has (the paper's identity included)."""
    ent = pspec.get("entries") or {}
    if kind == "paper":
        out = {n: {"name": n, "type": "list" if n == "authors" else "integer" if n == "year" else "string", "label": ps.format_key(n)} for n in IDENTITY}
        out.update({f["name"]: f for f in (pspec.get("paper") or {}).get("fields") or [] if isinstance(f, dict) and f.get("name")})
        return out
    if kind == "entries":
        return {f["name"]: f for f in ent.get("fields") or [] if isinstance(f, dict) and f.get("name")}
    if kind == "child":
        ch = next((c for c in ent.get("children") or [] if isinstance(c, dict) and c.get("key") == key), None)
        return {f["name"]: f for f in (ch or {}).get("fields") or [] if isinstance(f, dict) and f.get("name")}
    if kind == "table":
        t = next((f for f in (pspec.get("paper") or {}).get("fields") or [] if isinstance(f, dict) and f.get("name") == key), None)
        return {c["name"]: c for c in (t or {}).get("columns") or [] if isinstance(c, dict) and c.get("name")}
    if kind == "distinct":
        kids = {c.get("key") for c in (ent.get("children") or []) if isinstance(c, dict)}
        src = unit_fields(pspec, "child" if key in kids else "table", key)
        return {field: src.get(field) or {"name": field, "type": "string"}, "n": {"name": "n", "type": "integer", "label": "Rows"}}
    return {}


def unit_label(unit: Any) -> str:
    if isinstance(unit, dict):
        return unit.get("distinct", "distinct").split(".")[-1]
    return str(unit).replace("paper_metadata.", "")


# ── validation ────────────────────────────────────────────────────────────────

def validate(tpl: dict, pspec: dict | None = None, *, resolver: Callable[[str], dict | None] | None = None) -> tuple[list[str], list[str]]:
    """Every problem at once, path-prefixed. ``pspec`` is the preset the template is ``for``
    (resolved through ``resolver(base id)`` — the bundled presets by default — when absent)."""
    errs: list[str] = []
    warns: list[str] = []
    E, W = errs.append, warns.append
    if not isinstance(tpl, dict):
        return ["template must be a JSON object"], []

    def unknown(obj: dict, kind: str, at: str) -> None:
        for k in obj:
            if k not in _ALLOWED[kind]:
                E(f"{at}: unknown key {k!r}")

    unknown(tpl, "root", "$")
    if tpl.get("format") != FORMAT:
        E(f"$.format must be {FORMAT}")
    tid = tpl.get("id")
    if not isinstance(tid, str) or not ps.ID_RE.match(tid):
        E("$.id must match ^[a-z][a-z0-9-]{1,63}$")
    if not isinstance(tpl.get("version", 1), int) or tpl.get("version", 1) < 1:
        E("$.version must be a positive integer")
    meta = tpl.get("meta")
    if not isinstance(meta, dict):
        E("$.meta must be an object")
    else:
        unknown(meta, "meta", "$.meta")
        if not (isinstance(meta.get("title"), str) and meta["title"].strip()):
            E("$.meta.title is required")
    base = tpl.get("for")
    if not isinstance(base, str) or not ps.ID_RE.match(base):
        E("$.for must name the preset (base id) the template reviews"); base = None
    if pspec is None and base:
        if resolver is None:
            from . import presets
            resolver = presets.load_all().get
        pspec = resolver(base)
        if pspec is None:
            E(f"$.for: unknown preset {base!r}")
    if pspec is not None and base and pspec.get("id") != base:
        E(f"$.for is {base!r} but the preset given is {pspec.get('id')!r}")
    if tpl.get("layout") != "stages":
        E("$.layout must be 'stages' (the only template layout in this release)")
    if tpl.get("verify") not in ("entry", "paper"):
        E("$.verify must be 'entry' or 'paper'")
    if pspec is not None and tpl.get("items") is not None:
        ps._items_table({"items": tpl["items"]}, pspec, E, W)       # noqa: SLF001 - the same rule as a preset's review
    blind = tpl.get("blind")
    if blind is not None:
        if not isinstance(blind, dict):
            E("$.blind must be an object {fields, paper_fields}")
        else:
            unknown(blind, "blind", "$.blind")
            bf = blind.get("fields", [])
            if not isinstance(bf, list) or any(not isinstance(x, str) for x in bf):
                E("$.blind.fields must list field names")
            elif pspec is not None:
                known = set(ps.field_index(pspec)) | {c.get("name") for f in ((pspec.get("paper") or {}).get("fields") or []) if isinstance(f, dict) for c in f.get("columns") or [] if isinstance(c, dict)}
                for x in bf:
                    if x not in known:
                        E(f"$.blind.fields: {x!r} is not a field of the preset")
            if blind.get("paper_fields") not in (None, "unshown"):
                E("$.blind.paper_fields must be 'unshown' (every paper field no block shows is dropped) or absent")
    stages = tpl.get("stages")
    if not isinstance(stages, list) or not stages:
        E("$.stages must list at least one stage")
        return errs, warns
    seen: set[str] = set()
    for i, st in enumerate(stages):
        at = f"$.stages[{i}]"
        if not isinstance(st, dict):
            E(f"{at} must be an object"); continue
        unknown(st, "stage", at)
        sid = st.get("id")
        if not isinstance(sid, str) or not ps.NAME_RE.match(sid):
            E(f"{at}.id must match ^[A-Za-z_][A-Za-z0-9_]*$")
        elif sid in seen:
            E(f"{at}.id {sid!r} is duplicated")
        else:
            seen.add(sid)
        if not (isinstance(st.get("label"), str) and st["label"].strip()):
            E(f"{at}.label is required")
        if not (isinstance(st.get("help"), str) and st["help"].strip()):
            W(f"{at}: no help — the coder gets no instruction at the top of the stage")
        if st.get("evidence") not in ("items", "none"):
            E(f"{at}.evidence must be 'items' (the default) or 'none'")
        blocks = st.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            E(f"{at}.blocks must list at least one block"); continue
        bf = frozenset(x for x in (tpl["blind"].get("fields") or []) if isinstance(x, str)) if isinstance(tpl.get("blind"), dict) else frozenset()
        for j, bl in enumerate(blocks):
            _validate_block(bl, f"{at}.blocks[{j}]", pspec, E, W, blind=bf)
            if isinstance(bl, dict) and bl.get("grid") is not None and not tpl.get("items"):
                E(f"{at}.blocks[{j}].grid reads the exhibit's transcription from the template's items table: name it in $.items")
    paper_qs = {q["name"]: q for st in stages if isinstance(st, dict) for bl in st.get("blocks") or []
                if isinstance(bl, dict) and bl.get("unit") == "paper" for q in bl.get("questions") or [] if isinstance(q, dict) and isinstance(q.get("name"), str)}
    for i, st in enumerate(stages):
        for j, bl in enumerate(st.get("blocks") or [] if isinstance(st, dict) else []):
            pc = (bl.get("confirm") or {}).get("paper") if isinstance(bl, dict) and isinstance(bl.get("confirm"), dict) else None
            for qn, v in (pc.items() if isinstance(pc, dict) else []):
                if not _answer_ok(paper_qs.get(qn), v):
                    E(f"$.stages[{i}].blocks[{j}].confirm.paper: {qn!r} must be an enum question of a paper block and {v!r} one of its options")
    return errs, warns


def _answer_ok(q: dict | None, value: Any) -> bool:
    """A fixed answer a template gives on the coder's behalf (a flag's, a confirm's): one of the
    question's options."""
    if not isinstance(q, dict):
        return False
    opts = [str(o.get("value") if isinstance(o, dict) else o) for o in q.get("options") or []]
    return q.get("type") == "enum" and str(value) in opts


def _validate_layout(bl: dict, at: str, fields: dict[str, dict] | None, show: list, E, questions: dict | None = None) -> None:
    """``layout`` (cards | rows | flags), ``columns`` (rows, flags: the shown fields grouped into
    the table's columns), ``grid`` (rows, flags: the fields that place a unit in its exhibit's
    transcription; for flags also the question a value click answers, the mapping text and its
    question) and ``confirm`` (the answers one click gives every open unit)."""
    questions = questions or {}
    layout = bl.get("layout", "cards")
    if layout not in ("cards", "rows", "flags"):
        E(f"{at}.layout must be 'cards' (the default), 'rows' or 'flags'")
    tabular = layout in ("rows", "flags")
    cols = bl.get("columns")
    if cols is not None:
        if not tabular:
            E(f"{at}.columns applies to layout 'rows' or 'flags'")
        if not isinstance(cols, list) or not cols:
            E(f"{at}.columns must list the table's columns")
            cols = []
        for c, col in enumerate(cols):
            cat = f"{at}.columns[{c}]"
            if isinstance(col, dict):
                for k in col:
                    if k not in _ALLOWED["column"]:
                        E(f"{cat}: unknown key {k!r}")
                if col.get("label") is not None and not isinstance(col["label"], str):
                    E(f"{cat}.label must be a string")
                if not isinstance(col.get("wide", False), bool):
                    E(f"{cat}.wide must be a boolean")
                names = col.get("fields")
            else:
                names = [col] if isinstance(col, str) else col
            if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
                E(f"{cat} must be a shown field's name, a list of them, or {{label, fields, wide?}}"); continue
            for n in names:
                if n not in show:
                    E(f"{cat}: {n!r} is not among the block's shown fields")
    grid = bl.get("grid")
    if layout == "flags" and grid is None:
        E(f"{at}: layout 'flags' needs a grid (the table the coder flags on)")
    if grid is not None:
        if not tabular:
            E(f"{at}.grid applies to layout 'rows' or 'flags'")
        if not isinstance(grid, dict):
            E(f"{at}.grid must be {{of, row, column, panel?, tip?, value?, flag?, mapping?}}"); return
        for k in grid:
            if k not in _ALLOWED["grid"]:
                E(f"{at}.grid: unknown key {k!r}")
        for k in ("of", "row", "column", "panel", "value"):
            v = grid.get(k)
            if v is None and k not in ("panel", "value"):
                E(f"{at}.grid.{k} must name a field of the unit"); continue
            if v is not None and (not isinstance(v, str) or (fields is not None and v not in fields)):
                E(f"{at}.grid.{k}: {v!r} is not a field of the unit")
        if grid.get("tip") is not None:
            if not isinstance(grid["tip"], str):
                E(f"{at}.grid.tip must be a string")
            elif fields is not None:
                for n in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", grid["tip"]):
                    if n not in fields:
                        E(f"{at}.grid.tip: {{{n}}} is not a field of the unit")
        for k, allowed in (("flag", "grid_flag"), ("mapping", "grid_mapping")):
            g = grid.get(k)
            if g is None:
                if layout == "flags" and k == "flag":
                    E(f"{at}.grid.flag must say which question a click on a value answers, and how: {{question, answer}}")
                continue
            if layout != "flags":
                E(f"{at}.grid.{k} applies to layout 'flags'")
            if not isinstance(g, dict):
                E(f"{at}.grid.{k} must be an object"); continue
            for kk in g:
                if kk not in _ALLOWED[allowed]:
                    E(f"{at}.grid.{k}: unknown key {kk!r}")
            if not _answer_ok(questions.get(g.get("question")), g.get("answer")):
                E(f"{at}.grid.{k}: {g.get('question')!r} must be an enum question of the block and {g.get('answer')!r} one of its options")
            if k == "mapping":
                if not isinstance(g.get("field"), str) or (fields is not None and g["field"] not in fields):
                    E(f"{at}.grid.mapping.field: {g.get('field')!r} is not a field of the unit")
                if not (isinstance(g.get("sep"), str) and g["sep"]):
                    E(f"{at}.grid.mapping.sep must be the text between the mapping's parts (\" · \")")
                if g.get("note") is not None and (questions.get(g["note"]) or {}).get("type") not in ("string", "text"):
                    E(f"{at}.grid.mapping.note: {g['note']!r} must be a string or text question of the block (where the flagged part is written)")
    conf = bl.get("confirm")
    if conf is not None:
        if not tabular:
            E(f"{at}.confirm applies to layout 'rows' or 'flags'")
        if not isinstance(conf, dict):
            E(f"{at}.confirm must be {{label?, answers, paper?}}"); return
        for k in conf:
            if k not in _ALLOWED["confirm"]:
                E(f"{at}.confirm: unknown key {k!r}")
        if conf.get("label") is not None and not isinstance(conf["label"], str):
            E(f"{at}.confirm.label must be a string")
        ans = conf.get("answers")
        if not isinstance(ans, dict) or not ans:
            E(f"{at}.confirm.answers must give {{question: answer}} for the block's questions")
        else:
            for qn, v in ans.items():
                if not _answer_ok(questions.get(qn), v):
                    E(f"{at}.confirm.answers: {qn!r} must be an enum question of the block and {v!r} one of its options")
        if conf.get("paper") is not None and not isinstance(conf["paper"], dict):
            E(f"{at}.confirm.paper must give {{question: answer}} for a paper block's questions")


def _validate_where(where: Any, fields: dict[str, dict], pspec: dict | None, at: str, E) -> None:
    if not isinstance(where, dict) or not where:
        E(f"{at} must be an object {{<column>: value | {{in | not_in | not_null | gt | lt | sampled: …}}}}"); return
    pfields = unit_fields(pspec, "paper", None) if pspec is not None else {}
    for k, cond in where.items():
        if not isinstance(k, str):
            E(f"{at}: keys must be column names"); continue
        if k.startswith("paper_metadata.") or k.startswith("paper."):
            name = k.split(".", 1)[1]
            if pspec is not None and name not in pfields:
                E(f"{at}.{k}: {name!r} is not a paper field")
        elif pspec is not None and k not in fields:
            E(f"{at}.{k}: not a field of the unit")
        if isinstance(cond, dict):
            for op in cond:
                if op not in WHERE_OPS:
                    E(f"{at}.{k}: unknown operator {op!r} (one of {', '.join(WHERE_OPS)})")
            if "sampled" in cond and not (isinstance(cond["sampled"], str) and cond["sampled"].count(".") == 1):
                E(f"{at}.{k}.sampled must be '<table>.<column>' (the sampled rows' column the value must be among)")
            for op in ("in", "not_in"):
                if op in cond and not isinstance(cond[op], list):
                    E(f"{at}.{k}.{op} must be a list")


def _validate_block(bl: Any, at: str, pspec: dict | None, E, W, blind: frozenset = frozenset()) -> None:
    if not isinstance(bl, dict):
        E(f"{at} must be an object"); return
    for k in bl:
        if k not in _ALLOWED["block"]:
            E(f"{at}: unknown key {k!r}")
    unit = bl.get("unit")
    uk = unit_kind(unit, pspec) if pspec is not None else None
    if pspec is not None and uk is None:
        E(f"{at}.unit must be 'paper', 'entries', a child key, 'paper_metadata.<table>' or {{\"distinct\": \"<child or table>.<field>\"}} of the preset")
        fields: dict[str, dict] = {}
    else:
        fields = unit_fields(pspec, *uk) if uk else {}
    show = bl.get("show", [])
    if not isinstance(show, list) or any(not isinstance(x, str) for x in show):
        E(f"{at}.show must list field names of the unit")
    elif uk is not None:
        for x in show:
            if x not in fields:
                E(f"{at}.show: {x!r} is not a field of the unit")
    for k in ("label", "help", "title"):
        if bl.get(k) is not None and not isinstance(bl[k], str):
            E(f"{at}.{k} must be a string")
    if isinstance(bl.get("title"), str) and uk is not None:
        for n in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", bl["title"]):
            if n not in fields:
                E(f"{at}.title: {{{n}}} is not a field of the unit")
            elif n in blind:
                E(f"{at}.title: {{{n}}} is blind — a head may not print what the coder is to answer")
    for k in ("group_by_entry", "only_sampled", "mask_numbers", "stack"):
        if not isinstance(bl.get(k, False), bool):
            E(f"{at}.{k} must be a boolean")
    if bl.get("group_by_entry") and uk is not None and uk[0] != "child":
        E(f"{at}.group_by_entry only applies to a child's rows")
    _validate_layout(bl, at, fields if uk is not None else None, show if isinstance(show, list) else [], E,
                     questions={q["name"]: q for q in (bl.get("questions") or []) if isinstance(q, dict) and isinstance(q.get("name"), str)})
    if bl.get("where") is not None:
        _validate_where(bl["where"], fields, pspec, f"{at}.where", E)
    marks = bl.get("marks")
    if marks is not None:
        if not isinstance(marks, list):
            E(f"{at}.marks must list {{when | when_nonempty, text}}")
        else:
            for m, mk in enumerate(marks):
                mat = f"{at}.marks[{m}]"
                if not isinstance(mk, dict) or set(mk) - _ALLOWED["mark"] or ("when" in mk) == ("when_nonempty" in mk) or not isinstance(mk.get("text"), str):
                    E(f"{mat} needs exactly one of when (a boolean field) or when_nonempty (a field), and text"); continue
                f = mk.get("when") or mk.get("when_nonempty")
                if uk is not None and f not in fields:
                    E(f"{mat}: {f!r} is not a field of the unit")
                elif uk is not None and "when" in mk and (fields[f].get("type") != "boolean"):
                    E(f"{mat}.when must name a boolean field of the unit")
                for n in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", mk["text"]):
                    if uk is not None and n not in fields:
                        E(f"{mat}.text: {{{n}}} is not a field of the unit")
    qs = bl.get("questions", [])
    qnames: list[str] = []
    if not isinstance(qs, list):
        E(f"{at}.questions must be a list of question declarations"); qs = []
    for q, qd in enumerate(qs):
        qat = f"{at}.questions[{q}]"
        if not isinstance(qd, dict):
            E(f"{qat} must be an object"); continue
        for k in qd:
            if k not in _ALLOWED["question"]:
                E(f"{qat}: unknown key {k!r}")
        name = qd.get("name")
        if not isinstance(name, str) or not ps.NAME_RE.match(name):
            E(f"{qat}.name must match ^[A-Za-z_][A-Za-z0-9_]*$"); name = None
        elif name in qnames:
            E(f"{qat}.name {name!r} is asked twice in this block")
        elif uk is not None and name in fields and name in (show if isinstance(show, list) else []):
            E(f"{qat}.name {name!r} is a field of the unit the block shows — an answer beside the value it answers needs another name")
        if name:
            qnames.append(name)
        t = qd.get("type", "string")
        if t not in QUESTION_TYPES:
            E(f"{qat}.type must be one of {', '.join(QUESTION_TYPES)}")
        opts, ofrom = qd.get("options"), qd.get("options_from")
        if t in ("enum", "multi"):
            if (opts is None) == (ofrom is None):
                E(f"{qat}: an enum or multi question names exactly one of options or options_from")
            if opts is not None and not (isinstance(opts, list) and opts and all(isinstance(o, (str, int)) or (isinstance(o, dict) and "value" in o) for o in opts)):
                E(f"{qat}.options must list values (or {{value, label, help}})")
            if ofrom is not None:
                srcs = ofrom if isinstance(ofrom, list) else [{"field": ofrom}] if isinstance(ofrom, str) else None
                if srcs is None:
                    E(f"{qat}.options_from must name a field, or list {{field, prefix?, label_prefix?}}")
                else:
                    for s in srcs:
                        if not isinstance(s, dict) or set(s) - _ALLOWED["options_from"] or not isinstance(s.get("field"), str):
                            E(f"{qat}.options_from: each source names a field (with prefix and label_prefix)"); continue
                        if pspec is not None:
                            src = _field_anywhere(pspec, s["field"])
                            if src is None or src.get("type") not in ("enum", "multi") or not isinstance(src.get("options"), list):
                                E(f"{qat}.options_from: {s['field']!r} is not an enum or multi field of the preset with declared options")
        elif opts is not None or ofrom is not None:
            E(f"{qat}: options only apply to an enum or multi question")
        if not isinstance(qd.get("required", False), bool):
            E(f"{qat}.required must be a boolean")
        if not isinstance(qd.get("allow_other", False), bool):
            E(f"{qat}.allow_other must be a boolean")
        rw = qd.get("required_when")
        if rw is not None:
            _validate_where(rw, {**fields, **{n: {"name": n} for n in qnames}}, pspec, f"{qat}.required_when", E)
    add = bl.get("add")
    if add is not None:
        if not isinstance(add, dict) or set(add) - _ALLOWED["add"] or not isinstance(add.get("fields"), list) or not add["fields"]:
            E(f"{at}.add must be {{label?, fields: [{{name, type, label, help, options, required}}]}} — what a unit the coder adds is made of")
        else:
            if uk is not None and uk[0] == "paper":
                E(f"{at}.add: the paper is one unit; nothing to add")
            for k, f in enumerate(add["fields"]):
                fat = f"{at}.add.fields[{k}]"
                if not isinstance(f, dict) or set(f) - _ALLOWED["add_field"] or not isinstance(f.get("name"), str) or not ps.NAME_RE.match(f["name"]):
                    E(f"{fat} must be {{name, type?, label?, help?, options?, required?}}"); continue
                if f.get("type", "string") not in QUESTION_TYPES:
                    E(f"{fat}.type must be one of {', '.join(QUESTION_TYPES)}")
                if f["name"] in qnames:
                    E(f"{fat}.name {f['name']!r} is also a question of the block")
    own = set(show if isinstance(show, list) else []) | set(qnames)
    ps._validate_sections_inline(bl, own, E, at)                   # noqa: SLF001 - the same grammar as a preset's stage


def _field_anywhere(pspec: dict, name: str) -> dict | None:
    """A field of the preset by name: paper, entry, child, or a paper table's column."""
    hit = ps.field_index(pspec).get(name)
    if hit and hit.get("field"):
        return hit["field"]
    for f in (pspec.get("paper") or {}).get("fields") or []:
        if isinstance(f, dict) and f.get("type") == "table":
            for c in f.get("columns") or []:
                if isinstance(c, dict) and c.get("name") == name:
                    return c
    return None


def validate_for_step(st: dict, by_id: dict, at: str, E, *, resolver: Callable[[str], dict | None] | None = None) -> None:
    """A review step's ``template``: it exists and is ``for`` the preset of every step it reads."""
    tid = st.get("template")
    if not isinstance(tid, str):
        E(f"{at}.template must name a review template"); return
    tpl = get(tid)
    if tpl is None:
        E(f"{at}.template: unknown template {tid!r}"); return
    for o in st.get("over") or []:
        pin = (by_id.get(o) or {}).get("preset")
        if isinstance(pin, str) and pin.partition("@")[0] != tpl.get("for"):
            E(f"{at}.template: {tid!r} is for {tpl.get('for')!r}, not for the preset of {o!r} ({pin.partition('@')[0]!r})")


# ── identity ──────────────────────────────────────────────────────────────────

def template_contract(tpl: dict) -> dict:
    """What a template asks: the preset, the units, their filters, the questions' names, types,
    options and requiredness — not labels, helps, shown fields, marks or masks."""
    def q_(q: dict) -> dict:
        return {k: q.get(k) for k in ("name", "type", "options", "options_from", "allow_other", "required", "required_when") if q.get(k) not in (None, False)}
    return {"for": tpl.get("for"), "verify": tpl.get("verify"),
            "stages": [{"blocks": [{"unit": bl.get("unit"), **({"where": bl["where"]} if bl.get("where") else {}),
                                    "questions": [q_(q) for q in bl.get("questions") or [] if isinstance(q, dict)],
                                    **({"add": [f.get("name") for f in bl["add"].get("fields") or [] if isinstance(f, dict)]} if isinstance(bl.get("add"), dict) else {})}
                                   for bl in st.get("blocks") or [] if isinstance(bl, dict)]}
                       for st in tpl.get("stages") or [] if isinstance(st, dict)]}


def content_hash(tpl: dict) -> str:
    return hashlib.sha256(ps.canonical_json(template_contract(tpl)).encode("utf-8")).hexdigest()


def template_id(tpl: dict) -> str:
    return f"{tpl['id']}@{content_hash(tpl)[:8]}"


# ── a preset's own review as a template ───────────────────────────────────────

def from_preset(pspec: dict) -> dict | None:
    """The preset's ``display.review`` as the implicit template: the same declaration, marked
    ``source: "preset"`` — no blocks, no questions; the workspace draws it as it always has."""
    rv = ((pspec or {}).get("display") or {}).get("review")
    if not isinstance(rv, dict):
        return None
    return {**rv, "id": pspec.get("id"), "for": pspec.get("id"), "source": "preset"}


# ── questions and their options ───────────────────────────────────────────────

def question_defs(block: dict, pspec: dict) -> list[dict]:
    """The block's questions as field declarations the workspace renders: ``options_from``
    expanded into ``options`` (value, label, help), the rest as declared."""
    out = []
    for q in block.get("questions") or []:
        if not isinstance(q, dict):
            continue
        d = {k: v for k, v in q.items() if k != "options_from"}
        ofrom = q.get("options_from")
        if ofrom is not None:
            srcs = ofrom if isinstance(ofrom, list) else [{"field": ofrom}]
            opts: list[dict] = []
            seen: set[str] = set()
            for s in srcs:
                src = _field_anywhere(pspec, s.get("field")) if isinstance(s, dict) else None
                for o in (src or {}).get("options") or []:
                    value = o.get("value") if isinstance(o, dict) else o
                    label = (o.get("label") if isinstance(o, dict) else None) or ps.format_key(str(value))
                    v = f"{s.get('prefix', '')}{value}"
                    if v in seen:
                        continue
                    seen.add(v)
                    opts.append({"value": v, "label": f"{s.get('label_prefix', '')}{label}", **({"help": o["help"]} if isinstance(o, dict) and o.get("help") else {})})
            d["options"] = opts
        out.append(d)
    return out


def _add_defs(block: dict) -> list[dict]:
    add = block.get("add")
    return [dict(f) for f in (add or {}).get("fields") or [] if isinstance(f, dict)] if isinstance(add, dict) else []


# ── where ─────────────────────────────────────────────────────────────────────

def _lookup(row: dict, key: str, paper: dict | None) -> Any:
    if key.startswith("paper_metadata."):
        return (paper or {}).get(key[len("paper_metadata."):])
    if key.startswith("paper."):
        return (paper or {}).get(key[len("paper."):])
    return (row or {}).get(key)


def where_match(row: dict, where: dict | None, *, paper: dict | None = None, sampled: dict[str, set] | None = None) -> bool:
    """Whether a row meets a ``where``: every key's condition holds. A scalar condition is
    equality; a dict holds operators (``in``, ``not_in``, ``not_null``, ``null``, ``gt``, ``gte``,
    ``lt``, ``lte``, ``eq``, ``sampled``). ``sampled: "<table>.<column>"`` holds when the value is
    among the sampled rows' values of that column — or always, when no sample is given."""
    if not where:
        return True
    for key, cond in where.items():
        v = _lookup(row, key, paper)
        if not isinstance(cond, dict):
            if v != cond and not (v is None and cond is None):
                return False
            continue
        for op, arg in cond.items():
            if op == "eq" and v != arg:
                return False
            if op == "in" and v not in (arg or []):
                return False
            if op == "not_in" and v in (arg or []):
                return False
            if op == "not_null" and (v is None or v == "" or v == []) == bool(arg):
                return False
            if op == "null" and (v is None or v == "" or v == []) != bool(arg):
                return False
            if op in ("gt", "gte", "lt", "lte"):
                try:
                    x, y = float(v), float(arg)
                except (TypeError, ValueError):
                    return False
                if (op == "gt" and not x > y) or (op == "gte" and not x >= y) or (op == "lt" and not x < y) or (op == "lte" and not x <= y):
                    return False
            if op == "sampled" and sampled is not None:
                if v not in (sampled.get(arg) or set()):
                    return False
    return True


# ── the units of a document ───────────────────────────────────────────────────

def _rid(row: dict, j: int) -> str:
    return str(row.get(contract.ROW_ID_KEY) if isinstance(row, dict) and row.get(contract.ROW_ID_KEY) is not None else f"i{j}")


def unit_rows(block: dict, view: dict, pspec: dict, *, sampled: dict[str, set] | None = None) -> list[dict]:
    """The units a block has in a document view, in order: ``{key, record_id, entry_index,
    index, path, row, title}`` — the paper (one), the entries (one per record), a child's rows
    (per record), a paper table's rows, or the distinct values of a column (``row`` =
    ``{field: value, n}``). ``where`` filters; ``only_sampled`` keeps the sampled keys."""
    uk = unit_kind(block.get("unit"), pspec)
    if uk is None:
        return []
    kind, key, field = uk
    pm = view.get("paper_metadata") if isinstance(view.get("paper_metadata"), dict) else {}
    ekey = (pspec.get("entries") or {}).get("key") or "records"
    recs = [r for r in view.get("records") or [] if isinstance(r, dict) and not r.get("screened_empty")]
    where = block.get("where")
    out: list[dict] = []
    if kind == "paper":
        row = {**{k: (view.get("paper") or {}).get(k) for k in IDENTITY}, **pm}
        if where_match(row, where, paper=pm, sampled=sampled):
            out.append({"key": "", "record_id": None, "entry_index": None, "index": None, "path": "paper_metadata", "row": row})
    elif kind == "entries":
        for r in recs:
            fv = r.get("field_values") or {}
            if where_match(fv, where, paper=pm, sampled=sampled):
                out.append({"key": "", "record_id": r.get("id"), "entry_index": r.get("entry_index"), "index": None,
                            "path": f"{ekey}[{r.get('entry_index')}]", "row": fv})
    elif kind == "child":
        for r in recs:
            rows = (r.get("field_values") or {}).get(key)
            for j, row in enumerate(rows if isinstance(rows, list) else []):
                if not isinstance(row, dict) or not where_match(row, where, paper=pm, sampled=sampled):
                    continue
                out.append({"key": f"{key}#{_rid(row, j)}", "record_id": r.get("id"), "entry_index": r.get("entry_index"), "index": j,
                            "path": f"{ekey}[{r.get('entry_index')}].{key}[{j}]", "row": row})
    elif kind == "table":
        rows = pm.get(key)
        for j, row in enumerate(rows if isinstance(rows, list) else []):
            if not isinstance(row, dict) or not where_match(row, where, paper=pm, sampled=sampled):
                continue
            out.append({"key": f"{key}#{_rid(row, j)}", "record_id": None, "entry_index": None, "index": j,
                        "path": f"paper_metadata.{key}[{j}]", "row": row})
    elif kind == "distinct":
        kids = {c.get("key") for c in ((pspec.get("entries") or {}).get("children") or []) if isinstance(c, dict)}
        src_rows: list[dict] = []
        if key in kids:
            for r in recs:
                rows = (r.get("field_values") or {}).get(key)
                src_rows += [x for x in (rows if isinstance(rows, list) else []) if isinstance(x, dict)]
        else:
            rows = pm.get(key)
            src_rows = [x for x in (rows if isinstance(rows, list) else []) if isinstance(x, dict)]
        seen: dict[str, int] = {}
        for x in src_rows:
            v = x.get(field)
            if not where_match({field: v}, where, paper=pm, sampled=sampled):
                continue
            s = "" if v is None else str(v)
            seen[s] = seen.get(s, 0) + 1
        for k, (s, n) in enumerate(seen.items()):
            out.append({"key": f"{key}.{field}={s}", "record_id": None, "entry_index": None, "index": k, "path": None, "row": {field: s, "n": n}})
    if block.get("only_sampled") and sampled is not None:
        keep = sampled.get("_keys") or set()
        out = [u for u in out if u["key"] in keep]
    return out


def unit_title(block: dict, unit: dict, pspec: dict) -> str:
    """What a unit is called on screen: the block's ``title`` with the row's values filled in, else
    the child's title from the preset, else the first shown value, else the key."""
    row = unit.get("row") or {}
    t = block.get("title")
    if isinstance(t, str) and t:
        return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: str(row.get(m.group(1)) if row.get(m.group(1)) is not None else ""), t).strip()
    uk = unit_kind(block.get("unit"), pspec)
    if uk and uk[0] == "child":
        ch = next((c for c in ((pspec.get("entries") or {}).get("children") or []) if isinstance(c, dict) and c.get("key") == uk[1]), None)
        if ch and isinstance(ch.get("title"), str):
            return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: str(row.get(m.group(1)) if row.get(m.group(1)) is not None else ""), ch["title"]).strip()
    for n in block.get("show") or []:
        if row.get(n) not in (None, ""):
            return str(row[n])
    return unit.get("key") or ""


# ── unit keys ─────────────────────────────────────────────────────────────────

_KEY_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)#(?P<plus>\+?)(?P<rid>.+)$")
_DISTINCT_RE = re.compile(r"^(?P<x>[A-Za-z_][A-Za-z0-9_]*)\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$", re.S)


def parse_unit_key(key: str) -> dict:
    """``{"kind": self | row | added | distinct, "name", "field", "value"}``."""
    if key == "":
        return {"kind": "self"}
    m = _KEY_RE.match(key)
    if m:
        return {"kind": "added" if m.group("plus") else "row", "name": m.group("name"), "rid": m.group("rid")}
    m = _DISTINCT_RE.match(key)
    if m:
        return {"kind": "distinct", "name": m.group("x"), "field": m.group("field"), "value": m.group("value")}
    return {"kind": "unknown"}


def blocks_for_key(tpl: dict, pspec: dict, key: str, *, has_record: bool) -> list[dict]:
    """The blocks whose unit a key names: several when two stages ask the same unit."""
    k = parse_unit_key(key)
    out = []
    for st in tpl.get("stages") or []:
        for bl in st.get("blocks") or []:
            uk = unit_kind(bl.get("unit"), pspec)
            if uk is None:
                continue
            kind, name, field = uk
            if k["kind"] == "self" and ((kind == "entries" and has_record) or (kind == "paper" and not has_record)):
                out.append(bl)
            elif k["kind"] == "row" and ((kind == "child" and has_record) or (kind == "table" and not has_record)) and name == k["name"]:
                out.append(bl)
            elif k["kind"] == "distinct" and kind == "distinct" and name == k["name"] and field == k["field"]:
                out.append(bl)
            elif k["kind"] == "added" and isinstance(bl.get("add"), dict) and k["name"] == unit_label(bl.get("unit")) and (kind in ("child", "entries")) == has_record:
                out.append(bl)
    return out


def validate_answers(tpl: dict, pspec: dict, key: str, values: dict, *, has_record: bool) -> None:
    """Refuse an answer that names no question of the unit, or a value outside the question's
    options or type (``None`` clears). Raises ``ValueError``."""
    if not isinstance(values, dict) or not values:
        raise ValueError("answers must be an object {question: value}")
    blocks = blocks_for_key(tpl, pspec, key, has_record=has_record)
    if not blocks:
        raise ValueError(f"no unit {key!r} in the template" + (" of this record" if has_record else ""))
    defs: dict[str, dict] = {}
    for bl in blocks:
        for q in question_defs(bl, pspec):
            defs.setdefault(q["name"], q)
        if parse_unit_key(key)["kind"] == "added":
            for f in _add_defs(bl):
                defs.setdefault(f["name"], f)
    for name, v in values.items():
        q = defs.get(name)
        if q is None:
            raise ValueError(f"{name!r} is not a question of unit {key!r}")
        if v is None:
            continue
        t = q.get("type", "string")
        if t in ("integer", "number") and not (isinstance(v, (int, float)) and not isinstance(v, bool)):
            raise ValueError(f"{name!r} takes a number")
        if t == "boolean" and not isinstance(v, bool):
            raise ValueError(f"{name!r} takes true or false")
        if t in ("list", "multi") and not isinstance(v, list):
            raise ValueError(f"{name!r} takes a list")
        if t in ("string", "text") and not isinstance(v, str):
            raise ValueError(f"{name!r} takes a string")
        opts = [str(o.get("value") if isinstance(o, dict) else o) for o in q.get("options") or []]
        if opts and not q.get("allow_other"):
            vals = [str(x) for x in v] if isinstance(v, list) else [str(v)]
            bad = [x for x in vals if x not in opts]
            if bad:
                raise ValueError(f"{bad[0]!r} is not an option of {name!r}")


# ── the coder's payload ───────────────────────────────────────────────────────

_DIGIT = re.compile(r"\d")


def mask_numbers(text: Any) -> Any:
    return _DIGIT.sub("#", text) if isinstance(text, str) else text


def _blind_path(path: str | None, names: set[str]) -> bool:
    """Whether an evidence path ends at (or inside) a blind field."""
    if not path or not names:
        return False
    tail = re.split(r"[.\[]", path)
    return any(part.rstrip("]") in names for part in tail)


def resolved(tpl: dict, pspec: dict) -> dict:
    """The template as the workspace reads it: questions with their options expanded, the
    template id beside."""
    out = json.loads(json.dumps(tpl))
    for st in out.get("stages") or []:
        for bl in st.get("blocks") or []:
            bl["questions"] = question_defs(bl, pspec)
            if isinstance(bl.get("add"), dict):
                bl["add"]["fields"] = _add_defs(bl)
            bl["unit_label"] = unit_label(bl.get("unit"))
            uk = unit_kind(bl.get("unit"), pspec)
            bl["unit_kind"] = uk[0] if uk else None
    out["template_id"] = template_id(tpl)
    return out


def project(view: dict, tpl: dict, pspec: dict, *, sampled: dict[str, set] | None = None, coder: bool = True) -> dict:
    """The document view a coder of a task gets: the template resolved (``view["template"]``),
    the blind fields' values gone from the records, the rows and the paper block — and the
    quotes, items and ratings that would give them away — every paper field no block shows
    dropped when the template says so, digits masked where a block says so. The owner
    (``coder=False``) gets the template whole over the document as stored."""
    pm = view.get("paper_metadata") if isinstance(view.get("paper_metadata"), dict) else {}
    blind = tpl.get("blind") or {}
    bf = set(blind.get("fields") or [])
    if not coder:
        view["template"] = {**resolved(tpl, pspec), "masked": False, "blind": False}
        return view
    blocks = [bl for st in tpl.get("stages") or [] for bl in st.get("blocks") or []]
    # what the paper block must keep: shown paper fields, the tables and columns units read, where keys, the items, the identity
    keep = set(IDENTITY) | {tpl.get("items")} - {None}
    for bl in blocks:
        uk = unit_kind(bl.get("unit"), pspec)
        if uk is None:
            continue
        if uk[0] == "paper":
            keep |= set(bl.get("show") or [])
        if uk[0] in ("table", "distinct") and uk[1] in {f.get("name") for f in (pspec.get("paper") or {}).get("fields") or [] if isinstance(f, dict)}:
            keep.add(uk[1])
        for k in (bl.get("where") or {}):
            if isinstance(k, str) and k.startswith("paper_metadata."):
                keep.add(k[len("paper_metadata."):])
        for q in bl.get("questions") or []:
            for k in ((q.get("required_when") or {}) if isinstance(q, dict) else {}):
                if isinstance(k, str) and k.startswith("paper_metadata."):
                    keep.add(k[len("paper_metadata."):])
    if blind.get("paper_fields") == "unshown":
        declared = {f.get("name") for f in (pspec.get("paper") or {}).get("fields") or [] if isinstance(f, dict)}
        for name in list(pm):
            if name in declared and name not in keep:
                pm.pop(name, None)
    if bf:
        for name in list(pm):
            if name in bf:
                pm.pop(name, None)
        for rows in pm.values():
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        for name in bf:
                            row.pop(name, None)
        for r in view.get("records") or []:
            fv = r.get("field_values")
            if not isinstance(fv, dict):
                continue
            for name in list(fv):
                if name in bf:
                    fv.pop(name, None)
            for rows in fv.values():
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            for name in bf:
                                row.pop(name, None)
            r.pop("confidence", None)                       # the model's own doubt about a value it may not show
            r.pop("chain", None)
            r.pop("corrections", None)
        view["paper_confidence"] = {}
        view["document_confidence"] = {}
        view["evidence"] = [e for e in view.get("evidence") or [] if not _blind_path(e.get("field_path"), bf)]
        items = pm.get(tpl.get("items")) if tpl.get("items") else None
        for row in items if isinstance(items, list) else []:
            if isinstance(row, dict) and isinstance(row.get("bears_on"), list):
                row["bears_on"] = [b for b in row["bears_on"] if b not in bf]
    masked = False
    for bl in blocks:
        if not bl.get("mask_numbers"):
            continue
        masked = True
        uk = unit_kind(bl.get("unit"), pspec)
        fields = unit_fields(pspec, *uk) if uk else {}
        shown = [n for n in bl.get("show") or [] if (fields.get(n) or {}).get("type") in ("string", "text", None)]
        for u in unit_rows(bl, view, pspec, sampled=sampled):
            row = u.get("row")
            target = pm if (uk and uk[0] == "paper") else row
            if not isinstance(target, dict):
                continue
            for n in shown:
                if isinstance(target.get(n), str):
                    target[n] = mask_numbers(target[n])
    if masked:
        items = pm.get(tpl.get("items")) if tpl.get("items") else None
        for row in items if isinstance(items, list) else []:
            if isinstance(row, dict):
                for k in ("text", "note"):
                    if isinstance(row.get(k), str):
                        row[k] = mask_numbers(row[k])
        for e in view.get("evidence") or []:
            if isinstance(e.get("snippet"), str):
                e["snippet"] = mask_numbers(e["snippet"])
    view["paper_metadata"] = pm if isinstance(view.get("paper_metadata"), dict) else view.get("paper_metadata")
    view["template"] = {**resolved(tpl, pspec), "masked": masked, "blind": bool(bf) or blind.get("paper_fields") == "unshown"}
    return view


# ── completeness ──────────────────────────────────────────────────────────────

def _empty(v: Any) -> bool:
    return v is None or v == "" or v == []


def answers_at(answers: dict | None, record_id: str | None, key: str) -> dict:
    """A coder's answers on one unit, from ``{"paper": {key: {...}}, "records": {rid: {key: {...}}}}``."""
    if not isinstance(answers, dict):
        return {}
    if record_id:
        return ((answers.get("records") or {}).get(record_id) or {}).get(key) or {}
    return (answers.get("paper") or {}).get(key) or {}


def added_units(answers: dict | None, block: dict, pspec: dict) -> list[dict]:
    """The units the coder added to a block (answers under ``"<unit>#+<id>"`` keys), as unit rows."""
    name = unit_label(block.get("unit"))
    uk = unit_kind(block.get("unit"), pspec)
    out = []
    if not isinstance(answers, dict) or uk is None:
        return out
    if uk[0] in ("child", "entries"):
        for rid, units in (answers.get("records") or {}).items():
            for key, vals in (units or {}).items():
                k = parse_unit_key(key)
                if k["kind"] == "added" and k["name"] == name:
                    out.append({"key": key, "record_id": rid, "entry_index": None, "index": None, "path": None, "row": dict(vals or {}), "added": True})
    else:
        for key, vals in (answers.get("paper") or {}).items():
            k = parse_unit_key(key)
            if k["kind"] == "added" and k["name"] == name:
                out.append({"key": key, "record_id": None, "entry_index": None, "index": None, "path": None, "row": dict(vals or {}), "added": True})
    return out


def completeness(tpl: dict, view: dict, pspec: dict, answers: dict | None, *, sampled: dict[str, set] | None = None) -> dict:
    """What a coder still owes a task: every required question (``required``, or ``required_when``
    met) of every unit without an answer. ``{"required", "answered", "missing": [{stage, block,
    unit, record_id, question}], "complete"}``."""
    missing: list[dict] = []
    n_req = 0
    pm = view.get("paper_metadata") if isinstance(view.get("paper_metadata"), dict) else {}
    for st in tpl.get("stages") or []:
        for bi, bl in enumerate(st.get("blocks") or []):
            qs = [q for q in question_defs(bl, pspec) if q.get("required") or q.get("required_when")]
            if not qs:
                continue
            units = unit_rows(bl, view, pspec, sampled=sampled) + added_units(answers, bl, pspec)
            for u in units:
                a = answers_at(answers, u.get("record_id"), u["key"])
                ctx = {**(u.get("row") or {}), **a}
                for q in qs:
                    if q.get("required_when") and not where_match(ctx, q["required_when"], paper=pm, sampled=sampled):
                        continue
                    n_req += 1
                    if _empty(a.get(q["name"])):
                        missing.append({"stage": st.get("id"), "block": bi, "unit": u["key"], "record_id": u.get("record_id"), "question": q["name"]})
    return {"required": n_req, "answered": n_req - len(missing), "missing": missing, "complete": not missing}


# ── a task's template ─────────────────────────────────────────────────────────

def for_step(run: dict, step_id: str) -> tuple[dict | None, dict | None]:
    """``(template, step)`` of a run's review step — ``(None, step)`` when it declares none."""
    from . import workflow_spec as ws
    st = ws.steps_by_id(run.get("workflow") or {}).get(step_id)
    if st is None:
        return None, None
    return get(st.get("template")), st
