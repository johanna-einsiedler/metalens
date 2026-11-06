"""Workflow datasets: a workflow over several presets lives on a dataset of its own. Its id is
the group id of the linked datasets (one per preset), it carries the workflow, holds no records,
lists each paper with both sides beside it, starts the runs and releases its members. Also: the
startup step that gives an older group its workflow dataset, the My workspace list, and what a
run says it is doing now (with a measured estimate). Skips without Postgres."""
from __future__ import annotations

import copy
import json
import os
import sys
import uuid
from datetime import timedelta

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import records, workflows  # noqa: E402
from test_analysis_table import _db_ok, _seed  # noqa: E402
from test_register_presets import CLAIMS  # noqa: E402
from test_workflows import _client, _force_local, _pdf, _result, _sha  # noqa: E402


def _skip():
    if not _db_ok():
        pytest.skip("no Postgres")


def test_a_workflow_dataset_holds_the_linked_datasets_and_their_papers(monkeypatch) -> None:
    _skip()
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    sess = f"wfd-{uuid.uuid4().hex[:6]}"
    c, H = _client(sess)
    ds_t, _doc = _seed(conn, _result("p0", n=1), "econ-regression-tables", sess)
    # "add to a workflow" from the tables dataset: a workflow dataset above it, the tables in their role
    r = c.post("/api/workflow-datasets", headers=H, json={"workflow": "econ", "datasets": {"tables": ds_t}})
    assert r.status_code == 200, r.text
    wd = r.json()
    assert wd["kind"] == "workflow" and wd["group_id"] == wd["id"] and wd["workflow"].startswith("econ@")
    conn.rollback()
    d_t = records.get_dataset(conn, ds_t)
    assert d_t["group_id"] == wd["id"] and d_t["group_role"] == "tables" and d_t["kind"] == "dataset" and d_t["workflow"] == wd["workflow"]
    # a dataset already in a workflow cannot be linked into a second one
    r = c.post("/api/workflow-datasets", headers=H, json={"workflow": "econ", "datasets": {"tables": ds_t}})
    assert r.status_code == 409
    # it is listed under Workflows, never as a dataset to put papers into
    mine = [x["id"] for x in c.get("/api/datasets", headers=H).json()["datasets"]]
    assert ds_t in mine and wd["id"] not in mine
    wl = {x["id"]: x for x in c.get("/api/workflow-datasets", headers=H).json()["workflows"]}
    assert wd["id"] in wl and ds_t not in wl
    assert [(x["role"], x["id"]) for x in wl[wd["id"]]["datasets"]] == [("tables", ds_t), ("claims", None)]
    # its page: the workflow, the linked datasets, the papers
    ov = c.get(f"/api/datasets/{wd['id']}/overview", headers=H).json()
    assert ov["kind"] == "workflow" and [m["role"] for m in ov["members"]] == ["tables", "claims"]
    assert ov["members"][0]["dataset"]["id"] == ds_t and ov["members"][1]["dataset"] is None
    assert ov["stats"]["n_papers"] == 1 and list(ov["papers"][0]["documents"]) == ["tables"] and ov["papers"][0]["complete"]
    # the claims role filled from the workflow dataset's page
    assert wd["title"] == "Analysis set (workflow)"
    g = c.post(f"/api/datasets/{wd['id']}/group/link", headers=H, json={"role": "claims", "create": True}).json()
    ds_c = g["roles"]["claims"]["dataset_id"]
    assert g["parent"]["id"] == wd["id"] and g["roles"]["claims"]["title"] == "Analysis set — Claims and their results"
    # a member's panel names its workflow dataset; the workflow dataset's own panel runs the steps
    member = c.get(f"/api/datasets/{ds_c}/workflow", headers=H).json()
    assert member["member_of"] == {"id": wd["id"], "title": wd["title"], "role": "claims", "label": "Claims and their results"}
    panel = c.get(f"/api/datasets/{wd['id']}/workflow", headers=H).json()
    by = {s["id"]: s for s in panel["steps"]}
    assert panel["kind"] == "workflow" and panel["member_of"] is None and by["tables"]["dataset_id"] == ds_t and by["claims"]["dataset_id"] == ds_c
    # a run from the workflow dataset: it waits for the uploads and says so
    pdf = _pdf("w1")
    run = c.post(f"/api/datasets/{wd['id']}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": _sha(pdf), "filename": "w1.pdf"}]}).json()
    assert run["status"] == "running" and run["now"]["step"] == "tables" and run["now"]["phase"] == "waiting for uploads"
    assert run["now"]["done"] == 0 and run["now"]["total"] == 1 and run["now"]["eta_s"] is None
    claims_doc = copy.deepcopy(CLAIMS); claims_doc["paper_metadata"]["doi"] = "10.1/w1"; claims_doc["paper_metadata"]["title"] = "Paper w1"
    for step, doc in (("tables", _result("w1", n=1)), ("claims", claims_doc)):
        r = c.post("/api/ingest-pdf", headers=H, files={"pdf": ("w1.pdf", pdf, "application/pdf")},
                   data={"result": json.dumps(doc), "run_id": run["id"], "step_id": step})
        assert r.status_code == 200, r.text
    view = c.get(f"/api/workflow-runs/{run['id']}", headers=H).json()
    assert view["status"] == "done", view["status"]           # no dataset-level step: the checks ran, the joint review holds only the release
    assert {s["step_id"]: s["status"] for s in view["steps"]}["links"] == "done"
    # both sides of the new paper sit on one row of the workflow dataset's page; the old paper has only its tables
    ov = c.get(f"/api/datasets/{wd['id']}/overview", headers=H).json()
    rows = {p["title"]: p for p in ov["papers"]}
    assert set(rows["Paper w1"]["documents"]) == {"tables", "claims"} and rows["Paper w1"]["complete"]
    assert not rows["Paper p0"]["complete"] and ov["stats"]["n_complete"] == 1 and ov["stats"]["n_papers"] == 2
    assert ov["derived"] == []
    wl = {x["id"]: x for x in c.get("/api/workflow-datasets", headers=H).json()["workflows"]}
    assert wl[wd["id"]]["run"]["status"] == "done"
    # a workflow dataset is never made public itself, and keeps its workflow
    assert c.patch(f"/api/datasets/{wd['id']}", headers=H, json={"visibility": "public"}).status_code == 422
    assert c.patch(f"/api/datasets/{wd['id']}", headers=H, json={"workflow": ""}).status_code == 409
    # its release freezes the linked datasets; the open gate is reported per member, overriding it releases both
    c.post(f"/api/workflow-runs/{run['id']}/cancel", headers=H)
    r = c.post(f"/api/datasets/{wd['id']}/releases", headers=H, json={"notes": "first"})
    assert r.status_code == 200 and r.json()["group"] == [] and len(r.json()["problems"]) == 2, r.text
    r = c.post(f"/api/datasets/{wd['id']}/releases", headers=H, json={"notes": "first", "allow_stale": True, "override_gates": True})
    assert r.status_code == 200 and {x["role"]: x["number"] for x in r.json()["group"]} == {"tables": 1, "claims": 1}, r.text
    assert c.post(f"/api/datasets/{wd['id']}/releases", headers=H, json={"allow_stale": True, "override_gates": True}).status_code == 409
    # deleting the workflow dataset frees its members: their records stay, the group goes
    assert c.delete(f"/api/datasets/{wd['id']}", headers=H).status_code == 200
    conn.rollback()
    for ds in (ds_t, ds_c):
        d = records.get_dataset(conn, ds)
        assert d["group_id"] is None and d["group_role"] is None and d["workflow"] is None
    assert conn.execute("SELECT count(*) FROM record WHERE dataset_id = %s::uuid", (ds_t,)).fetchone()[0] == 2
    conn.close()


