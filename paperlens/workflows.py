"""Workflow runs: the planner and the engine that walks a workflow's steps over a dataset.

A dataset names its workflow (``dataset.workflow``, by contract) or runs its preset's implicit
one-step workflow. A RUN freezes the workflow, lists the papers it will add (the PDFs not yet in
the dataset under the step's pinned contract), fans the per-paper jobs out through the ordinary
extraction path — tagged with the run and step — and advances step by step as they land:
checks run, review gates open, the release is gated. Everything a run knows is in three tables
(``workflow_run``, ``workflow_step_run``, ``workflow_job``), so a run survives the queue
forgetting a job and a restart of the web process.

The first release runs ONE extract step per workflow (the dataset's preset), deterministic
checks, the preset's update steps and the release gate. k-runs with a vote, steps reading
earlier steps, dataset-level derive steps, loops and agent steps are grammar-valid and refused
at plan time with a message naming the release they arrive in.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Json

from . import checks, presets, records, review_gate
from . import workflow_spec as ws

TERMINAL = ("failed", "done", "cancelled")
JOB_DONE = ("done", "attached")
JOB_TERMINAL = ("done", "attached", "failed", "cancelled")
STALE_AFTER_MIN = 10


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(t) -> str | None:
    return t.isoformat(timespec="seconds") if t else None


# ── declarations ──────────────────────────────────────────────────────────────

def load_all() -> dict[str, dict]:
    """Every bundled workflow plus those in ``PAPERLENS_WORKFLOW_DIRS``; bundled ids win."""
    out: dict[str, dict] = {}
    for d in (os.environ.get("PAPERLENS_WORKFLOW_DIRS") or "").split(os.pathsep):
        if d.strip():
            try:
                out.update(ws.load_dir(Path(d.strip())))
            except Exception as exc:  # noqa: BLE001 - never fail the run over a side effect
                print(f"[workflows] skipping directory {d!r}: {exc}", flush=True)
    out.update(ws.load_dir())
    return out


def get_workflow(wid: str | None) -> dict | None:
    if not wid:
        return None
    return load_all().get(wid.partition("@")[0])


def _preset_spec(conn, base: str) -> dict | None:
    meta = presets.get(base, conn)
    if not meta or meta.get("legacy") or not meta.get("spec"):
        return None
    return meta["spec"]


def preset_for_pin(conn, pin: str) -> dict | None:
    """The preset spec a pin names: the live preset when its contract IS the pin, else the
    frozen schema row (an older contract of the same preset)."""
    base = ws.preset_base(pin)
    live = _preset_spec(conn, base)
    if live is not None and ws.ps.schema_id(live) == pin:
        return live
    return records.schema_spec(conn, pin)


def resolve(conn, dataset: dict) -> tuple[dict, bool]:
    """The dataset's workflow: the declared one, else its preset's implicit one-step workflow.
    Returns ``(spec, implicit)``."""
    wf = dataset.get("workflow")
    spec = get_workflow(wf) if wf else None
    if spec is not None:
        return spec, False
    sid = dataset.get("schema_id") or ""
    base = sid.partition("@")[0]
    pspec = _preset_spec(conn, base) if base else None
    if pspec is None:
        pspec = {"id": base if base and ws.ps.ID_RE.match(base) else "extract",
                 "meta": {"title": base or "Extraction", "mode": "extraction"}}
    return ws.implicit(pspec, sid or f"{pspec['id']}@v1"), True


def validate_for(conn, spec: dict) -> tuple[list[str], list[str]]:
    """Validate against the live presets AND the schema rows this server knows (a pin may be an
    older contract)."""
    return ws.validate(spec, resolver=lambda b: _preset_spec(conn, b),
                       pin_known=lambda pin: records.get_schema(conn, pin) is not None)


def later_problems(spec: dict) -> list[str]:
    """What this release cannot run yet, named per step."""
    out = []
    n_extract = 0
    for st in spec.get("steps") or []:
        sid, kind = st.get("id"), st.get("kind")
        if kind in ("extract", "agent"):
            n_extract += 1
        if kind == "extract":
            if (st.get("k") or 1) > 1:
                out.append(f"step {sid!r}: {ws.LATER['k']}")
            if (st.get("input") or {}).get("previous"):
                out.append(f"step {sid!r}: {ws.LATER['input']}")
        if kind in ("derive", "loop", "agent"):
            out.append(f"step {sid!r}: {ws.LATER[kind]}")
        rv = st.get("review") or {}
        if rv.get("mode") == "sample":
            out.append(f"step {sid!r}: a sampled review arrives in a later release — declare 'all' or 'none' for now")
    if n_extract > 1:
        out.append("several extract steps need linked datasets (a later release); this release runs one per workflow")
    return out


def workflows_for(conn, dataset: dict) -> list[dict]:
    """The bundled workflows whose extract step pins this dataset's preset (any contract)."""
    base = (dataset.get("schema_id") or "").partition("@")[0]
    out = []
    for spec in load_all().values():
        if (spec.get("meta") or {}).get("hidden"):
            continue
        bases = {ws.preset_base(p) for _s, p in ws.pins(spec)}
        if base and base in bases:
            out.append({"id": spec["id"], "contract": ws.workflow_id(spec), "title": (spec.get("meta") or {}).get("title"),
                        "tagline": (spec.get("meta") or {}).get("tagline"), "steps": [s["id"] for s in spec["steps"]]})
    return out


# ── what the dataset holds ────────────────────────────────────────────────────

def papers(conn, dataset_id: str) -> dict[str, dict]:
    """Per paper (by PDF hash): its documents in the dataset, by schema id."""
    rows = conn.execute(
        """SELECT ed.pdf_sha256, ed.id::text, ed.schema_id, ed.filename, p.title, p.doi, ed.created_at
           FROM extraction_document ed LEFT JOIN paper p ON p.id = ed.paper_id
           WHERE EXISTS (SELECT 1 FROM record r WHERE r.document_id = ed.id AND r.dataset_id = %s::uuid)
           ORDER BY ed.created_at""", (dataset_id,)).fetchall()
    out: dict[str, dict] = {}
    for sha, did, sid, fn, title, doi, created in rows:
        key = sha or f"doc:{did}"
        p = out.setdefault(key, {"pdf_sha256": sha, "filename": fn, "title": title, "doi": doi, "documents": {}})
        p["documents"].setdefault(sid or "", []).append(did)
    return out


