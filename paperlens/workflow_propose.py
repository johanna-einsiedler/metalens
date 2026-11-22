"""A workflow proposed from a question.

The user says what they want to get out of their papers; a model answers — inside the grammar —
with a preset to use (an existing one, stamped ones first) or a new preset document (the fewest
fields that answer the questions), and a workflow around it: extract, the contract check, rules
where the fields imply them, an audit review, the release. The validator is the guardrail: the
preset document is normalised and validated like one saved in the editor, the workflow like a
bundled one (a new preset's pin is known to the check before it is saved), and every problem
comes back path-prefixed for the editor to show under its step. Nothing is saved and no stamp is
set by a proposal: the person reviews it, saves the preset (if new) and then the workflow, and
runs it. The pattern is the dashboard proposer's (``dashboard_spec``).
"""
from __future__ import annotations

import json
import re

from . import checks, presets, records, templates, user_workflows, validation, workflows
from . import preset_spec as ps
from . import workflow_spec as ws

REGISTRY_VERSION = 1
MAX_QUESTIONS = 12
KINDS = ("extract", "check", "review", "release")
NEW = "new"                      # the pin an extract step names when its preset is the one proposed

_EXAMPLE = json.dumps({
    "preset": {"new": {"format": 2, "meta": {"title": "Minimum wage employment effects", "tagline": "one row per estimate", "mode": "extraction"},
                       "prompt": {"text": "Extract every employment elasticity the paper reports with its context."},
                       "paper": {"fields": [{"name": "country", "type": "string", "label": "Country", "help": "where the data come from"}]},
                       "entries": {"key": "estimates", "label": "Estimate", "cardinality": "many",
                                   "fields": [{"name": "outcome", "type": "string", "label": "Outcome", "help": "the employment outcome measured", "evidence": "row", "confidence": "main"},
                                              {"name": "elasticity", "type": "number", "label": "Elasticity", "help": "the point estimate as printed", "evidence": "value", "confidence": "main"},
                                              {"name": "se", "type": "number", "label": "SE", "help": "its standard error", "evidence": "value", "confidence": "main"}],
                                   "children": []},
                       "confidence": {"levels": ["high", "medium", "low"], "notes": True, "groups": [{"id": "main", "label": "Main values", "scope": "entry"}]},
                       "display": {"tabs": [{"id": "details", "label": "Details", "fields": ["outcome", "elasticity", "se"]}], "triage": "low_confidence_first"}}},
    "workflow": {"format": 1, "id": "minimum-wage", "meta": {"title": "Minimum wage employment effects", "tagline": "estimates, checked and audited"},
                 "steps": [{"id": "extract", "kind": "extract", "label": "Extraction", "preset": "new"},
                           {"id": "contract", "kind": "check", "label": "Contract check", "check": "schema", "over": ["extract"]},
                           {"id": "plausible", "kind": "check", "label": "Plausibility", "check": "rules", "over": ["extract"], "on_fail": "flag",
                            "params": {"rules": [{"code": "se_positive", "require": {"se": {"gt": 0}}}]}},
                           {"id": "audit", "kind": "review", "label": "Audit", "purpose": "audit", "over": ["extract"], "mode": "sample", "unit": "paper", "n": 20, "blocks": "release"},
                           {"id": "release", "kind": "release", "label": "Release", "gate": ["extract", "audit"]}]},
    "notes": ["No existing preset records employment elasticities; a new preset with three fields covers the questions."],
    "unanswered": []}, ensure_ascii=False, indent=1)


# ── what the model may build with ─────────────────────────────────────────────

def _field_line(f: dict) -> str:
    help_ = str(f.get("help") or "")[:70]
    return f"{f.get('name')}:{f.get('type')}" + (f" ({help_})" if help_ else "")


