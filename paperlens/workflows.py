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


# ── dataset groups: one workflow over linked datasets ─────────────────────────

def group_of(conn, dataset: dict) -> dict | None:
    """The group a dataset belongs to: ``{id, role, roles: {role: {dataset_id, title, schema_id}},
    parent: {id, title} | None}``, or None for a dataset outside any group. The parent is the
    workflow dataset — the row whose id is the group id, which carries the workflow and holds no
    records; the members are the datasets of the workflow's presets, one per role."""
    gid = dataset.get("group_id")
    if not gid:
        return None
    rows = conn.execute("SELECT id::text, group_role, title, schema_id FROM dataset WHERE group_id = %s::uuid ORDER BY created_at", (gid,)).fetchall()
    roles = {r[1]: {"dataset_id": r[0], "title": r[2], "schema_id": r[3]} for r in rows if r[1]}
    parent = next(({"id": r[0], "title": r[2]} for r in rows if r[0] == gid), None)
    return {"id": gid, "role": dataset.get("group_role"), "roles": roles, "parent": parent}


def is_workflow_dataset(dataset: dict | None) -> bool:
    return bool(dataset) and bool(dataset.get("group_id")) and dataset.get("group_id") == dataset.get("id")


def step_datasets(conn, dataset: dict, spec: dict) -> dict[str, str]:
    """{step id: dataset id} — an extract step with a role writes to the group's dataset of that
    role (this dataset when it has no group), every other step reads the dataset of the step it
    is over. Steps of a role the group has not linked yet map to nothing."""
    grp = group_of(conn, dataset)
    own = dataset["id"]
    out: dict[str, str] = {}
    by_id = ws.steps_by_id(spec)
    for st in spec.get("steps") or []:
        kind = st.get("kind")
        if kind in ("extract", "agent"):
            role = st.get("dataset")
            if role and grp and role in grp["roles"]:
                out[st["id"]] = grp["roles"][role]["dataset_id"]
            elif role and grp and role != grp.get("role"):
                continue                                     # not linked yet
            else:
                out[st["id"]] = own
    for st in spec.get("steps") or []:
        kind = st.get("kind")
        if kind in ("check", "derive", "crosscheck", "vocabulary", "pages"):
            over = st.get("over")
            first = over[0] if isinstance(over, list) and over else over if isinstance(over, str) else None
            src = by_id.get(first) if first else None
            if src and src.get("kind") == "check":
                first = (src.get("over") or [None])[0]
            out[st["id"]] = out.get(first, own) if first else own
        elif kind in ("review", "loop", "release"):
            out[st["id"]] = own
    return out


def dataset_for_step(conn, run: dict, step_id: str) -> str:
    """The dataset a run's step writes to or reads."""
    d = records.get_dataset(conn, run["dataset_id"]) or {"id": run["dataset_id"]}
    return step_datasets(conn, d, run["workflow"]).get(step_id) or run["dataset_id"]


def declare(conn, dataset: dict, spec: dict | None) -> dict:
    """Set (or clear) a dataset's workflow. A workflow over several presets (extract steps with
    dataset roles) lives on a workflow dataset of its own: declaring it on a plain dataset makes
    that workflow dataset and links this one into the role whose preset it uses; the other roles
    are linked afterwards (``link_role``). Clearing the workflow of a member takes it out of the
    group. Returns the dataset as stored."""
    did = dataset["id"]
    parent = is_workflow_dataset(dataset)
    if spec is None:
        if parent:
            raise RunRefused("A workflow dataset keeps its workflow; delete it to free its datasets.", 409)
        with conn.transaction():
            conn.execute("UPDATE dataset SET workflow = NULL, group_id = NULL, group_role = NULL, updated_at = now() WHERE id = %s::uuid", (did,))
        return records.get_dataset(conn, did)
    roles = ws.roles(spec)
    wid = ws.workflow_id(spec)
    if parent:
        if not roles:
            raise RunRefused("A workflow dataset runs a workflow over several presets.", 422)
        with conn.transaction():
            conn.execute("UPDATE dataset SET workflow = %s, updated_at = now() WHERE group_id = %s::uuid", (wid, did))
        _sync_companions(conn, records.get_dataset(conn, did), spec)
        return records.get_dataset(conn, did)
    if not roles:
        with conn.transaction():
            conn.execute("UPDATE dataset SET workflow = %s, group_id = NULL, group_role = NULL, updated_at = now() WHERE id = %s::uuid", (wid, did))
        return records.get_dataset(conn, did)
    base = (dataset.get("schema_id") or "").partition("@")[0]
    mine = next((r for r, info in roles.items() if ws.preset_base(info["preset"]) == base), None)
    if mine is None:
        raise RunRefused(f"This workflow does not extract with this dataset's preset ({base}).", 422)
    gid = dataset.get("group_id")
    if gid:                                                # already linked: the whole group follows
        with conn.transaction():
            conn.execute("""UPDATE dataset SET workflow = %s, group_role = CASE WHEN id = %s::uuid THEN %s ELSE group_role END, updated_at = now()
                            WHERE group_id = %s::uuid""", (wid, did, mine, gid))
        _sync_companions(conn, records.get_dataset(conn, did), spec)
        return records.get_dataset(conn, did)
    create_workflow_dataset(conn, spec, owner_user_id=dataset.get("owner_user_id"), session_id=dataset.get("session_id"),
                            members={mine: did}, title=None)
    return records.get_dataset(conn, did)


def create_workflow_dataset(conn, spec: dict, *, owner_user_id: str | None, session_id: str | None,
                            title: str | None = None, members: dict[str, str] | None = None, who=None) -> dict:
    """Make the dataset a workflow runs on. A workflow over several presets gets a workflow
    dataset — the group's own row, carrying the workflow — with the given datasets linked into
    their roles (``{role: dataset id}``; the rest are linked or created later). A workflow over
    one preset runs on a dataset of that preset, which carries the workflow itself. Returns the
    dataset to open."""
    roles = ws.roles(spec)
    meta = spec.get("meta") or {}
    wf_title = meta.get("title") or spec["id"]
    wid = ws.workflow_id(spec)
    if not roles:
        consumed = consumed_steps(spec)
        ex = next((st for st in spec.get("steps") or [] if st.get("kind") in ("extract", "agent") and st["id"] not in consumed), None)
        if ex is None or not ex.get("preset"):
            raise RunRefused("This workflow extracts nothing.", 422)
        run_spec = presets.resolve_run(conn, schema_id=ex["preset"], params={})
        records.upsert_schema(conn, run_spec.schema_id, run_spec.field_defs)
        d = records.create_dataset(conn, title=title or wf_title, schema_id=ex["preset"], owner_user_id=owner_user_id, session_id=session_id)
        return declare(conn, records.get_dataset(conn, d["id"]), spec)
    members = {r: m for r, m in (members or {}).items() if m}
    first_title = None
    for role, mid in members.items():
        if role not in roles:
            raise RunRefused(f"The workflow has no dataset role {role!r}.", 422)
        other = records.get_dataset(conn, mid) if records._is_uuid(mid) else None   # noqa: SLF001
        if other is None or (who is not None and not records.is_dataset_owner(conn, mid, who)):
            raise RunRefused("Dataset not found.", 404)
        if (other.get("schema_id") or "").partition("@")[0] != ws.preset_base(roles[role]["preset"]):
            raise RunRefused(f"That dataset is not under the {ws.preset_base(roles[role]['preset'])!r} preset.", 422)
        if other.get("group_id"):
            raise RunRefused("That dataset is already part of a workflow.", 409)
        first_title = first_title or other.get("title")
    pid = str(uuid.uuid4())
    name = (title or "").strip() or (f"{first_title} (workflow)" if first_title else wf_title)
    with conn.transaction():
        conn.execute("""INSERT INTO dataset (id, slug, title, owner_user_id, session_id, visibility, workflow, group_id, updated_at)
                        VALUES (%s::uuid, %s, %s, %s, %s, 'private', %s, %s::uuid, now())""",
                     (pid, f"workflow-{pid}", name, owner_user_id if records._is_uuid(owner_user_id or "") else None,   # noqa: SLF001
                      session_id, wid, pid))
        for role, mid in members.items():
            conn.execute("UPDATE dataset SET group_id = %s::uuid, group_role = %s, workflow = %s, updated_at = now() WHERE id = %s::uuid",
                         (pid, role, wid, mid))
    out = records.get_dataset(conn, pid)
    _sync_companions(conn, out, spec)
    return out


