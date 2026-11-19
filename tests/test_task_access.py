"""Access follows the task (paperlens/coders.py, projects.py): a person added to a project sees
its page and nothing of its papers until a task assigns them one; then every paper of the
task's datasets; a plain dataset's grant is as it was; the owner's hand on a task — how many of
the people, or which — over the workflow's own number. Skips without Postgres."""
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
from test_register_presets import CLAIMS
from test_workflows import _pdf, _result, _skip

from paperlens import coders, records, workflows
from paperlens import workflow_spec as ws
from paperlens.principal import Principal

SPEC = {"format": 1, "id": "acc", "meta": {"title": "Access by task"},
        "steps": [{"id": "tables", "kind": "extract", "dataset": "tables", "preset": "econ-regression-tables@67780825", "source": "supplied"},
                  {"id": "claims", "kind": "extract", "dataset": "claims", "preset": "register-claims@f7387875", "source": "supplied"},
                  {"id": "two", "kind": "review", "label": "Two per table", "over": ["tables"], "mode": "all", "unit": "paper", "coders": 2}]}


def _project(owner, monkeypatch, spec):
    monkeypatch.setattr(workflows, "get_workflow", lambda wid, _s=spec: _s if wid.startswith(_s["id"]) else None)
    pid = owner.post("/api/projects", json={"workflow": spec["id"], "title": "Access"}).json()["id"]
    members = {}
    for role in ("tables", "claims"):
        members[role] = owner.post(f"/api/datasets/{pid}/group/link", json={"role": role, "create": True}).json()["roles"][role]["dataset_id"]
    return pid, members


def _upload(owner, rid, tag):
    pdf = _pdf(f"acc-{tag}")
    cl = copy.deepcopy(CLAIMS); cl["paper_metadata"]["doi"] = f"10.1/acc{tag}"; cl["paper_metadata"]["title"] = f"Paper {tag}"
    for step, doc in (("tables", _result(f"acc{tag}", n=2)), ("claims", cl)):
        r = owner.post("/api/ingest-pdf", files={"pdf": (f"{tag}.pdf", pdf, "application/pdf")}, data={"result": json.dumps(doc), "run_id": rid, "step_id": step})
        assert r.status_code == 200, r.text
    return r.json()["document_id"]


