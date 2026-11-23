"""PROJECTS: a workflow's instance. One owner; the datasets it links (one per preset role); the
people who code for it; its runs; its review TASKS — the review step-runs of a run, each with
its template, the datasets it reads, its coders and who reviews which paper; its releases.

Internally a project is the workflow dataset row (``kind: "workflow"``: the row whose id is its
members' ``group_id``, carrying the workflow and holding no records). This module reads that
row as a project and names it so; the engine stays in ``workflows.py``, the people are
``coders.py``'s grants on the row, the gates are ``review_gate.py``'s."""
from __future__ import annotations

import psycopg

from . import coders, records, review_gate, workflows
from . import coders as coders_module
from . import workflow_spec as ws


def get(conn: psycopg.Connection, project_id: str) -> dict | None:
    """The project row, or None for an id that is not a project (a plain dataset, even one that
    runs a workflow on itself)."""
    d = records.get_dataset(conn, project_id) if records._is_uuid(project_id or "") else None   # noqa: SLF001
    return d if d and workflows.is_workflow_dataset(d) else None


def project_of(conn: psycopg.Connection, dataset: dict) -> dict | None:
    """The project a dataset belongs to (itself when it is one), else None."""
    if workflows.is_workflow_dataset(dataset):
        return dataset
    gid = dataset.get("group_id")
    return get(conn, gid) if gid else None


# ── what workflows.py already does, under the project's name ──────────────────

create = workflows.create_workflow_dataset
list_mine = workflows.list_mine
members = workflows.group_of
link_role = workflows.link_role
people = coders.coders_of
add_person = coders.add
remove_person = coders.remove


# ── tasks ─────────────────────────────────────────────────────────────────────

def _task(conn: psycopg.Connection, run: dict, sr: dict, st: dict, labels: dict) -> dict:
    rv = ws.review_of(st)
    header = sr.get("review") or {}
    state = review_gate.state(conn, sr) if header else None
    cfg = review_gate.task_config(conn, run.get("dataset_id"), sr["step_id"])
    out = {"step_run_id": sr["id"], "run_id": run["id"], "step_id": sr["step_id"], "label": st.get("label") or sr["step_id"], "help": st.get("help"),
           "config": cfg, "coders_from": header.get("coders_from"), "named": header.get("named"), "missing": header.get("missing"),
           "status": sr["status"], "over": list(st.get("over") or []), "datasets": list((sr.get("outputs") or {}).get("datasets") or []),
           "template": rv.get("template"), "template_id": header.get("template_id"), "unit": rv.get("unit"), "mode": rv.get("mode"),
           "table": rv.get("table"), "per_paper": rv.get("per_paper"), "where": rv.get("where"), "coders": rv.get("coders"),
           "blocks": rv.get("blocks"), "queue": f"/workspace?queue={run['id']}:{sr['step_id']}", "gate": state}
    if rv.get("coders") and header:
        asg = review_gate.assignments(conn, sr["id"])
        titles = {}
        if asg:
            for did, title, fn in conn.execute(
                    """SELECT ed.id::text, p.title, ed.filename FROM extraction_document ed LEFT JOIN paper p ON p.id = ed.paper_id
                       WHERE ed.id = ANY(%s::uuid[])""", (list(asg),)).fetchall():
                titles[did] = title or fn or did
        done = {}                                                   # per (document, coder): whether every item has a verdict
        for it in review_gate.items(conn, sr):
            k = (it.get("document_id"), it.get("coder_user_id"))
            done[k] = done.get(k, True) and bool(it.get("checked"))
        out["assignments"] = [{"document_id": did, "title": titles.get(did, did), "step_run_id": sr["id"], "run_id": run["id"],
                               "coders": [{"user_id": uid, "label": (labels.get(uid) or {}).get("label", "former"), "done": bool(done.get((did, uid)))} for uid in uids]}
                              for did, uids in asg.items()]
    return out


_DEAD_RUNS = ("cancelled", "failed", "planned")


def _run_tasks(conn: psycopg.Connection, project: dict, run: dict, labels: dict) -> list[dict]:
    by_id = ws.steps_by_id(run.get("workflow") or {})
    return [_task(conn, run, sr, by_id[sr["step_id"]], labels) for sr in workflows.step_runs(conn, run["id"])
            if sr["kind"] == "review" and sr["step_id"] in by_id]


