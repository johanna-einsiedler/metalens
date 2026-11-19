"""The declarative WORKFLOW format: a versioned, citable combination of presets, checks and
aggregation steps run incrementally on a dataset, with human review declared per step.

A preset says what one model call per paper extracts; a workflow says which presets run, in
what order, what deterministic checks and dataset-level steps follow, and where a person looks
— every record, a random sample, or none — before a release. The dataset keeps the state
(records, drafts, runs); the workflow only declares the process.

  * ``dataset``   — the unit a run counts in (papers) and what identifies one (the PDF hash)
  * ``steps``     — an ordered list; the order IS the dependency graph (a step may only read
                    earlier ones), the last step is the release
  * ``review``    — a step of its own: what it reads (``over``), ``mode`` ``all`` | ``sample``
                    (unit, size, seed, strata), how many coders per paper, and whether an
                    unsatisfied gate holds the next step or only the release

Presets are pinned BY DATA CONTRACT (``<preset>@<sha8>``, the schema id): the same contract
means the same dataset shape; wording changes do not re-version a workflow. The workflow's own
contract (``workflow_id``) hashes the steps, their pins and their review declarations — not
labels, help texts or a derive prompt's wording. A review step over one data step is hashed as
that step's ``review`` attribute (the form before review steps stood on their own), so a
workflow keeps its id across the two spellings; ``normalize`` reads both and writes the step.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import preset_spec as ps

WORKFLOWS_DIR = Path(__file__).parent / "workflows"
FORMAT = 1
KINDS = ("extract", "check", "derive", "vocabulary", "pages", "crosscheck", "loop", "release", "agent", "review", "pass")
DATA_KINDS = ("extract", "derive", "agent")           # steps whose output a release gate may name
GATE_KINDS = ("extract", "derive", "agent", "review") # …plus a joint review stage
REVIEWED_KINDS = ("extract", "derive", "vocabulary", "pages", "agent", "review")
DRAFT_KINDS = ("derive", "vocabulary", "pages")        # steps whose output is a draft a person commits
REVIEW_MODES = ("none", "all", "sample")
REVIEW_UNITS = ("entry", "paper", "row")      # row: a paper table's rows (a later release)
BLOCKS = ("next", "release")
SOURCES = ("live", "supplied")
MAX_K = 5
MAX_LOOP = 3
PIN_RE = re.compile(r"^([a-z][a-z0-9-]{1,63})@([0-9a-f]{8})$")
PASS_PIN_RE = re.compile(r"^([a-z_][a-z0-9_]*\.[a-z][a-z0-9_]*)@([0-9a-f]{8})$")   # a registered pass at its prompt sha (chains.pin)
UPDATE_KINDS = ("vocabulary", "pages", "crosscheck")   # the preset-level update steps, as workflow kinds
LATER = {"agent": "agent steps arrive in a later release"}
REVIEW_KEYS = ("mode", "unit", "n", "share", "seed", "stratify", "blocks", "oversample_flagged", "coders")   # the attribute form's keys
REVIEW_STEP_KEYS = REVIEW_KEYS + ("template", "where", "new_only", "table", "per_paper")                     # …and what only a step may say

_ALLOWED_KEYS = {
    "root": {"format", "id", "version", "meta", "card", "dataset", "steps", "implicit"},
    "meta": {"title", "tagline", "description", "authors", "keywords", "license", "related", "hidden", "brands"},
    "card": {"file", "text"},
    "dataset": {"unit", "key"},
    "step": {"id", "kind", "label", "help", "review"},
    "extract": {"preset", "source", "k", "vote", "input", "params", "dataset"},
    "agent": {"preset", "source", "dataset"},
    "review_step": {"over", "asks", *REVIEW_STEP_KEYS},
    "pass": {"pass", "reads", "k", "match", "over", "params", "model"},
    "vote": {"identity", "fields"},
    "input": {"previous"},
    "check": {"check", "over", "on_fail", "severity", "params"},
    "derive": {"over", "identity", "calls", "output", "group_by", "input"},
    "call": {"id", "kind", "prompt", "op", "by", "fields", "model"},
    "vocabulary": {"unit", "column"},
    "pages": {"group", "keyed_by"},
    "crosscheck": set(),
    "loop": {"steps", "until", "max_iterations"},
    "release": {"gate"},
    "review": {"mode", "unit", "n", "share", "seed", "stratify", "blocks", "oversample_flagged", "coders"},
}


class WorkflowError(ValueError):
    """A workflow document that failed validation; ``errors`` lists every problem."""

    def __init__(self, errors: list[str], workflow_id: str | None = None):
        self.errors = list(errors)
        self.workflow_id = workflow_id
        head = f"workflow {workflow_id!r}: " if workflow_id else "workflow: "
        super().__init__(head + "; ".join(self.errors[:6]) + (" …" if len(self.errors) > 6 else ""))


canonical_json = ps.canonical_json


def is_v1(doc: Any) -> bool:
    return isinstance(doc, dict) and doc.get("format") == FORMAT and isinstance(doc.get("steps"), list)


# ── loading ───────────────────────────────────────────────────────────────────

def load_file(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise WorkflowError([f"cannot read {Path(path).name}: {e}"])
    if not is_v1(doc):
        raise WorkflowError([f"{Path(path).name} is not a format-{FORMAT} workflow"])
    return normalize(doc, base_dir=Path(path).parent)


def load_dir(directory: Path = WORKFLOWS_DIR) -> dict[str, dict]:
    """Every valid workflow directly under ``directory``; invalid files are reported on stderr
    and skipped, never fatal (the pattern of ``preset_spec.load_dir``)."""
    out: dict[str, dict] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[workflows] skipping {path.name}: {e}", file=sys.stderr, flush=True)
            continue
        if not is_v1(raw):
            continue
        try:
            spec = normalize(raw, base_dir=path.parent)
        except (WorkflowError, ps.SpecError) as e:
            print(f"[workflows] skipping {path.name}: {e}", file=sys.stderr, flush=True)
            continue
        if spec["id"] in out:
            print(f"[workflows] duplicate id {spec['id']!r} in {path.name}; ignoring", file=sys.stderr, flush=True)
            continue
        out[spec["id"]] = spec
    return out


# ── normalisation: defaults and sibling files ─────────────────────────────────

def normalize(doc: dict, base_dir: Path | None = None) -> dict:
    """Fill the defaults, inline sibling files (the card, derive prompts) and read a ``review``
    attribute on a data step as the review step it is (``_shim_review_steps``). Shape errors
    are left to ``validate``; only what cannot be defaulted raises here."""
    if not isinstance(doc, dict):
        raise WorkflowError(["workflow must be a JSON object"])
    spec = json.loads(json.dumps(doc))                        # a deep copy, plain JSON
    spec.setdefault("format", FORMAT)
    spec.setdefault("version", 1)
    spec.setdefault("meta", {})
    ds = spec.get("dataset")
    spec["dataset"] = {"unit": "paper", "key": "pdf_sha256", **(ds if isinstance(ds, dict) else {})}
    card = spec.get("card")
    if isinstance(card, dict) and card.get("file") and not card.get("text"):
        if base_dir is None:
            raise WorkflowError([f"card.file {card['file']!r}: no base directory to read it from"], spec.get("id"))
        try:
            card["text"] = ps._read_sibling(card.pop("file"), base_dir, what="card.file")
        except ps.SpecError as e:
            raise WorkflowError(e.errors, spec.get("id"))
    steps = spec.get("steps")
    if not isinstance(steps, list):
        return spec
    for st in steps:
        if not isinstance(st, dict):
            continue
        kind = st.get("kind")
        sid = st.get("id")
        if isinstance(sid, str) and not st.get("label"):
            st["label"] = ps.format_key(sid)
        if kind in ("extract", "agent"):
            st.setdefault("k", 1)
            st.setdefault("vote", None)
            inp = st.get("input")
            st["input"] = {"previous": []} if not isinstance(inp, dict) else {"previous": list(inp.get("previous") or [])}
            st.setdefault("params", {})
            if kind == "agent":
                st["source"] = "supplied"
        if kind == "pass":
            st.setdefault("k", 1)
            st.setdefault("reads", [])
            st.setdefault("params", {})
        if kind == "check":
            st.setdefault("on_fail", "note")
            st.setdefault("severity", "error")
            st.setdefault("params", {})
            if isinstance(st.get("over"), str):
                st["over"] = [st["over"]]
        if kind == "derive":
            inp = st.get("input")
            st["input"] = {"unit": "entries", **(inp if isinstance(inp, dict) else {})}
            for call in st.get("calls") or []:
                if isinstance(call, dict) and isinstance(call.get("prompt"), dict) and call["prompt"].get("file") and not call["prompt"].get("text"):
                    if base_dir is None:
                        raise WorkflowError([f"step {sid!r}: prompt.file {call['prompt']['file']!r} needs a base directory"], spec.get("id"))
                    try:
                        call["prompt"]["text"] = ps._read_sibling(call["prompt"].pop("file"), base_dir, what="prompt.file")
                    except ps.SpecError as e:
                        raise WorkflowError(e.errors, spec.get("id"))
        if kind == "review":
            # a review step says its declaration on the step itself; a nested `review` is hoisted
            rv = st.pop("review", None)
            if isinstance(rv, dict):
                for k, v in rv.items():
                    st.setdefault(k, v)
        elif (kind in UPDATE_KINDS and kind in REVIEWED_KINDS) or (kind in REVIEWED_KINDS and isinstance(st.get("review"), dict)):
            # the update steps' drafts keep the attribute; on an extract, agent or derive step it is
            # the form before review steps stood on their own, read as one below
            rv = st.get("review")
            rv = dict(rv) if isinstance(rv, dict) else {}
            rv.setdefault("mode", "all" if kind in DRAFT_KINDS else "none")
            rv.setdefault("unit", "entry")
            rv.setdefault("blocks", "release")
            rv.setdefault("oversample_flagged", False)
            st["review"] = rv
    _shim_review_steps(steps)
    by_id = {st.get("id"): st for st in steps if isinstance(st, dict)}
    for st in steps:
        if isinstance(st, dict) and st.get("kind") == "review":
            over = st.get("over") if isinstance(st.get("over"), list) else []
            st.setdefault("mode", "all")
            st.setdefault("unit", "entry" if any((by_id.get(o) or {}).get("kind") in DRAFT_KINDS for o in over if isinstance(o, str)) else "paper")
            st.setdefault("blocks", "release")
            st.setdefault("oversample_flagged", False)
    if not any(isinstance(st, dict) and st.get("kind") == "release" for st in steps):
        steps.append({"id": "release", "kind": "release", "label": "Release",
                      "gate": [st["id"] for st in steps if isinstance(st, dict) and st.get("kind") in GATE_KINDS and isinstance(st.get("id"), str)]})
    return spec


def _shim_review_steps(steps: list) -> None:
    """Review steps stand on their own. The form before — a ``review`` attribute on an extract,
    agent or derive step — is read as a review step right after that step (``<id>_review``,
    ``over`` naming it, the attribute's keys flat on it), the attribute goes (a draft step
    without a review step over it is committed at once; an extract step is never reviewed in
    place), and the release gate names the new step beside the data step. Idempotent."""
    by_id = {st.get("id"): st for st in steps if isinstance(st, dict)}
    out: list = []
    inserted: dict[str, str] = {}                      # data step → the review step made for it
    for st in steps:
        out.append(st)
        if not isinstance(st, dict) or st.get("kind") not in ("extract", "agent", "derive"):
            continue
        rv = st.pop("review", None)
        sid = st.get("id")
        if not isinstance(rv, dict) or rv.get("mode", "none") == "none" or not isinstance(sid, str):
            continue
        rid, k = f"{sid}_review", 2
        while rid in by_id:
            rid, k = f"{sid}_review{k}", k + 1
        step = {"id": rid, "kind": "review", "label": f"Review of {st.get('label') or ps.format_key(sid)}", "over": [sid],
                **{kk: v for kk, v in rv.items() if kk != "over"}}
        out.append(step); by_id[rid] = step; inserted[sid] = rid
    if inserted:
        for st in out:
            if isinstance(st, dict) and st.get("kind") == "release" and isinstance(st.get("gate"), list):
                gate: list = []
                for g in st["gate"]:
                    gate.append(g)
                    if isinstance(g, str) and g in inserted and inserted[g] not in st["gate"]:
                        gate.append(inserted[g])
                st["gate"] = gate
    steps[:] = out


# ── validation ────────────────────────────────────────────────────────────────

def validate(spec: dict, *, resolver: Callable[[str], dict | None] | None = None,
             pin_known: Callable[[str], bool] | None = None) -> tuple[list[str], list[str]]:
    """Every problem at once, path-prefixed. ``resolver(base_id)`` gives a preset's spec (the
    bundled presets when None); ``pin_known(schema_id)`` says whether a pinned contract is the
    live one or an existing schema row — only a caller with a database can tell, so without it
    only the pin's shape is checked."""
    errs: list[str] = []
    warns: list[str] = []
    E, W = errs.append, warns.append
    if resolver is None:
        from . import presets
        resolver = presets.load_all().get

    def unknown(obj: dict, kind: str, at: str) -> None:
        for k in obj:
            if k not in _ALLOWED_KEYS[kind]:
                E(f"{at}: unknown key {k!r}")

    if not isinstance(spec, dict):
        return ["workflow must be a JSON object"], []
    unknown(spec, "root", "$")
    if spec.get("format") != FORMAT:
        E(f"$.format must be {FORMAT}")
    wid = spec.get("id")
    if not isinstance(wid, str) or not ps.ID_RE.match(wid):
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
        if not isinstance(meta.get("hidden", False), bool):
            E("$.meta.hidden must be a boolean")
        brands = meta.get("brands", [])
        if not (isinstance(brands, list) and all(isinstance(b, str) and b for b in brands)):
            E("$.meta.brands must be a list of brand ids")
        ps._validate_card(spec, meta, E, W) # authors, keywords, licence, related, card text
    card = spec.get("card")
    if card is not None:
        if not isinstance(card, dict):
            E("$.card must be an object {file | text}")
        else:
            unknown(card, "card", "$.card")
            if not (isinstance(card.get("text"), str) and card["text"].strip()):
                E("$.card.text is empty (card.file is inlined when the workflow is loaded)")
    ds = spec.get("dataset")
    if not isinstance(ds, dict):
        E("$.dataset must be an object {unit, key}")
    else:
        unknown(ds, "dataset", "$.dataset")
        if ds.get("unit") != "paper":
            E("$.dataset.unit must be 'paper'")
        if ds.get("key") not in ("pdf_sha256", "doi"):
            E("$.dataset.key must be 'pdf_sha256' or 'doi'")

    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        E("$.steps must be a non-empty list")
        return errs, warns
    ids: list[str] = []
    by_id: dict[str, dict] = {}
    presets_of: dict[str, dict | None] = {}        # step id → the pinned preset's spec (extract steps)
    for i, st in enumerate(steps):
        at = f"$.steps[{i}]"
        if not isinstance(st, dict):
            E(f"{at} must be an object"); continue
        sid, kind = st.get("id"), st.get("kind")
        if not isinstance(sid, str) or not ps.NAME_RE.match(sid):
            E(f"{at}.id must match ^[A-Za-z_][A-Za-z0-9_]*$"); sid = None
        elif sid in by_id:
            E(f"{at}.id {sid!r} is declared twice")
        if kind not in KINDS:
            E(f"{at}.kind must be one of {', '.join(KINDS)}"); continue
        for k in st:
            if k not in _ALLOWED_KEYS["step"] and k not in _ALLOWED_KEYS["review_step" if kind == "review" else kind]:
                E(f"{at}: unknown key {k!r} for a {kind} step")
        if kind == "release" and i != len(steps) - 1:
            E(f"{at}: the release step must be the last step")
        if kind in ("check", "crosscheck", "release", "loop") and st.get("review") is not None:
            E(f"{at}: a {kind} step declares no review" + (" (a check declares on_fail)" if kind == "check" else ""))
        earlier = list(ids)
        if sid:
            ids.append(sid); by_id[sid] = st
        if kind in ("extract", "agent"):
            pspec = _validate_extract(st, at, E, W, resolver=resolver, pin_known=pin_known, earlier=earlier, by_id=by_id)
            if sid:
                presets_of[sid] = pspec
        elif kind == "check":
            _validate_check(st, at, E, W, earlier=earlier, by_id=by_id, presets_of=presets_of)
        elif kind == "derive":
            _validate_derive(st, at, E, W, earlier=earlier, by_id=by_id, presets_of=presets_of)
        elif kind in UPDATE_KINDS:
            ps._validate_update_step({"type": kind, **{k: st[k] for k in ("unit", "column", "group", "keyed_by") if k in st}}, E, at)
        elif kind == "loop":
            _validate_loop(st, at, E, W, earlier=earlier, by_id=by_id)
        elif kind == "review":
            _validate_review_step(st, at, E, W, earlier=earlier, by_id=by_id, presets_of=presets_of)
        elif kind == "pass":
            _validate_pass(st, at, E, W, earlier=earlier, by_id=by_id)
        elif kind == "release":
            gate = st.get("gate")
            if not isinstance(gate, list) or not all(isinstance(g, str) for g in gate):
                E(f"{at}.gate must list step ids")
            else:
                for g in gate:
                    if g not in by_id or by_id[g].get("kind") not in GATE_KINDS:
                        E(f"{at}.gate: {g!r} is not an earlier extract, derive or review step")
        if kind in REVIEWED_KINDS and kind != "review" and st.get("review") is not None:      # the drafts of the update steps
            _validate_review(st.get("review"), st, presets_of.get(sid) if sid else None, f"{at}.review", E, W)
    rel = [st for st in steps if isinstance(st, dict) and st.get("kind") == "release"]
    if len(rel) != 1:
        E("$.steps must end with exactly one release step")
    return errs, warns