def _preset_entry(conn, spec: dict, *, source: str) -> dict:
    meta = spec.get("meta") or {}
    sid = ps.schema_id(spec)
    ent = spec.get("entries") or {}
    stamp = None
    try:
        st = validation.for_preset(conn, spec["id"], current_schema_id=sid)["stamp"]
        stamp = st["label"] if st and st.get("current") else None
    except Exception:  # noqa: BLE001 - a catalogue line never fails a proposal
        stamp = None
    return {"id": spec["id"], "schema_id": sid, "title": meta.get("title"), "tagline": meta.get("tagline"), "mode": meta.get("mode"), "source": source,
            "stamp": stamp, "paper_fields": [_field_line(f) for f in (spec.get("paper") or {}).get("fields") or [] if isinstance(f, dict)],
            "entries_key": ent.get("key"), "entry_fields": [_field_line(f) for f in ent.get("fields") or [] if isinstance(f, dict)],
            "children": [f"{c.get('key')}[{', '.join(_field_line(f) for f in c.get('fields') or [] if isinstance(f, dict))}]" for c in ent.get("children") or [] if isinstance(c, dict)]}


def catalogue(conn, who, brand) -> dict:
    """The presets the caller may use (this surface's bundled ones, their own and the public
    personal ones) with their fields and stamps, the workflows they may copy, the checks, the
    templates, and the review grammar."""
    out_p = []
    for spec in presets.load_all().values():
        meta = spec.get("meta") or {}
        if meta.get("hidden") or not brand.shows_preset(meta):
            continue
        out_p.append(_preset_entry(conn, spec, source="bundled"))
    for row in records.list_personal_presets(conn, owner_user_id=who.user_id, session_id=who.session_id, brand=brand.id):
        meta = presets.get(row["id"], conn, row=row)
        if meta and meta.get("spec") and not meta.get("legacy"):
            out_p.append(_preset_entry(conn, meta["spec"], source="mine" if records._owns(who, row.get("owner_user_id"), row.get("session_id")) else "shared"))   # noqa: SLF001
    out_w = []
    for spec in list(workflows.load_all().values()) + [r["spec"] for r in user_workflows.list_for(conn, who)]:
        meta = spec.get("meta") or {}
        if meta.get("hidden") or not brand.shows_preset(meta):
            continue
        out_w.append({"id": spec["id"], "title": meta.get("title"), "tagline": meta.get("tagline"),
                      "steps": [f"{st['id']}:{st['kind']}" + (f"({st.get('preset') or st.get('check') or st.get('purpose') or ''})" if st.get("preset") or st.get("check") or st.get("purpose") else "")
                                for st in spec.get("steps") or []]})
    out_c = [{"id": cid, "label": reg.get("label"), "presets": (reg.get("applies_to") or {}).get("presets"), "params": sorted(reg.get("params") or {})}
             for cid, reg in checks.REGISTRY.items() if not (reg.get("applies_to") or {}).get("passes")]
    out_t = [{"id": t["id"], "for": t.get("for"), "title": (t.get("meta") or {}).get("title")} for t in templates.load_all().values() if not (t.get("meta") or {}).get("hidden")]
    return {"presets": out_p, "workflows": out_w, "checks": out_c, "templates": out_t,
            "grammar": {"purposes": list(ws.PURPOSES), "modes": ["all", "sample"], "units": list(ws.REVIEW_UNITS), "blocks": list(ws.BLOCKS)}}


# ── the prompt ────────────────────────────────────────────────────────────────