def basis(conn, dataset_id: str, *, document_ids: list[str] | None = None) -> dict:
    """What a step ran on: the records (by id) and the newest CORRECTION among them — a value
    changed after the step ran makes it stale (the correction-after-the-fact rule); a verdict
    changes no value and stales nothing."""
    rows = conn.execute(
        """SELECT r.id::text FROM record r WHERE r.dataset_id = %s::uuid AND NOT COALESCE(r.screened_empty, false)
             AND (%s::uuid[] IS NULL OR r.document_id = ANY(%s::uuid[])) ORDER BY r.id""",
        (dataset_id, document_ids, document_ids)).fetchall()
    ids = [r[0] for r in rows]
    ev = conn.execute(
        """SELECT max(ve.created_at) FROM verification_event ve JOIN record r ON r.id = ve.record_id
           WHERE r.dataset_id = %s::uuid AND ve.status = 'corrected'
             AND (%s::uuid[] IS NULL OR r.document_id = ANY(%s::uuid[]))""",
        (dataset_id, document_ids, document_ids)).fetchone()[0]
    return {"n_records": len(ids), "record_ids_sha": hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16],
            "events_max": _iso(ev)}


# ── rows ──────────────────────────────────────────────────────────────────────

_RUN_COLS = ("id::text, dataset_id::text, workflow_id, contract, workflow, workflow_release, plan, status, current_step, "
             "started_by::text, session_id, model, use_credits, credits_planned, credits_consumed, credits_refunded, engine, error, "
             "created_at, updated_at, finished_at")


def _run_row(r) -> dict:
    return {"id": r[0], "dataset_id": r[1], "workflow_id": r[2], "contract": r[3], "workflow": r[4], "workflow_release": r[5],
            "plan": r[6], "status": r[7], "current_step": r[8], "started_by": r[9], "session_id": r[10], "model": r[11],
            "use_credits": r[12], "credits": {"planned": r[13], "consumed": r[14], "refunded": r[15]}, "engine": r[16],
            "error": r[17], "created_at": _iso(r[18]), "updated_at": _iso(r[19]), "finished_at": _iso(r[20])}


def get_run(conn, run_id: str) -> dict | None:
    if not records._is_uuid(run_id or ""):
        return None
    r = conn.execute(f"SELECT {_RUN_COLS} FROM workflow_run WHERE id = %s::uuid", (run_id,)).fetchone()
    return _run_row(r) if r else None


def list_runs(conn, dataset_id: str, *, limit: int = 20) -> list[dict]:
    rows = conn.execute(f"SELECT {_RUN_COLS} FROM workflow_run WHERE dataset_id = %s::uuid ORDER BY created_at DESC LIMIT %s",
                        (dataset_id, limit)).fetchall()
    return [_run_row(r) for r in rows]


def active_run(conn, dataset_id: str) -> dict | None:
    r = conn.execute(f"SELECT {_RUN_COLS} FROM workflow_run WHERE dataset_id = %s::uuid AND status NOT IN ('failed','done','cancelled') "
                     "ORDER BY created_at DESC LIMIT 1", (dataset_id,)).fetchone()
    return _run_row(r) if r else None


def last_done_run(conn, dataset_id: str) -> dict | None:
    r = conn.execute(f"SELECT {_RUN_COLS} FROM workflow_run WHERE dataset_id = %s::uuid AND status = 'done' ORDER BY finished_at DESC LIMIT 1",
                     (dataset_id,)).fetchone()
    return _run_row(r) if r else None


_SR_COLS = ("id::text, run_id::text, step_id, kind, ordinal, status, inputs, outputs, preset_schema_id, preset_release, prompt_sha256, "
            "model, usage, n_calls, credits, review, superseded_by::text, error, started_at, finished_at")


def _sr_row(r) -> dict:
    return {"id": r[0], "run_id": r[1], "step_id": r[2], "kind": r[3], "ordinal": r[4], "status": r[5], "inputs": r[6] or {},
            "outputs": r[7] or {}, "preset_schema_id": r[8], "preset_release": r[9], "prompt_sha256": r[10], "model": r[11],
            "usage": r[12], "n_calls": r[13], "credits": r[14], "review": r[15] or {}, "superseded_by": r[16], "error": r[17],
            "started_at": _iso(r[18]), "finished_at": _iso(r[19])}


def get_step_run(conn, step_run_id: str) -> dict | None:
    r = conn.execute(f"SELECT {_SR_COLS} FROM workflow_step_run WHERE id = %s::uuid", (step_run_id,)).fetchone()
    return _sr_row(r) if r else None


def step_runs(conn, run_id: str) -> list[dict]:
    return [_sr_row(r) for r in conn.execute(f"SELECT {_SR_COLS} FROM workflow_step_run WHERE run_id = %s::uuid ORDER BY ordinal", (run_id,)).fetchall()]


def latest_step_run(conn, dataset_id: str, step_id: str, *, done_only: bool = True) -> dict | None:
    r = conn.execute(
        f"""SELECT {', '.join('sr.' + c.split('::')[0] + ('::text' if '::text' in c else '') for c in _SR_COLS.split(', '))}
            FROM workflow_step_run sr JOIN workflow_run r ON r.id = sr.run_id
            WHERE r.dataset_id = %s::uuid AND sr.step_id = %s AND r.status NOT IN ('cancelled', 'failed')
              AND sr.superseded_by IS NULL AND (NOT %s OR sr.status = 'done')
            ORDER BY r.created_at DESC LIMIT 1""", (dataset_id, step_id, done_only)).fetchone()
    return _sr_row(r) if r else None


_JOB_COLS = "id::text, run_id::text, step_id, pdf_sha256, filename, job_id, status, tries, document_id::text, error, credits, refunded, replaces::text, created_at, updated_at"


def _job_row(r) -> dict:
    return {"id": r[0], "run_id": r[1], "step_id": r[2], "pdf_sha256": r[3], "filename": r[4], "job_id": r[5], "status": r[6],
            "tries": r[7], "document_id": r[8], "error": r[9], "credits": r[10], "refunded": r[11], "replaces": r[12],
            "created_at": _iso(r[13]), "updated_at": _iso(r[14])}


def jobs(conn, run_id: str, step_id: str | None = None) -> list[dict]:
    return [_job_row(r) for r in conn.execute(
        f"SELECT {_JOB_COLS} FROM workflow_job WHERE run_id = %s::uuid AND (%s::text IS NULL OR step_id = %s) ORDER BY created_at, pdf_sha256",
        (run_id, step_id, step_id)).fetchall()]


def _touch(conn, run_id: str, **fields) -> None:
    sets = ["updated_at = now()"]
    vals: list[Any] = []
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        vals.append(Json(v) if isinstance(v, (dict, list)) else v)
    conn.execute(f"UPDATE workflow_run SET {', '.join(sets)} WHERE id = %s::uuid", (*vals, run_id))


def _set_sr(conn, sr_id: str, **fields) -> None:
    sets, vals = [], []
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        vals.append(Json(v) if isinstance(v, (dict, list)) else v)
    conn.execute(f"UPDATE workflow_step_run SET {', '.join(sets)} WHERE id = %s::uuid", (*vals, sr_id))