def _validate_extract(st, at, E, W, *, resolver, pin_known, earlier, by_id) -> dict | None:
    pin = st.get("preset")
    m = PIN_RE.match(pin) if isinstance(pin, str) else None
    pspec = None
    if not m:
        E(f"{at}.preset must pin a data contract: <preset id>@<8 hex> (the schema id)")
    else:
        pspec = resolver(m.group(1)) if resolver else None
        if pspec is None:
            E(f"{at}.preset: unknown preset {m.group(1)!r}")
        else:
            live = ps.schema_id(pspec)
            if pin_known is not None and live != pin and not pin_known(pin):
                E(f"{at}.preset: unknown contract {pin!r}; the live contract is {live!r}")
            elif live != pin:
                W(f"{at}.preset is pinned to an older contract than the live {live!r}")
            mode = (pspec.get("meta") or {}).get("mode")
            src = st.get("source")
            if src is None:
                st["source"] = "supplied" if mode == "import" else "live"
            elif src not in SOURCES:
                E(f"{at}.source must be 'live' or 'supplied'")
            elif src == "live" and mode == "import":
                E(f"{at}.source: {m.group(1)!r} has no prompt (an import-mode preset) — its documents are supplied")
    k = st.get("k", 1)
    if not isinstance(k, int) or not 1 <= k <= MAX_K:
        E(f"{at}.k must be an integer from 1 to {MAX_K}")
    vote = st.get("vote")
    if vote is not None:
        if not isinstance(vote, dict):
            E(f"{at}.vote must be an object {{identity, fields}}")
        else:
            for kk in vote:
                if kk not in _ALLOWED_KEYS["vote"]:
                    E(f"{at}.vote: unknown key {kk!r}")
            ident = vote.get("identity")
            scalars = _scalar_entry_fields(pspec)
            if not isinstance(ident, list) or not ident or any(not isinstance(x, str) for x in ident):
                E(f"{at}.vote.identity must list the entry fields that make two entries the same")
            elif pspec is not None and any(x not in scalars for x in ident):
                E(f"{at}.vote.identity: every field must be a scalar entry field of the preset")
    if isinstance(k, int) and k > 1 and not vote:
        E(f"{at}: k > 1 needs vote.identity (how the runs are merged)")
    inp = st.get("input") or {}
    followup = bool(pspec) and ps.is_followup(pspec)
    if not isinstance(inp, dict) or set(inp) - _ALLOWED_KEYS["input"]:
        E(f"{at}.input must be {{previous: [step ids]}}")
    else:
        prev = inp.get("previous") or []
        if not isinstance(prev, list) or any(not isinstance(p, str) for p in prev):
            E(f"{at}.input.previous must list step ids")
        else:
            if len(prev) > 1:
                E(f"{at}.input.previous: one earlier step in this release (the runs r1…rk are unambiguous only then)")
            for p in prev:
                ps_ = by_id.get(p, {})
                if p not in earlier or ps_.get("kind") != "extract":
                    E(f"{at}.input.previous: {p!r} is not an earlier extract step")
                elif ps_.get("source") == "supplied":
                    E(f"{at}.input.previous: {p!r} is a supplied step; a follow-up reads live runs")
                elif (ps_.get("input") or {}).get("previous"):
                    E(f"{at}.input.previous: {p!r} reads earlier outputs itself; one follow-up per pipeline in this release")
            if prev and st.get("source") == "supplied":
                E(f"{at}: a step reading earlier outputs runs live (source: supplied makes no sense)")
            if prev and pspec is not None and not followup:
                E(f"{at}.input.previous: the preset's prompt must read ${{previous}} (a follow-up preset)")
            if not prev and followup:
                E(f"{at}: the preset reads ${{previous}} — declare input.previous (the step whose runs it reads)")
    if not isinstance(st.get("params", {}), dict):
        E(f"{at}.params must be an object")
    role = st.get("dataset")
    if role is not None and not (isinstance(role, str) and re.match(r"^[a-z][a-z0-9_]{0,31}$", role)):
        E(f"{at}.dataset must be a short lowercase role name (tables, claims, …)")
    return pspec