def test_a_workflow_dataset_made_from_scratch_fills_its_roles_from_the_page(monkeypatch) -> None:
    _skip()
    _force_local(monkeypatch)
    sess = f"wfe-{uuid.uuid4().hex[:6]}"
    c, H = _client(sess)
    r = c.post("/api/workflow-datasets", headers=H, json={"workflow": "econ", "title": "Validation — A"})
    assert r.status_code == 200, r.text
    wd = r.json()
    g = c.get(f"/api/datasets/{wd['id']}/group", headers=H).json()
    assert [x["role"] for x in g["roles"]] == ["tables", "claims"] and all(x["dataset"] is None for x in g["roles"])   # link or make, per role
    assert c.post(f"/api/datasets/{wd['id']}/workflow/runs", headers=H, json={}).status_code == 422   # nothing to run on yet
    for role in ("tables", "claims"):                     # "make one" (the page then imports or extracts into it)
        g = c.post(f"/api/datasets/{wd['id']}/group/link", headers=H, json={"role": role, "create": True}).json()
    titles = [g["roles"][r]["title"] for r in ("tables", "claims")]
    assert titles == ["Validation — A — Regression tables", "Validation — A — Claims and their results"], titles
    conn = records.connect()
    ds_t, ds_c = [g["roles"][r]["dataset_id"] for r in ("tables", "claims")]
    assert records.get_dataset(conn, ds_c)["companion_dataset_id"] == ds_t     # the claims check reads the tables
    run = c.post(f"/api/datasets/{wd['id']}/workflow/runs", headers=H, json={}).json()
    assert run["status"] == "running" and run["now"]["phase"] == "waiting for uploads"    # a run starts at once: it waits for the files
    # files arriving one by one, none planned: a side's step waits for Uploads complete, not for its first file
    for i in range(2):
        pdf = _pdf(f"e{i}")
        r = c.post("/api/ingest-pdf", headers=H, files={"pdf": (f"e{i}.pdf", pdf, "application/pdf")},
                   data={"result": json.dumps(_result(f"e{i}", n=1)), "run_id": run["id"], "step_id": "tables"})
        assert r.status_code == 200, r.text
        view = c.get(f"/api/workflow-runs/{run['id']}", headers=H).json()
        assert view["status"] == "running" and view["now"]["step"] == "tables" and view["now"]["phase"] == "waiting for uploads", (i, view["status"], view["now"])
    cl = copy.deepcopy(CLAIMS); cl["paper_metadata"]["doi"] = "10.1/e0"; cl["paper_metadata"]["title"] = "Paper e0"
    r = c.post("/api/ingest-pdf", headers=H, files={"pdf": ("e0.pdf", _pdf("e0"), "application/pdf")}, data={"result": json.dumps(cl), "run_id": run["id"], "step_id": "claims"})
    assert r.status_code == 200, r.text
    assert c.get(f"/api/workflow-runs/{run['id']}", headers=H).json()["status"] == "running"
    for step in ("tables", "claims"):
        view = c.post(f"/api/workflow-runs/{run['id']}/complete", headers=H, json={"step_id": step}).json()
    assert view["status"] == "done" and {j["step_id"] for j in view["papers"]} == {"tables", "claims"} and len(view["papers"]) == 3, view["status"]
    # a second run over what the datasets hold (nothing uploaded): the joint review covers every paper of both sides,
    # the panel offers the review, the gate holds the release until it is done
    run2 = c.post(f"/api/datasets/{wd['id']}/workflow/runs", headers=H, json={}).json()
    for step in ("tables", "claims"):
        view = c.post(f"/api/workflow-runs/{run2['id']}/complete", headers=H, json={"step_id": step}).json()
    assert view["status"] == "done"
    q = c.get(f"/api/workflow-runs/{run2['id']}/queue/joint", headers=H).json()
    n_rec = sum(x["n_records"] for x in view["papers"]) if view["papers"] else None
    assert q["joint"] and q["progress"]["drawn"] >= 3 and q["progress"]["pending"] == q["progress"]["drawn"] and set(q["datasets"]) == {ds_t, ds_c}, q["progress"]
    by = {s["id"]: s for s in c.get(f"/api/datasets/{wd['id']}/workflow", headers=H).json()["steps"]}
    assert by["joint"]["gate"]["pending"] == q["progress"]["drawn"] and by["joint"]["gate"]["run_id"] == run2["id"]
    assert c.get(f"/api/datasets/{wd['id']}/releases/pending", headers=H).json()["gate"]["open"] is False
    conn.close()