def _set_job(conn, row_id: str, **fields) -> None:
    sets = ["updated_at = now()"]
    vals: list[Any] = []
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        vals.append(v)
    conn.execute(f"UPDATE workflow_job SET {', '.join(sets)} WHERE id = %s::uuid", (*vals, row_id))


def is_run_owner(run: dict, who) -> bool:
    return records._owns(who, run.get("started_by"), run.get("session_id"))


# ── per-step state ────────────────────────────────────────────────────────────

def update_step_state(conn, dataset_id: str, st: dict) -> dict:
    """The state of a preset-level update step (vocabulary / pages / crosscheck) — what the
    Update card painted before workflows: missing, committed (+ unresolved), the pages with their
    staleness."""
    from . import pages, vocabulary
    kind = st.get("kind") or st.get("type")
    item: dict = {}
    if kind == "vocabulary":
        vs = [v for v in vocabulary.list_for_dataset(conn, dataset_id) if v["column"] == st.get("column") and v["status"] == "committed"]
        item["state"] = "missing" if not vs else "fresh"
        if vs:
            v = vocabulary.get(conn, max(vs, key=lambda x: x["version"])["id"])
            item["version"] = v["version"]
            item["unresolved"] = len(vocabulary.unresolved(
                vocabulary.values(conn, dataset_id, v["unit"], v["column"], examples=False), v["assignments"]))
            if item["unresolved"]:
                item["state"] = "stale"
                item["because"] = [f"{item['unresolved']} new value{'s' if item['unresolved'] != 1 else ''} unresolved"]
    elif kind == "pages":
        rows = [r for r in pages.list_for_dataset(conn, dataset_id) if r["group"] == st.get("group")]
        now = set(pages.current_record_ids(conn, dataset_id))
        item["pages"] = [{"key": r["key"], "version": r["version"], "status": r["status"], "id": r["id"],
                          "stale": bool(set((r.get("built_from") or {}).get("record_ids") or []) ^ now)} for r in rows]
        committed = [p for p in item["pages"] if p["status"] == "committed"]
        if not committed:
            item["state"] = "draft" if item["pages"] else "missing"
        elif any(p["stale"] for p in committed):
            item["state"] = "stale"; item["because"] = ["records were added or removed since a page was written"]
        else:
            item["state"] = "fresh"
    elif kind == "crosscheck":
        d = records.get_dataset(conn, dataset_id) or {}
        item["state"] = "fresh" if d.get("companion_dataset_id") else "missing"
        if not d.get("companion_dataset_id"):
            item["because"] = ["no companion dataset set"]
    else:
        item["state"] = "missing"
    return item


def _extract_facts(conn, dataset_id: str, st: dict, *, who=None, new_pdfs=None, reextract=False) -> dict:
    """What an extract step would do: which staged PDFs are new, which can be attached from
    the caller's library, which papers sit under an older contract."""
    pin = st["preset"]
    base = ws.preset_base(pin)
    ppr = papers(conn, dataset_id)
    have = {k for k, p in ppr.items() if pin in p["documents"]}
    old = {k for k, p in ppr.items() if pin not in p["documents"] and any(ws.preset_base(s) == base for s in p["documents"])}
    foreign = {k for k, p in ppr.items() if k not in have and k not in old}
    staged = [p for p in (new_pdfs or []) if isinstance(p, dict) and p.get("sha256")]
    present = [p for p in staged if p["sha256"] in ppr]
    new = [p for p in staged if p["sha256"] not in ppr]
    attach: dict[str, dict] = {}
    if new and who is not None and st.get("source") == "live":
        found = records.documents_by_hashes(conn, [p["sha256"] for p in new], owner_user_id=getattr(who, "user_id", None),
                                            session_id=getattr(who, "session_id", None), schema_id=pin)
        for sha, docs in found.items():
            if docs:
                attach[sha] = docs[0]
    items = []
    for p in new:
        action = "attach" if p["sha256"] in attach else ("extract" if st.get("source") == "live" else "wait")
        items.append({"pdf_sha256": p["sha256"], "filename": p.get("filename"), "action": action,
                      **({"document_id": attach[p["sha256"]]["document_id"]} if p["sha256"] in attach else {})})
    if reextract:
        for k in sorted(old):
            p = ppr[k]
            docs = [d for s, ds in p["documents"].items() if ws.preset_base(s) == base for d in ds]
            items.append({"pdf_sha256": p["pdf_sha256"], "filename": p["filename"], "action": "reextract" if st.get("source") == "live" else "wait",
                          "replaces": docs[-1]})
    work = {"extract": sum(1 for i in items if i["action"] in ("extract", "reextract")), "attach": sum(1 for i in items if i["action"] == "attach"),
            "wait_for": sum(1 for i in items if i["action"] == "wait"), "present": len(present), "old_contract": len(old), "foreign": len(foreign)}
    return {"pin": pin, "base": base, "have": sorted(have), "old": sorted(old), "items": items, "work": work, "n_papers": len(ppr)}


