"""A review gate reviewed by coders (`review.coders: N`): every paper of the gate assigned to N
coders, evenly; the gate waits for coders when there are too few; each coder's queue is their
own; the gate is satisfied by the coders' verdicts, never the record's status; the owner may
reassign a paper. Skips without Postgres."""
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
from test_workflows import _pdf, _result, _sha, _skip

from paperlens import records, review_gate, workflows
from paperlens import workflow_spec as ws

SPEC = {"format": 1, "id": "coded", "meta": {"title": "Coded"},
        "steps": [{"id": "tables", "kind": "extract", "preset": "econ-regression-tables@67780825", "source": "supplied",
                   "review": {"mode": "all", "unit": "paper", "coders": 2, "blocks": "release"}}]}


def _declare(conn, ds: str, spec: dict, monkeypatch) -> None:
    monkeypatch.setattr(workflows, "get_workflow", lambda wid, _s=spec: _s if wid.startswith(_s["id"]) else None)
    conn.execute("UPDATE dataset SET workflow = %s WHERE id = %s::uuid", (ws.workflow_id(spec), ds)); conn.commit()


def test_the_grammar_takes_coders_and_the_contract_moves_with_it() -> None:
    spec = ws.normalize(copy.deepcopy(SPEC))
    assert ws.validate(spec)[0] == [] and spec["steps"][0]["review"]["coders"] == 2
    plain = copy.deepcopy(SPEC); plain["steps"][0]["review"].pop("coders")
    plain = ws.normalize(plain)
    assert "coders" not in plain["steps"][0]["review"] and ws.workflow_id(plain) != ws.workflow_id(spec)   # a declaration, not a default
    def errs(mut):
        d = copy.deepcopy(SPEC); mut(d); return ws.validate(ws.normalize(d))[0]
    assert any("coders must be an integer" in e for e in errs(lambda d: d["steps"][0]["review"].update(coders=0)))
    assert any("coders must be an integer" in e for e in errs(lambda d: d["steps"][0]["review"].update(coders=True)))
    assert any("needs a review" in e for e in errs(lambda d: d["steps"][0]["review"].update(mode="none")))
    sampled = copy.deepcopy(SPEC); sampled["steps"][0]["review"].update(mode="sample", n=2, unit="entry")
    assert ws.validate(ws.normalize(sampled))[0] == []


