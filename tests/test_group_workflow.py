"""A workflow over linked datasets: the tables and the claims of the same papers as a group,
one run that takes both files of a new paper into their own datasets, the checks reading across
the group, a joint review stage whose worklist walks each paper's documents together, and a
release of one member that releases the other. Skips without Postgres."""
from __future__ import annotations

import copy
import json
import os
import sys
import uuid

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import derived, providers, records, releases, review_gate, workflows  # noqa: E402
from paperlens import workflow_spec as ws  # noqa: E402
from test_analysis_table import _db_ok, _seed  # noqa: E402
from test_register_presets import CLAIMS  # noqa: E402
from test_regression_tables_preset import RESULT  # noqa: E402
from test_workflows import _client, _force_local, _pdf, _result, _sha, _who  # noqa: E402


def test_the_grammar_of_roles_and_the_joint_stage() -> None:
    spec = workflows.load_all()["econ"]
    assert ws.roles(spec) == {"tables": {"step": "tables", "preset": spec["steps"][0]["preset"], "label": "Regression tables"},
                              "claims": {"step": "claims", "preset": spec["steps"][1]["preset"], "label": "Claims and their results"}}
    assert ws.gate_steps(spec) == ["tables", "claims", "edges", "joint"] and workflows.later_problems(spec) == []
    joint = ws.steps_by_id(spec)["joint"]
    assert joint["review"] == {"mode": "all", "unit": "paper", "blocks": "release", "oversample_flagged": False}
    # two extract steps without roles are refused; a review stage must name earlier extract steps
    bad = copy.deepcopy(spec); del bad["steps"][1]["dataset"]
    assert any("distinct dataset role" in m for m in workflows.later_problems(bad))
    bad = copy.deepcopy(spec); bad["steps"][7]["over"] = ["edges"]
    assert any("not an earlier extract step" in e for e in ws.validate(bad)[0])
    bad = copy.deepcopy(spec); bad["steps"][0]["dataset"] = "Tables!"
    assert any("role name" in e for e in ws.validate(bad)[0])