def step_states(conn, dataset_id: str, spec: dict, *, who=None, new_pdfs=None, reextract=False, gates=None) -> list[dict]:
    """One entry per declared step with its state: fresh | stale | missing | draft | awaiting_review |
    blocked | unsupported, why, and the work a run would do."""
    gates = review_gate.open_gates(conn, dataset_id) if gates is None else gates
    open_by_step = {g["step_id"]: g for g in gates if not g.get("satisfied")}
    later = {}
    for msg in later_problems(spec):
        sid = msg.split("'")[1] if msg.startswith("step '") else None
        later.setdefault(sid, []).append(msg)
    out = []
    facts_by_step: dict[str, dict] = {}
    for st in spec.get("steps") or []:
        sid, kind = st["id"], st["kind"]
        item: dict = {"id": sid, "kind": kind, "label": st.get("label") or sid, "state": "fresh", "because": [], "n_calls": 0}
        if kind in ws.REVIEWED_KINDS:
            item["review"] = ws.review_of(st)
        if sid in later:
            item["state"] = "unsupported"; item["because"] = later[sid]
            out.append(item); continue
        if kind in ("extract", "agent"):
            f = _extract_facts(conn, dataset_id, st, who=who, new_pdfs=new_pdfs, reextract=reextract)
            facts_by_step[sid] = f
            item.update({"preset": f["pin"], "source": st.get("source"), "work": f["work"], "items": f["items"]})
            item["n_calls"] = f["work"]["extract"] * (st.get("k") or 1) if st.get("source") == "live" else 0
            if not f["have"] and not f["old"]:
                item["state"] = "missing"; item["because"] = ["no paper extracted under this contract yet"]
            if f["work"]["extract"] or f["work"]["attach"] or f["work"]["wait_for"]:
                item["state"] = "stale"; item["because"].append(f"{len(f['items'])} new paper{'s' if len(f['items']) != 1 else ''} to add")
            if f["old"] and not reextract:
                item["because"].append(f"{len(f['old'])} paper{'s' if len(f['old']) != 1 else ''} under an older contract (kept)")
            if sid in open_by_step:
                item["state"] = "awaiting_review"; item["gate"] = open_by_step[sid]
            elif gates:
                g = next((g for g in gates if g["step_id"] == sid), None)
                if g:
                    item["gate"] = g
        elif kind == "check":
            over = st.get("over") or []
            src = facts_by_step.get(over[0]) if over else None
            if src is None or (not src["have"] and not src["old"]):
                item["state"] = "blocked"; item["because"] = [f"{over[0] if over else 'its input'} has no documents yet"]
            else:
                sr = latest_step_run(conn, dataset_id, sid)
                if sr is None:
                    item["state"] = "missing"; item["because"] = ["never run"]
                else:
                    now = basis(conn, dataset_id)
                    was = sr.get("inputs") or {}
                    if was.get("record_ids_sha") != now["record_ids_sha"]:
                        item["state"] = "stale"; item["because"] = ["records added or removed since it ran"]
                    elif (was.get("events_max") or "") != (now.get("events_max") or ""):
                        item["state"] = "stale"; item["because"] = ["corrections after it ran"]
                    item["findings"] = {k: sr["outputs"].get(k) for k in ("n_checked", "findings", "by_severity", "by_code") if k in sr["outputs"]}
                    item["last_run"] = sr["run_id"]
            if sid in open_by_step:
                item["gate"] = open_by_step[sid]
        elif kind in ws.UPDATE_KINDS:
            item.update(update_step_state(conn, dataset_id, st))
            item.setdefault("because", [])
        elif kind == "release":
            from . import releases
            last = releases.latest(conn, dataset_id)
            gate = st.get("gate") or []
            blocked = [o["id"] for o in out if o["id"] in gate and o["state"] not in ("fresh",)]
            if last is None:
                item["state"] = "missing"; item["because"] = ["no release yet"]
            elif releases.changed_since(conn, dataset_id, last):
                item["state"] = "stale"; item["because"] = [f"changed since v{last['number']}"]
            if blocked:
                item["state"] = "blocked"; item["blocked_by"] = blocked
            item["release"] = {"number": last["number"], "created_at": last["created_at"]} if last else None
        out.append(item)
    return out


def gate_report(conn, dataset_id: str, spec: dict | None = None) -> dict:
    """For the release form: what holds the release and what only warns."""
    d = records.get_dataset(conn, dataset_id) or {}
    if spec is None:
        spec, _ = resolve(conn, d)
    gates = review_gate.open_gates(conn, dataset_id)
    states = step_states(conn, dataset_id, spec, gates=gates)
    gate_ids = set(ws.gate_steps(spec))
    blocking = [{"step": s["id"], "state": s["state"], "because": s.get("because") or []}
                for s in states if s["id"] in gate_ids and s["state"] in ("missing", "stale", "draft", "blocked", "awaiting_review")]
    warnings = [{"step": s["id"], "state": s["state"], "because": s.get("because") or []}
                for s in states if s["id"] not in gate_ids and s["kind"] != "release" and s["state"] in ("stale", "draft", "missing")]
    open_g = review_gate.blocking_release(gates)
    return {"open": not blocking and not open_g, "blocking": blocking, "warnings": warnings,
            "gates": gates, "gates_blocking": [g["step_id"] for g in open_g]}


# ── planning ──────────────────────────────────────────────────────────────────

def plan(conn, dataset_id: str, *, who, new_pdfs: list[dict] | None = None, reextract: bool = False,
         use_credits: bool = False, model: str | None = None) -> dict:
    from . import credits
    d = records.get_dataset(conn, dataset_id)
    if d is None:
        raise LookupError("Dataset not found.")
    spec, implicit = resolve(conn, d)
    errs, _warns = validate_for(conn, spec)
    problems = list(errs) + later_problems(spec)
    states = step_states(conn, dataset_id, spec, who=who, new_pdfs=new_pdfs, reextract=reextract)
    n_calls = sum(s.get("n_calls") or 0 for s in states)
    ex = [s for s in states if s["kind"] in ("extract", "agent")]
    present = ex[0]["work"]["present"] if ex and ex[0].get("work") else 0
    pin = ex[0].get("preset") if ex else None
    contract_change = None
    if pin and d.get("schema_id") and d["schema_id"] != pin:
        contract_change = {"from": d["schema_id"], "to": pin, "n_papers_old": ex[0]["work"].get("old_contract", 0) if ex[0].get("work") else 0,
                           "policy": "reextract" if reextract else "mixed"}
    uid = getattr(who, "user_id", None)
    balance = credits.balance(conn, uid) if (use_credits and uid) else None
    cmodel = (model if (model and credits.is_allowed_model(model)) else credits.credit_model()) if use_credits else model
    active = active_run(conn, dataset_id)
    return {"workflow": {"id": spec["id"], "contract": ws.workflow_id(spec), "version": spec.get("version", 1),
                         "title": (spec.get("meta") or {}).get("title"), "implicit": implicit, "pins_ok": not errs, "problems": problems},
            "dataset": {"id": dataset_id, "schema_id": d.get("schema_id"), "n_papers": ex[0]["work"].get("present", 0) + len(ex[0].get("items") or []) if ex and ex[0].get("work") else 0,
                        "contract_change": contract_change},
            "papers": {"present": present, "new": sum(1 for s in ex for i in s.get("items") or [] if i["action"] != "reextract"),
                       "attach": sum(1 for s in ex for i in s.get("items") or [] if i["action"] == "attach"),
                       "reextract": sum(1 for s in ex for i in s.get("items") or [] if i["action"] == "reextract")},
            "steps": states, "n_calls": n_calls,
            "credits": {"needed": n_calls if use_credits else 0, "balance": balance, "use_credits": bool(use_credits), "model": cmodel},
            "active_run": run_view(conn, active) if active else None,
            "inputs": {"new_pdfs": [{"sha256": p.get("sha256"), "filename": p.get("filename")} for p in (new_pdfs or []) if isinstance(p, dict)],
                       "reextract": bool(reextract), "use_credits": bool(use_credits), "model": model}}