def _merge_gates(gates: list[dict]) -> dict:
    """Several runs' gates of one review step as one: the counts summed, satisfied when every
    one is, each coder's share summed over the runs."""
    g = dict(gates[0])                                              # the newest run's declaration (mode, unit, template, …)
    for k in ("total", "checked", "pending", "disagreed"):
        g[k] = sum(int(x.get(k) or 0) for x in gates)
    for k in ("frame_n", "n_target", "drawn"):
        if all(x.get(k) is not None for x in gates):
            g[k] = sum(int(x.get(k) or 0) for x in gates)
    g["satisfied"] = all(bool(x.get("satisfied")) for x in gates)
    g["documents"] = [d for x in gates for d in (x.get("documents") or [])]
    if any(x.get("coders") for x in gates):
        acc: dict[str, dict] = {}
        for x in gates:
            for b in x.get("by_coder") or []:
                a = acc.setdefault(b["user_id"], {**b, "total": 0, "checked": 0, "pending": 0, "disagreed": 0, "papers": 0})
                for k in ("total", "checked", "pending", "disagreed", "papers"):
                    a[k] += int(b.get(k) or 0)
        g["by_coder"] = sorted(acc.values(), key=lambda a: a.get("label") or "")
        if any(x.get("plants") for x in gates):                     # the attention checks, summed per coder over the runs
            pl: dict[str, dict] = {}
            for x in gates:
                for b in x.get("plants") or []:
                    a = pl.setdefault(b["user_id"], {**b, "planted": 0, "caught": 0, "missed": 0, "open": 0})
                    for k in ("planted", "caught", "missed", "open"):
                        a[k] += int(b.get(k) or 0)
            g["plants"] = sorted(pl.values(), key=lambda a: a.get("label") or "")
        g["assigned"] = all(bool(x.get("assigned")) for x in gates if x.get("coders"))
        g["n_coders"] = max(int(x.get("n_coders") or 0) for x in gates)
        waits = [int(x["needs_coders"]) for x in gates if x.get("needs_coders")]
        if waits:
            g["needs_coders"] = max(waits)
        else:
            g.pop("needs_coders", None)
    return g


def _merge(ts: list[dict]) -> dict:
    """One review step over several runs (newest first): one task, its gate summed, the papers of
    every run in its matrix, the queue of the oldest run with papers still to check."""
    out = dict(ts[0])
    gates = [t["gate"] for t in ts if t.get("gate")]
    if gates:
        out["gate"] = _merge_gates(gates)
    out["assignments"] = [a for t in ts for a in (t.get("assignments") or [])]
    pending = [t for t in ts if (t.get("gate") or {}).get("pending")]
    out["queue"] = (pending[-1] if pending else ts[0])["queue"]     # the oldest batch still open first
    out["runs"] = [{"run_id": t["run_id"], "step_run_id": t["step_run_id"], "queue": t["queue"], "status": t["status"],
                    **{k: (t.get("gate") or {}).get(k) for k in ("total", "checked", "pending", "disagreed", "satisfied")}} for t in ts]
    return out


def tasks(conn: psycopg.Connection, project: dict, *, run: dict | None = None) -> list[dict]:
    """The review tasks of the project: one per review step of its workflow, over every run that
    opened it (not cancelled, failed or merely planned) — the gate summed over the runs, the papers
    of all of them, who reviews which. ``run`` names one run instead (its tasks alone: what a
    release's statistics read)."""
    labels = coders.labels_for(conn, project["id"])
    if run is not None:
        return _run_tasks(conn, project, run, labels)
    runs = [r for r in workflows.list_runs(conn, project["id"], limit=50) if r["status"] not in _DEAD_RUNS]   # newest first
    if not runs:                                                    # only dead runs: the latest one, as before
        last = workflows.active_run(conn, project["id"]) or next(iter(workflows.list_runs(conn, project["id"], limit=1)), None)
        return _run_tasks(conn, project, last, labels) if last else []
    per_step: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in runs:
        for t in _run_tasks(conn, project, r, labels):
            if t["step_id"] not in per_step:
                order.append(t["step_id"])
            per_step.setdefault(t["step_id"], []).append(t)
    spec_order = [s["id"] for s in (runs[0].get("workflow") or {}).get("steps") or [] if isinstance(s, dict)]
    order.sort(key=lambda sid: spec_order.index(sid) if sid in spec_order else len(spec_order))
    return [_merge(per_step[sid]) for sid in order]


