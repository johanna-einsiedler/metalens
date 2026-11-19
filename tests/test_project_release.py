"""Project releases (paperlens/releases.py create_project, release_export.build_project): one number
for the project — every linked dataset under its own number (kept when unchanged), the workflow
and its run, the review statistics per task and coder, one badge over all; the gate; the export
with every member's files; what counts as a change. Skips without Postgres."""
from __future__ import annotations

import copy
import io
import json
import os
import sys
import uuid
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_coders import _account
from test_register_presets import CLAIMS
from test_workflows import _pdf, _result, _skip

from paperlens import checks, records, releases, workflows
from paperlens import workflow_spec as ws

SPEC = {"format": 1, "id": "prel", "meta": {"title": "Released as one"},
        "steps": [{"id": "tables", "kind": "extract", "dataset": "tables", "preset": "econ-regression-tables@67780825", "source": "supplied"},
                  {"id": "claims", "kind": "extract", "dataset": "claims", "preset": "register-claims@f7387875", "source": "supplied"},
                  {"id": "numbers", "kind": "check", "check": "regression-cells", "over": ["tables"], "on_fail": "note"},
                  {"id": "coding", "kind": "review", "label": "Code the tables", "over": ["tables"], "mode": "all", "unit": "paper", "coders": 1}]}


def _upload(owner, rid, tag):
    pdf = _pdf(f"prel-{tag}")
    cl = copy.deepcopy(CLAIMS); cl["paper_metadata"]["doi"] = f"10.1/prel{tag}"; cl["paper_metadata"]["title"] = f"Paper {tag}"
    for step, doc in (("tables", _result(f"prel{tag}", n=2)), ("claims", cl)):
        r = owner.post("/api/ingest-pdf", files={"pdf": (f"{tag}.pdf", pdf, "application/pdf")}, data={"result": json.dumps(doc), "run_id": rid, "step_id": step})
        assert r.status_code == 200, r.text


def _run(owner, pid, tag):
    rid = owner.post(f"/api/datasets/{pid}/workflow/runs", json={}).json()["id"]
    _upload(owner, rid, tag)
    for step in ("tables", "claims"):
        view = owner.post(f"/api/workflow-runs/{rid}/complete", json={"step_id": step}).json()
    assert view["status"] == "done", view
    return rid