_PASS_PARAM_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "list": list, "object": dict}


def _validate_pass(st, at, E, W, *, earlier, by_id) -> None:
    """A pass step: a registered pass at its current pin, the earlier pass steps it reads (one
    per read the pass declares), ``k`` with a matcher of the same chain, ``over`` only for a
    pass that reads the record, typed params."""
    from . import chains
    pin = st.get("pass")
    entry = None
    if not isinstance(pin, str) or not PASS_PIN_RE.match(pin):
        E(f"{at}.pass must be '<chain>.<name>@<sha8>' (the pass at its prompt sha)")
    else:
        entry = chains.get(pin)
        if entry is None or entry.get("kind") != "pass":
            E(f"{at}.pass: unknown pass {pin.partition('@')[0]!r}"); entry = None
        elif not chains.pin_ok(pin):
            E(f"{at}.pass: {pin!r} is not the pass's current pin ({chains.pin(pin)}); re-pin after a prompt change")
    reads = st.get("reads", [])
    if not isinstance(reads, list) or any(not isinstance(r, str) for r in reads):
        E(f"{at}.reads must list earlier pass steps"); reads = []
    names: dict[str, str] = {}
    for r in reads:
        src = by_id.get(r) if r in earlier else None
        if src is None or src.get("kind") != "pass":
            E(f"{at}.reads: {r!r} is not an earlier pass step")
        else:
            names[str(src.get("pass") or "").partition("@")[0].partition(".")[2]] = r
    if entry is not None:
        for need in entry.get("reads") or []:
            if need not in names:
                E(f"{at}.reads: {entry['id']} reads {need!r} — name the step that runs it")
        for n in names:
            if n not in (entry.get("reads") or []):
                E(f"{at}.reads: {entry['id']} does not read {n!r}")
    k = st.get("k", 1)
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_K:
        E(f"{at}.k must be an integer from 1 to {MAX_K}")
    elif k > 1:
        if entry is not None and not entry.get("k_ok"):
            E(f"{at}.k: {entry['id']} runs once per paper")
        m = st.get("match")
        me = chains.get(m) if isinstance(m, str) else None
        if me is None or me.get("kind") != "match" or (entry is not None and me.get("chain") != entry.get("chain")):
            E(f"{at}.match must name a matcher of the same chain when k > 1")
    elif st.get("match") is not None:
        E(f"{at}.match only applies with k > 1")
    over = st.get("over")
    if over is not None:
        if entry is not None and not entry.get("post_record"):
            E(f"{at}.over: {entry['id']} does not read the record")
        elif not (isinstance(over, str) and over in earlier and (by_id.get(over) or {}).get("kind") in DATA_KINDS):
            E(f"{at}.over must name an earlier data step (the record the pass reads)")
    elif entry is not None and entry.get("post_record"):
        E(f"{at}: {entry['id']} reads the record — declare over")
    params = st.get("params", {})
    if not isinstance(params, dict):
        E(f"{at}.params must be an object")
    elif entry is not None:
        for name, v in params.items():
            decl = (entry.get("params") or {}).get(name)
            want = _PASS_PARAM_TYPES.get((decl or {}).get("type"))
            if decl is None:
                E(f"{at}.params: {entry['id']} takes no {name!r}")
            elif want is not None and (not isinstance(v, want) or (want is not bool and isinstance(v, bool) and want is not object)):
                E(f"{at}.params.{name} must be a {decl.get('type')}")
    if st.get("model") is not None and not isinstance(st.get("model"), str):
        E(f"{at}.model must be a string")