def test_a_person_reads_the_papers_of_their_task_and_nothing_before(monkeypatch) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    spec = ws.normalize(copy.deepcopy(SPEC)); assert ws.validate(spec)[0] == []
    owner, _oid, _ = _account("acc-owner")
    a, a_id, a_email = _account("acc-a")
    b, b_id, b_email = _account("acc-b")
    pid, members = _project(owner, monkeypatch, spec)
    for email in (a_email, b_email):
        assert owner.post(f"/api/projects/{pid}/people", json={"email": email}).status_code == 200
    conn.rollback()
    who_a = Principal(user_id=a_id, session_id=None)
    # in the pool, no task yet: the project page, not its papers
    assert a.get(f"/api/projects/{pid}").json()["viewer"]["is_coder"] is True
    assert coders.is_coder(conn, pid, who_a) and not coders.is_coder(conn, members["tables"], who_a) and not coders.is_coder(conn, members["claims"], who_a)
    assert a.get(f"/api/datasets/{members['tables']}/overview").status_code == 404
    assert a.get("/api/datasets?coder=1").json()["datasets"] == []
    # a run: the task over the tables assigns both; each reads every paper of the tables, nothing of the claims
    run = owner.post(f"/api/datasets/{pid}/workflow/runs", json={}).json(); rid = run["id"]
    tag = uuid.uuid4().hex[:6]
    doc = _upload(owner, rid, tag)
    for step in ("tables", "claims"):
        view = owner.post(f"/api/workflow-runs/{rid}/complete", json={"step_id": step}).json()
    assert view["status"] == "done"
    conn.rollback()
    assert coders.is_coder(conn, members["tables"], who_a) and not coders.is_coder(conn, members["claims"], who_a)
    mine = a.get("/api/datasets?coder=1").json()["datasets"]
    assert [d["id"] for d in mine] == [members["tables"]] and mine[0]["workflow_title"] == "Access"
    docs = a.get("/api/documents", params={"dataset": members["tables"]}).json()["documents"]
    tables_doc = next(d["document_id"] for d in docs)
    assert a.get(f"/api/documents/{tables_doc}/view").json()["coding"]["as"] == a_id
    assert a.get(f"/api/documents/{doc}/view").status_code == 404            # the claims document: not of the task
    assert a.get(f"/api/documents", params={"dataset": members["claims"]}).json()["documents"] == []
    # a second paper later in the same run's datasets: read too (every paper of the task's datasets)
    run2 = owner.post(f"/api/datasets/{pid}/workflow/runs", json={}).json()
    doc2 = _upload(owner, run2["id"], uuid.uuid4().hex[:6])
    for step in ("tables", "claims"):
        owner.post(f"/api/workflow-runs/{run2['id']}/complete", json={"step_id": step})
    assert len(a.get("/api/documents", params={"dataset": members["tables"]}).json()["documents"]) == 2
    # the task done, the run done: the coder keeps reading their own coding; taken out of the pool, nothing
    q = a.get(f"/api/workflow-runs/{rid}/queue/two").json()
    for it in q["items"]:
        assert a.post(f"/api/records/{it['record_id']}/verify", json={"status": "verified"}).status_code == 200
    assert a.get(f"/api/documents/{tables_doc}/view").json()["records"][0]["verification_status"] == "verified"
    assert owner.delete(f"/api/projects/{pid}/people/{a_id}").status_code == 200
    conn.rollback()
    assert not coders.is_coder(conn, members["tables"], who_a) and not coders.is_coder(conn, pid, who_a)
    assert a.get(f"/api/documents/{tables_doc}/view").status_code == 404
    assert a.get("/api/me/tasks").json()["tasks"] == []
    # a plain dataset's grant is as it was: every paper, at once
    plain = owner.post("/api/datasets", json={"title": "Plain", "schema_id": "econ-regression-tables@67780825", "visibility": "private"}).json()["id"]
    assert owner.post(f"/api/datasets/{plain}/coders", json={"email": a_email}).status_code == 200
    conn.rollback()
    assert coders.is_coder(conn, plain, who_a) and [d["id"] for d in a.get("/api/datasets?coder=1").json()["datasets"]] == [plain]
    conn.close()


def test_in_local_mode_the_local_owner_is_the_projects_one_person(monkeypatch) -> None:
    """One machine, one owner, no accounts: a task's gate assigns to the local owner, who is then
    listed as the project's one person and codes like any coder."""
    _skip()
    from fastapi.testclient import TestClient
    from paperlens import app as appmod, localmode
    conn = records.connect(); records.init_db(conn)
    monkeypatch.setenv("PAPERLENS_LOCAL_MODE", "1")
    row = conn.execute("SELECT id::text FROM users WHERE email = %s", (localmode.LOCAL_EMAIL,)).fetchone()   # an earlier test may have made the local owner under another id
    monkeypatch.setenv("PAPERLENS_LOCAL_USER_ID", row[0] if row else str(uuid.uuid4()))
    monkeypatch.delenv("PAPERLENS_BASIC_PASSWORD", raising=False)
    uid = localmode.ensure_local_user(conn)
    spec = ws.normalize(copy.deepcopy(SPEC)); spec["steps"][2]["coders"] = 1
    me = TestClient(appmod.app)
    pid, members = _project(me, monkeypatch, spec)
    assert me.get(f"/api/projects/{pid}/people").json()["people"] == []                 # nobody yet
    run = me.post(f"/api/datasets/{pid}/workflow/runs", json={}).json(); rid = run["id"]
    _upload(me, rid, uuid.uuid4().hex[:6])
    for step in ("tables", "claims"):
        r = me.post(f"/api/workflow-runs/{rid}/complete", json={"step_id": step})
        assert r.status_code == 200, r.text
    assert r.json()["status"] == "done", (r.json().get("error"), [(s_["step_id"], s_["status"], s_.get("error")) for s_ in r.json().get("steps") or []])
    conn.rollback()
    t = next(t for t in me.get(f"/api/projects/{pid}/tasks").json()["tasks"] if t["step_id"] == "two")
    assert t["gate"] and t["gate"]["assigned"] is True and t["gate"]["total"] == 2 and not t["gate"].get("needs_coders"), t   # the paper's two records, one coder
    assert [x["coders"][0]["user_id"] for x in t["assignments"]] == [uid] and t["assignments"][0]["coders"][0]["label"] == "coder_1"
    assert [p["user_id"] for p in me.get(f"/api/projects/{pid}/people").json()["people"]] == [uid]
    assert coders.is_coder(conn, pid, Principal(user_id=uid, session_id=None))
    q = me.get(f"/api/workflow-runs/{rid}/queue/two").json()
    assert q["viewer_coder"] == uid and len(q["items"]) == 2                                # their own share: the paper's two records


