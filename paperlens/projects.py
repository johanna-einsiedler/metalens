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
    out = {"step_run_id": sr["id"], "run_id": run["id"], "step_id": sr["step_id"], "label": st.get("label") or sr["step_id"], "help": st.get("help"),
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
        out["assignments"] = [{"document_id": did, "title": titles.get(did, did),
                               "coders": [{"user_id": uid, "label": (labels.get(uid) or {}).get("label", "former"), "done": bool(done.get((did, uid)))} for uid in uids]}
                              for did, uids in asg.items()]
    return out


def tasks(conn: psycopg.Connection, project: dict, *, run: dict | None = None) -> list[dict]:
    """The review tasks of a run of the project (the active run, else the latest): one per
    review step-run — its template, the datasets it reads, its gate, its coders and who reviews
    which paper."""
    run = run or workflows.active_run(conn, project["id"]) or next(iter(workflows.list_runs(conn, project["id"], limit=1)), None)
    if run is None:
        return []
    by_id = ws.steps_by_id(run.get("workflow") or {})
    labels = coders.labels_for(conn, project["id"])
    return [_task(conn, run, sr, by_id[sr["step_id"]], labels) for sr in workflows.step_runs(conn, run["id"])
            if sr["kind"] == "review" and sr["step_id"] in by_id]


def tasks_for(conn: psycopg.Connection, user_id: str | None) -> list[dict]:
    """My review tasks: every task of a live run that assigned this account a paper, with what is
    left of their share, newest run first."""
    if not user_id or not records._is_uuid(user_id):   # noqa: SLF001
        return []
    rows = conn.execute(
        """SELECT DISTINCT a.run_id::text, a.step_id, a.step_run_id::text, r.dataset_id::text, d.title,
                  CASE WHEN d.group_id = d.id THEN 'workflow' ELSE 'dataset' END, r.created_at
           FROM workflow_assignment a JOIN workflow_run r ON r.id = a.run_id JOIN dataset d ON d.id = r.dataset_id
           WHERE a.coder_user_id = %s::uuid AND r.status NOT IN ('cancelled', 'failed', 'planned')
           ORDER BY r.created_at DESC, a.step_id""", (user_id,)).fetchall()
    out = []
    for run_id, step_id, srid, ds_id, title, kind, _created in rows:
        sr = workflows.get_step_run(conn, srid)
        run = workflows.get_run(conn, run_id)
        if sr is None or run is None:
            continue
        st = ws.steps_by_id(run.get("workflow") or {}).get(step_id) or {}
        header = sr.get("review") or {}
        mine = next((b for b in review_gate.by_coder(review_gate.items(conn, sr)) if b.get("user_id") == user_id), None)
        out.append({"run_id": run_id, "step_id": step_id, "step_run_id": srid, "label": st.get("label") or step_id, "help": st.get("help"),
                    "project": {"id": ds_id, "title": title, "kind": kind or "dataset"},
                    "template": header.get("template"), "unit": header.get("unit"), "mode": header.get("mode"),
                    "run_status": run.get("status"), "papers": mine["papers"] if mine else 0, "total": mine["total"] if mine else 0,
                    "checked": mine["checked"] if mine else 0, "pending": mine["pending"] if mine else 0,
                    "queue": f"/workspace?queue={run_id}:{step_id}"})
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