def test_a_single_preset_workflow_gets_a_plain_dataset(monkeypatch) -> None:
    _skip()
    _force_local(monkeypatch)
    sess = f"wfs-{uuid.uuid4().hex[:6]}"
    c, H = _client(sess)
    r = c.post("/api/workflow-datasets", headers=H, json={"workflow": "masem", "title": "My MASEM"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "dataset" and d["group_id"] is None and d["title"] == "My MASEM" and d["workflow"].startswith("masem@")
    conn = records.connect()
    assert records.get_dataset(conn, d["id"])["schema_id"] == workflows.load_all()["masem"]["steps"][0]["preset"]
    wl = {x["id"]: x for x in c.get("/api/workflow-datasets", headers=H).json()["workflows"]}
    assert wl[d["id"]]["datasets"] == [{"role": None, "label": None, "id": d["id"], "title": "My MASEM"}]
    conn.close()


def test_an_older_group_gets_its_workflow_dataset_at_startup() -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    sess = f"wfm-{uuid.uuid4().hex[:6]}"
    gid = str(uuid.uuid4())
    a = records.create_dataset(conn, title="Tables of the old group", session_id=sess)
    b = records.create_dataset(conn, title="Claims of the old group", session_id=sess)
    with conn.transaction():
        for did, role in ((a["id"], "tables"), (b["id"], "claims")):
            conn.execute("UPDATE dataset SET group_id = %s::uuid, group_role = %s, workflow = 'econ@00000000' WHERE id = %s::uuid", (gid, role, did))
    conn.commit()
    records.init_db(conn); records.init_db(conn)               # idempotent: one parent, however often it runs
    p = records.get_dataset(conn, gid)
    assert p is not None and p["kind"] == "workflow" and p["workflow"] == "econ@00000000" and p["session_id"] == sess
    assert p["title"] == "Tables of the old group (workflow)"
    assert conn.execute("SELECT count(*) FROM dataset WHERE group_id = %s::uuid", (gid,)).fetchone()[0] == 3
    assert workflows.group_of(conn, records.get_dataset(conn, a["id"]))["parent"] == {"id": gid, "title": p["title"]}
    conn.close()


def test_a_run_says_what_it_is_doing_and_measures_how_long_it_still_takes() -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    now = workflows._now()
    iso = lambda dt: dt.isoformat(timespec="seconds")   # noqa: E731
    spec = {"steps": [{"id": "x", "kind": "extract", "source": "live", "label": "Extraction"}, {"id": "e", "kind": "derive"}]}
    run = {"status": "running", "current_step": "x", "workflow": spec, "plan": {"steps": [{"id": "e", "n_calls": 3}]}}
    steps = [{"step_id": "x", "kind": "extract", "started_at": iso(now - timedelta(seconds=120)), "preset_schema_id": "nope@00000000", "review": {}}]
    js = [{"step_id": "x", "status": "done", "updated_at": iso(now - timedelta(seconds=60))},
          {"step_id": "x", "status": "done", "updated_at": iso(now - timedelta(seconds=30))},
          {"step_id": "x", "status": "queued", "updated_at": None}, {"step_id": "x", "status": "pending", "updated_at": None},
          {"step_id": "x", "status": "failed", "updated_at": None}]
    n = workflows.now_running(conn, run, steps, js)
    # two papers landed 90 s after the start: 45 s a paper, two still to go (the failed one is not waited for)
    assert n["label"] == "Extraction" and n["phase"] == "extracting" and (n["done"], n["total"]) == (2, 5) and not n["waiting_for_you"]
    assert n["eta_s"] == 90 and n["basis"] == "this run" and 119 <= n["elapsed_s"] <= 125
    # nothing landed and no earlier run of this contract: no number is invented
    n = workflows.now_running(conn, run, steps, [dict(j, status="queued") for j in js])
    assert n["eta_s"] is None and n["basis"] is None
    # a step that waits for people has no estimate; a finished run is doing nothing
    assert workflows.now_running(conn, {**run, "status": "needs_key:e", "current_step": "e"},
                                 [{"step_id": "e", "kind": "derive", "started_at": None, "review": {}}], [])["waiting_for_you"]
    assert workflows.now_running(conn, {**run, "status": "done"}, steps, js) is None
    # an uploaded step waits for its owner: nothing is computed, no estimate, and every supplied step
    # still open (a group takes both sides' files at once) says what it has received so far
    sup = {"steps": [{"id": "t", "kind": "extract", "source": "supplied", "label": "Tables", "dataset": "tables"},
                     {"id": "c", "kind": "extract", "source": "supplied", "label": "Claims", "dataset": "claims"}]}
    srs = [{"step_id": "t", "kind": "extract", "status": "running", "started_at": iso(now - timedelta(minutes=35)), "review": {}},
           {"step_id": "c", "kind": "extract", "status": "pending", "started_at": None, "review": {}}]
    n = workflows.now_running(conn, {"status": "running", "current_step": "t", "workflow": sup, "plan": {}}, srs,
                              [{"step_id": "c", "status": "done", "updated_at": None}])
    assert n["waiting_for_you"] and n["phase"] == "waiting for uploads" and n["eta_s"] is None and n["elapsed_s"] >= 35 * 60
    assert [(u["step"], u["role"], u["done"], u["total"], u["pending"]) for u in n["uploads"]] == [("t", "tables", 0, 0, 0), ("c", "claims", 1, 1, 0)]
    conn.close()