def _scalar_entry_fields(pspec: dict | None) -> set[str]:
    if not pspec:
        return set()
    return {f.get("name") for f in ((pspec.get("entries") or {}).get("fields") or [])
            if isinstance(f, dict) and f.get("type", "string") in ps.SCALAR_TYPES}


def _paper_fields(pspec: dict | None) -> set[str]:
    out = {name for name, _t, _h in ps.IDENTITY_FIELDS} if hasattr(ps, "IDENTITY_FIELDS") else set()
    if pspec:
        out |= {f.get("name") for f in ((pspec.get("paper") or {}).get("fields") or []) if isinstance(f, dict)}
    return out


def _validate_check(st, at, E, W, *, earlier, by_id, presets_of) -> None:
    from . import checks
    over = st.get("over")
    if not isinstance(over, list) or not over or any(not isinstance(o, str) for o in over):
        E(f"{at}.over must list the step(s) the check reads"); over = []
    for o in over:
        if o not in earlier or by_id.get(o, {}).get("kind") not in DATA_KINDS:
            E(f"{at}.over: {o!r} is not an earlier extract or derive step")
    if st.get("on_fail") not in checks.ON_FAIL:
        E(f"{at}.on_fail must be one of {', '.join(checks.ON_FAIL)}")
    if st.get("severity") not in checks.SEVERITIES:
        E(f"{at}.severity must be one of {', '.join(checks.SEVERITIES)}")
    checks.validate_step(st, at, E, W, presets_of={o: presets_of.get(o) for o in over})