def test_a_project_release_freezes_its_datasets_the_run_and_the_review_as_one(monkeypatch) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    spec = ws.normalize(copy.deepcopy(SPEC)); assert ws.validate(spec)[0] == []
    monkeypatch.setattr(workflows, "get_workflow", lambda wid, _s=spec: _s if wid.startswith("prel") else None)
    owner, _oid, _ = _account("prel-owner")
    a, a_id, a_email = _account("prel-a")
    pid = owner.post("/api/projects", json={"workflow": "prel", "title": "Released as one"}).json()["id"]
    members = {}
    for role in ("tables", "claims"):
        members[role] = owner.post(f"/api/datasets/{pid}/group/link", json={"role": role, "create": True}).json()["roles"][role]["dataset_id"]
    assert owner.post(f"/api/projects/{pid}/people", json={"email": a_email}).status_code == 200
    # nothing to release yet; a coder may look, not release
    assert owner.get(f"/api/projects/{pid}/releases").json() == {"releases": [], "head": {"changed": True}}
    assert a.get(f"/api/projects/{pid}/releases").json() == {"releases": []}
    assert a.post(f"/api/projects/{pid}/releases", json={}).status_code == 403
    rid = _run(owner, pid, uuid.uuid4().hex[:6])
    # the gate holds: the task waits for the coder's verdicts
    r = owner.post(f"/api/projects/{pid}/releases", json={"notes": "too early"})
    assert r.status_code == 409 and r.json()["detail"]["gate"]["gates_blocking"] == ["coding"], r.text
    q = a.get(f"/api/workflow-runs/{rid}/queue/coding").json()
    for it in q["items"]:
        assert a.post(f"/api/records/{it['record_id']}/verify", json={"status": "verified"}).status_code == 200
    # the release: both members under v1, the run, the task's statistics, one badge over both
    r = owner.post(f"/api/projects/{pid}/releases", json={"notes": "first cut"})
    assert r.status_code == 200, r.text
    rel = r.json()
    assert rel["number"] == 1 and rel["notes"] == "first cut" and rel["workflow_contract"] == ws.workflow_id(spec) and rel["workflow_run_id"] == rid
    assert {m["role"]: (m["number"], m["unchanged"]) for m in rel["members"]} == {"tables": (1, False), "claims": (1, False)}
    assert all(m["release_id"] and m["content_sha"] for m in rel["members"])
    t = rel["review"]["tasks"][0]
    assert rel["review"]["run_id"] == rid and t["step_id"] == "coding" and t["total"] == 2 and t["checked"] == 2 and t["disagreed"] == 0 and t["satisfied"] is True
    assert t["by_coder"] == [{"label": "coder_1", "total": 2, "checked": 2, "pending": 0, "disagreed": 0, "papers": 1}]
    cred = rel["credibility"]
    n_tables = 2; n_claims = len(CLAIMS["claims"])
    assert cred["n_datasets"] == 2 and cred["n_records"] == n_tables + n_claims and cred["audited"] == n_tables and cred["tier"] == "sample_verified"
    assert "Across 2 datasets" in cred["label_long"] and rel["stats"]["n_datasets"] == 2 and rel["stats"]["n_records"] == n_tables + n_claims
    assert rel["gates"] == {"blocking": [], "gates_blocking": [], "stale_overridden": False, "gates_overridden": False}
    assert rel["content_sha"] and "snapshot" not in rel
    conn.rollback()
    for ds in members.values():
        assert releases.latest(conn, ds)["number"] == 1 and releases.latest(conn, ds)["reason"] == "group"
    # listed; nothing moved: 409; the member's frozen findings are its project's run's, on its own records
    lst = owner.get(f"/api/projects/{pid}/releases").json()
    assert [x["number"] for x in lst["releases"]] == [1] and lst["head"]["changed"] is False
    assert owner.post(f"/api/projects/{pid}/releases", json={}).status_code == 409
    fz = checks.frozen(conn, members["tables"])
    assert fz is None or fz["run_id"] == rid
    assert owner.get(f"/api/projects/{pid}/releases/1").json()["number"] == 1 and owner.get(f"/api/projects/{pid}/releases/9").status_code == 404
    # the export: project.json, README.md, every member's own release files under members/<role>/
    z = owner.get(f"/api/projects/{pid}/releases/1/export")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    folder = names[0].split("/")[0]
    assert f"{folder}/project.json" in names and f"{folder}/README.md" in names
    assert f"{folder}/members/tables/release.json" in names and f"{folder}/members/claims/release.json" in names and f"{folder}/members/tables/codings.json" in names
    meta = json.loads(zipfile.ZipFile(io.BytesIO(z.content)).read(f"{folder}/project.json"))
    assert meta["kind"] == "project" and meta["release"]["number"] == 1 and {m["role"] for m in meta["members"]} == {"tables", "claims"}
    assert meta["review"]["tasks"][0]["by_coder"][0]["label"] == "coder_1" and all("release.json" in m["files"] for m in meta["members"])
    readme = owner.get(f"/api/projects/{pid}/releases/1/export?file=README.md").text
    assert "release v1" in readme and "Code the tables" in readme and "members/tables/" in readme
    assert owner.get(f"/api/projects/{pid}/releases/1/export?file=nope.json").status_code == 404
    # a second run with a new paper: the project moved; v2 releases both members anew, the claims too (a new paper on both sides)
    rid2 = _run(owner, pid, uuid.uuid4().hex[:6])
    assert owner.get(f"/api/projects/{pid}/releases").json()["head"]["changed"] is True
    r = owner.post(f"/api/projects/{pid}/releases", json={"notes": "second", "override_gates": True})
    assert r.status_code == 200, r.text
    rel2 = r.json()
    assert rel2["number"] == 2 and rel2["workflow_run_id"] == rid2 and {m["role"]: m["number"] for m in rel2["members"]} == {"tables": 2, "claims": 2}
    assert rel2["gates"]["gates_overridden"] is True and rel2["review"]["tasks"][0]["pending"] == 2
    assert rel2["content_sha"] != rel["content_sha"]
    # a project with nothing linked cannot release
    empty = owner.post("/api/projects", json={"workflow": "prel", "title": "Empty"}).json()["id"]
    assert owner.post(f"/api/projects/{empty}/releases", json={}).status_code == 409
    conn.close()