def test_a_coders_gate_assigns_waits_queues_and_is_satisfied_by_the_coders(monkeypatch) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    owner, owner_id, _ = _account("gate-owner")
    ds = owner.post("/api/datasets", json={"title": "Gated", "schema_id": "econ-regression-tables@67780825"}).json()["id"]
    spec = ws.normalize(copy.deepcopy(SPEC))
    _declare(conn, ds, spec, monkeypatch)
    run = owner.post(f"/api/datasets/{ds}/workflow/runs", json={}).json()
    assert run["status"] == "running", run
    pdfs = {t: _pdf(f"{t}-{uuid.uuid4().hex[:4]}") for t in ("g0", "g1", "g2")}
    docs = {}
    for t, b in pdfs.items():
        r = owner.post("/api/ingest-pdf", files={"pdf": (f"{t}.pdf", b, "application/pdf")},
                       data={"result": json.dumps(_result(t, n=2)), "run_id": run["id"], "step_id": "tables"})
        assert r.status_code == 200, r.text
        docs[t] = r.json()["document_id"]
    view = owner.post(f"/api/workflow-runs/{run['id']}/complete", json={"step_id": "tables"}).json()
    # the gate opened with nobody to code: it waits, every item pending
    g = view["steps"][0]["review"]
    assert view["status"] == "done" and g["coders"] == 2 and g["assigned"] is False and g["needs_coders"] == 2
    assert g["total"] == 6 and g["pending"] == 6 and g["satisfied"] is False
    # three coders join: the papers are assigned two each, the load even (3 papers × 2 = 6 slots over 3 coders)
    cs = {}
    for tag in ("A", "B", "C"):
        c, uid, email = _account(f"gate-{tag}")
        assert owner.post(f"/api/datasets/{ds}/coders", json={"email": email}).status_code == 200
        cs[tag] = (c, uid)
    view = owner.get(f"/api/workflow-runs/{run['id']}").json()
    g = view["steps"][0]["review"]
    assert g["assigned"] is True and "needs_coders" not in g and g["n_coders"] == 3 and g["total"] == 12 and g["pending"] == 12
    assert sorted((b["label"], b["total"], b["papers"]) for b in g["by_coder"]) == [("coder_1", 4, 2), ("coder_2", 4, 2), ("coder_3", 4, 2)]
    conn.rollback()
    asg = conn.execute("SELECT document_id::text, coder_user_id::text FROM workflow_assignment WHERE run_id = %s::uuid ORDER BY ord", (run["id"],)).fetchall()
    assert len(asg) == 6 and all(len({u for d, u in asg if d == did}) == 2 for did in docs.values())
    # each coder's queue is their own; the owner sees all, or one coder's
    qa = cs["A"][0].get(f"/api/workflow-runs/{run['id']}/queue/tables").json()
    assert qa["viewer_coder"] == cs["A"][1] and len(qa["items"]) == 4 and all(i["coder_user_id"] == cs["A"][1] for i in qa["items"])
    assert qa["progress"] == {"drawn": 4, "checked": 0, "pending": 4, "disagreed": 0, "papers": 2, "papers_done": 0} and qa["satisfied"] is False
    assert len(qa["documents"]) == 2 and len(qa["by_coder"]) == 3 and qa["gate"]["total"] == 12
    qo = owner.get(f"/api/workflow-runs/{run['id']}/queue/tables").json()
    assert len(qo["items"]) == 12 and qo["viewer_coder"] is None
    assert len(owner.get(f"/api/workflow-runs/{run['id']}/queue/tables?coder={cs['B'][1]}").json()["items"]) == 4
    assert cs["A"][0].get(f"/api/workflow-runs/{run['id']}").json()["status"] == "done"         # a coder reads the run
    # the owner moves one paper from its second coder to the third — before any verdict
    first_doc = qa["documents"][0]["document_id"]
    others = [u for d, u in asg if d == first_doc and u != cs["A"][1]]
    spare = next(uid for _c, uid in cs.values() if uid not in (cs["A"][1], *others))
    r = owner.post(f"/api/workflow-runs/{run['id']}/assign", json={"step_id": "tables", "document_id": first_doc, "coders": [cs["A"][1], spare]})
    assert r.status_code == 200, r.text
    assert r.json()["gate"]["total"] == 12
    assert cs["A"][0].post(f"/api/workflow-runs/{run['id']}/assign", json={"step_id": "tables", "document_id": first_doc, "coders": [cs["A"][1]]}).status_code == 404
    # the coders give their verdicts; the record's own status never moves; the gate closes only when every slot has one
    def verdicts(tag: str, status: str = "verified"):
        c, uid = cs[tag]
        q = c.get(f"/api/workflow-runs/{run['id']}/queue/tables").json()
        for it in q["items"]:
            if not it["checked"]:
                assert c.post(f"/api/records/{it['record_id']}/verify", json={"status": status}).status_code == 200
        return c.get(f"/api/workflow-runs/{run['id']}/queue/tables").json()
    qa = verdicts("A")
    assert qa["progress"]["pending"] == 0 and qa["satisfied"] is False and qa["next"] is None
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM record WHERE dataset_id = %s::uuid AND verification_status <> 'unverified'", (ds,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM verification_event ve JOIN record r ON r.id = ve.record_id WHERE r.dataset_id = %s::uuid", (ds,)).fetchone()[0] == 0
    assert review_gate.blocking_release(review_gate.open_gates(conn, ds))
    verdicts("B", "flagged")
    qc = verdicts("C")
    assert qc["satisfied"] is True and qc["next"]["action"] == "release"
    conn.rollback()
    st = review_gate.state(conn, workflows.get_step_run(conn, qc["items"][0] and conn.execute(
        "SELECT id::text FROM workflow_step_run WHERE run_id = %s::uuid AND step_id = 'tables'", (run["id"],)).fetchone()[0]))
    n_b = sum(1 for i in owner.get(f"/api/workflow-runs/{run['id']}/queue/tables").json()["items"] if i["coder_user_id"] == cs["B"][1])
    assert st["satisfied"] is True and st["checked"] == 12 and st["disagreed"] == n_b and n_b >= 2    # B flagged every slot of theirs
    assert not review_gate.blocking_release(review_gate.open_gates(conn, ds))
    # a coder who already gave a verdict cannot be taken off a paper
    r = owner.post(f"/api/workflow-runs/{run['id']}/assign", json={"step_id": "tables", "document_id": first_doc, "coders": [spare]})
    assert r.status_code == 409 and "already gave a verdict" in r.text
    # the badge: every record audited by its coders, the flagged ones not agreed
    cred = records.dataset_credibility(conn, ds)
    assert cred["n_records"] == 6 and cred["audited"] == 6 and cred["agreed"] < 6 and cred["tier"] == "human_verified"
    conn.close()