def _validate_derive(st, at, E, W, *, earlier, by_id, presets_of) -> None:
    from . import derived
    over = st.get("over")
    if not isinstance(over, str) or over not in earlier or by_id.get(over, {}).get("kind") not in ("extract", "agent"):
        E(f"{at}.over must name an earlier extract step")
    ident = st.get("identity")
    if not isinstance(ident, list) or not ident or any(not isinstance(x, str) for x in ident):
        E(f"{at}.identity must list the output fields that identify a row")
    calls = st.get("calls")
    if not isinstance(calls, list) or not calls:
        E(f"{at}.calls must be a non-empty list of calls")
    else:
        for j, c in enumerate(calls):
            cat = f"{at}.calls[{j}]"
            if not isinstance(c, dict):
                E(f"{cat} must be an object"); continue
            for k in c:
                if k not in _ALLOWED_KEYS["call"]:
                    E(f"{cat}: unknown key {k!r}")
            if c.get("kind") not in ("llm", "transform"):
                E(f"{cat}.kind must be 'llm' or 'transform'")
            if c.get("kind") == "llm" and not (isinstance(c.get("prompt"), dict) and (c["prompt"].get("text") or "").strip()):
                E(f"{cat}.prompt must carry text (or a file next to the workflow)")
            if c.get("kind") == "transform" and c.get("op") != "group":
                E(f"{cat}.op must be 'group'")
    out = st.get("output")
    if not isinstance(out, dict) or not isinstance(out.get("fields"), list) or not out["fields"]:
        E(f"{at}.output.fields must list the derived unit's fields")
    else:
        names = set()
        for j, f in enumerate(out["fields"]):
            if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not ps.NAME_RE.match(f["name"]):
                E(f"{at}.output.fields[{j}].name must match ^[A-Za-z_][A-Za-z0-9_]*$"); continue
            if f["name"] in ("_id", "sources", "_status"):
                E(f"{at}.output.fields[{j}].name {f['name']!r} is engine-owned")
            if f.get("type", "string") not in ps.SCALAR_TYPES:
                E(f"{at}.output.fields[{j}].type must be a scalar type")
            names.add(f["name"])
        if isinstance(ident, list) and any(x not in names for x in ident if isinstance(x, str)):
            E(f"{at}.identity must name output fields")
    if isinstance(over, str) and over in earlier:
        vocab_cols = [by_id[x].get("column") for x in earlier if by_id.get(x, {}).get("kind") == "vocabulary" and by_id[x].get("column")]
        derived.validate(st, at, E, W, pspec=presets_of.get(over), vocab_columns=vocab_cols)


def _validate_loop(st, at, E, W, *, earlier, by_id) -> None:
    inner = st.get("steps")
    if not isinstance(inner, list) or not inner or any(not isinstance(x, str) for x in inner):
        E(f"{at}.steps must list the earlier steps the loop repeats"); inner = []
    for x in inner:
        if x not in earlier or by_id.get(x, {}).get("kind") not in ("extract", "check"):
            E(f"{at}.steps: {x!r} is not an earlier extract or check step")
        elif by_id[x].get("kind") == "extract" and by_id[x].get("source") != "live":
            E(f"{at}.steps: {x!r} is a supplied step; a loop re-runs live extractions")
    if inner and not any(by_id.get(x, {}).get("kind") == "extract" for x in inner):
        E(f"{at}.steps must include the extract step the loop re-runs")
    until = st.get("until")
    if not isinstance(until, dict) or until.get("check") not in inner or by_id.get(until.get("check"), {}).get("kind") != "check":
        E(f"{at}.until.check must name a check step inside the loop")
    mi = st.get("max_iterations", 1)
    if not isinstance(mi, int) or not 1 <= mi <= MAX_LOOP:
        E(f"{at}.max_iterations must be an integer from 1 to {MAX_LOOP}")