def set_task_config(conn: psycopg.Connection, project: dict, step_id: str, coders) -> dict | None:
    """The owner's hand on a task: ``coders`` an integer (how many of the project's people review
    each paper, over the step's N) or a list of emails or user ids (which ones; each must be in
    the pool), or None to clear. A gate of the active run that still waits for coders assigns
    at once. Returns the stored config, or None when cleared."""
    pool = coders_module.coders_of(conn, project["id"])
    by_email = {c["email"].lower(): c["user_id"] for c in pool}
    ids = {c["user_id"] for c in pool}
    if coders is None:
        with conn.transaction():
            conn.execute("DELETE FROM project_task_config WHERE project_id = %s::uuid AND step_id = %s", (project["id"], step_id))
        stored = None
    else:
        if isinstance(coders, int) and not isinstance(coders, bool):
            if coders < 1:
                raise ValueError("coders must be at least 1")
            stored = coders
        elif isinstance(coders, list):
            stored = []
            for x in coders:
                uid = by_email.get(str(x).lower()) or (x if x in ids else None)
                if uid is None:
                    raise ValueError(f"{x} is not one of the project's people")
                if uid not in stored:
                    stored.append(uid)
            if not stored:
                raise ValueError("name at least one of the project's people")
        else:
            raise ValueError("coders must be a count or a list of the project's people")
        with conn.transaction():
            conn.execute("""INSERT INTO project_task_config (project_id, step_id, coders) VALUES (%s::uuid, %s, %s)
                            ON CONFLICT (project_id, step_id) DO UPDATE SET coders = EXCLUDED.coders, updated_at = now()""",
                         (project["id"], step_id, __import__("psycopg").types.json.Json(stored)))
    run = workflows.active_run(conn, project["id"]) or next(iter(workflows.list_runs(conn, project["id"], limit=1)), None)
    if run:
        for sr in workflows.step_runs(conn, run["id"]):
            if sr["step_id"] == step_id and (sr.get("review") or {}).get("coders") and not (sr.get("review") or {}).get("assigned"):
                review_gate.ensure_assigned(conn, sr)
    return {"coders": stored} if stored is not None else None


def tasks_for(conn: psycopg.Connection, user_id: str | None) -> list[dict]:
    """My review tasks: every task of a live run that assigned this account a paper, with what is
    left of their share, newest run first."""
    if not user_id or not records._is_uuid(user_id):   # noqa: SLF001
        return []
    rows = conn.execute(
        """SELECT DISTINCT a.run_id::text, a.step_id, a.step_run_id::text, r.dataset_id::text, d.title,
                  CASE WHEN d.group_id = d.id THEN 'workflow' ELSE 'dataset' END, r.created_at
           FROM workflow_assignment a JOIN workflow_run r ON r.id = a.run_id JOIN dataset d ON d.id = r.dataset_id
           JOIN dataset_coder c ON c.dataset_id = r.dataset_id AND c.user_id = a.coder_user_id          -- still in the project's pool
           WHERE a.coder_user_id = %s::uuid AND r.status NOT IN ('cancelled', 'failed', 'planned')
           ORDER BY r.created_at DESC, a.step_id""", (user_id,)).fetchall()
    out = []
    groups: dict[tuple, dict] = {}                                  # one task per project and review step, over its runs
    for run_id, step_id, srid, ds_id, title, kind, _created in rows:
        sr = workflows.get_step_run(conn, srid)
        run = workflows.get_run(conn, run_id)
        if sr is None or run is None:
            continue
        st = ws.steps_by_id(run.get("workflow") or {}).get(step_id) or {}
        header = sr.get("review") or {}
        mine = next((b for b in review_gate.by_coder(review_gate.items(conn, sr)) if b.get("user_id") == user_id), None)
        share = {"run_id": run_id, "step_run_id": srid, "queue": f"/workspace?queue={run_id}:{step_id}",
                 **{k: (mine or {}).get(k, 0) for k in ("papers", "total", "checked", "pending")}}
        g = groups.get((ds_id, step_id))
        if g is None:                                               # the newest run first: its wording stands for the task
            g = groups[(ds_id, step_id)] = {
                "run_id": run_id, "step_id": step_id, "step_run_id": srid, "label": st.get("label") or step_id, "help": st.get("help"),
                "project": {"id": ds_id, "title": title, "kind": kind or "dataset"},
                "template": header.get("template"), "unit": header.get("unit"), "mode": header.get("mode"),
                "run_status": run.get("status"), "papers": 0, "total": 0, "checked": 0, "pending": 0, "runs": []}
            out.append(g)
        for k in ("papers", "total", "checked", "pending"):
            g[k] += share[k]
        g["runs"].append(share)
    for g in out:                                                   # the queue: the oldest batch with papers still to check
        open_ = [s for s in g["runs"] if s["pending"]]
        g["queue"] = (open_[-1] if open_ else g["runs"][0])["queue"]
    return out


# ── the page ──────────────────────────────────────────────────────────────────

def overview(conn: psycopg.Connection, project: dict, *, owner: bool) -> dict:
    """The project page: the workflow dataset's overview (members, papers, derived units) plus the
    runs, the review tasks of the latest run and, for the owner, the people."""
    ov = workflows.overview(conn, project, owner=owner)
    ov["kind"] = "project"
    ov["runs"] = [{k: r[k] for k in ("id", "status", "created_at", "finished_at", "workflow_release")} for r in workflows.list_runs(conn, project["id"], limit=10)]
    ov["tasks"] = tasks(conn, project)
    if owner:
        ov["people"] = people(conn, project["id"])
    return ov