class RunRefused(ValueError):
    def __init__(self, message: str, status: int = 422, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.extra = extra or {}


def start(conn, dataset_id: str, *, who, new_pdfs: list[dict] | None = None, reextract: bool = False,
          use_credits: bool = False, model: str | None = None) -> dict:
    """Plan again from the inputs, refuse what cannot run, consume the credits, insert the run
    with its step-runs and jobs, attach what the caller already has, and advance."""
    from . import __version__, credits, exporter
    p = plan(conn, dataset_id, who=who, new_pdfs=new_pdfs, reextract=reextract, use_credits=use_credits, model=model)
    if p["workflow"]["problems"]:
        raise RunRefused("This workflow cannot run yet: " + "; ".join(p["workflow"]["problems"][:4]), 422, {"problems": p["workflow"]["problems"]})
    if p["active_run"]:
        raise RunRefused(f"A run is already in progress ({p['active_run']['status']}).", 409, {"active_run": p["active_run"]["id"]})
    uid = getattr(who, "user_id", None)
    n_calls = p["n_calls"]
    cmodel = p["credits"]["model"]
    if use_credits:
        if not uid:
            raise RunRefused("Log in to use Metalens credits.", 401)
        if not cmodel or not credits.server_key_for(__import__("paperlens.providers", fromlist=["x"]).get_provider(cmodel, None)):
            raise RunRefused("Metalens credits aren’t available right now — use your own API key.", 400)
        if n_calls and not credits.try_consume(conn, uid, model=cmodel, n=n_calls, reason=f"workflow:{p['workflow']['id']}"):
            raise RunRefused(f"This run needs {n_calls} credit{'s' if n_calls != 1 else ''}; you have {p['credits']['balance']}.", 402)
    d = records.get_dataset(conn, dataset_id) or {}
    spec, _implicit = resolve(conn, d)
    rid = str(uuid.uuid4())
    try:
        with conn.transaction():
            conn.execute(
                """INSERT INTO workflow_run (id, dataset_id, workflow_id, contract, workflow, workflow_release, plan, status, started_by, session_id,
                                             model, use_credits, credits_planned, credits_consumed, engine)
                   VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, 'running', %s::uuid, %s, %s, %s, %s, %s, %s)""",
                (rid, dataset_id, spec["id"], p["workflow"]["contract"], Json(spec), _release_number(conn, spec), Json(p),
                 uid if records._is_uuid(uid or "") else None, getattr(who, "session_id", None), cmodel, bool(use_credits),
                 n_calls if use_credits else 0, n_calls if use_credits else 0,
                 Json({"name": "metalens", "version": __version__, "git_sha": exporter._git_sha()})))
            for i, st in enumerate(spec["steps"]):
                conn.execute(
                    """INSERT INTO workflow_step_run (id, run_id, step_id, kind, ordinal, status, preset_schema_id)
                       VALUES (%s::uuid, %s::uuid, %s, %s, %s, 'pending', %s)""",
                    (str(uuid.uuid4()), rid, st["id"], st["kind"], i, st.get("preset")))
            for s in p["steps"]:
                for it in s.get("items") or []:
                    conn.execute(
                        """INSERT INTO workflow_job (id, run_id, step_id, pdf_sha256, filename, status, document_id, replaces, credits)
                           VALUES (%s::uuid, %s::uuid, %s, %s, %s, 'pending', %s::uuid, %s::uuid, %s)""",
                        (str(uuid.uuid4()), rid, s["id"], it["pdf_sha256"], it.get("filename"),
                         it.get("document_id") if it["action"] == "attach" else None, it.get("replaces"),
                         1 if (use_credits and it["action"] in ("extract", "reextract")) else 0))
    except psycopg.errors.UniqueViolation:
        if use_credits and n_calls:
            credits.refund(conn, uid, model=cmodel, n=n_calls, reason="workflow refund")
        raise RunRefused("A run is already in progress.", 409)
    # what the caller already has under this contract joins without a model call
    for job in jobs(conn, rid):
        if job["document_id"] and job["status"] == "pending":
            try:
                records.assign_document_to_dataset(conn, dataset_id, job["document_id"])
                on_document(conn, rid, job["step_id"], job["pdf_sha256"], job["document_id"], attached=True, advance_run=False)
            except Exception as exc:  # noqa: BLE001 - never fail the run over a side effect
                _set_job(conn, job["id"], status="failed", error=str(exc)[:500])
    conn.commit()
    return run_view(conn, advance(conn, rid))


def _release_number(conn, spec: dict) -> int | None:
    """The workflow release this exact document matches, once workflow releases exist."""
    if conn.execute("SELECT to_regclass('workflow_release')").fetchone()[0] is None:
        return None
    row = conn.execute("SELECT number FROM workflow_release WHERE workflow_id = %s AND spec_sha = %s ORDER BY number DESC LIMIT 1",
                       (spec["id"], ws.spec_sha(spec))).fetchone()
    return row[0] if row else None


# ── execution: the per-paper jobs ─────────────────────────────────────────────

def claim_job(conn, run_id: str, step_id: str, pdf_sha256: str, who, *, filename: str | None = None) -> dict:
    """A paper's document is about to be made for this run and step (the page posts the PDF,
    the pipeline uploads its result): the job row to tag it with. A supplied step accepts
    papers the plan did not list; a live step only its planned ones."""
    run = get_run(conn, run_id)
    if run is None or not is_run_owner(run, who):
        raise RunRefused("Run not found.", 404)
    if run["status"] in TERMINAL:
        raise RunRefused(f"The run is {run['status']}.", 409)
    st = ws.steps_by_id(run["workflow"]).get(step_id)
    if st is None or st.get("kind") not in ("extract", "agent"):
        raise RunRefused("Not an extract step of this run.", 422)
    row = conn.execute(f"SELECT {_JOB_COLS} FROM workflow_job WHERE run_id = %s::uuid AND step_id = %s AND pdf_sha256 = %s",
                       (run_id, step_id, pdf_sha256)).fetchone()
    job = _job_row(row) if row else None
    if job is None:
        if st.get("source") != "supplied":
            raise RunRefused("This paper is not in the run's plan.", 409)
        jid = str(uuid.uuid4())
        conn.execute("INSERT INTO workflow_job (id, run_id, step_id, pdf_sha256, filename, status) VALUES (%s::uuid, %s::uuid, %s, %s, %s, 'pending')",
                     (jid, run_id, step_id, pdf_sha256, filename))
        job = _job_row(conn.execute(f"SELECT {_JOB_COLS} FROM workflow_job WHERE id = %s::uuid", (jid,)).fetchone())
    elif job["status"] in JOB_DONE:
        raise RunRefused("This paper already landed in the run.", 409)
    _set_job(conn, job["id"], status="running", tries=job["tries"] + 1, error=None, **({"filename": filename} if filename and not job["filename"] else {}))
    _touch(conn, run_id)
    conn.commit()
    job["status"] = "running"
    return {"job": job, "run": run, "step": st, "prepaid": bool(run["use_credits"]) and job["credits"] > 0}


def mark_queued(conn, job_row_id: str, job_id: str) -> None:
    _set_job(conn, job_row_id, status="queued", job_id=job_id)
    conn.commit()


def on_document(conn, run_id: str, step_id: str, pdf_sha256: str | None, document_id: str, *,
                job_id: str | None = None, attached: bool = False, advance_run: bool = True) -> None:
    """A paper landed for this run and step: mark its job, make sure the document carries the
    run and sits in the dataset (replacing an older contract's document when the job says so),
    then advance."""
    run = get_run(conn, run_id)
    if run is None:
        return
    row = None
    if pdf_sha256:
        row = conn.execute(f"SELECT {_JOB_COLS} FROM workflow_job WHERE run_id = %s::uuid AND step_id = %s AND pdf_sha256 = %s",
                           (run_id, step_id, pdf_sha256)).fetchone()
    if row is None and job_id:
        row = conn.execute(f"SELECT {_JOB_COLS} FROM workflow_job WHERE run_id = %s::uuid AND job_id = %s", (run_id, job_id)).fetchone()
    with conn.transaction():
        conn.execute("UPDATE extraction_document SET run_id = %s::uuid, step_id = %s WHERE id = %s::uuid AND run_id IS NULL",
                     (run_id, step_id, document_id))
    job = _job_row(row) if row else None
    if job and job.get("replaces"):
        records.replace_document_in_dataset(conn, run["dataset_id"], job["replaces"], document_id)
    else:
        records.assign_document_to_dataset(conn, run["dataset_id"], document_id)
    if job:
        _set_job(conn, job["id"], status="attached" if attached else "done", document_id=document_id, error=None)
    else:
        conn.execute("INSERT INTO workflow_job (id, run_id, step_id, pdf_sha256, status, document_id, job_id) VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s::uuid, %s)",
                     (str(uuid.uuid4()), run_id, step_id, pdf_sha256 or f"doc:{document_id}", "attached" if attached else "done", document_id, job_id))
    _touch(conn, run_id)
    conn.commit()
    _drop_run_pdf(run_id, pdf_sha256)
    if advance_run:
        advance(conn, run_id)


def on_job_failed(conn, run_id: str, *, job_id: str | None = None, pdf_sha256: str | None = None, error: str,
                  final: bool = True) -> None:
    run = get_run(conn, run_id)
    if run is None:
        return
    row = None
    if pdf_sha256:
        row = conn.execute(f"SELECT {_JOB_COLS} FROM workflow_job WHERE run_id = %s::uuid AND pdf_sha256 = %s ORDER BY created_at DESC LIMIT 1",
                           (run_id, pdf_sha256)).fetchone()
    if row is None and job_id:
        row = conn.execute(f"SELECT {_JOB_COLS} FROM workflow_job WHERE run_id = %s::uuid AND job_id = %s", (run_id, job_id)).fetchone()
    if row is None:
        return
    job = _job_row(row)
    if job["status"] in JOB_DONE:
        return
    _set_job(conn, job["id"], status="failed" if final else "queued", error=(error or "")[:500], refunded=bool(final and job["credits"]))
    _touch(conn, run_id)
    conn.commit()
    if final:
        advance(conn, run_id)


def _drop_run_pdf(run_id: str, pdf_sha256: str | None) -> None:
    if not pdf_sha256 or pdf_sha256.startswith("doc:"):
        return
    try:
        from . import storage
        store = storage.get_store()
        key = storage.run_pdf_key(run_id, pdf_sha256)
        if store.exists(key):
            store.delete(key)
    except Exception:  # noqa: BLE001, S110 - never fail the run over a side effect
        pass


# ── execution: the barrier ────────────────────────────────────────────────────

def advance(conn, run_id: str, *, complete_step: str | None = None) -> dict | None:
    """Idempotent: take the run as far as it can go now. Serialised per run."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"workflow-run:{run_id}",))
        run = get_run(conn, run_id)
        if run is None or run["status"] in TERMINAL:
            return run
        spec = run["workflow"]
        by_id = ws.steps_by_id(spec)
        d_id = run["dataset_id"]
        try:
            for sr in step_runs(conn, run_id):
                if sr["status"] in ("done", "skipped"):
                    continue
                st = by_id.get(sr["step_id"])
                if st is None:
                    _set_sr(conn, sr["id"], status="skipped"); continue
                if sr["status"] == "awaiting_review":
                    if review_gate.state(conn, sr)["satisfied"]:
                        _set_sr(conn, sr["id"], status="done"); continue
                    _touch(conn, run_id, status=f"awaiting_review:{sr['step_id']}", current_step=sr["step_id"]); return get_run(conn, run_id)
                if sr["status"] == "pending":
                    _set_sr(conn, sr["id"], status="running", started_at=_now()); sr["status"] = "running"
                _touch(conn, run_id, status="running", current_step=sr["step_id"])
                kind = st["kind"]
                if kind in ("extract", "agent"):
                    done = _advance_extract(conn, run, sr, st, complete_step == sr["step_id"])
                    if not done:
                        return get_run(conn, run_id)
                elif kind == "check":
                    _advance_check(conn, run, sr, st, by_id)
                elif kind in ws.UPDATE_KINDS:
                    state = update_step_state(conn, d_id, st)
                    _set_sr(conn, sr["id"], status="done", outputs=state, inputs=basis(conn, d_id), finished_at=_now())
                elif kind == "release":
                    _set_sr(conn, sr["id"], status="done", finished_at=_now(), inputs=basis(conn, d_id))
                    _touch(conn, run_id, status="done", current_step=None, finished_at=_now())
                    return get_run(conn, run_id)
                else:
                    raise RuntimeError(ws.LATER.get(kind, f"{kind} steps cannot run in this release"))
                sr2 = get_step_run(conn, sr["id"])
                if sr2 and sr2["status"] == "awaiting_review":
                    _touch(conn, run_id, status=f"awaiting_review:{sr['step_id']}", current_step=sr["step_id"]); return get_run(conn, run_id)
                if sr2 and sr2["status"] == "failed":
                    _touch(conn, run_id, status="failed", error=sr2.get("error"), finished_at=_now()); return get_run(conn, run_id)
            _touch(conn, run_id, status="done", current_step=None, finished_at=_now())
        except Exception as exc:  # noqa: BLE001 - never fail the run over a side effect
            _touch(conn, run_id, status="failed", error=str(exc)[:1000], finished_at=_now())
            _refund_unused(conn, run_id)
        return get_run(conn, run_id)


def _advance_extract(conn, run: dict, sr: dict, st: dict, complete: bool) -> bool:
    """True when the step is done (its documents landed), False while it waits."""
    js = jobs(conn, run["id"], sr["step_id"])
    if complete:
        for j in js:
            if j["status"] in ("pending",):
                _set_job(conn, j["id"], status="cancelled", error="not uploaded")
        js = jobs(conn, run["id"], sr["step_id"])
    if any(j["status"] not in JOB_TERMINAL for j in js):
        return False
    landed = [j for j in js if j["status"] in JOB_DONE]
    if not js and not complete and st.get("source") == "supplied":
        return False                                        # waiting for the pipeline's uploads
    failed = [{"pdf_sha256": j["pdf_sha256"], "filename": j["filename"], "error": j["error"]} for j in js if j["status"] == "failed"]
    if js and not landed and not complete:
        _set_sr(conn, sr["id"], status="failed", error=f"no paper landed: {failed[0]['error'] if failed else 'every job failed'}", finished_at=_now(),
                outputs={"document_ids": [], "failed": failed})
        return True
    doc_ids = [j["document_id"] for j in landed if j["document_id"]]
    n_records = conn.execute("SELECT count(*) FROM record WHERE document_id = ANY(%s::uuid[]) AND NOT COALESCE(screened_empty, false)",
                             (doc_ids,)).fetchone()[0] if doc_ids else 0
    outputs = {"document_ids": doc_ids, "n_records": n_records, "attached": sum(1 for j in landed if j["status"] == "attached"),
               "replaced": sum(1 for j in landed if j.get("replaces")), "failed": failed, "n_papers": len(landed)}
    refunded = _refund_failed(conn, run, js)
    d_id = run["dataset_id"]
    _set_sr(conn, sr["id"], status="done", outputs=outputs, inputs=basis(conn, d_id, document_ids=doc_ids) if doc_ids else {},
            finished_at=_now(), n_calls=len([j for j in landed if j["status"] == "done" and st.get("source") == "live"]),
            credits=sum(j["credits"] for j in landed), prompt_sha256=_prompt_sha(conn, doc_ids), preset_release=_preset_release(conn, st.get("preset")))
    sr = get_step_run(conn, sr["id"])
    header = review_gate.open_gate(conn, sr, st)
    if header["mode"] != "none" and header["blocks"] == "next" and not review_gate.state(conn, get_step_run(conn, sr["id"]))["satisfied"]:
        _set_sr(conn, sr["id"], status="awaiting_review")
    if refunded:
        _touch(conn, run["id"], credits_refunded=run["credits"]["refunded"] + refunded)
    return True


def _refund_failed(conn, run: dict, js: list[dict]) -> int:
    """The credits of planned calls that were never made (a paper never uploaded, a job that
    failed before the worker could refund it)."""
    if not run["use_credits"]:
        return 0
    from . import credits
    n = 0
    for j in js:
        if j["status"] in ("cancelled", "failed") and j["credits"] and not j["refunded"] and not j["job_id"]:
            n += j["credits"]
            _set_job(conn, j["id"], refunded=True)
    if n and run.get("started_by"):
        credits.refund(conn, run["started_by"], model=run.get("model"), n=n, reason="workflow refund")
    return n


def _refund_unused(conn, run_id: str) -> None:
    run = get_run(conn, run_id)
    if not run or not run["use_credits"]:
        return
    js = jobs(conn, run_id)
    n = _refund_failed(conn, run, [j for j in js if j["status"] in ("pending", "cancelled", "failed")] + [dict(j, status="cancelled") for j in js if j["status"] == "pending"])
    if n:
        _touch(conn, run_id, credits_refunded=run["credits"]["refunded"] + n)


def _prompt_sha(conn, doc_ids: list[str]) -> str | None:
    if not doc_ids:
        return None
    rows = conn.execute("SELECT DISTINCT prompt_sha256 FROM extraction_document WHERE id = ANY(%s::uuid[]) AND prompt_sha256 IS NOT NULL", (doc_ids,)).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def _preset_release(conn, pin: str | None) -> int | None:
    if not pin:
        return None
    try:
        from . import preset_releases
        rows = preset_releases.by_schema(conn, ws.preset_base(pin), pin)
        return rows[-1]["number"] if rows else None
    except Exception:  # noqa: BLE001 - never fail the run over a side effect
        return None


def _advance_check(conn, run: dict, sr: dict, st: dict, by_id: dict) -> None:
    d_id = run["dataset_id"]
    over = st.get("over") or []
    inputs = []
    for o in over:
        src = by_id.get(o) or {}
        inputs.append(checks.load_input(conn, d_id, src.get("preset")))
    res = checks.run_step(conn, run["id"], sr["id"], st, inputs)
    status = res.pop("status")
    flagged = res.pop("flagged_record_ids", [])
    _set_sr(conn, sr["id"], status="failed" if status == "failed" else "done", outputs=res, inputs=basis(conn, d_id), finished_at=_now(),
            error=f"{st['check']}: {res['findings']} finding(s) at or above {st.get('severity')}" if status == "failed" else None)
    if status != "failed" and st.get("on_fail") == "flag" and flagged:
        review_gate.open_gate(conn, get_step_run(conn, sr["id"]), {**st, "review": {"mode": "all", "unit": "entry", "blocks": "release"}},
                              record_ids=flagged)


# ── pause, resume, cancel, retry, reconcile ───────────────────────────────────

def resume(conn, run_id: str, *, who, step_id: str | None = None, override: bool = False) -> dict:
    run = get_run(conn, run_id)
    if run is None or not is_run_owner(run, who):
        raise RunRefused("Run not found.", 404)
    if not run["status"].startswith("awaiting_review:"):
        raise RunRefused(f"The run is {run['status']}, not waiting for a review.", 409)
    sid = run["status"].split(":", 1)[1]
    if step_id and step_id != sid:
        raise RunRefused(f"The run waits on {sid!r}.", 409)
    sr = next((s for s in step_runs(conn, run_id) if s["step_id"] == sid), None)
    if sr is None:
        raise RunRefused("Step not found.", 404)
    st = review_gate.state(conn, sr)
    if not st["satisfied"]:
        if not override:
            raise RunRefused(f"{st['pending']} of {st['total']} records still need a verdict.", 409, {"gate": st})
        review_gate.override(conn, sr, getattr(who, "user_id", None) or getattr(who, "session_id", None))
    _set_sr(conn, sr["id"], status="done")
    conn.commit()
    return run_view(conn, advance(conn, run_id))


def complete_uploads(conn, run_id: str, *, who, step_id: str) -> dict:
    """The owner says a supplied step has everything it will get: papers never uploaded are
    dropped from the plan and the run goes on."""
    run = get_run(conn, run_id)
    if run is None or not is_run_owner(run, who):
        raise RunRefused("Run not found.", 404)
    st = ws.steps_by_id(run["workflow"]).get(step_id)
    if st is None or st.get("source") != "supplied":
        raise RunRefused("Not a supplied step of this run.", 422)
    return run_view(conn, advance(conn, run_id, complete_step=step_id))


def cancel(conn, run_id: str, *, who) -> dict:
    run = get_run(conn, run_id)
    if run is None or not is_run_owner(run, who):
        raise RunRefused("Run not found.", 404)
    if run["status"] in TERMINAL:
        return run_view(conn, run)
    from . import worker
    for j in jobs(conn, run_id):
        if j["status"] in ("queued", "running") and j["job_id"]:
            try:
                worker.cancel_job(j["job_id"])
            except Exception:  # noqa: BLE001, S110 - never fail the run over a side effect
                pass
        if j["status"] in ("pending", "queued", "running"):
            _set_job(conn, j["id"], status="cancelled", error="run cancelled")
    _touch(conn, run_id, status="cancelled", finished_at=_now())
    conn.commit()
    _refund_unused(conn, run_id)
    try:
        from . import storage
        storage.get_store().delete_prefix(storage.run_prefix(run_id))
    except Exception:  # noqa: BLE001, S110 - never fail the run over a side effect
        pass
    return run_view(conn, get_run(conn, run_id))


def failed_jobs(conn, run_id: str, *, who, pdf_sha256: str | None = None) -> tuple[dict, list[dict]]:
    run = get_run(conn, run_id)
    if run is None or not is_run_owner(run, who):
        raise RunRefused("Run not found.", 404)
    js = [j for j in jobs(conn, run_id) if j["status"] == "failed" and (pdf_sha256 is None or j["pdf_sha256"] == pdf_sha256)]
    return run, js


def reconcile_candidates(conn) -> list[tuple[dict, dict]]:
    """Runs that have not moved for a while, with their jobs the queue may have finished or
    forgotten — the cron's work list."""
    rows = conn.execute(
        f"""SELECT {_RUN_COLS} FROM workflow_run WHERE status = 'running' AND updated_at < now() - make_interval(mins => %s)
            ORDER BY updated_at""", (STALE_AFTER_MIN,)).fetchall()
    out = []
    for r in rows:
        run = _run_row(r)
        for j in jobs(conn, run["id"]):
            if j["status"] in ("queued", "running") and j["job_id"]:
                out.append((run, j))
    return out


def apply_job_status(conn, run: dict, job: dict, status: dict | None) -> str:
    """What the queue says about a job the run still waits for: landed → the document; gone →
    look for a document the worker persisted before the queue forgot; else failed."""
    if status and status.get("success") is True and isinstance(status.get("result"), dict) and status["result"].get("document_id"):
        on_document(conn, run["id"], job["step_id"], job["pdf_sha256"], status["result"]["document_id"], job_id=job["job_id"])
        return "landed"
    if status and status.get("success") is False:
        on_job_failed(conn, run["id"], job_id=job["job_id"], error=str(status.get("error") or "failed"), final=True)
        return "failed"
    if status and str(status.get("status") or "") in ("JobStatus.queued", "JobStatus.deferred", "JobStatus.in_progress", "queued", "deferred", "in_progress"):
        return "waiting"
    row = conn.execute("SELECT id::text FROM extraction_document WHERE run_id = %s::uuid AND pdf_sha256 = %s ORDER BY created_at DESC LIMIT 1",
                       (run["id"], job["pdf_sha256"])).fetchone()
    if row:
        on_document(conn, run["id"], job["step_id"], job["pdf_sha256"], row[0], job_id=job["job_id"])
        return "recovered"
    on_job_failed(conn, run["id"], job_id=job["job_id"], error="the queue lost this job", final=True)
    return "lost"


# ── views ─────────────────────────────────────────────────────────────────────

def run_view(conn, run: dict | None) -> dict | None:
    if run is None:
        return None
    srs = step_runs(conn, run["id"])
    js = jobs(conn, run["id"])
    steps = []
    for sr in srs:
        item = {k: sr[k] for k in ("id", "step_id", "kind", "status", "outputs", "preset_schema_id", "preset_release", "prompt_sha256", "n_calls", "credits", "error", "started_at", "finished_at")}
        if sr.get("review"):
            item["review"] = review_gate.state(conn, sr)
        item["jobs"] = _job_counts([j for j in js if j["step_id"] == sr["step_id"]])
        steps.append(item)
    out = {k: v for k, v in run.items() if k not in ("workflow", "plan")}
    out["steps"] = steps
    out["jobs"] = _job_counts(js)
    out["papers"] = [{k: j[k] for k in ("pdf_sha256", "filename", "step_id", "status", "document_id", "error")} for j in js]
    out["progress"] = {"papers": {"done": out["jobs"]["done"] + out["jobs"]["attached"], "total": out["jobs"]["total"]},
                       "steps": {"done": sum(1 for s in steps if s["status"] in ("done", "skipped")), "total": len(steps), "current": run.get("current_step")}}
    return out


def _job_counts(js: list[dict]) -> dict:
    out = {"total": len(js), "pending": 0, "queued": 0, "running": 0, "done": 0, "attached": 0, "failed": 0, "cancelled": 0}
    for j in js:
        out[j["status"]] = out.get(j["status"], 0) + 1
    return out


def panel(conn, dataset_id: str, *, who, owner: bool) -> dict:
    """The dataset page's Workflow panel: the declared steps with their state, the active run,
    the past runs, the release gate."""
    d = records.get_dataset(conn, dataset_id) or {}
    spec, implicit = resolve(conn, d)
    gates = review_gate.open_gates(conn, dataset_id)
    states = step_states(conn, dataset_id, spec, who=who if owner else None, gates=gates)
    if not owner:
        for s in states:
            s.pop("items", None)
    # the run in progress, else the newest one: a finished run's gate and papers stay in view
    active = active_run(conn, dataset_id) or next(iter(list_runs(conn, dataset_id, limit=1)), None)
    errs, _w = validate_for(conn, spec)
    problems = list(errs) + later_problems(spec)
    gate_ids = set(ws.gate_steps(spec))
    blocking = [s["id"] for s in states if s["id"] in gate_ids and s["state"] != "fresh"]
    return {"workflow": {"id": spec["id"], "contract": ws.workflow_id(spec), "version": spec.get("version", 1),
                         "title": (spec.get("meta") or {}).get("title"), "tagline": (spec.get("meta") or {}).get("tagline"),
                         "implicit": implicit, "declared": d.get("workflow"), "problems": problems, "has_card": ws.has_card(spec)},
            "steps": states, "current_run": run_view(conn, active) if active else None,
            "runs": [{k: r[k] for k in ("id", "status", "created_at", "finished_at", "credits", "workflow_release")} | {"n_papers": (r.get("plan") or {}).get("papers", {}).get("new")}
                     for r in list_runs(conn, dataset_id, limit=10)],
            "gate": {"open": not blocking and not review_gate.blocking_release(gates), "blocking": blocking,
                     "gates_blocking": [g["step_id"] for g in review_gate.blocking_release(gates)]},
            "available": workflows_for(conn, d) if owner else [], "owner": owner}