def _validate_review(rv, st, pspec, at, E, W) -> None:
    if not isinstance(rv, dict):
        E(f"{at} must be an object {{mode, unit, n | share, seed, stratify, blocks}}"); return
    for k in rv:
        if k not in _ALLOWED_KEYS["review"]:
            E(f"{at}: unknown key {k!r}")
    mode = rv.get("mode")
    if mode not in REVIEW_MODES:
        E(f"{at}.mode must be one of {', '.join(REVIEW_MODES)}"); return
    if rv.get("unit") not in REVIEW_UNITS:
        E(f"{at}.unit must be 'entry' or 'paper'")
    if rv.get("blocks") not in BLOCKS:
        E(f"{at}.blocks must be 'next' or 'release'")
    if not isinstance(rv.get("oversample_flagged", False), bool):
        E(f"{at}.oversample_flagged must be a boolean")
    if mode == "sample" and st.get("kind") in DRAFT_KINDS:
        E(f"{at}: a draft is reviewed whole — mode 'all' or 'none'")
    coders = rv.get("coders")                               # each paper reviewed by N of the dataset's coders, independently
    if coders is not None:
        if not (isinstance(coders, int) and not isinstance(coders, bool) and coders >= 1):
            E(f"{at}.coders must be an integer ≥ 1 (how many coders each paper gets)")
        elif mode == "none":
            E(f"{at}.coders needs a review (mode 'all' or 'sample')")
        elif st.get("kind") in DRAFT_KINDS:
            E(f"{at}.coders: a draft is committed by the owner, not coded")
    if mode != "sample":
        for k in ("n", "share", "seed", "stratify"):
            if rv.get(k) is not None:
                W(f"{at}.{k} only applies to mode 'sample'")
        return
    has_n, has_share = rv.get("n") is not None, rv.get("share") is not None
    if has_n == has_share:
        E(f"{at}: a sample names exactly one of n (a count) or share (a fraction)")
    if has_n and not (isinstance(rv["n"], int) and rv["n"] >= 1):
        E(f"{at}.n must be an integer ≥ 1")
    if has_share and not (isinstance(rv["share"], (int, float)) and 0 < rv["share"] <= 1):
        E(f"{at}.share must be a fraction in (0, 1]")
    seed = rv.get("seed")
    if seed is not None and not isinstance(seed, (int, str)):
        E(f"{at}.seed must be an integer or a string")
    strat = rv.get("stratify")
    if strat is not None:
        if not isinstance(strat, list) or any(not isinstance(x, str) for x in strat):
            E(f"{at}.stratify must list field names")
        else:
            if len(strat) > 2:
                W(f"{at}.stratify: more than two strata fields make small strata")
            allowed = _paper_fields(pspec) if rv.get("unit") == "paper" else _scalar_entry_fields(pspec)
            if pspec is not None:
                for x in strat:
                    if x not in allowed:
                        E(f"{at}.stratify: {x!r} is not a {'paper-level' if rv.get('unit') == 'paper' else 'scalar entry'} field of the step's preset")