def test_the_owner_sets_how_many_of_the_people_a_task_uses_or_which(monkeypatch) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    spec = ws.normalize(copy.deepcopy(SPEC))
    owner, _oid, _ = _account("cfg-owner")
    a, a_id, a_email = _account("cfg-a")
    b, b_id, b_email = _account("cfg-b")
    pid, members = _project(owner, monkeypatch, spec)
    assert owner.post(f"/api/projects/{pid}/people", json={"email": a_email}).status_code == 200
    # one person, a step that wants two: the gate waits
    run = owner.post(f"/api/datasets/{pid}/workflow/runs", json={}).json(); rid = run["id"]
    _upload(owner, rid, uuid.uuid4().hex[:6])
    for step in ("tables", "claims"):
        owner.post(f"/api/workflow-runs/{rid}/complete", json={"step_id": step})
    t = owner.get(f"/api/projects/{pid}/tasks").json()["tasks"][0]
    assert t["gate"]["needs_coders"] == 2 and t["gate"]["assigned"] is False and t["config"] is None
    # the owner says one per paper: the waiting gate assigns at once, the config is on the task
    assert a.get("/api/me/tasks").json()["tasks"] == []
    r = owner.patch(f"/api/projects/{pid}/tasks/two", json={"coders": 1})
    assert r.status_code == 200, r.text
    t = r.json()["tasks"][0]
    assert t["config"] == {"coders": 1} and t["gate"]["assigned"] is True and t["gate"]["coders"] == 1 and t["coders_from"] == "project"
    assert a.get("/api/me/tasks").json()["tasks"][0]["pending"] == 2
    # refusals: a coder, an unknown step, a bad count, a stranger named
    assert a.patch(f"/api/projects/{pid}/tasks/two", json={"coders": 1}).status_code == 403
    assert owner.patch(f"/api/projects/{pid}/tasks/tables", json={"coders": 1}).status_code == 404
    assert owner.patch(f"/api/projects/{pid}/tasks/two", json={"coders": 0}).status_code == 422
    assert owner.patch(f"/api/projects/{pid}/tasks/two", json={"coders": ["nobody@coders.test"]}).status_code == 422
    # naming the people: a new run assigns the named ones only; one missing from the pool is said so
    assert owner.post(f"/api/projects/{pid}/people", json={"email": b_email}).status_code == 200
    r = owner.patch(f"/api/projects/{pid}/tasks/two", json={"coders": [b_email]})
    assert r.status_code == 200 and r.json()["config"] == {"coders": [b_id]}
    run2 = owner.post(f"/api/datasets/{pid}/workflow/runs", json={}).json()
    _upload(owner, run2["id"], uuid.uuid4().hex[:6])
    for step in ("tables", "claims"):
        owner.post(f"/api/workflow-runs/{run2['id']}/complete", json={"step_id": step})
    t2 = owner.get(f"/api/projects/{pid}/tasks").json()["tasks"][0]
    assert t2["run_id"] == run2["id"] and t2["named"] == ["coder_2"] and t2["gate"]["coders"] == 1 and t2["gate"]["assigned"] is True
    assert all(c["user_id"] == b_id for x in t2["assignments"] for c in x["coders"])
    assert b.get("/api/me/tasks").json()["tasks"][0]["run_id"] == run2["id"]
    # cleared: the step's own number again (and the pool), on the next run
    assert owner.patch(f"/api/projects/{pid}/tasks/two", json={"coders": None}).json()["config"] is None
    conn.close()
