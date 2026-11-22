"""User workflows: a workflow document saved from the editor — stored under a minted id beside
the bundled files, listed, read, edited and deleted by its owner through the API, shared when
public, refused when it pins someone else's private preset; a project runs one like a bundled
workflow. The grammar, checks and rule-suggestion endpoints the editor reads. Skips without
Postgres."""
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
from test_workflows import _pdf, _result, _skip

from paperlens import presets, records, user_workflows, workflows
from paperlens import workflow_spec as ws

PIN = "econ-regression-tables@67780825"
SPEC = {"format": 1, "id": "mine", "meta": {"title": "Mine", "tagline": "a workflow of my own"},
        "steps": [{"id": "tables", "kind": "extract", "preset": PIN, "source": "supplied"},
                  {"id": "contract", "kind": "check", "check": "schema", "over": ["tables"]},
                  {"id": "audit", "kind": "review", "over": ["tables"], "mode": "sample", "unit": "entry", "n": 1, "blocks": "release"}]}


def test_a_user_workflow_is_saved_listed_read_edited_shared_and_deleted() -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    owner, uid, _ = _account("uw-owner")
    other, _oid, _ = _account("uw-other")
    r = owner.post("/api/workflows", json={"spec": SPEC})
    assert r.status_code == 200, r.text
    w = r.json()
    assert w["personal"] and w["owned"] and w["visibility"] == "private" and w["version"] == 1
    assert w["id"].startswith("mine-") and len(w["id"]) == len("mine-") + 8 and w["spec"]["id"] == w["id"]
    assert w["contract"] == ws.workflow_id(w["spec"]) and w["contract"].startswith(w["id"] + "@") and w["presets"] == [PIN]
    wid = w["id"]
    # listed for the owner beside the bundled ones; not for anyone else while private
    mine = owner.get("/api/workflows").json()["workflows"]
    assert any(x["id"] == wid and x["personal"] and x["owned"] for x in mine) and any(x["id"] == "econ" and not x.get("personal") for x in mine)
    assert not any(x["id"] == wid for x in other.get("/api/workflows").json()["workflows"])
    assert other.get(f"/api/workflows/{wid}").status_code == 404 and other.get(f"/api/workflows/{wid}/card").status_code == 404
    d = owner.get(f"/api/workflows/{wid}").json()
    assert d["personal"] and d["owned"] and d["spec"]["id"] == wid and d["errors"] == [] and d["contract"] == w["contract"]
    conn.rollback()
    assert workflows.spec_for(conn, wid)["id"] == wid and workflows.get_workflow(wid) is None          # the row needs a connection; the files never shadow
    # the owner edits: a second check added with a parameter, the version moves and the contract follows
    spec2 = copy.deepcopy(SPEC); spec2["steps"].insert(2, {"id": "cells", "kind": "check", "check": "regression-cells", "over": ["tables"], "on_fail": "flag",
                                                           "params": {"t_tolerance": 0.2}})
    r = owner.patch(f"/api/workflows/{wid}", json={"spec": spec2})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and r.json()["contract"] != w["contract"] and [s["id"] for s in r.json()["steps"]] == ["tables", "contract", "cells", "audit", "release"]
    card = owner.get(f"/api/workflows/{wid}/card").json()
    assert card["personal"] and card["owned"] and card["releases"] == [] and card["cite_as"] is None and "numeric_mismatch" in next(s for s in card["steps"] if s["id"] == "cells")["check_info"]["codes"]
    # a bad document: every problem at once, path-prefixed
    bad = copy.deepcopy(SPEC); bad["steps"][2]["mode"] = "sometimes"; bad["steps"][1]["check"] = "nope"
    r = owner.patch(f"/api/workflows/{wid}", json={"spec": bad})
    assert r.status_code == 422 and r.json()["detail"]["message"] == "Invalid workflow"
    errs = r.json()["detail"]["errors"]
    assert any(e.startswith("$.steps[2].mode") for e in errs) and any(e.startswith("$.steps[1].check") for e in errs)
    assert owner.get(f"/api/workflows/{wid}").json()["version"] == 2                         # nothing stored
    # shared: public, readable and runnable by anyone, edited by the owner alone
    assert owner.patch(f"/api/workflows/{wid}", json={"visibility": "public"}).status_code == 200
    d = other.get(f"/api/workflows/{wid}").json()
    assert d["personal"] and d["owned"] is False and d["visibility"] == "public"
    assert other.patch(f"/api/workflows/{wid}", json={"visibility": "private"}).status_code == 403
    assert other.delete(f"/api/workflows/{wid}").status_code == 403
    assert any(x["id"] == wid and not x["owned"] for x in other.get("/api/workflows").json()["workflows"])
    # a bundled id is never shadowed, never edited, never deleted
    r = owner.post("/api/workflows", json={"spec": {**copy.deepcopy(SPEC), "id": "econ"}})
    assert r.status_code == 200 and r.json()["id"].startswith("econ-") and r.json()["id"] != "econ"
    assert owner.get("/api/workflows/econ").json()["personal"] is False
    assert owner.patch("/api/workflows/econ", json={"visibility": "public"}).status_code == 409
    assert owner.delete("/api/workflows/econ").status_code == 409
    # in use: a project runs it → 409; the project gone → deleted → 404
    made = owner.post("/api/projects", json={"workflow": wid, "title": "Runs mine"}).json()
    assert owner.delete(f"/api/workflows/{wid}").status_code == 409
    assert owner.delete(f"/api/datasets/{made['id']}").status_code == 200
    assert owner.delete(f"/api/workflows/{wid}").json() == {"deleted": 1}
    assert owner.get(f"/api/workflows/{wid}").status_code == 404