def test_a_group_runs_both_sides_reviews_them_together_and_releases_as_one(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    sess = f"grp-{uuid.uuid4().hex[:6]}"
    c, H = _client(sess)
    who = _who(sess)
    # the tables dataset declares the group workflow: it takes the tables role; the claims role is open
    ds_t, _d = _seed(conn, _result("p0", n=1), "econ-regression-tables", sess)
    assert c.patch(f"/api/datasets/{ds_t}", headers=H, json={"workflow": "econ"}).status_code == 200
    conn.rollback()
    d_t = records.get_dataset(conn, ds_t)
    assert d_t["group_id"] and d_t["group_role"] == "tables" and d_t["workflow"].startswith("econ@")
    g = c.get(f"/api/datasets/{ds_t}/group", headers=H).json()
    assert [r["role"] for r in g["roles"]] == ["tables", "claims"] and g["roles"][0]["dataset"]["dataset_id"] == ds_t and g["roles"][1]["dataset"] is None
    panel = c.get(f"/api/datasets/{ds_t}/workflow", headers=H).json()
    by = {s["id"]: s for s in panel["steps"]}
    assert by["claims"]["state"] == "blocked" and "no dataset" in by["claims"]["because"][0] and panel["group"]["role"] == "tables"
    r = c.post(f"/api/datasets/{ds_t}/workflow/runs", headers=H, json={})
    assert r.status_code == 422 and "claims" in r.text
    # create the claims dataset into the role: it takes the workflow, the group, and the tables as its companion
    g = c.post(f"/api/datasets/{ds_t}/group/link", headers=H, json={"role": "claims", "create": True, "title": "Claims of the econ papers"}).json()
    ds_c = g["roles"]["claims"]["dataset_id"]
    conn.rollback()
    d_c = records.get_dataset(conn, ds_c)
    assert d_c["group_id"] == d_t["group_id"] and d_c["group_role"] == "claims" and d_c["workflow"] == d_t["workflow"]
    assert d_c["schema_id"] == workflows.load_all()["econ"]["steps"][1]["preset"] and d_c["companion_dataset_id"] == ds_t
    panel = c.get(f"/api/datasets/{ds_c}/workflow", headers=H).json()
    by = {s["id"]: s for s in panel["steps"]}
    assert by["claims"]["state"] == "missing" and by["tables"]["dataset_id"] == ds_t and by["links"]["state"] == "blocked"
    assert [x["role"] for x in panel["group"]["roles"]] == ["tables", "claims"]
    # one run from the tables side: a new paper's two files go into their own datasets
    pdf = _pdf("g1")
    run = c.post(f"/api/datasets/{ds_t}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": _sha(pdf), "filename": "g1.pdf"}]}).json()
    assert run["status"] == "running" and {j["step_id"] for j in run["papers"]} == {"tables", "claims"}
    tables_doc = _result("g1", n=2)
    claims_doc = copy.deepcopy(CLAIMS); claims_doc["paper_metadata"]["doi"] = "10.1/g1"; claims_doc["paper_metadata"]["title"] = "Paper g1"
    r = c.post("/api/ingest-pdf", headers=H, files={"pdf": ("g1.pdf", pdf, "application/pdf")},
               data={"result": json.dumps(tables_doc), "run_id": run["id"], "step_id": "tables"})
    assert r.status_code == 200, r.text
    t_doc = r.json()["document_id"]
    r = c.post("/api/ingest-pdf", headers=H, files={"pdf": ("g1.pdf", pdf, "application/pdf")},
               data={"result": json.dumps(claims_doc), "run_id": run["id"], "step_id": "claims"})
    assert r.status_code == 200, r.text
    c_doc = r.json()["document_id"]
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM record WHERE document_id = %s::uuid AND dataset_id = %s::uuid", (t_doc, ds_t)).fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM record WHERE document_id = %s::uuid AND dataset_id = %s::uuid", (c_doc, ds_c)).fetchone()[0] >= 1
    # the edges step wants a key; everything before it ran across the group
    view = c.get(f"/api/workflow-runs/{run['id']}", headers=H).json()
    assert view["status"] == "needs_key:edges", view["status"]
    steps = {s["step_id"]: s for s in view["steps"]}
    assert steps["links"]["status"] == "done" and steps["numbers"]["status"] == "done" and steps["contract_claims"]["status"] == "done"
    monkeypatch.setattr(providers, "generate_text", lambda model, key, prompt, **kw: json.dumps(
        {"rows": [{"cause_concept": "import_competition", "effect_concept": "employment", "sign": "-", "sources": ["r1"]}]}))
    view = c.post(f"/api/workflow-runs/{run['id']}/resume", headers=H, json={"api_key": "k", "model": "test-model"}).json()
    assert view["status"] == "done", view
    steps = {s["step_id"]: s for s in view["steps"]}
    assert steps["joint"]["status"] == "done" and steps["joint"]["outputs"]["document_ids"] == [t_doc, c_doc]
    assert not steps["joint"]["review"]["satisfied"] and steps["joint"]["review"]["unit"] == "paper"
    # the joint worklist: the paper's tables first, then its claims; it spans both datasets
    q = c.get(f"/api/workflow-runs/{run['id']}/queue/joint", headers=H).json()
    assert q["joint"] and set(q["datasets"]) == {ds_t, ds_c} and [d["document_id"] for d in q["documents"]] == [t_doc, c_doc]
    assert q["documents"][0]["role"] == "tables" and q["documents"][1]["role"] == "claims" and q["progress"]["papers"] == 1
    assert [it["document_id"] for it in q["items"]][:2] == [t_doc, t_doc] and q["items"][-1]["document_id"] == c_doc
    # the gate holds the release of either dataset; the claims page shows the same run
    for ds in (ds_t, ds_c):
        conn.rollback()
        gate = releases.pending(conn, ds)["gate"]
        assert not gate["open"] and "joint" in gate["gates_blocking"], (ds, gate)
    assert c.get(f"/api/datasets/{ds_c}/workflow", headers=H).json()["current_run"]["id"] == run["id"]
    # verdicts on every record of the paper (both sides), the edges draft committed → the gate opens
    for it in q["items"]:
        assert c.post(f"/api/records/{it['record_id']}/verify", headers=H, json={"status": "verified"}).status_code == 200
    draft = derived.latest_draft(conn, ds_c, "edges")
    assert draft is not None and c.post(f"/api/derived/{draft['id']}/commit", headers=H).status_code == 200
    conn.rollback()
    q = c.get(f"/api/workflow-runs/{run['id']}/queue/joint", headers=H).json()
    assert q["satisfied"] and q["progress"]["papers_done"] == 1
    for (rid,) in conn.execute("SELECT id FROM record WHERE dataset_id = %s::uuid", (ds_t,)).fetchall():
        records.verify_record(conn, str(rid), status="verified")
    conn.commit()
    gate = releases.pending(conn, ds_t)["gate"]
    assert gate["open"], gate
    # one release from the tables side freezes both datasets
    r = c.post(f"/api/datasets/{ds_t}/releases", headers=H, json={"notes": "both sides"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["number"] == 1 and {x["role"]: x["number"] for x in out["group"]} == {"tables": 1, "claims": 1} and out["problems"] == [], out
    conn.rollback()
    assert releases.latest(conn, ds_c)["number"] == 1 and releases.latest(conn, ds_c)["workflow_run_id"] == run["id"]
    snap = releases.get(conn, releases.latest(conn, ds_c)["id"], with_snapshot=True)["snapshot"]
    assert snap["workflow"]["run_id"] == run["id"] and snap["derived"][0]["step"] == "edges"
    assert c.get(f"/api/datasets/{ds_t}/workflow", headers=H).json()["gate"]["open"]
    conn.close()