def _validate_review_step(st, at, E, W, *, earlier, by_id, presets_of) -> None:
    """A review step: what it reads (``over``: earlier extract steps, or one draft step), what
    it asks of each side (``asks``), and its declaration flat on the step — mode, unit, size,
    seed, strata, coders, what holds."""
    over = st.get("over")
    if not isinstance(over, list) or not over or any(not isinstance(o, str) for o in over):
        E(f"{at}.over must list the steps the review reads (extract steps, or one derive step)"); over = []
    kinds = {o: (by_id.get(o) or {}).get("kind") for o in over}
    for o in over:
        if o not in earlier or kinds[o] not in ("extract", "agent", "derive"):
            E(f"{at}.over: {o!r} is not an earlier extract step")
    draft = any(kinds[o] in DRAFT_KINDS for o in over)
    if draft and len(over) > 1:
        E(f"{at}.over: a draft is reviewed on its own (one derive step)")
    # asks: per side, the entry fields the review asks the reviewer to verify beside a sheet's
    # cells (what a validation protocol checks); a side without it shows every field
    asks = st.get("asks")
    if asks is not None:
        if not isinstance(asks, dict):
            E(f"{at}.asks must map extract steps of `over` to lists of entry fields")
        else:
            for o, fields in asks.items():
                if o not in over:
                    E(f"{at}.asks: {o!r} is not one of the steps the review reads")
                elif not isinstance(fields, list) or any(not isinstance(f, str) for f in fields):
                    E(f"{at}.asks.{o} must list entry fields")
                elif presets_of.get(o):
                    ent = presets_of[o].get("entries") or {}
                    names = {f.get("name") for f in ent.get("fields") or [] if isinstance(f, dict)} | {c.get("key") for c in ent.get("children") or [] if isinstance(c, dict)}
                    for f in fields:
                        if f not in names:
                            E(f"{at}.asks.{o}: {f!r} is not an entry field of {by_id[o].get('preset')}")
    mode = st.get("mode")
    if mode not in REVIEW_MODES:
        E(f"{at}.mode must be one of {', '.join(REVIEW_MODES)}"); return
    if mode == "none":
        E(f"{at}.mode: a review step reviews — 'all' or 'sample' (leave the step out for no review)"); return
    unit = st.get("unit")
    if unit not in REVIEW_UNITS:
        E(f"{at}.unit must be 'entry', 'paper' or 'row'")
    elif draft and unit != "entry":
        E(f"{at}.unit: a draft is reviewed as entries")
    # unit row: the units are the rows of a paper table of the read presets (one exhibit of each
    # paper, say); a sample draws per_paper of them per paper; where filters the rows
    cols: dict[str, dict] = {}
    if unit == "row":
        table = st.get("table")
        if not isinstance(table, str):
            E(f"{at}.table must name a paper table of the preset — the rows unit 'row' reviews")
        else:
            for o in over:
                pspec = presets_of.get(o)
                if pspec is None:
                    continue
                t = next((f for f in (pspec.get("paper") or {}).get("fields") or [] if isinstance(f, dict) and f.get("name") == table), None)
                if t is None or t.get("type") != "table":
                    E(f"{at}.table: {table!r} is not a paper table of the preset of {o!r}")
                else:
                    cols = {c.get("name"): c for c in t.get("columns") or [] if isinstance(c, dict) and c.get("name")}
        pp = st.get("per_paper")
        if st.get("mode") == "sample":
            if not (isinstance(pp, int) and not isinstance(pp, bool) and pp >= 1):
                E(f"{at}.per_paper must be an integer ≥ 1 — how many rows a sample draws per paper")
            for k in ("n", "share"):
                if st.get(k) is not None:
                    E(f"{at}.{k} does not apply to a sample of rows: per_paper says how many")
        elif pp is not None:
            W(f"{at}.per_paper only applies to mode 'sample'")
    else:
        for k in ("table", "per_paper"):
            if st.get(k) is not None:
                E(f"{at}.{k} only applies to unit 'row'")
    where = st.get("where")                                   # rows (unit row) or papers (the others) the review keeps
    if where is not None:
        from . import templates
        pspec0 = next((presets_of.get(o) for o in over if presets_of.get(o) is not None), None)
        fields = cols if unit == "row" else (templates.unit_fields(pspec0, "paper", None) if pspec0 else {})
        templates._validate_where(where, fields, pspec0, f"{at}.where", E)   # noqa: SLF001 - one grammar for steps, blocks and the sampler
    if st.get("blocks") not in BLOCKS:
        E(f"{at}.blocks must be 'next' or 'release'")
    if not isinstance(st.get("oversample_flagged", False), bool):
        E(f"{at}.oversample_flagged must be a boolean")
    if not isinstance(st.get("new_only", True), bool):
        E(f"{at}.new_only must be a boolean (the run's new papers only, or every paper of the sides)")
    if st.get("template") is not None:                       # a review template: how the coder checks, what they answer
        if draft:
            E(f"{at}.template: a draft is reviewed as a change, not by a template")
        else:
            from . import templates
            templates.validate_for_step(st, by_id, at, E)
    coders = st.get("coders")                               # each paper reviewed by N of the coders, independently
    if coders is not None:
        if not (isinstance(coders, int) and not isinstance(coders, bool) and coders >= 1):
            E(f"{at}.coders must be an integer ≥ 1 (how many coders each paper gets)")
        elif draft:
            E(f"{at}.coders: a draft is committed by the owner, not coded")
    if mode != "sample":
        for k in ("n", "share", "seed", "stratify"):
            if st.get(k) is not None:
                W(f"{at}.{k} only applies to mode 'sample'")
        return
    if draft:
        E(f"{at}: a draft is reviewed whole — mode 'all'")
    has_n, has_share = st.get("n") is not None, st.get("share") is not None
    if has_n == has_share and unit != "row":
        E(f"{at}: a sample names exactly one of n (a count) or share (a fraction)")
    if has_n and not (isinstance(st["n"], int) and st["n"] >= 1):
        E(f"{at}.n must be an integer ≥ 1")
    if has_share and not (isinstance(st["share"], (int, float)) and 0 < st["share"] <= 1):
        E(f"{at}.share must be a fraction in (0, 1]")
    seed = st.get("seed")
    if seed is not None and not isinstance(seed, (int, str)):
        E(f"{at}.seed must be an integer or a string")
    strat = st.get("stratify")
    if strat is not None:
        if not isinstance(strat, list) or any(not isinstance(x, str) for x in strat):
            E(f"{at}.stratify must list field names")
        else:
            if len(strat) > 2:
                W(f"{at}.stratify: more than two strata fields make small strata")
            for o in over:
                pspec = presets_of.get(o)
                if pspec is None:
                    continue
                allowed = {n for n, c in cols.items() if c.get("type") != "table"} if unit == "row" else _paper_fields(pspec) if unit == "paper" else _scalar_entry_fields(pspec)
                for x in strat:
                    if x not in allowed:
                        E(f"{at}.stratify: {x!r} is not a {'column of the table' if unit == 'row' else 'paper-level' if unit == 'paper' else 'scalar entry'} field of the preset of {o!r}")


# ── identity ──────────────────────────────────────────────────────────────────

_WORDING = ("label", "help", "asks")                         # asks: what the review shows, not what it promises


_NONE_REVIEW = {"mode": "none", "unit": "entry", "blocks": "release", "oversample_flagged": False}


def _review_declared(key: str, value) -> bool:
    """Whether a review step's key enters the contract: the defaults never do."""
    if key == "new_only":
        return value is False
    return value is not None


def unshim(spec: dict) -> dict:
    """The document in the form before review steps stood on their own, for the contract hash:
    a review step over one extract, agent or derive step — saying nothing only a step can say —
    is folded back into that step's ``review`` attribute and the release gate repointed; every
    other review step keeps its place with its declaration nested; a data step no review step
    covers carries the ``none`` attribute. So a workflow keeps its id across the two spellings."""
    doc = json.loads(json.dumps(spec))
    steps = doc.get("steps") if isinstance(doc.get("steps"), list) else []
    by_id = {st["id"]: st for st in steps if isinstance(st, dict) and isinstance(st.get("id"), str)}
    folded: dict[str, str] = {}
    out: list = []
    for st in steps:
        if not isinstance(st, dict) or st.get("kind") != "review":
            out.append(st); continue
        nested = {k: st[k] for k in REVIEW_KEYS if k in st}
        extra = {k: st[k] for k in REVIEW_STEP_KEYS if k not in REVIEW_KEYS and k in st and _review_declared(k, st[k])}
        over = st.get("over") if isinstance(st.get("over"), list) else []
        target = by_id.get(over[0]) if len(over) == 1 and isinstance(over[0], str) else None
        if target is not None and target.get("kind") in ("extract", "agent", "derive") and "review" not in target and not extra:
            target["review"] = nested
            folded[st["id"]] = over[0]
            continue
        rest = {k: v for k, v in st.items() if k not in REVIEW_STEP_KEYS}
        rest["review"] = nested
        rest.update(extra)
        out.append(rest)
    for st in out:
        if isinstance(st, dict) and st.get("kind") in ("extract", "agent", "derive") and "review" not in st:
            st["review"] = dict(_NONE_REVIEW)
        if isinstance(st, dict) and st.get("kind") == "release" and isinstance(st.get("gate"), list):
            gate: list = []
            for g in st["gate"]:
                g2 = folded.get(g, g) if isinstance(g, str) else g
                if g2 not in gate:
                    gate.append(g2)
            st["gate"] = gate
    doc["steps"] = out
    return doc