def _preset_spec(title: str) -> dict:
    return {"format": 2, "meta": {"title": title, "mode": "extraction"}, "prompt": {"text": "Extract each reported outcome with its confidence interval."}, "paper": {"fields": []},
            "entries": {"key": "records", "label": "Record", "cardinality": "many",
                        "fields": [{"name": "outcome", "type": "string", "label": "Outcome", "evidence": "row", "confidence": "main"},
                                   {"name": "ci_low", "type": "number", "label": "CI low", "evidence": "value", "confidence": "main"},
                                   {"name": "ci_high", "type": "number", "label": "CI high", "evidence": "value", "confidence": "main"}], "children": []},
            "confidence": {"levels": ["high", "medium", "low"], "notes": True, "groups": [{"id": "main", "label": "Main values", "scope": "entry"}]},
            "display": {"tabs": [{"id": "details", "label": "Details", "fields": ["outcome", "ci_low", "ci_high"]}], "triage": "low_confidence_first"}}


def test_a_pin_to_someone_elses_private_preset_is_refused_until_it_is_public() -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    owner, _uid, _ = _account("uw-pinner")
    other, _oid, _ = _account("uw-presetter")
    r = other.post("/api/presets", json={"spec": _preset_spec("Theirs " + uuid.uuid4().hex[:4]), "visibility": "private"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    conn.rollback()
    pin = presets.get(pid, conn)["schema_id"]
    spec = copy.deepcopy(SPEC); spec["steps"][0]["preset"] = pin; spec["steps"][0]["source"] = "live"; spec["steps"][2]["unit"] = "paper"
    r = owner.post("/api/workflows", json={"spec": spec})
    assert r.status_code == 422 and any("someone else's private preset" in e for e in r.json()["detail"]["errors"])
    assert other.post("/api/workflows", json={"spec": spec}).status_code == 200                # its owner may pin it
    assert other.patch(f"/api/presets/{pid}", json={"visibility": "public"}).status_code == 200
    assert owner.post("/api/workflows", json={"spec": spec}).status_code == 200
    # the rules a preset's field names imply, for the editor's "suggest" (an engine without the rules check answers with none)
    try:
        from paperlens import rules as _rules  # noqa: F401
        have_rules = True
    except ImportError:
        have_rules = False
    sug = owner.get(f"/api/presets/{pid}/rules").json()
    assert sug["schema_id"] == pin and [x["code"] for x in sug["rules"]] == (["ci_order"] if have_rules else [])
    assert owner.get("/api/presets/econ-regression-tables/rules").json()["rules"] == []
    assert owner.get("/api/presets/no-such-preset/rules").status_code == 404


def test_a_project_runs_a_user_workflow_like_a_bundled_one(monkeypatch, no_queue) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    owner, uid, _ = _account("uw-runner")
    wid = owner.post("/api/workflows", json={"spec": SPEC}).json()["id"]
    made = owner.post("/api/projects", json={"workflow": wid, "title": "Mine, run"}).json()
    ds = made["id"]
    assert made["kind"] != "workflow" and made["workflow"].startswith(wid + "@")              # one preset: a plain dataset carrying the workflow
    panel = owner.get(f"/api/datasets/{ds}/workflow").json()
    assert panel["workflow"]["id"] == wid and panel["workflow"]["implicit"] is False and panel["workflow"]["declared"] == made["workflow"]
    assert [s["id"] for s in panel["steps"]] == ["tables", "contract", "audit", "release"]
    run = owner.post(f"/api/datasets/{ds}/workflow/runs", json={}).json()
    assert run["status"] == "running", run
    for t in ("u0", "u1"):
        r = owner.post("/api/ingest-pdf", files={"pdf": (f"{t}.pdf", _pdf(f"{t}-{uuid.uuid4().hex[:4]}"), "application/pdf")},
                       data={"result": json.dumps(_result(t, n=2)), "run_id": run["id"], "step_id": "tables"})
        assert r.status_code == 200, r.text
    view = owner.post(f"/api/workflow-runs/{run['id']}/complete", json={"step_id": "tables"}).json()
    assert view["status"] == "done" and view["contract"] == made["workflow"]
    gate = next(s for s in view["steps"] if s["step_id"] == "audit")["review"]
    assert gate["mode"] == "sample" and gate["total"] == 1 and gate["satisfied"] is False      # one entry drawn, the release held
    # another dataset of the same preset is offered the owner's workflow beside the bundled ones
    plain = owner.post("/api/datasets", json={"title": "Plain", "schema_id": PIN}).json()["id"]
    avail = owner.get(f"/api/datasets/{plain}/workflow").json()["available"]
    assert any(a["id"] == wid for a in avail) and any(a["id"] == "econ-regressions" or a["id"] == "econ" for a in avail)
    assert owner.patch(f"/api/datasets/{plain}", json={"workflow": wid}).status_code == 200
    assert owner.get(f"/api/datasets/{plain}/workflow").json()["workflow"]["id"] == wid
    conn.rollback()
    assert user_workflows.declared_by(conn, wid) == sorted([ds, plain], key=lambda x: [ds, plain].index(x)) or set(user_workflows.declared_by(conn, wid)) == {ds, plain}


def test_the_editor_reads_the_grammar_and_the_checks_this_engine_accepts() -> None:
    _skip()
    owner, _uid, _ = _account("uw-reader")
    g = owner.get("/api/workflows/grammar").json()
    assert "review" in g["kinds"] and g["review"]["modes"] == ["all", "sample"] and g["review"]["units"] == ["entry", "paper", "row"]
    assert "release" in g["review"]["blocks"] and isinstance(g["review"]["purposes"], list) and g["on_fail"] == ["note", "flag", "fail"]
    c = {x["id"]: x for x in owner.get("/api/checks").json()["checks"]}
    assert c["refs"]["params"]["refs"]["type"] == "table" and c["schema"]["applies_to"]["presets"] == "*" and c["claims-vs-tables"]["needs_companion"]
    v = owner.post("/api/workflows/validate", json={"spec": SPEC}).json()
    assert v["ok"] and v["spec"]["steps"][-1]["kind"] == "release" and v["contract"].startswith("mine@")
    v = owner.post("/api/workflows/validate", json={"spec": {**SPEC, "steps": SPEC["steps"][:1]}}).json()
    assert v["ok"] and v["spec"] is not None
