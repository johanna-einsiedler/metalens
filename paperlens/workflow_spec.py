"""The declarative WORKFLOW format: a versioned, citable combination of presets, checks and
aggregation steps run incrementally on a dataset, with human review declared per step.

A preset says what one model call per paper extracts; a workflow says which presets run, in
what order, what deterministic checks and dataset-level steps follow, and where a person looks
— every record, a random sample, or none — before a release. The dataset keeps the state
(records, drafts, runs); the workflow only declares the process.

  * ``dataset``   — the unit a run counts in (papers) and what identifies one (the PDF hash)
  * ``steps``     — an ordered list; the order IS the dependency graph (a step may only read
                    earlier ones), the last step is the release
  * ``review``    — per step: ``none`` | ``all`` | ``sample`` (unit, size, seed, strata), and
                    whether an unsatisfied gate holds the next step or only the release

Presets are pinned BY DATA CONTRACT (``<preset>@<sha8>``, the schema id): the same contract
means the same dataset shape; wording changes do not re-version a workflow. The workflow's own
contract (``workflow_id``) hashes the steps, their pins and their review declarations — not
labels, help texts or a derive prompt's wording.
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
KINDS = ("extract", "check", "derive", "vocabulary", "pages", "crosscheck", "loop", "release", "agent")
DATA_KINDS = ("extract", "derive", "agent")           # steps whose output a release gate may name
REVIEWED_KINDS = ("extract", "derive", "vocabulary", "pages", "agent")
DRAFT_KINDS = ("derive", "vocabulary", "pages")        # steps whose output is a draft a person commits
REVIEW_MODES = ("none", "all", "sample")
REVIEW_UNITS = ("entry", "paper")
BLOCKS = ("next", "release")
SOURCES = ("live", "supplied")
MAX_K = 5
MAX_LOOP = 3
PIN_RE = re.compile(r"^([a-z][a-z0-9-]{1,63})@([0-9a-f]{8})$")
UPDATE_KINDS = ("vocabulary", "pages", "crosscheck")   # the preset-level update steps, as workflow kinds
LATER = {"k": "running a preset several times needs the vote engine (a later release)",
         "input": "a step reading an earlier step's output needs the chain engine (a later release)",
         "loop": "loops need the chain engine (a later release)",
         "derive": "dataset-level derive steps arrive in a later release",
         "agent": "agent steps arrive in a later release"}

_ALLOWED_KEYS = {
    "root": {"format", "id", "version", "meta", "card", "dataset", "steps", "implicit"},
    "meta": {"title", "tagline", "description", "authors", "keywords", "license", "related", "hidden", "brands"},
    "card": {"file", "text"},
    "dataset": {"unit", "key"},
    "step": {"id", "kind", "label", "help", "review"},
    "extract": {"preset", "source", "k", "vote", "input", "params"},
    "agent": {"preset", "source"},
    "vote": {"identity", "fields"},
    "input": {"previous"},
    "check": {"check", "over", "on_fail", "severity", "params"},
    "derive": {"over", "scope", "identity", "calls", "output", "group_by", "input"},
    "call": {"id", "kind", "prompt", "op", "by", "fields", "model"},
    "vocabulary": {"unit", "column"},
    "pages": {"group", "keyed_by"},
    "crosscheck": set(),
    "loop": {"steps", "until", "max_iterations"},
    "release": {"gate"},
    "review": {"mode", "unit", "n", "share", "seed", "stratify", "blocks", "oversample_flagged"},
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
    """Fill the defaults and inline sibling files (the card, derive prompts). Shape errors are
    left to ``validate``; only what cannot be defaulted raises here."""
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
        if kind == "check":
            st.setdefault("on_fail", "note")
            st.setdefault("severity", "error")
            st.setdefault("params", {})
            if isinstance(st.get("over"), str):
                st["over"] = [st["over"]]
        if kind == "derive":
            for call in st.get("calls") or []:
                if isinstance(call, dict) and isinstance(call.get("prompt"), dict) and call["prompt"].get("file") and not call["prompt"].get("text"):
                    if base_dir is None:
                        raise WorkflowError([f"step {sid!r}: prompt.file {call['prompt']['file']!r} needs a base directory"], spec.get("id"))
                    try:
                        call["prompt"]["text"] = ps._read_sibling(call["prompt"].pop("file"), base_dir, what="prompt.file")
                    except ps.SpecError as e:
                        raise WorkflowError(e.errors, spec.get("id"))
        if kind in REVIEWED_KINDS:
            rv = st.get("review")
            rv = dict(rv) if isinstance(rv, dict) else {}
            rv.setdefault("mode", "all" if kind in DRAFT_KINDS else "none")
            rv.setdefault("unit", "entry")
            rv.setdefault("blocks", "release")
            rv.setdefault("oversample_flagged", False)
            st["review"] = rv
    if not any(isinstance(st, dict) and st.get("kind") == "release" for st in steps):
        steps.append({"id": "release", "kind": "release", "label": "Release",
                      "gate": [st["id"] for st in steps if isinstance(st, dict) and st.get("kind") in DATA_KINDS and isinstance(st.get("id"), str)]})
    return spec


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
            if k not in _ALLOWED_KEYS["step"] and k not in _ALLOWED_KEYS[kind]:
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
            _validate_derive(st, at, E, W, earlier=earlier, by_id=by_id)
        elif kind in UPDATE_KINDS:
            ps._validate_update_step({"type": kind, **{k: st[k] for k in ("unit", "column", "group", "keyed_by") if k in st}}, E, at)
        elif kind == "loop":
            _validate_loop(st, at, E, W, earlier=earlier, by_id=by_id)
        elif kind == "release":
            gate = st.get("gate")
            if not isinstance(gate, list) or not all(isinstance(g, str) for g in gate):
                E(f"{at}.gate must list step ids")
            else:
                for g in gate:
                    if g not in by_id or by_id[g].get("kind") not in DATA_KINDS:
                        E(f"{at}.gate: {g!r} is not an earlier extract or derive step")
        if kind in REVIEWED_KINDS:
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
    if not isinstance(inp, dict) or set(inp) - _ALLOWED_KEYS["input"]:
        E(f"{at}.input must be {{previous: [step ids]}}")
    else:
        prev = inp.get("previous") or []
        if not isinstance(prev, list) or any(not isinstance(p, str) for p in prev):
            E(f"{at}.input.previous must list step ids")
        else:
            for p in prev:
                if p not in earlier or by_id.get(p, {}).get("kind") not in ("extract", "agent"):
                    E(f"{at}.input.previous: {p!r} is not an earlier extract step")
    if not isinstance(st.get("params", {}), dict):
        E(f"{at}.params must be an object")
    return pspec


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


def _validate_derive(st, at, E, W, *, earlier, by_id) -> None:
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


def _validate_loop(st, at, E, W, *, earlier, by_id) -> None:
    inner = st.get("steps")
    if not isinstance(inner, list) or not inner or any(not isinstance(x, str) for x in inner):
        E(f"{at}.steps must list the earlier steps the loop repeats"); inner = []
    for x in inner:
        if x not in earlier or by_id.get(x, {}).get("kind") not in ("extract", "check", "agent"):
            E(f"{at}.steps: {x!r} is not an earlier extract or check step")
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


# ── identity ──────────────────────────────────────────────────────────────────

_WORDING = ("label", "help")


def workflow_contract(spec: dict) -> dict:
    """What a workflow version promises: the dataset unit, the steps with their pins, checks,
    calls and REVIEW declarations — not labels, help texts, nor a derive prompt's wording."""
    def strip_step(st: dict) -> dict:
        out = {k: v for k, v in st.items() if k not in _WORDING}
        if st.get("kind") == "derive":
            out["calls"] = [{k: v for k, v in c.items() if k != "prompt"} for c in st.get("calls") or [] if isinstance(c, dict)]
        return out
    return {"dataset": spec.get("dataset") or {},
            "steps": [strip_step(st) for st in spec.get("steps") or [] if isinstance(st, dict)]}


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
    rv = step.get("review") if isinstance(step, dict) else None
    return dict(rv) if isinstance(rv, dict) else {"mode": "none", "unit": "entry", "blocks": "release"}