def workflow_contract(spec: dict) -> dict:
    """What a workflow version promises: the dataset unit, the steps with their pins, checks,
    calls and REVIEW declarations — not labels, help texts, nor a derive prompt's wording. A
    review step over one data step is hashed as that step's attribute (``unshim``)."""
    def strip_step(st: dict) -> dict:
        out = {k: v for k, v in st.items() if k not in _WORDING}
        if st.get("kind") == "pass":
            out.pop("model", None)                      # the run's choice, not the workflow's promise
        if st.get("kind") == "derive":
            out["calls"] = [{k: v for k, v in c.items() if k != "prompt"} for c in st.get("calls") or [] if isinstance(c, dict)]
        return out
    doc = unshim(spec)
    return {"dataset": doc.get("dataset") or {},
            "steps": [strip_step(st) for st in doc.get("steps") or [] if isinstance(st, dict)]}


def content_hash(spec: dict) -> str:
    return hashlib.sha256(canonical_json(workflow_contract(spec)).encode("utf-8")).hexdigest()


def workflow_id(spec: dict) -> str:
    return f"{spec['id']}@{content_hash(spec)[:8]}"


def spec_sha(spec: dict) -> str:
    """The whole normalised document (prompts and card inlined): "changed since the release?"."""
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


def has_card(spec: dict) -> bool:
    return bool(((spec.get("card") or {}).get("text") or "").strip())


def label(spec: dict) -> str:
    return f"{spec['meta']['title']} v{spec.get('version', 1)} ({content_hash(spec)[:8]})"


# ── reading a workflow ────────────────────────────────────────────────────────

def steps_by_id(spec: dict) -> dict[str, dict]:
    return {st["id"]: st for st in spec.get("steps") or [] if isinstance(st, dict) and isinstance(st.get("id"), str)}


def release_step(spec: dict) -> dict | None:
    return next((st for st in spec.get("steps") or [] if isinstance(st, dict) and st.get("kind") == "release"), None)


def gate_steps(spec: dict) -> list[str]:
    rel = release_step(spec)
    return list((rel or {}).get("gate") or [])


def roles(spec: dict) -> dict[str, dict]:
    """{role: {step, preset}} for the extract steps whose documents enter a dataset of their own
    (a step read by a later step's input.previous runs inside that step's pipeline and has none)."""
    consumed = {p for st in spec.get("steps") or [] if isinstance(st, dict) and st.get("kind") == "extract"
                for p in ((st.get("input") or {}).get("previous") or [])}
    out: dict[str, dict] = {}
    for st in spec.get("steps") or []:
        if isinstance(st, dict) and st.get("kind") in ("extract", "agent") and st.get("id") not in consumed and st.get("dataset"):
            out[st["dataset"]] = {"step": st["id"], "preset": st.get("preset"), "label": st.get("label") or st["id"]}
    return out


def pins(spec: dict) -> list[tuple[str, str]]:
    """(step id, pinned schema id) of every extract step."""
    return [(st["id"], st["preset"]) for st in spec.get("steps") or []
            if isinstance(st, dict) and st.get("kind") in ("extract", "agent") and isinstance(st.get("preset"), str)]


def preset_base(pin: str) -> str:
    return pin.partition("@")[0]


# ── the implicit workflow of a preset ─────────────────────────────────────────

def implicit(pspec: dict, schema_id: str) -> dict:
    """A dataset without a declared workflow runs its preset as a one-step workflow, followed
    by the preset's own ``meta.update`` steps and a release gated on the extraction — so the
    planner, the Update panel and the release gate have one code path, and ``meta.update``
    keeps working unchanged."""
    meta = pspec.get("meta") or {}
    steps: list[dict] = [{"id": "extract", "kind": "extract", "preset": schema_id,
                          "source": "supplied" if meta.get("mode") == "import" else "live",
                          "label": meta.get("title") or ps.format_key(pspec.get("id") or "extract")}]
    for st in meta.get("update") or []:
        if not isinstance(st, dict) or st.get("type") not in UPDATE_KINDS:
            continue
        kind = st["type"]
        suffix = st.get("column") if kind == "vocabulary" else st.get("group") if kind == "pages" else None
        sid = f"{kind}_{re.sub(r'[^A-Za-z0-9_]+', '_', str(suffix))}" if suffix else kind
        item = {"id": sid, "kind": kind, **{k: st[k] for k in ("unit", "column", "group", "keyed_by", "label") if k in st}}
        steps.append(item)
    steps.append({"id": "release", "kind": "release", "gate": ["extract"]})
    doc = {"format": FORMAT, "id": pspec.get("id") or "preset", "version": pspec.get("version", 1),
           "meta": {"title": meta.get("title") or "Preset", "tagline": meta.get("tagline"), "hidden": bool(meta.get("hidden", False))},
           "implicit": True, "steps": steps}
    return normalize(doc)


def review_of(step: dict) -> dict:
    """A step's review declaration: a review step's own (flat on the step), else the nested
    ``review`` of a draft step — or of a data step in a run frozen before review steps stood on
    their own — else none."""
    if isinstance(step, dict) and step.get("kind") == "review":
        out = {k: step[k] for k in REVIEW_STEP_KEYS if k in step}
        out.setdefault("mode", "all"); out.setdefault("unit", "paper"); out.setdefault("blocks", "release")
        return out
    rv = step.get("review") if isinstance(step, dict) else None
    return dict(rv) if isinstance(rv, dict) else {"mode": "none", "unit": "entry", "blocks": "release"}


def review_steps_over(spec: dict, step_id: str) -> list[dict]:
    """The review steps that read ``step_id``, in order."""
    return [st for st in spec.get("steps") or [] if isinstance(st, dict) and st.get("kind") == "review" and step_id in (st.get("over") or [])]


def review_for(spec: dict, step_id: str) -> dict:
    """The review that covers a data step: its first review step's declaration (with the step's
    id as ``step``), else the step's own attribute (a draft of an update step, a frozen run), else
    none."""
    over = review_steps_over(spec, step_id)
    if over:
        return {**review_of(over[0]), "step": over[0]["id"]}
    return review_of(steps_by_id(spec).get(step_id) or {})