def build_prompt(questions: list[dict], cat: dict, *, context: str | None = None, previous: dict | None = None, feedback: str | None = None) -> str:
    parts = ["You design an extraction workflow for a living systematic review: what to pull out of each scientific paper, how to check it, where a person looks.",
             "Return EXACTLY ONE JSON object and nothing else.", "", "# WHAT THE USER WANTS FROM THE PAPERS"]
    parts += [f"{q['id']}: {q['text']}" for q in questions] or ["(nothing given: propose a small general-purpose extraction)"]
    if (context or "").strip():
        parts += ["", "# CONTEXT FROM THE USER (field, the papers, what to emphasise or leave out)", context.strip()[:2000]]
    parts += ["", "# PRESETS AVAILABLE (a preset is the record's contract: fields with types; use one of these when its fields answer the questions — a stamped one first)"]
    for p in cat["presets"]:
        line = f"- {p['id']} (pin {p['schema_id']}, {p['mode']}, {p['source']}{', stamp: ' + p['stamp'] if p['stamp'] else ''}): {p['title'] or ''} — {p['tagline'] or ''}"
        if p["paper_fields"]:
            line += f"\n    paper: {', '.join(p['paper_fields'][:12])}"
        line += f"\n    {p['entries_key']}: {', '.join(p['entry_fields'][:16])}"
        if p["children"]:
            line += f"\n    children: {'; '.join(p['children'][:4])}"
        parts.append(line[:1200])
    parts += ["", "# WORKFLOWS ALREADY DEFINED (their steps, to copy a shape from)"]
    parts += [f"- {w['id']}: {w['title'] or ''} — {' → '.join(w['steps'])}"[:400] for w in cat["workflows"]] or ["(none)"]
    parts += ["", "# CHECKS (deterministic, in the registry; `rules` takes rules in the grammar below)"]
    parts += [f"- {c['id']}: {c['label']}" + (f" (for {', '.join(c['presets'])})" if isinstance(c["presets"], list) else " (any preset)") + (f"; params {', '.join(c['params'])}" if c["params"] else "") for c in cat["checks"]]
    parts += ["", "# REVIEW TEMPLATES (the questions a coder answers; only for the preset they are for)"]
    parts += [f"- {t['id']} (for {t['for']}): {t['title'] or ''}" for t in cat["templates"]] or ["(none)"]
    g = cat["grammar"]
    parts += ["", "# THE WORKFLOW DOCUMENT",
              "Steps run in order on every paper. Kinds: extract (\"preset\": an existing pin from the list above, or \"new\" for the preset you propose; \"source\": \"live\" for a model call per paper, \"supplied\" when results are uploaded — leave it out to take the preset's default), "
              "check (\"check\": a registry id above; \"over\": [extract step ids]; \"on_fail\": note | flag | fail; \"severity\": error | warn | info; \"params\" per the check), "
              "review (\"over\": [extract step ids]; \"purpose\": " + " | ".join(g["purposes"]) + "; \"mode\": all | sample; \"unit\": " + " | ".join(g["units"]) + "; \"n\" or \"share\" for a sample; \"coders\": N; \"template\": an id above for its preset only; \"blocks\": " + " | ".join(g["blocks"]) + "), "
              "release (\"gate\": [the step ids that must be fresh and the reviews done]).",
              "Purposes: audit — a first look at the model's work, its agreement feeds the preset's stamp; routed — the papers a rule sends on (needs \"where\"); monitor — a sample of each run's new papers, holds nothing (blocks none); calibrate — two or more coders on the same units; adjudicate — settles an earlier coders' disagreements.",
              "Rules (the `rules` check, params.rules): [{\"code\", \"severity\"?, \"unit\"?: entry | paper | <child key>, \"require\" | \"when\": {<field>: {op: arg}}, \"where\"?}] with ops eq ne in not_in null not_null gt gte lt lte between matches; a term may be a field of the unit, paper.<field>, or sum|count|min|max|mean(<child>.<column>); an argument may be {\"field\": \"<other field>\"}. Only fields that exist.",
              "", "# A NEW PRESET DOCUMENT (format 2), when no existing preset fits",
              "{\"format\": 2, \"meta\": {\"title\", \"tagline\", \"mode\": \"extraction\"}, \"prompt\": {\"text\": the task instructions for the model, the domain rules}, \"paper\": {\"fields\": [...]}, "
              "\"entries\": {\"key\": a plural noun, \"label\", \"cardinality\": \"many\", \"fields\": [...], \"children\": []}, \"confidence\": {\"levels\": [\"high\",\"medium\",\"low\"], \"notes\": true, \"groups\": [{\"id\": \"main\", \"label\": \"Main values\", \"scope\": \"entry\"}]}, "
              "\"display\": {\"tabs\": [{\"id\": \"details\", \"label\": \"Details\", \"fields\": [entry field names]}], \"triage\": \"low_confidence_first\"}}. "
              "A field: {\"name\": snake_case, \"type\": string | text | integer | number | boolean | enum | multi | list | table, \"label\", \"help\": what it holds, \"options\" for enum/multi, \"required\"?, \"range\"? [low, high], \"evidence\": value | row | table | none, \"confidence\": \"main\"}. Entry fields that hold a number or a quote cite evidence (\"value\" or \"row\").",
              "", "# RULES",
              "- Reuse an existing preset when its fields answer the questions; prefer one with a stamp. Then \"preset\": {\"use\": \"<its id>\"} and the extract step's \"preset\" is its pin.",
              "- Otherwise propose the smallest new preset that answers the questions: every field with a type and a help text, enum options spelled out, numbers with evidence. Then \"preset\": {\"new\": {...}} and the extract step's \"preset\" is \"new\".",
              "- The workflow: one extract step, the `schema` check, a `rules` check when a rule follows from the fields (an ordered pair, a share in [0, 1], a count ≥ 1), one audit review (a sample of 20 papers, holding the release), the release gated on the extract step and the review. Add a routed review only when a rule names the papers to send on.",
              "- Use ONLY the preset ids, check ids, template ids and field names listed or defined here; never invent one.",
              "- Say in \"notes\" what you assumed; list what the papers cannot answer under \"unanswered\" with the reason.",
              "", "# OUTPUT (shape and an example)",
              '{"preset": {"use": "<preset id>"} | {"new": {<preset document>}}, "workflow": {<workflow document>}, "notes": [strings], "unanswered": [{"question": id, "reason"}]}',
              "", _EXAMPLE]
    if previous:
        parts += ["", "# REVISION", "This is your earlier proposal. Revise it according to the feedback; keep what the feedback does not touch.",
                  json.dumps({k: previous.get(k) for k in ("preset", "workflow", "notes") if k in previous}, ensure_ascii=False)[:8000],
                  f"Feedback from the user: {feedback or '(none)'}"]
    return "\n".join(parts)