def link_role(conn, dataset: dict, role: str, *, other_id: str | None = None, create_title: str | None = None, who=None) -> dict:
    """Link an existing dataset (the owner's, under the role's preset, in no other group) or
    create an empty one for a role of the group's workflow. Returns the group."""
    spec, _ = resolve(conn, dataset)
    roles = ws.roles(spec)
    if role not in roles:
        raise RunRefused(f"The workflow has no dataset role {role!r}.", 422)
    gid = dataset.get("group_id")
    if not gid:
        raise RunRefused("Declare the workflow on this dataset first.", 409)
    pin = roles[role]["preset"]
    base = ws.preset_base(pin)
    with conn.transaction():
        if other_id:
            other = records.get_dataset(conn, other_id)
            if other is None or (who is not None and not records.is_dataset_owner(conn, other_id, who)):
                raise RunRefused("Dataset not found.", 404)
            if (other.get("schema_id") or "").partition("@")[0] != base:
                raise RunRefused(f"That dataset is not under the {base!r} preset.", 422)
            if other.get("group_id") and other["group_id"] != gid:
                raise RunRefused("That dataset belongs to another group.", 409)
            conn.execute("UPDATE dataset SET group_id = %s::uuid, group_role = %s, workflow = %s, updated_at = now() WHERE id = %s::uuid",
                         (gid, role, ws.workflow_id(spec), other_id))
        else:
            from . import presets
            run_spec = presets.resolve_run(conn, schema_id=pin, params={})
            records.upsert_schema(conn, run_spec.schema_id, run_spec.field_defs)
            base_title = (dataset.get("title") or "Dataset").removesuffix(" (workflow)")
            new = records.create_dataset(conn, title=create_title or f"{base_title} — {roles[role]['label']}", schema_id=pin,
                                         owner_user_id=dataset.get("owner_user_id"), session_id=dataset.get("session_id"))
            conn.execute("UPDATE dataset SET group_id = %s::uuid, group_role = %s, workflow = %s, updated_at = now() WHERE id = %s::uuid",
                         (gid, role, ws.workflow_id(spec), new["id"]))
    d = records.get_dataset(conn, dataset["id"])
    _sync_companions(conn, d, spec)
    return group_of(conn, d)


def _sync_companions(conn, dataset: dict, spec: dict) -> None:
    """A check that reads a companion (claims against tables) finds it inside the group: the
    member whose preset the check names as its companion. Set once, so the dataset page's own
    cross-check card and the single-dataset path keep working."""
    grp = group_of(conn, dataset)
    if not grp:
        return
    by_id = ws.steps_by_id(spec)
    for st in spec.get("steps") or []:
        if st.get("kind") != "check":
            continue
        reg = checks.REGISTRY.get(st.get("check")) or {}
        comp_bases = set((reg.get("applies_to") or {}).get("companion") or [])
        if not reg.get("needs_companion") or not comp_bases:
            continue
        over = (st.get("over") or [None])[0]
        role_over = (by_id.get(over) or {}).get("dataset")
        mine = grp["roles"].get(role_over) if role_over else None
        other = next((info for r, info in grp["roles"].items() if r != role_over and (info.get("schema_id") or "").partition("@")[0] in comp_bases), None)
        if mine and other:
            with conn.transaction():
                conn.execute("UPDATE dataset SET companion_dataset_id = %s::uuid WHERE id = %s::uuid AND companion_dataset_id IS NULL",
                             (other["dataset_id"], mine["dataset_id"]))


def validate_for(conn, spec: dict) -> tuple[list[str], list[str]]:
    """Validate against the live presets AND the schema rows this server knows (a pin may be an
    older contract)."""
    return ws.validate(spec, resolver=lambda b: _preset_spec(conn, b),
                       pin_known=lambda pin: records.get_schema(conn, pin) is not None)


def consumed_steps(spec: dict) -> dict[str, str]:
    """{step id: the later step that reads its runs} — an extract step named in a later step's
    ``input.previous`` runs inside that step's pipeline (its documents are runs, never the
    dataset's), so the planner, the jobs and the release gate treat it as part of the consumer."""
    out = {}
    for st in spec.get("steps") or []:
        for p in ((st.get("input") or {}).get("previous") or []) if st.get("kind") == "extract" else []:
            out[p] = st["id"]
    return out


def later_problems(spec: dict) -> list[str]:
    """What this release cannot run yet, named per step."""
    out = []
    consumed = consumed_steps(spec)
    finals = [st for st in spec.get("steps") or [] if st.get("kind") in ("extract", "agent") and st.get("id") not in consumed]
    for st in spec.get("steps") or []:
        sid, kind = st.get("id"), st.get("kind")
        if kind in ("agent",):
            out.append(f"step {sid!r}: {ws.LATER[kind]}")
    if len(finals) > 1:
        rs = [st.get("dataset") for st in finals]
        if not all(rs) or len(set(rs)) != len(rs):
            out.append("several extract steps whose documents enter a dataset must each name a distinct dataset role "
                       "(`dataset: tables`, `dataset: claims`): the datasets are linked as a group")
    return out


def pipeline_for(spec: dict, step_id: str) -> list[dict] | None:
    """The stages one paper of ``step_id`` runs through, or None for a plain single call:
    ``[{step_id, schema_id, k, vote, previous, params}]`` — the step it reads (k runs of that
    preset) first, then itself. ``/api/extract`` hands this to the job; the worker resolves the
    prompts by contract."""
    by_id = ws.steps_by_id(spec)
    st = by_id.get(step_id)
    if st is None or st.get("kind") != "extract":
        return None
    prev = (st.get("input") or {}).get("previous") or []
    stages = []
    for p in prev:
        src = by_id.get(p) or {}
        stages.append({"step_id": p, "schema_id": src.get("preset"), "k": int(src.get("k") or 1), "vote": src.get("vote"), "previous": False,
                       "params": src.get("params") or {}})
    stages.append({"step_id": step_id, "schema_id": st.get("preset"), "k": int(st.get("k") or 1), "vote": st.get("vote"), "previous": bool(prev),
                   "params": st.get("params") or {}})
    if len(stages) == 1 and stages[0]["k"] <= 1:
        return None
    return stages


def calls_per_paper(spec: dict, step_id: str) -> int:
    stages = pipeline_for(spec, step_id)
    return sum(int(s["k"]) for s in stages) if stages else 1


def workflows_for(conn, dataset: dict) -> list[dict]:
    """The bundled workflows whose extract step pins this dataset's preset (any contract)."""
    base = (dataset.get("schema_id") or "").partition("@")[0]
    out = []
    for spec in load_all().values():
        if (spec.get("meta") or {}).get("hidden"):
            continue
        bases = {ws.preset_base(p) for _s, p in ws.pins(spec)}
        if base and base in bases:
            roles = ws.roles(spec)
            role = next((r for r, info in roles.items() if ws.preset_base(info["preset"]) == base), None)
            out.append({"id": spec["id"], "contract": ws.workflow_id(spec), "title": (spec.get("meta") or {}).get("title"),
                        "tagline": (spec.get("meta") or {}).get("tagline"), "steps": [s["id"] for s in spec["steps"]],
                        # over several presets: the dataset joins a workflow dataset in this role
                        "role": role, "roles": [{"role": r, "label": info["label"]} for r, info in roles.items()]})
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


def _run_scope(conn, dataset_id: str) -> tuple[str, tuple]:
    """SQL for "the runs of this dataset": its own, plus its group's when it has one."""
    gid = conn.execute("SELECT group_id::text FROM dataset WHERE id = %s::uuid", (dataset_id,)).fetchone()
    if gid and gid[0]:
        return "(dataset_id = %s::uuid OR group_id = %s::uuid)", (dataset_id, gid[0])
    return "dataset_id = %s::uuid", (dataset_id,)


def list_runs(conn, dataset_id: str, *, limit: int = 20) -> list[dict]:
    where, args = _run_scope(conn, dataset_id)
    rows = conn.execute(f"SELECT {_RUN_COLS} FROM workflow_run WHERE {where} ORDER BY created_at DESC LIMIT %s", (*args, limit)).fetchall()
    return [_run_row(r) for r in rows]


