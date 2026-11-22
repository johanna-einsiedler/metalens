"""A project's review task is one per review step over every run that opened it: the project
page sums the runs' gates and lists every run's papers in its matrix, a coder's own task list
shows one tile per task (its share summed, the queue of the oldest batch still open), and a
queue whose papers are done points to the same task's next open batch. And the Workflow card's
step panel: one step's declaration and, run by run, the papers it ran on. Skips without Postgres."""
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
from test_projects_model import SPEC as PROJ
from test_workflow_datasets import CLAIMS
from test_workflows import _pdf, _result, _skip

from paperlens import records, workflows
from paperlens import workflow_spec as ws

SPEC = copy.deepcopy(PROJ)
SPEC["steps"].insert(2, {"id": "contract", "kind": "check", "check": "schema", "over": ["tables"], "label": "Contract check"})


def _run(owner, pid: str, n_papers: int, tag: str) -> str:
    """One run of the project with ``n_papers`` papers on each side; returns its id."""
    rid = owner.post(f"/api/datasets/{pid}/workflow/runs", json={}).json()["id"]
    for k in range(n_papers):
        t = f"{tag}{k}{uuid.uuid4().hex[:4]}"
        pdf = _pdf(t)
        cl = copy.deepcopy(CLAIMS); cl["paper_metadata"]["doi"] = f"10.1/{t}"; cl["paper_metadata"]["title"] = f"Paper {t}"
        res = _result(t, n=1); res["paper_metadata"]["title"] = f"Paper {t}"
        for step, doc in (("tables", res), ("claims", cl)):
            r = owner.post("/api/ingest-pdf", files={"pdf": (f"{t}.pdf", pdf, "application/pdf")}, data={"result": json.dumps(doc), "run_id": rid, "step_id": step})
            assert r.status_code == 200, r.text
    for step in ("tables", "claims"):
        view = owner.post(f"/api/workflow-runs/{rid}/complete", json={"step_id": step}).json()
    assert view["status"] == "done", view
    return rid


def test_a_task_is_one_per_review_step_over_every_run(monkeypatch, no_queue) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    spec = ws.normalize(copy.deepcopy(SPEC))
    assert ws.validate(spec)[0] == []
    monkeypatch.setattr(workflows, "get_workflow", lambda wid, _s=spec: _s if wid.startswith("proj") else None)
    owner, _oid, _ = _account("tr-owner")
    coder, coder_id, email = _account("tr-coder")
    pid = owner.post("/api/projects", json={"workflow": "proj", "title": "Two runs"}).json()["id"]
    for role in ("tables", "claims"):
        owner.post(f"/api/datasets/{pid}/group/link", json={"role": role, "create": True})
    assert owner.post(f"/api/projects/{pid}/people", json={"email": email}).status_code == 200
    r1 = _run(owner, pid, 2, "a")
    r2 = _run(owner, pid, 1, "b")
    # the project page: ONE coding task, both runs' papers in it
    ts = owner.get(f"/api/projects/{pid}/tasks").json()["tasks"]
    assert [t["step_id"] for t in ts] == ["coding"]
    t = ts[0]
    assert t["gate"]["total"] == 3 and t["gate"]["pending"] == 3 and t["gate"]["satisfied"] is False
    assert [x["run_id"] for x in t["runs"]] == [r2, r1] and [x["total"] for x in t["runs"]] == [1, 2]
    assert sorted(a["run_id"] for a in t["assignments"]) == sorted([r1, r1, r2]) and all(a["step_run_id"] for a in t["assignments"])
    assert t["queue"] == f"/workspace?queue={r1}:coding"                         # the oldest batch still open first
    assert [b["total"] for b in t["gate"]["by_coder"]] == [3]
    # one run named: that run's task alone (what a release's statistics read)
    one = owner.get(f"/api/projects/{pid}/tasks?run={r2}").json()["tasks"]
    assert len(one) == 1 and one[0]["gate"]["total"] == 1 and "runs" not in one[0]
    # the coder's own list: one tile, the share summed, the oldest open batch's queue
    mine = coder.get("/api/me/tasks").json()["tasks"]
    assert len(mine) == 1 and mine[0]["total"] == 3 and mine[0]["pending"] == 3 and mine[0]["papers"] == 3 and len(mine[0]["runs"]) == 2
    assert mine[0]["queue"] == f"/workspace?queue={r1}:coding"
    # the first batch's queue points on to the second when its papers are done
    q = coder.get(f"/api/workflow-runs/{r1}/queue/coding").json()
    assert [m["queue"] for m in q["more"]] == [f"{r2}:coding"] and q["more"][0]["pending"] == 1
    for it in q["items"]:
        assert coder.post(f"/api/records/{it['record_id']}/verify", json={"status": "verified"}).status_code == 200
    mine = coder.get("/api/me/tasks").json()["tasks"]
    assert mine[0]["pending"] == 1 and mine[0]["checked"] == 2 and mine[0]["queue"] == f"/workspace?queue={r2}:coding"
    q2 = coder.get(f"/api/workflow-runs/{r2}/queue/coding").json()
    assert q2["more"] == []                                                   # nothing left in the first batch
    t = owner.get(f"/api/projects/{pid}/tasks").json()["tasks"][0]
    assert t["gate"]["checked"] == 2 and t["gate"]["pending"] == 1 and t["queue"] == f"/workspace?queue={r2}:coding"
    assert owner.get(f"/api/projects/{pid}").json()["tasks"][0]["gate"]["total"] == 3
    # ── the step panel: one step's declaration, the papers each run ran it on ──
    d = owner.get(f"/api/datasets/{pid}/workflow/steps/tables").json()
    assert d["kind"] == "extract" and d["step"]["preset"] == "econ-regression-tables@67780825" and [r["run_id"] for r in d["runs"]] == [r2, r1]
    assert [len(r["papers"]) for r in d["runs"]] == [1, 2] and all(p["title"].startswith("Paper b") and p["n_records"] == 1 for p in d["runs"][0]["papers"])
    c = owner.get(f"/api/datasets/{pid}/workflow/steps/contract").json()
    assert c["check_info"]["label"] and [len(r["papers"]) for r in c["runs"]] == [1, 2] and all(p["status"] == "checked" and isinstance(p["findings"], dict) for r in c["runs"] for p in r["papers"])
    rv = owner.get(f"/api/datasets/{pid}/workflow/steps/coding").json()
    newest, oldest = rv["runs"]
    assert oldest["summary"]["checked"] == 2 and oldest["summary"]["satisfied"] is True and [p["status"] for p in oldest["papers"]] == ["done", "done"]
    assert newest["summary"]["pending"] == 1 and newest["papers"][0]["status"] == "open" and newest["papers"][0]["coders"][0]["label"] == "coder_1"
    assert newest["summary"]["queue"] == f"/workspace?queue={r2}:coding"
    rel = owner.get(f"/api/datasets/{pid}/workflow/steps/release").json()
    assert rel["kind"] == "release" and rel["step"]["gate"]
    assert owner.get(f"/api/datasets/{pid}/workflow/steps/nope").status_code == 404
    assert coder.get(f"/api/datasets/{pid}/workflow/steps/tables").status_code == 200          # a coder of the project reads it too
    stranger, _s, _e = _account("tr-stranger")
    assert stranger.get(f"/api/datasets/{pid}/workflow/steps/tables").status_code == 404