# ── the answer, checked ───────────────────────────────────────────────────────

def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:48]
    return s if s and ps.ID_RE.match(s) else fallback


def _preset_readable(conn, base: str, who) -> bool:
    row = records.get_personal_preset(conn, base)
    return row is None or row.get("visibility") == "public" or records.is_preset_owner(conn, base, who)


def normalize(conn, parsed: dict, who, *, questions: list[dict] | None = None) -> dict:
    """The model's answer as a proposal the editor can show: the preset (an existing one resolved,
    or the new document normalised and validated), the workflow with its pins resolved and the
    document validated with the new preset known, the cost per paper, what was dropped. Errors
    are path-prefixed; a proposal with errors still comes back (the editor places them)."""
    errors: list[str] = []; warnings: list[str] = []; notes = [str(n)[:300] for n in parsed.get("notes") or [] if isinstance(n, (str, int, float))][:12]
    unanswered = [u for u in parsed.get("unanswered") or [] if isinstance(u, dict)][:12]
    dropped: list[str] = []
    # the preset
    pr = parsed.get("preset") if isinstance(parsed.get("preset"), dict) else {}
    preset_out: dict = {}
    draft_spec, draft_sid = None, None
    if isinstance(pr.get("use"), str):
        meta = presets.get(pr["use"], conn)
        if not meta or not meta.get("spec") or meta.get("legacy") or not _preset_readable(conn, meta["id"], who):
            errors.append(f"preset: {pr['use']!r} is not a preset you can use")
        else:
            preset_out = {"use": meta["id"], "schema_id": meta["schema_id"], "title": meta.get("title"), "mode": meta.get("mode")}
    elif isinstance(pr.get("new"), dict):
        doc = dict(pr["new"]); doc.setdefault("format", ps.FORMAT)
        doc["id"] = _slug(doc.get("id") or ((doc.get("meta") or {}).get("title")), "proposed-preset")
        try:
            draft_spec = ps.normalize(doc)
            p_errs, p_warns = ps.validate(draft_spec)
        except ps.SpecError as exc:
            draft_spec, p_errs, p_warns = None, list(exc.errors), []
        errors += [f"preset.{e.lstrip('$.')}" if not e.startswith("preset") else e for e in p_errs]; warnings += [f"preset.{w.lstrip('$.')}" for w in p_warns]
        if draft_spec is not None and not p_errs:
            draft_sid = ps.schema_id(draft_spec)
        preset_out = {"new": draft_spec if draft_spec is not None else doc, "schema_id": draft_sid, "title": ((draft_spec or doc).get("meta") or {}).get("title"),
                      "mode": ((draft_spec or doc).get("meta") or {}).get("mode", "extraction"), "errors": p_errs, "warnings": p_warns}
    else:
        errors.append("preset: say which preset to use ({\"use\": id}) or propose one ({\"new\": {...}})")
    # the workflow
    wf = dict(parsed.get("workflow") or {}) if isinstance(parsed.get("workflow"), dict) else {}
    wf.setdefault("format", 1)
    title = ((wf.get("meta") or {}).get("title")) or (questions[0]["text"][:60] if questions else "Proposed workflow")
    wf["meta"] = {**(wf.get("meta") or {}), "title": title}
    wf["id"] = _slug(wf.get("id") or title, "proposed")
    steps = []
    for st in wf.get("steps") or []:
        if not isinstance(st, dict) or st.get("kind") not in KINDS:
            dropped.append(f"step {str((st or {}).get('id') if isinstance(st, dict) else st)[:40]!r}: not an extract, check, review or release step"); continue
        st = dict(st)
        if st["kind"] == "extract":
            pin = st.get("preset")
            if pin in (NEW, f"{NEW}@", None, "") or (draft_spec is not None and isinstance(pin, str) and pin.partition("@")[0] in (draft_spec["id"], "draft")):
                if draft_sid:
                    st["preset"] = draft_sid
                elif preset_out.get("use"):
                    st["preset"] = preset_out["schema_id"]
            elif isinstance(pin, str) and "@" not in pin:                   # a bare id: the live pin of that preset
                meta = presets.get(pin, conn)
                if meta and meta.get("schema_id"):
                    st["preset"] = meta["schema_id"]
            if preset_out.get("use") and isinstance(st.get("preset"), str) and ws.preset_base(st["preset"]) == preset_out["use"]:
                st["preset"] = preset_out["schema_id"]
            st.pop("source", None) if st.get("source") not in ws.SOURCES else None
        if st["kind"] == "check" and st.get("check") not in checks.REGISTRY:
            dropped.append(f"check {str(st.get('id'))[:40]!r}: {str(st.get('check'))[:40]!r} is not in the registry"); continue
        steps.append(st)
    wf["steps"] = steps
    draft_id = draft_spec["id"] if draft_spec is not None else None
    spec = None
    try:
        spec = ws.normalize(wf)
    except ws.WorkflowError as exc:
        errors += [str(e) for e in exc.errors]
    if spec is not None:
        w_errs, w_warns = ws.validate(spec, resolver=lambda base: draft_spec if base == draft_id else workflows._preset_spec(conn, base),   # noqa: SLF001
                                      pin_known=lambda pin: pin == draft_sid or records.get_schema(conn, pin) is not None)
        for _s, pin in ws.pins(spec):
            base = ws.preset_base(pin)
            if base != draft_id and not _preset_readable(conn, base, who):
                w_errs.append(f"preset {base!r} is someone else's private preset")
        errors += w_errs; warnings += w_warns
    out_wf = spec if spec is not None else wf
    calls = 0
    if spec is not None and not errors:
        calls = sum(workflows.calls_per_paper(spec, st["id"]) for st in spec["steps"] if st.get("kind") == "extract" and st.get("source") == "live")
    return {"preset": preset_out, "workflow": out_wf, "notes": notes, "unanswered": unanswered, "dropped": dropped,
            "errors": errors, "warnings": warnings, "contract": ws.workflow_id(spec) if spec is not None and not errors else None,
            "cost": {"calls_per_paper": calls, "credits_per_paper": calls}}