def active_run(conn, dataset_id: str) -> dict | None:
    where, args = _run_scope(conn, dataset_id)
    r = conn.execute(f"SELECT {_RUN_COLS} FROM workflow_run WHERE {where} AND status NOT IN ('failed','done','cancelled') "
                     "ORDER BY created_at DESC LIMIT 1", args).fetchone()
    return _run_row(r) if r else None


def last_done_run(conn, dataset_id: str) -> dict | None:
    where, args = _run_scope(conn, dataset_id)
    r = conn.execute(f"SELECT {_RUN_COLS} FROM workflow_run WHERE {where} AND status = 'done' ORDER BY finished_at DESC LIMIT 1", args).fetchone()
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
    where, scope_args = _run_scope(conn, dataset_id)
    where = where.replace("dataset_id", "r.dataset_id", 1).replace("group_id", "r.group_id")
    r = conn.execute(
        f"""SELECT {', '.join('sr.' + c.split('::')[0] + ('::text' if '::text' in c else '') for c in _SR_COLS.split(', '))}
            FROM workflow_step_run sr JOIN workflow_run r ON r.id = sr.run_id
            WHERE {where} AND sr.step_id = %s AND r.status NOT IN ('cancelled', 'failed')
              AND sr.superseded_by IS NULL AND (NOT %s OR sr.status = 'done')
            ORDER BY r.created_at DESC LIMIT 1""", (*scope_args, step_id, done_only)).fetchone()
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
    blocked | unsupported, why, and the work a run would do. In a dataset group every step is
    judged against the member it writes to or reads (``step_datasets``)."""
    gates = review_gate.open_gates(conn, dataset_id) if gates is None else gates
    own = records.get_dataset(conn, dataset_id) or {"id": dataset_id}
    ds_of = step_datasets(conn, own, spec)
    group = group_of(conn, own)
    open_by_step = {g["step_id"]: g for g in gates if not g.get("satisfied")}
    later = {}
    for msg in later_problems(spec):
        sid = msg.split("'")[1] if msg.startswith("step '") else None
        later.setdefault(sid, []).append(msg)
    out = []
    facts_by_step: dict[str, dict] = {}
    consumed = consumed_steps(spec)
    for st in spec.get("steps") or []:
        sid, kind = st["id"], st["kind"]
        item: dict = {"id": sid, "kind": kind, "label": st.get("label") or sid, "state": "fresh", "because": [], "n_calls": 0}
        if kind in ws.REVIEWED_KINDS:
            item["review"] = ws.review_of(st)
        if sid in later:
            item["state"] = "unsupported"; item["because"] = later[sid]
            out.append(item); continue
        if kind == "extract" and sid in consumed:
            # its runs are made inside the consumer's pipeline, per paper; nothing to plan on its own
            item.update({"preset": st.get("preset"), "source": st.get("source"), "k": st.get("k") or 1, "pipeline_of": consumed[sid],
                         "work": {"extract": 0, "attach": 0, "wait_for": 0, "present": 0, "old_contract": 0, "foreign": 0}, "items": [],
                         "because": [f"runs inside the pipeline of {consumed[sid]!r} ({st.get('k') or 1} run{'s' if (st.get('k') or 1) != 1 else ''} per paper)"]})
            facts_by_step[sid] = {"pin": st.get("preset"), "have": [], "old": [], "items": [], "work": item["work"]}
            out.append(item); continue
        if kind in ("extract", "agent"):
            if st.get("dataset"):
                item["role"] = st["dataset"]
                item["dataset_id"] = ds_of.get(sid)
            if sid not in ds_of:
                item["state"] = "blocked"; item["because"] = [f"the group has no dataset for the role {st.get('dataset')!r} yet"]
                item.update({"preset": st.get("preset"), "source": st.get("source"), "work": {"extract": 0, "attach": 0, "wait_for": 0, "present": 0, "old_contract": 0, "foreign": 0}, "items": []})
                facts_by_step[sid] = {"pin": st.get("preset"), "have": [], "old": [], "items": [], "work": item["work"]}
                out.append(item); continue
            f = _extract_facts(conn, ds_of[sid], st, who=who, new_pdfs=new_pdfs, reextract=reextract)
            facts_by_step[sid] = f
            item.update({"preset": f["pin"], "source": st.get("source"), "work": f["work"], "items": f["items"], "k": st.get("k") or 1,
                         "calls_per_paper": calls_per_paper(spec, sid)})
            item["n_calls"] = f["work"]["extract"] * calls_per_paper(spec, sid) if st.get("source") == "live" else 0
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
            needs_companion = bool((checks.REGISTRY.get(st.get("check")) or {}).get("needs_companion"))
            item["check"] = st.get("check")
            c_ds = ds_of.get(sid, dataset_id)
            if src is None or (not src["have"] and not src["old"]):
                item["state"] = "blocked"; item["because"] = [f"{over[0] if over else 'its input'} has no documents yet"]
            elif needs_companion and not (records.get_dataset(conn, c_ds) or {}).get("companion_dataset_id"):
                item["state"] = "blocked"; item["because"] = ["no companion dataset set (link the group's other dataset, or Advanced on the dataset page)"]
            else:
                sr = latest_step_run(conn, dataset_id, sid)
                if sr is None:
                    item["state"] = "missing"; item["because"] = ["never run"]
                else:
                    now = basis(conn, c_ds)
                    was = sr.get("inputs") or {}
                    if was.get("record_ids_sha") != now["record_ids_sha"]:
                        item["state"] = "stale"; item["because"] = ["records added or removed since it ran"]
                    elif (was.get("events_max") or "") != (now.get("events_max") or ""):
                        item["state"] = "stale"; item["because"] = ["corrections after it ran"]
                    item["findings"] = {k: sr["outputs"].get(k) for k in ("n_checked", "findings", "by_severity", "by_code") if k in sr["outputs"]}
                    item["findings"]["first_document"] = _first_finding_document(conn, sr["id"])
                    item["last_run"] = sr["run_id"]
            if sid in open_by_step:
                item["gate"] = open_by_step[sid]
        elif kind == "derive":
            from . import derived
            src = facts_by_step.get(st.get("over"))
            item["over"] = st.get("over")
            d_ds = ds_of.get(sid, dataset_id)
            item["dataset_id"] = d_ds
            if src is None or (not src["have"] and not src["old"]):
                item["state"] = "blocked"; item["because"] = [f"{st.get('over')} has no documents yet"]
            else:
                item.update(derived.state(conn, d_ds, st))
                if item["state"] in ("stale", "missing") and derived.llm_calls(st):
                    try:
                        item["n_calls"] = derived.plan(derived.input_rows(conn, d_ds, st)["rows"], st)["n_calls"]
                    except Exception as exc:  # noqa: BLE001 - the plan is advisory
                        item["because"].append(f"cannot plan: {exc}")
            if sid in open_by_step:
                item["gate"] = open_by_step[sid]
        elif kind in ws.UPDATE_KINDS:
            item.update(update_step_state(conn, dataset_id, st))
            item.setdefault("because", [])
            if kind == "crosscheck" and item["state"] != "missing":      # runs as the claims-vs-tables check
                sr = latest_step_run(conn, dataset_id, sid)
                if sr is None:
                    item["state"] = "missing"; item["because"] = ["never run"]
                else:
                    now = basis(conn, dataset_id)
                    was = sr.get("inputs") or {}
                    if was.get("record_ids_sha") != now["record_ids_sha"] or (was.get("events_max") or "") != (now.get("events_max") or ""):
                        item["state"] = "stale"; item["because"] = ["records or values changed since it ran"]
                    item["findings"] = {k: sr["outputs"].get(k) for k in ("n_checked", "findings", "by_severity", "by_code") if k in sr["outputs"]}
                    item["findings"]["first_document"] = _first_finding_document(conn, sr["id"])
        elif kind == "loop":
            sr = latest_step_run(conn, dataset_id, sid)
            item["max_iterations"] = st.get("max_iterations", 1)
            if sr is not None:
                item["last"] = {k: (sr.get("outputs") or {}).get(k) for k in ("iteration", "papers_rerun", "stopped")}
        elif kind == "review":
            item["over"] = list(st.get("over") or [])
            sr = latest_step_run(conn, dataset_id, sid)
            if sid in open_by_step:
                item["state"] = "awaiting_review"; item["gate"] = open_by_step[sid]
            elif sr is None:
                item["state"] = "missing"; item["because"] = ["no run reviewed yet"]
            else:
                g = next((g for g in gates if g["step_id"] == sid), None)
                if g:
                    item["gate"] = g
        elif kind == "release":
            from . import releases
            last = releases.latest(conn, dataset_id)
            gate = st.get("gate") or []
            blocked = [o["id"] for o in out if o["id"] in gate and o["state"] not in ("fresh",)]
            if last is None:
                item["state"] = "missing"; item["because"] = ["no release yet"]
            elif releases.changed_since(conn, dataset_id, last):
                item["state"] = "stale"; item["because"] = [f"changed since v{last['number']}"]
            if group:
                for role, info in group["roles"].items():
                    if info["dataset_id"] == dataset_id:
                        continue
                    other_last = releases.latest(conn, info["dataset_id"])
                    if other_last is None or releases.changed_since(conn, info["dataset_id"], other_last):
                        if item["state"] == "fresh":
                            item["state"] = "stale"
                        item["because"].append(f"{role}: " + ("no release yet" if other_last is None else f"changed since v{other_last['number']}"))
            if blocked:
                item["state"] = "blocked"; item["blocked_by"] = blocked
            item["release"] = {"number": last["number"], "created_at": last["created_at"]} if last else None
        out.append(item)
    return out


def _first_finding_document(conn, step_run_id: str) -> str | None:
    """The document the review should open on: the one with the most serious findings of the step."""
    row = conn.execute(
        """SELECT document_id::text FROM workflow_finding WHERE step_run_id = %s::uuid AND document_id IS NOT NULL
           GROUP BY document_id ORDER BY sum(CASE severity WHEN 'error' THEN 2 WHEN 'warn' THEN 1 ELSE 0 END) DESC, document_id LIMIT 1""",
        (step_run_id,)).fetchone()
    return row[0] if row else None


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
    ds_of = step_datasets(conn, d, spec)
    missing = [st["id"] for st in spec["steps"] if st.get("kind") in ("extract", "agent") and st["id"] not in ds_of and st["id"] not in consumed_steps(spec)]
    if missing:
        raise RunRefused(f"The group has no dataset yet for {', '.join(missing)}: link or create it on the dataset page.", 422, {"missing": missing})
    rid = str(uuid.uuid4())
    try:
        with conn.transaction():
            conn.execute(
                """INSERT INTO workflow_run (id, dataset_id, workflow_id, contract, workflow, workflow_release, plan, status, started_by, session_id,
                                             model, use_credits, credits_planned, credits_consumed, engine, group_id)
                   VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, 'running', %s::uuid, %s, %s, %s, %s, %s, %s, %s::uuid)""",
                (rid, dataset_id, spec["id"], p["workflow"]["contract"], Json(spec), _release_number(conn, spec), Json(p),
                 uid if records._is_uuid(uid or "") else None, getattr(who, "session_id", None), cmodel, bool(use_credits),
                 n_calls if use_credits else 0, n_calls if use_credits else 0,
                 Json({"name": "metalens", "version": __version__, "git_sha": exporter._git_sha()}), d.get("group_id")))
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
                         calls_per_paper(spec, s["id"]) if (use_credits and it["action"] in ("extract", "reextract")) else 0))
    except psycopg.errors.UniqueViolation:
        if use_credits and n_calls:
            credits.refund(conn, uid, model=cmodel, n=n_calls, reason="workflow refund")
        raise RunRefused("A run is already in progress.", 409)
    # what the caller already has under this contract joins without a model call
    for job in jobs(conn, rid):
        if job["document_id"] and job["status"] == "pending":
            try:
                records.assign_document_to_dataset(conn, ds_of.get(job["step_id"], dataset_id), job["document_id"])
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
    target = dataset_for_step(conn, run, step_id)
    if job and job.get("replaces"):
        records.replace_document_in_dataset(conn, target, job["replaces"], document_id)
    else:
        records.assign_document_to_dataset(conn, target, document_id)
    if job:
        _set_job(conn, job["id"], status="attached" if attached else "done", document_id=document_id, error=None)
    else:
        conn.execute("INSERT INTO workflow_job (id, run_id, step_id, pdf_sha256, status, document_id, job_id) VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s::uuid, %s)",
                     (str(uuid.uuid4()), run_id, step_id, pdf_sha256 or f"doc:{document_id}", "attached" if attached else "done", document_id, job_id))
    _touch(conn, run_id)
    conn.commit()
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
                if sr["status"] == "needs_key":
                    _touch(conn, run_id, status=f"needs_key:{sr['step_id']}", current_step=sr["step_id"]); return get_run(conn, run_id)
                if sr["status"] == "pending":
                    _set_sr(conn, sr["id"], status="running", started_at=_now()); sr["status"] = "running"
                _touch(conn, run_id, status="running", current_step=sr["step_id"])
                kind = st["kind"]
                if kind == "extract" and sr["step_id"] in consumed_steps(spec):
                    _set_sr(conn, sr["id"], status="done", finished_at=_now(), outputs={"pipeline_of": consumed_steps(spec)[sr["step_id"]], "document_ids": []})
                    continue
                if kind in ("extract", "agent"):
                    done = _advance_extract(conn, run, sr, st, complete_step == sr["step_id"])
                    if not done:
                        return get_run(conn, run_id)
                elif kind == "check":
                    _advance_check(conn, run, sr, st, by_id)
                elif kind == "derive":
                    if not _advance_derive(conn, run, sr, st):
                        return get_run(conn, run_id)
                elif kind == "loop":
                    if not _advance_loop(conn, run, sr, st, by_id):
                        return get_run(conn, run_id)
                elif kind == "review":
                    _advance_review(conn, run, sr, st)
                elif kind == "crosscheck" and (records.get_dataset(conn, d_id) or {}).get("companion_dataset_id"):
                    # the preset-level cross-check step is the claims-vs-tables check over the extract step
                    first = next((x["id"] for x in spec["steps"] if x.get("kind") in ("extract", "agent")), None)
                    _advance_check(conn, run, sr, {"id": sr["step_id"], "kind": "check", "check": "claims-vs-tables", "over": [first] if first else [],
                                                   "on_fail": "note", "severity": "error", "params": {}}, by_id)
                elif kind in ws.UPDATE_KINDS:
                    state = update_step_state(conn, d_id, st)
                    _set_sr(conn, sr["id"], status="done", outputs=state, inputs=basis(conn, d_id), finished_at=_now())
                elif kind == "release":
                    _set_sr(conn, sr["id"], status="done", finished_at=_now(), inputs=basis(conn, d_id))
                    _touch(conn, run_id, status="done", current_step=None, finished_at=_now())
                    _drop_run_pdfs(run_id)
                    return get_run(conn, run_id)
                else:
                    raise RuntimeError(ws.LATER.get(kind, f"{kind} steps cannot run in this release"))
                sr2 = get_step_run(conn, sr["id"])
                if sr2 and sr2["status"] == "awaiting_review":
                    _touch(conn, run_id, status=f"awaiting_review:{sr['step_id']}", current_step=sr["step_id"]); return get_run(conn, run_id)
                if sr2 and sr2["status"] == "needs_key":
                    _touch(conn, run_id, status=f"needs_key:{sr['step_id']}", current_step=sr["step_id"]); return get_run(conn, run_id)
                if sr2 and sr2["status"] == "failed":
                    _touch(conn, run_id, status="failed", error=sr2.get("error"), finished_at=_now()); return get_run(conn, run_id)
            _touch(conn, run_id, status="done", current_step=None, finished_at=_now())
            _drop_run_pdfs(run_id)
        except Exception as exc:  # noqa: BLE001 - never fail the run over a side effect
            _touch(conn, run_id, status="failed", error=str(exc)[:1000], finished_at=_now())
            _refund_unused(conn, run_id)
            _drop_run_pdfs(run_id)
        return get_run(conn, run_id)


def _drop_run_pdfs(run_id: str) -> None:
    try:
        from . import storage
        storage.get_store().delete_prefix(storage.run_prefix(run_id))
    except Exception:  # noqa: BLE001, S110 - the retention sweep catches what this misses
        pass


_SEV = {"info": 0, "warn": 1, "error": 2}


def _advance_loop(conn, run: dict, sr: dict, st: dict, by_id: dict) -> bool:
    """A loop: while the named check still finds problems in some papers, re-run the live
    extract step for those papers with the findings fed back into the prompt, then the check
    again — at most ``max_iterations`` times. True when the loop is done, False while the
    re-extraction runs (or waits for the owner's key)."""
    outputs = dict(sr.get("outputs") or {})
    it = int(outputs.get("iteration") or 0)
    check_id = (st.get("until") or {}).get("check")
    ex_id = next((x for x in st.get("steps") or [] if by_id.get(x, {}).get("kind") == "extract"), None)
    check_st = by_id.get(check_id) or {}
    if not ex_id or not check_id:
        _set_sr(conn, sr["id"], status="done", finished_at=_now(), outputs={**outputs, "stopped": "nothing to loop"})
        return True
    floor = _SEV.get(check_st.get("severity") or "error", 2)
    rows = conn.execute(
        """SELECT f.document_id::text, f.path, f.code, f.severity, f.message FROM workflow_finding f
           WHERE f.run_id = %s::uuid AND f.step_id = %s AND f.document_id IS NOT NULL""", (run["id"], check_id)).fetchall()
    by_doc: dict[str, list[dict]] = {}
    for did, path, code, sev, msg in rows:
        if _SEV.get(sev, 0) >= floor:
            by_doc.setdefault(did, []).append({"path": path, "code": code, "severity": sev, "message": msg})
    max_find = int((st.get("until") or {}).get("max") or 0)
    bad = {did: fs for did, fs in by_doc.items() if len(fs) > max_find}
    js = {j["document_id"]: j for j in jobs(conn, run["id"], ex_id) if j.get("document_id")}
    todo = [js[did] for did in bad if did in js]
    history = list(outputs.get("history") or [])
    if not todo or it >= int(st.get("max_iterations") or 1):
        _set_sr(conn, sr["id"], status="done", finished_at=_now(),
                outputs={**outputs, "iteration": it, "stopped": "clean" if not todo else "max_iterations", "papers_left": len(todo), "history": history})
        return True
    feedback = {j["pdf_sha256"]: bad[j["document_id"]] for j in todo}
    history.append({"iteration": it + 1, "papers": [j["pdf_sha256"] for j in todo], "findings": sum(len(v) for v in bad.values())})
    _set_sr(conn, sr["id"], outputs={**outputs, "iteration": it + 1, "feedback": feedback, "papers_rerun": [j["pdf_sha256"] for j in todo], "history": history})
    # the inner steps run again: the extract step's jobs for those papers, then the check
    ex_sr = next((x for x in step_runs(conn, run["id"]) if x["step_id"] == ex_id), None)
    ck_sr = next((x for x in step_runs(conn, run["id"]) if x["step_id"] == check_id), None)
    if ex_sr:
        _set_sr(conn, ex_sr["id"], status="running", finished_at=None)
    if ck_sr:
        _set_sr(conn, ck_sr["id"], status="pending", finished_at=None)
    for j in todo:
        _set_job(conn, j["id"], status="pending", replaces=j["document_id"], document_id=None, error=None)
    if run["use_credits"]:
        requeue_papers(conn, run, todo, feedback=feedback)
    else:
        if ex_sr:
            _set_sr(conn, ex_sr["id"], status="needs_key")
        _touch(conn, run["id"], status=f"needs_key:{ex_id}", current_step=ex_id)
    return False


def requeue_papers(conn, run: dict, job_rows: list[dict], *, who=None, api_key: str | None = None, base_url: str | None = None,
                   model: str | None = None, feedback: dict | None = None) -> dict:
    """Run these papers of a live step again from the PDFs the run kept: a retry of a failed
    paper, or a loop's second attempt (``feedback`` = the check's findings per paper, fed into
    the prompt). A credit run spends the pipeline's calls again on the server's key; an
    own-key run needs the key given here."""
    from . import chain, credits, extract, presets, storage, worker
    store = storage.get_store()
    steps = ws.steps_by_id(run["workflow"])
    out = {"retried": [], "skipped": []}
    uid = run.get("started_by")
    sess = getattr(who, "session_id", None) or run.get("session_id")
    for j in job_rows:
        st = steps.get(j["step_id"]) or {}
        key = storage.run_pdf_key(run["id"], j["pdf_sha256"])
        if st.get("source") != "live" or not store.exists(key):
            out["skipped"].append({"pdf_sha256": j["pdf_sha256"], "why": "no stored PDF (upload it again)" if st.get("source") == "live" else "a supplied paper: upload it again"})
            _set_job(conn, j["id"], status="failed", error="no stored PDF")
            continue
        data = store.get(key)
        pipeline = pipeline_for(run["workflow"], j["step_id"])
        n_calls = chain.n_calls_of(pipeline) if pipeline else 1
        run_spec = presets.resolve_run(conn, schema_id=st.get("preset"), params=st.get("params") or None)
        if run["use_credits"]:
            cmodel = run.get("model") or credits.credit_model()
            if not (uid and credits.try_consume(conn, uid, model=cmodel, n=n_calls, reason="workflow loop")):
                _set_job(conn, j["id"], status="failed", error="not enough credits for another attempt")
                out["skipped"].append({"pdf_sha256": j["pdf_sha256"], "why": "not enough credits"})
                continue
            _touch(conn, run["id"], credits_consumed=run["credits"]["consumed"] + n_calls, credits_planned=run["credits"]["planned"] + n_calls)
            run = get_run(conn, run["id"])
            m, k_, b, uc = cmodel, "", None, True
        else:
            m, k_, b, uc = model or run.get("model") or "", api_key or "", base_url, False
            if not k_ and not b:
                raise RunRefused("This run uses your own API key: pass it to run the papers again.", 422, {"needs_key": j["step_id"]})
        fb = (feedback or {}).get(j["pdf_sha256"]) or None
        _set_job(conn, j["id"], status="running", tries=int(j.get("tries") or 0) + 1, error=None, credits=n_calls if uc else 0, refunded=False)
        conn.commit()
        import base64
        job_id = worker.enqueue("extract_chain_job" if pipeline else "extract_job", base64.b64encode(data).decode(), run_spec.prompt,
                                model=m, api_key=k_, base_url=b, schema_id=st.get("preset"), session_id=sess, owner_user_id=uid,
                                filename=j["filename"], params=run_spec.params, use_credits=uc, credit_user_id=uid if uc else None,
                                dataset_id=run["dataset_id"], run_id=run["id"], step_id=j["step_id"], credit_cost=n_calls,
                                pipeline=pipeline, feedback=fb, _expires=1800)
        if job_id:
            mark_queued(conn, j["id"], job_id)
        else:
            try:
                from . import providers
                sk = k_ or (credits.server_key_for(providers.get_provider(m, None)) or "" if uc else "")
                prompt = extract.with_feedback(run_spec.prompt, fb) if fb else run_spec.prompt
                res = extract.run(conn, data, prompt, pipeline=pipeline, model=m, api_key=sk, base_url=b, schema_id=st.get("preset"),
                                  session_id=sess, owner_user_id=uid, filename=j["filename"], spec=run_spec.spec, params=run_spec.params,
                                  run_id=run["id"], step_id=j["step_id"])
                on_document(conn, run["id"], j["step_id"], j["pdf_sha256"], res["document_id"], advance_run=False)
            except Exception as exc:  # noqa: BLE001
                on_job_failed(conn, run["id"], pdf_sha256=j["pdf_sha256"], error=str(exc)[:500], final=True)
        out["retried"].append(j["pdf_sha256"])
    return out


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
    if not complete and st.get("source") == "supplied":
        # waiting for the pipeline's uploads: until every paper planned at the start has landed —
        # or, when none was planned and the files arrive one by one, until the owner says so
        planned = {it.get("pdf_sha256") for s2 in ((run.get("plan") or {}).get("steps") or []) if s2.get("id") == sr["step_id"] for it in s2.get("items") or []}
        if not planned:
            return False
        if any(j["pdf_sha256"] in planned and j["status"] not in JOB_TERMINAL for j in js):
            return False
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
    d_id = dataset_for_step(conn, run, sr["step_id"])
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


def _advance_review(conn, run: dict, sr: dict, st: dict) -> None:
    """A joint review stage: one gate over the documents this run made in the steps it names,
    step by step, so the worklist walks each paper's documents together (the tables, then the
    claims). Opened as the stage declares (every paper, or a random draw of papers)."""
    doc_ids: list[str] = []
    for o in st.get("over") or []:
        osr = next((x for x in step_runs(conn, run["id"]) if x["step_id"] == o), None)
        for did in ((osr or {}).get("outputs") or {}).get("document_ids") or []:
            if did not in doc_ids:
                doc_ids.append(did)
    outputs = {"document_ids": doc_ids, "n_documents": len(doc_ids), "over": list(st.get("over") or [])}
    _set_sr(conn, sr["id"], status="done", outputs=outputs, finished_at=_now(), inputs=basis(conn, run["dataset_id"]))
    sr = get_step_run(conn, sr["id"])
    header = review_gate.open_gate(conn, sr, st)
    if header["mode"] != "none" and header["blocks"] == "next" and not review_gate.state(conn, get_step_run(conn, sr["id"]))["satisfied"]:
        _set_sr(conn, sr["id"], status="awaiting_review")


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


def _advance_derive(conn, run: dict, sr: dict, st: dict) -> bool:
    """A derive step: a transform-only step runs here, free; model calls run on the worker with
    the server key when the run spends credits (the job advances the run), inline when there is
    no queue, and wait for the owner's key otherwise (``needs_key``). True when the step is done
    or waiting on a review, False while it waits for the job or the key."""
    from . import derived, worker
    outputs = sr.get("outputs") or {}
    if outputs.get("job_id"):
        return False                                        # the worker is on it
    d_id = dataset_for_step(conn, run, sr["step_id"])
    if not derived.llm_calls(st):
        res = derived.run_step(conn, d_id, st, model=None, api_key=None, run_id=run["id"], owner_user_id=run.get("started_by"))
        _finish_derive(conn, run, sr, st, res)
        return True
    if run["use_credits"]:
        job_id = worker.enqueue("workflow_step_job", run["id"], sr["step_id"])
        if job_id:
            _set_sr(conn, sr["id"], outputs={**outputs, "job_id": job_id})
            return False
        from . import credits, providers
        key = credits.server_key_for(providers.get_provider(run.get("model") or "", None)) or ""
        res = derived.run_step(conn, d_id, st, model=run.get("model"), api_key=key, run_id=run["id"], owner_user_id=run.get("started_by"))
        _finish_derive(conn, run, sr, st, res)
        return True
    _set_sr(conn, sr["id"], status="needs_key")
    _touch(conn, run["id"], status=f"needs_key:{sr['step_id']}", current_step=sr["step_id"])
    return False


def _finish_derive(conn, run: dict, sr: dict, st: dict, res: dict) -> None:
    """The draft is made: record it on the step-run, commit it when the step declares no
    review, else open the draft gate (the review is the commit)."""
    from . import derived
    draft = res["draft"]
    rv = ws.review_of(st)
    outputs = {k: v for k, v in (sr.get("outputs") or {}).items() if k != "job_id"}
    outputs.update({"derived_id": draft["id"], "version": draft["version"], "n_rows": len(draft["rows"]), "repairs": len(res.get("repairs") or [])})
    _set_sr(conn, sr["id"], status="done", outputs=outputs, inputs=basis(conn, dataset_for_step(conn, run, sr["step_id"])), finished_at=_now(),
            n_calls=res.get("n_calls") or 0, credits=(res.get("n_calls") or 0) if run["use_credits"] else 0,
            model=draft.get("model"), prompt_sha256=draft.get("prompt_sha256"))
    if rv.get("mode", "none") == "none":
        derived.commit(conn, draft["id"])
        return
    header = {"mode": "all", "unit": "entry", "blocks": rv.get("blocks", "release"), "opened_at": _iso(_now()),
              "draft": {"kind": "derived", "id": draft["id"], "step": st["id"], "version": draft["version"]}}
    conn.execute("UPDATE workflow_step_run SET review = %s WHERE id = %s::uuid", (Json(header), sr["id"]))
    if header["blocks"] == "next":
        _set_sr(conn, sr["id"], status="awaiting_review")


def run_step_job(conn, run_id: str, step_id: str, *, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, generate=None) -> dict | None:
    """Run one derive step of a run — on the worker with the server key (a credit run), or here
    with the key the owner just gave (``resume``) — then advance the run. A failure fails the
    run and gives the step's planned credits back."""
    from . import credits, derived, providers
    run = get_run(conn, run_id)
    if run is None or run["status"] in TERMINAL:
        return run
    sr = next((s for s in step_runs(conn, run_id) if s["step_id"] == step_id), None)
    st = ws.steps_by_id(run["workflow"]).get(step_id)
    if sr is None or st is None or st.get("kind") != "derive" or sr["status"] in ("done", "skipped", "failed"):
        return run
    model = model or run.get("model") or (derived.llm_calls(st)[0].get("model") if derived.llm_calls(st) else None)
    key = api_key
    if key is None and run["use_credits"]:
        key = credits.server_key_for(providers.get_provider(model or "", None)) or ""
    try:
        res = derived.run_step(conn, dataset_for_step(conn, run, step_id), st, model=model, api_key=key, base_url=base_url, generate=generate,
                               run_id=run_id, owner_user_id=run.get("started_by"))
        with conn.transaction():
            _finish_derive(conn, run, sr, st, res)
    except Exception as exc:  # noqa: BLE001 - the run records the failure
        planned = 0
        try:
            planned = derived.plan(derived.input_rows(conn, dataset_for_step(conn, run, step_id), st)["rows"], st)["n_calls"]
        except Exception:  # noqa: BLE001, S110 - the refund is best effort
            pass
        with conn.transaction():
            _set_sr(conn, sr["id"], status="failed", error=str(exc)[:1000], finished_at=_now(), outputs={k: v for k, v in (sr.get("outputs") or {}).items() if k != "job_id"})
            if run["use_credits"] and planned and run.get("started_by"):
                credits.refund(conn, run["started_by"], model=run.get("model"), n=planned, reason="workflow refund")
                _touch(conn, run_id, credits_refunded=run["credits"]["refunded"] + planned)
    conn.commit()
    return advance(conn, run_id)


def step_job_candidates(conn) -> list[tuple[dict, dict]]:
    """Derive steps whose worker job has not reported for a while: (run, step-run) pairs."""
    rows = conn.execute(
        """SELECT sr.id::text FROM workflow_step_run sr JOIN workflow_run r ON r.id = sr.run_id
           WHERE r.status = 'running' AND sr.kind = 'derive' AND sr.status = 'running' AND sr.outputs ? 'job_id'
             AND r.updated_at < now() - interval '10 minutes'""").fetchall()
    out = []
    for (srid,) in rows:
        sr = get_step_run(conn, srid)
        run = get_run(conn, sr["run_id"]) if sr else None
        if sr and run:
            out.append((run, sr))
    return out


def apply_step_job_status(conn, run: dict, sr: dict, status: dict | None) -> str:
    """What the queue says about a derive step's job: still at it, finished (the step-run already
    moved on — nothing to do), or forgotten → the step fails and the run with it."""
    if status and str(status.get("status")) in ("JobStatus.queued", "JobStatus.deferred", "JobStatus.in_progress", "queued", "deferred", "in_progress"):
        _touch(conn, run["id"])
        return "waiting"
    fresh = get_step_run(conn, sr["id"])
    if fresh and fresh["status"] != "running":
        return "landed"
    if status and status.get("success"):
        advance(conn, run["id"])
        return "landed"
    with conn.transaction():
        _set_sr(conn, sr["id"], status="failed", error=(status or {}).get("error") or "the worker lost the step's job", finished_at=_now())
        _touch(conn, run["id"], status="failed", error=f"{sr['step_id']}: the worker lost the step's job", finished_at=_now())
    _refund_unused(conn, run["id"])
    return "lost"


def _advance_check(conn, run: dict, sr: dict, st: dict, by_id: dict) -> None:
    over = st.get("over") or []
    d_id = dataset_for_step(conn, run, over[0]) if over else run["dataset_id"]
    inputs = []
    for o in over:
        src = by_id.get(o) or {}
        inputs.append(checks.load_input(conn, dataset_for_step(conn, run, o), src.get("preset")))
    if (checks.REGISTRY.get(st.get("check")) or {}).get("needs_companion"):
        comp = checks.companion_input(conn, d_id)
        if comp is None:
            _set_sr(conn, sr["id"], status="skipped", outputs={"skipped": "no companion dataset set"}, finished_at=_now())
            return
        if inputs:
            inputs[0]["companion"] = comp
    res = checks.run_step(conn, run["id"], sr["id"], st, inputs)
    status = res.pop("status")
    flagged = res.pop("flagged_record_ids", [])
    _set_sr(conn, sr["id"], status="failed" if status == "failed" else "done", outputs=res, inputs=basis(conn, d_id), finished_at=_now(),
            error=f"{st['check']}: {res['findings']} finding(s) at or above {st.get('severity')}" if status == "failed" else None)
    if status != "failed" and st.get("on_fail") == "flag" and flagged:
        review_gate.open_gate(conn, get_step_run(conn, sr["id"]), {**st, "review": {"mode": "all", "unit": "entry", "blocks": "release"}},
                              record_ids=flagged)


# ── pause, resume, cancel, retry, reconcile ───────────────────────────────────

def resume(conn, run_id: str, *, who, step_id: str | None = None, override: bool = False,
           api_key: str | None = None, base_url: str | None = None, model: str | None = None) -> dict:
    run = get_run(conn, run_id)
    if run is None or not is_run_owner(run, who):
        raise RunRefused("Run not found.", 404)
    if run["status"].startswith("needs_key:"):
        sid = run["status"].split(":", 1)[1]
        if step_id and step_id != sid:
            raise RunRefused(f"The run waits on {sid!r}.", 409)
        if not (api_key or base_url):
            raise RunRefused(f"The step {sid!r} makes model calls: give an API key (or a local model server) to go on.", 422, {"needs_key": sid})
        sr = next((s for s in step_runs(conn, run_id) if s["step_id"] == sid), None)
        if sr is None:
            raise RunRefused("Step not found.", 404)
        _set_sr(conn, sr["id"], status="running")
        _touch(conn, run_id, status="running", model=model or run.get("model"))
        conn.commit()
        if sr["kind"] == "extract":                        # a loop's second attempt on an own-key run
            loop_sr = next((s for s in step_runs(conn, run_id) if s["kind"] == "loop" and sid in (ws.steps_by_id(run["workflow"]).get(s["step_id"]) or {}).get("steps", [])), None)
            fb = ((loop_sr or {}).get("outputs") or {}).get("feedback") or {}
            todo = [j for j in jobs(conn, run_id, sid) if j["status"] == "pending" and j["pdf_sha256"] in fb]
            requeue_papers(conn, get_run(conn, run_id), todo, who=who, api_key=api_key, base_url=base_url, model=model, feedback=fb)
            conn.commit()
            return run_view(conn, advance(conn, run_id))
        return run_view(conn, run_step_job(conn, run_id, sid, api_key=api_key or "", base_url=base_url, model=model))
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
    out["now"] = now_running(conn, run, steps, js)
    return out


def _parse_iso(v) -> datetime | None:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def history_pace(conn, kind: str, preset_schema_id: str | None = None) -> float | None:
    """Seconds per unit of work in the last finished steps of this kind on this server: per paper
    for an extract step (live papers only), per model call for a derive step. None without history."""
    rows = conn.execute(
        """SELECT extract(epoch FROM (finished_at - started_at)), n_calls FROM workflow_step_run
           WHERE kind = %s AND status = 'done' AND n_calls > 0 AND started_at IS NOT NULL AND finished_at IS NOT NULL
             AND (%s::text IS NULL OR preset_schema_id = %s)
           ORDER BY finished_at DESC LIMIT 10""", (kind, preset_schema_id, preset_schema_id)).fetchall()
    secs = sum(float(r[0] or 0) for r in rows)
    units = sum(int(r[1] or 0) for r in rows)
    return secs / units if units and secs > 0 else None


def now_running(conn, run: dict, steps: list[dict], js: list[dict]) -> dict | None:
    """The step a run is on and what it is doing: ``{step, label, kind, phase, done, total,
    elapsed_s, eta_s, basis, waiting_for_you}``. The estimate is measured, never guessed: from this
    run's own pace once a paper has landed, else from the last finished steps of the same kind (and
    contract) on this server, else none. A step that waits for people (uploads, a review, a key) has
    none and says ``waiting_for_you``: nothing happens until the owner acts; uploads also list what
    each supplied step has received (``uploads``)."""
    status = run.get("status") or ""
    if status in TERMINAL or status == "planned":
        return None
    sid = run.get("current_step") or (status.split(":", 1)[1] if ":" in status else None)
    sr = next((x for x in steps if x["step_id"] == sid), None)
    if sr is None:
        return None
    st = ws.steps_by_id(run.get("workflow") or {}).get(sid) or {}
    out: dict = {"step": sid, "label": st.get("label") or sid, "kind": sr["kind"], "phase": None, "done": None, "total": None,
                 "elapsed_s": None, "eta_s": None, "basis": None, "waiting_for_you": False}
    started = _parse_iso(sr.get("started_at"))
    if started:
        out["elapsed_s"] = max(0, int((_now() - started).total_seconds()))
    if status.startswith("awaiting_review:"):
        rv = sr.get("review") or {}
        out.update(phase="waiting for review", done=rv.get("checked"), total=rv.get("total"), waiting_for_you=True)
        return out
    if status.startswith("needs_key:"):
        out.update(phase="waiting for an API key", waiting_for_you=True)
        return out
    kind = sr["kind"]
    if kind in ("extract", "agent"):
        mine = [j for j in js if j["step_id"] == sid]
        done = [j for j in mine if j["status"] in JOB_DONE]
        failed = sum(1 for j in mine if j["status"] in ("failed", "cancelled"))
        out.update(done=len(done), total=len(mine))
        if st.get("source") == "supplied":
            # nothing is computed: the run waits for the owner's files on every supplied step not done yet
            # (a group takes each side's files at once) or for "Uploads complete"
            open_ids = {x["step_id"] for x in steps if x.get("status") not in ("done", "skipped")}
            uploads = []
            for s2 in (run.get("workflow") or {}).get("steps") or []:
                if s2.get("kind") in ("extract", "agent") and s2.get("source") == "supplied" and s2.get("id") in open_ids:
                    sj = [j for j in js if j["step_id"] == s2["id"]]
                    uploads.append({"step": s2["id"], "label": s2.get("label") or s2["id"], "role": s2.get("dataset"),
                                    "done": sum(1 for j in sj if j["status"] in JOB_DONE), "total": len(sj),
                                    "pending": sum(1 for j in sj if j["status"] == "pending")})
            out.update(phase="waiting for uploads", waiting_for_you=True, uploads=uploads)
            return out
        out["phase"] = "extracting"
        remaining = max(0, len(mine) - len(done) - failed)
        last = max((t for t in (_parse_iso(j.get("updated_at")) for j in done) if t), default=None)
        if done and started and last:
            out["eta_s"] = int(remaining * max(1.0, (last - started).total_seconds() / len(done)))
            out["basis"] = "this run"
        else:
            pace = history_pace(conn, "extract", sr.get("preset_schema_id"))
            if pace:
                out["eta_s"] = max(0, int(len(mine) * pace - (out["elapsed_s"] or 0)))
                out["basis"] = "earlier runs"
        return out
    if kind == "derive":
        planned = next((x.get("n_calls") for x in ((run.get("plan") or {}).get("steps") or []) if x.get("id") == sid), None) or 0
        out.update(phase="model calls" if planned else "building", total=planned or None)
        pace = history_pace(conn, "derive") if planned else None
        if pace:
            out["eta_s"] = max(0, int(planned * pace - (out["elapsed_s"] or 0)))
            out["basis"] = "earlier runs"
        return out
    out["phase"] = {"check": "checking", "crosscheck": "checking", "review": "collecting the review", "loop": "deciding whether to repeat"}.get(kind, "working")
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
    grp = group_of(conn, d)
    roles = ws.roles(spec)
    group = None
    if roles:
        group = {"id": (grp or {}).get("id"), "role": (grp or {}).get("role"),
                 "roles": [{"role": r, "step": info["step"], "label": info["label"], "preset": info["preset"],
                            "dataset": ((grp or {}).get("roles") or {}).get(r)} for r, info in roles.items()]}
    member_of = None
    if grp and grp.get("parent") and not is_workflow_dataset(d):
        member_of = {"id": grp["parent"]["id"], "title": grp["parent"]["title"], "role": d.get("group_role"),
                     "label": (roles.get(d.get("group_role")) or {}).get("label")}
    return {"group": group, "kind": d.get("kind") or "dataset", "member_of": member_of,
            "workflow": {"id": spec["id"], "contract": ws.workflow_id(spec), "version": spec.get("version", 1),
                         "title": (spec.get("meta") or {}).get("title"), "tagline": (spec.get("meta") or {}).get("tagline"),
                         "implicit": implicit, "declared": d.get("workflow"), "problems": problems, "has_card": ws.has_card(spec)},
            "steps": states, "current_run": run_view(conn, active) if active else None,
            "runs": [{k: r[k] for k in ("id", "status", "created_at", "finished_at", "credits", "workflow_release")} | {"n_papers": (r.get("plan") or {}).get("papers", {}).get("new")}
                     for r in list_runs(conn, dataset_id, limit=10)],
            "gate": {"open": not blocking and not review_gate.blocking_release(gates), "blocking": blocking,
                     "gates_blocking": [g["step_id"] for g in review_gate.blocking_release(gates)]},
            "available": workflows_for(conn, d) if owner else [], "owner": owner}


# ── workflow datasets: the page and the list ──────────────────────────────────

def review_asks(conn, dataset: dict) -> dict[str, list[str]]:
    """What the dataset's group workflow asks its joint review to verify, per linked dataset:
    ``{dataset_id: [entry fields]}`` from the review steps' ``asks`` (keyed there by extract step,
    whose dataset role names the linked dataset). Empty when nothing is declared."""
    spec = get_workflow(dataset.get("workflow"))
    grp = group_of(conn, dataset)
    if not spec or not grp:
        return {}
    by_id = ws.steps_by_id(spec)
    out: dict[str, list[str]] = {}
    for st in spec.get("steps") or []:
        if st.get("kind") != "review":
            continue
        for sid, fields in (st.get("asks") or {}).items():
            role = (by_id.get(sid) or {}).get("dataset")
            did = (grp["roles"].get(role) or {}).get("dataset_id") if role else None
            if did:
                out[did] = list(fields)
    return out


def overview(conn, parent: dict, *, owner: bool) -> dict:
    """A workflow dataset's page: the workflow, the linked datasets (one per preset, each with its
    counts and latest release), the papers — one row per PDF with each side's document beside
    it — and what the dataset-level steps made."""
    from . import derived, releases
    spec, _implicit = resolve(conn, parent)
    grp = group_of(conn, parent) or {"roles": {}}
    roles = ws.roles(spec)
    by_id = ws.steps_by_id(spec)
    members = []
    for role, info in roles.items():
        m = grp["roles"].get(role)
        pspec = _preset_spec(conn, ws.preset_base(info["preset"])) or {}
        item = {"role": role, "label": info["label"], "step": info["step"], "preset": info["preset"],
                "preset_id": ws.preset_base(info["preset"]), "preset_title": (pspec.get("meta") or {}).get("title"),
                "source": (by_id.get(info["step"]) or {}).get("source"), "dataset": None}
        if m:
            n = conn.execute("SELECT count(*), count(DISTINCT document_id) FROM record WHERE dataset_id = %s::uuid", (m["dataset_id"],)).fetchone()
            rel = releases.latest(conn, m["dataset_id"])
            item["dataset"] = {"id": m["dataset_id"], "title": m["title"], "schema_id": m["schema_id"], "n_records": n[0], "n_papers": n[1],
                               "release": {"number": rel["number"], "created_at": str(rel.get("created_at") or "")[:10],
                                           "doi": rel.get("doi")} if rel else None}
        members.append(item)
    ids = [m["dataset"]["id"] for m in members if m["dataset"]]
    papers: dict[str, dict] = {}
    if ids:
        for (role, sha, doc_id, filename, title, doi, year, n, n_ver, n_flag, made) in conn.execute(
                """SELECT d.group_role, ed.pdf_sha256, ed.id::text, ed.filename, p.title, p.doi, p.year,
                          count(r.id), count(r.id) FILTER (WHERE r.verification_status = 'verified'),
                          count(r.id) FILTER (WHERE r.verification_status = 'flagged'), min(ed.created_at)
                   FROM record r JOIN dataset d ON d.id = r.dataset_id
                   JOIN extraction_document ed ON ed.id = r.document_id
                   LEFT JOIN paper p ON p.id = ed.paper_id
                   WHERE r.dataset_id = ANY(%s::uuid[])
                   GROUP BY d.group_role, ed.pdf_sha256, ed.id, ed.filename, p.title, p.doi, p.year
                   ORDER BY min(ed.created_at)""", (ids,)).fetchall():
            key = sha or doc_id
            row = papers.setdefault(key, {"pdf_sha256": sha, "title": None, "doi": None, "year": None, "filename": None, "documents": {}})
            row["title"] = row["title"] or title
            row["doi"] = row["doi"] or doi
            row["year"] = row["year"] or year
            row["filename"] = row["filename"] or (filename if owner else None)
            row["documents"][role] = {"document_id": doc_id, "n_records": n, "n_verified": n_ver, "n_flagged": n_flag}
    linked_roles = [m["role"] for m in members if m["dataset"]]
    out_papers = list(papers.values())
    for row in out_papers:
        row["complete"] = all(r in row["documents"] for r in linked_roles)
        row["reviewed"] = bool(row["documents"]) and all(d["n_records"] and d["n_verified"] + d["n_flagged"] >= d["n_records"] for d in row["documents"].values())
    made = []
    for st in derived.steps_for(spec):
        did = step_datasets(conn, parent, spec).get(st["id"])
        if not did:
            continue
        stt = derived.state(conn, did, st)
        made.append({"step": st["id"], "label": st.get("label") or st["id"], "dataset_id": did, **{k: stt.get(k) for k in ("state", "because", "version", "draft_id", "derived_id", "committed_rows")}})
    meta = spec.get("meta") or {}
    return {"kind": "workflow", "id": parent["id"], "title": parent.get("title"), "description": parent.get("description"),
            "visibility": parent.get("visibility"), "owner_user_id": parent.get("owner_user_id"), "created_at": parent.get("created_at"),
            "workflow": {"id": spec["id"], "contract": ws.workflow_id(spec), "version": spec.get("version", 1), "title": meta.get("title"),
                         "tagline": meta.get("tagline"), "has_card": ws.has_card(spec)},
            "members": members, "papers": out_papers, "derived": made,
            "stats": {"n_papers": len(out_papers), "n_complete": sum(1 for r in out_papers if r["complete"]),
                      "n_reviewed": sum(1 for r in out_papers if r["reviewed"]),
                      "n_records": sum(m["dataset"]["n_records"] for m in members if m["dataset"])}}


def list_mine(conn, *, owner_user_id: str | None, session_id: str | None) -> list[dict]:
    """My workspace → Workflows: every workflow dataset of the caller and every dataset that runs
    a declared single-preset workflow, each with its datasets and what its latest run is doing."""
    uid = owner_user_id if records._is_uuid(owner_user_id or "") else None   # noqa: SLF001
    if not uid and not session_id:
        return []
    rows = conn.execute(
        """SELECT id::text FROM dataset d
           WHERE d.workflow IS NOT NULL AND (d.group_id IS NULL OR d.group_id = d.id)
             AND ((%s::uuid IS NOT NULL AND d.owner_user_id = %s::uuid) OR (%s::text IS NOT NULL AND d.session_id = %s AND d.owner_user_id IS NULL))
           ORDER BY coalesce(d.updated_at, d.created_at) DESC LIMIT 50""", (uid, uid, session_id, session_id)).fetchall()
    out = []
    for (did,) in rows:
        d = records.get_dataset(conn, did)
        if d is None:
            continue
        spec = get_workflow(d.get("workflow"))
        meta = (spec or {}).get("meta") or {}
        if is_workflow_dataset(d):
            grp = group_of(conn, d) or {"roles": {}}
            roles = ws.roles(spec) if spec else {}
            datasets = [{"role": r, "label": info["label"], "id": (grp["roles"].get(r) or {}).get("dataset_id"),
                         "title": (grp["roles"].get(r) or {}).get("title")} for r, info in roles.items()]
        else:
            datasets = [{"role": None, "label": None, "id": did, "title": d.get("title")}]
        run = active_run(conn, did) or next(iter(list_runs(conn, did, limit=1)), None)
        v = run_view(conn, run) if run else None
        out.append({"id": did, "kind": d.get("kind") or "dataset", "title": d.get("title"),
                    "workflow": {"id": (spec or {}).get("id") or d.get("workflow"), "title": meta.get("title") or d.get("workflow"),
                                 "version": (spec or {}).get("version")},
                    "datasets": datasets,
                    "run": {k: v.get(k) for k in ("id", "status", "created_at", "finished_at", "now", "progress")} if v else None})
    return out
