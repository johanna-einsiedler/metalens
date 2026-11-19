"""Projects (paperlens/projects.py): a workflow's instance by its own name — made from a
workflow, its datasets linked into their roles, its people, its review tasks with who reviews
which paper, and a coder's own task list. Skips without Postgres."""
from __future__ import annotations

import copy
import json
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_coders import _account
from test_workflow_datasets import CLAIMS
from test_workflows import _pdf, _result, _skip

from paperlens import projects, records, workflows
from paperlens import workflow_spec as ws

SPEC = {"format": 1, "id": "proj", "meta": {"title": "Two sides, one coder each"},
        "steps": [{"id": "tables", "kind": "extract", "dataset": "tables", "preset": "econ-regression-tables@67780825", "source": "supplied"},
                  {"id": "claims", "kind": "extract", "dataset": "claims", "preset": "register-claims@f7387875", "source": "supplied"},
                  {"id": "coding", "kind": "review", "label": "Code the tables", "over": ["tables"], "mode": "all", "unit": "paper", "coders": 1}]}


def test_a_project_is_made_from_a_workflow_has_people_and_tasks_and_a_coder_finds_their_own(monkeypatch) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    spec = ws.normalize(copy.deepcopy(SPEC))
    assert ws.validate(spec)[0] == []
    monkeypatch.setattr(workflows, "get_workflow", lambda wid, _s=spec: _s if wid.startswith("proj") else None)
    owner, owner_id, _ = _account("pj-owner")
    a, a_id, a_email = _account("pj-a")
    b, b_id, b_email = _account("pj-b")
    stranger, _sid, _ = _account("pj-stranger")
    # made from the workflow, listed, its roles filled from the page
    r = owner.post("/api/projects", json={"workflow": "proj", "title": "Round 1"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert r.json()["kind"] == "workflow" and r.json()["group_id"] == pid
    assert any(p["id"] == pid and p["title"] == "Round 1" for p in owner.get("/api/projects").json()["projects"])
    members = {}
    for role in ("tables", "claims"):
        g = owner.post(f"/api/datasets/{pid}/group/link", json={"role": role, "create": True}).json()
        members[role] = g["roles"][role]["dataset_id"]
    conn.rollback()
    assert projects.get(conn, pid)["id"] == pid and projects.get(conn, members["tables"]) is None
    assert projects.project_of(conn, records.get_dataset(conn, members["claims"]))["id"] == pid
    # the page: datasets, no run yet, the owner's people; a stranger sees nothing; a plain dataset is no project
    ov = owner.get(f"/api/projects/{pid}").json()
    assert ov["kind"] == "project" and [m["role"] for m in ov["members"]] == ["tables", "claims"] and ov["tasks"] == [] and ov["runs"] == []
    assert ov["people"] == [] and ov["viewer"] == {"is_owner": True, "is_coder": False, "user_id": owner_id}
    assert stranger.get(f"/api/projects/{pid}").status_code == 404 and owner.get(f"/api/projects/{members['tables']}").status_code == 404
    assert owner.get(f"/api/projects/{uuid.uuid4()}").status_code == 404
    # people: added by email, listed by label, seen by the project page; a coder of the project sees the page without the people
    assert owner.post(f"/api/projects/{pid}/people", json={"email": a_email}).json()["person"]["label"] == "coder_1"
    assert owner.post(f"/api/projects/{pid}/people", json={"email": b_email}).json()["person"]["label"] == "coder_2"
    assert [p["label"] for p in owner.get(f"/api/projects/{pid}/people").json()["people"]] == ["coder_1", "coder_2"]
    assert a.post(f"/api/projects/{pid}/people", json={"email": b_email}).status_code == 403
    cov = a.get(f"/api/projects/{pid}").json()
    assert "people" not in cov and cov["viewer"]["is_coder"] is True and cov["viewer"]["is_owner"] is False
    assert owner.patch(f"/api/projects/{pid}", json={"title": "Round 1 (pilot)"}).status_code == 200
    assert owner.get(f"/api/projects/{pid}").json()["title"] == "Round 1 (pilot)"
    # a run with one paper on each side: the coding task assigns the paper to one coder
    run = owner.post(f"/api/datasets/{pid}/workflow/runs", json={}).json(); rid = run["id"]
    tag = uuid.uuid4().hex[:6]; pdf = _pdf(f"pj-{tag}")
    cl = copy.deepcopy(CLAIMS); cl["paper_metadata"]["doi"] = f"10.1/pj{tag}"; cl["paper_metadata"]["title"] = f"Paper {tag}"
    for step, doc in (("tables", _result(f"pj{tag}", n=2)), ("claims", cl)):
        r = owner.post("/api/ingest-pdf", files={"pdf": (f"{tag}.pdf", pdf, "application/pdf")}, data={"result": json.dumps(doc), "run_id": rid, "step_id": step})
        assert r.status_code == 200, r.text
    for step in ("tables", "claims"):
        view = owner.post(f"/api/workflow-runs/{rid}/complete", json={"step_id": step}).json()
    assert view["status"] == "done", view
    ts = owner.get(f"/api/projects/{pid}/tasks").json()["tasks"]
    assert len(ts) == 1 and ts[0]["step_id"] == "coding" and ts[0]["label"] == "Code the tables" and ts[0]["coders"] == 1
    t = ts[0]
    assert t["datasets"] == [members["tables"]] and t["gate"]["assigned"] and t["gate"]["total"] == 2 and t["queue"] == f"/workspace?queue={rid}:coding"
    assert len(t["assignments"]) == 1 and len(t["assignments"][0]["coders"]) == 1 and t["assignments"][0]["coders"][0]["done"] is False
    who_codes = t["assignments"][0]["coders"][0]
    coder, other = ((a, b) if who_codes["user_id"] == a_id else (b, a))
    assert owner.get(f"/api/projects/{pid}").json()["tasks"][0]["step_run_id"] == t["step_run_id"]
    assert owner.get(f"/api/projects/{pid}/tasks?run={uuid.uuid4()}").status_code == 404
    # the coder's own task list: their share, where to code it; the other coder has none
    mine = coder.get("/api/me/tasks").json()["tasks"]
    assert len(mine) == 1 and mine[0]["pending"] == 2 and mine[0]["total"] == 2 and mine[0]["papers"] == 1 and mine[0]["queue"] == t["queue"]
    assert mine[0]["project"] == {"id": pid, "title": "Round 1 (pilot)", "kind": "workflow"} and mine[0]["label"] == "Code the tables"
    assert other.get("/api/me/tasks").json()["tasks"] == []
    # the owner moves the paper to the other coder (no verdict yet); a coder may not
    doc_id = t["assignments"][0]["document_id"]
    other_id = b_id if coder is a else a_id
    assert coder.post(f"/api/projects/{pid}/tasks/{t['step_run_id']}/assign", json={"step_id": "coding", "document_id": doc_id, "coders": [other_id]}).status_code == 403
    r = owner.post(f"/api/projects/{pid}/tasks/{t['step_run_id']}/assign", json={"step_id": "coding", "document_id": doc_id, "coders": [other_id]})
    assert r.status_code == 200, r.text
    assert coder.get("/api/me/tasks").json()["tasks"] == [] and other.get("/api/me/tasks").json()["tasks"][0]["pending"] == 2
    assert owner.post(f"/api/projects/{pid}/tasks/{uuid.uuid4()}/assign", json={"step_id": "coding", "document_id": doc_id, "coders": [a_id]}).status_code == 404
    # the verdicts close the task; the task list says so
    q = other.get(f"/api/workflow-runs/{rid}/queue/coding").json()
    for it in q["items"]:
        assert other.post(f"/api/records/{it['record_id']}/verify", json={"status": "verified"}).status_code == 200
    done = other.get("/api/me/tasks").json()["tasks"][0]
    assert done["pending"] == 0 and done["checked"] == 2
    t2 = owner.get(f"/api/projects/{pid}/tasks").json()["tasks"][0]
    assert t2["gate"]["satisfied"] is True and t2["assignments"][0]["coders"][0]["done"] is True
    # the pages
    assert owner.get(f"/project?id={pid}").status_code == 200 and "ds-body" in owner.get(f"/project?id={pid}").text
    assert "My review tasks" in owner.get("/projects").text
    # deleting the project frees its datasets
    assert a.delete(f"/api/projects/{pid}").status_code == 403
    assert owner.delete(f"/api/projects/{pid}").status_code == 200
    conn.rollback()
    assert records.get_dataset(conn, members["tables"])["group_id"] is None and owner.get(f"/api/projects/{pid}").status_code == 404
    conn.close()
