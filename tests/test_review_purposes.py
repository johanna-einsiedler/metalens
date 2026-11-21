"""A review step's purpose (audit · routed · monitor · calibrate · adjudicate): what the grammar
asks of each, that none of them moves a workflow's id, and the stamp — the `validation` row an
audit, monitor or calibrate gate writes when it first closes, read back on the preset's card, the
workflow's, the dataset's Workflow card and the release. The database tests skip without Postgres."""
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

from test_coder_gate import SPEC, _declare
from test_coders import _account
from test_workflows import _pdf, _result, _skip

from paperlens import records, releases, review_gate, validation, workflows
from paperlens import workflow_spec as ws

PIN = "econ-regression-tables@67780825"


def _spec(**review) -> dict:
    d = copy.deepcopy(SPEC)
    if review.get("purpose") == "monitor" and "blocks" not in review:
        d["steps"][1].pop("blocks")                                            # the default a monitor gets is the test's subject
    if review.get("coders", 0) is None:
        d["steps"][1].pop("coders"); review.pop("coders")                      # the owner alone
    d["steps"][1].update(review)
    return d


def _errs(d: dict) -> tuple[list, list]:
    return ws.validate(ws.normalize(copy.deepcopy(d)))


# ── the grammar ───────────────────────────────────────────────────────────────

def test_a_purpose_is_one_of_five_and_never_moves_the_id() -> None:
    plain = ws.workflow_id(ws.normalize(copy.deepcopy(SPEC)))
    for p in ("audit", "calibrate"):
        d = ws.normalize(_spec(purpose=p))
        assert _errs(d)[0] == [] and ws.review_of(d["steps"][1])["purpose"] == p
        assert ws.workflow_id(d) == plain                                     # what the stamp reads, not what the workflow promises
    assert any("purpose must be one of" in e for e in _errs(_spec(purpose="vibes"))[0])
    folded = ws.workflow_contract(ws.normalize(_spec(purpose="audit")))["steps"][0]
    assert "purpose" not in json.dumps(folded)                                   # folded into the extract step's review, the purpose left out


def test_a_routed_review_names_its_rule() -> None:
    assert any("purpose routed" in e for e in _errs(_spec(purpose="routed"))[0])
    ok = _spec(purpose="routed", where={"paper_metadata.inventory_note": {"not_null": True}})
    assert _errs(ok)[0] == []


def test_a_monitor_samples_the_new_papers_and_holds_nothing() -> None:
    d = ws.normalize(_spec(purpose="monitor", mode="sample", n=5, unit="entry"))
    assert ws.validate(d)[0] == [] and d["steps"][1]["blocks"] == "none"       # the default a monitor gets
    rel = next(st for st in d["steps"] if st["kind"] == "release")
    assert rel["gate"] == ["tables"]                                            # the implicit gate leaves a monitor out
    assert any("mode 'sample'" in e for e in _errs(_spec(purpose="monitor"))[0])
    assert any("new_only" in e for e in _errs(_spec(purpose="monitor", mode="sample", n=5, unit="entry", new_only=False))[0])
    assert any("blocks: none" in e for e in _errs(_spec(purpose="monitor", mode="sample", n=5, unit="entry", blocks="release"))[0])
    gated = _spec(purpose="monitor", mode="sample", n=5, unit="entry")
    gated["steps"].append({"id": "release", "kind": "release", "gate": ["tables", "coding"]})
    assert any("never holds the release" in e for e in _errs(gated)[0])
    assert any("blocks must be" in e for e in _errs(_spec(blocks="sometimes"))[0])


def test_a_calibration_takes_two_coders_and_should_be_blind() -> None:
    errs, warns = _errs(_spec(purpose="calibrate", coders=1))
    assert any("two or more coders" in e for e in errs)
    errs, warns = _errs(_spec(purpose="calibrate", coders=2))
    assert errs == [] and any("usually blind" in w for w in warns)


def test_an_adjudication_settles_an_earlier_coders_review_that_held_the_run() -> None:
    def with_adj(prior: dict, adj: dict) -> dict:
        d = copy.deepcopy(SPEC)
        d["steps"][1].update(prior)
        d["steps"].append({"id": "settle", "kind": "review", "over": ["tables"], "mode": "all", "unit": "paper", "purpose": "adjudicate", **adj})
        return d
    assert any("needs an earlier review" in e for e in _errs(with_adj({"coders": 1}, {}))[0])
    assert any("must hold the run" in e for e in _errs(with_adj({"coders": 2, "blocks": "release"}, {}))[0])
    assert _errs(with_adj({"coders": 2, "blocks": "next"}, {}))[0] == []
    assert any("mode 'all'" in e for e in _errs(with_adj({"coders": 2, "blocks": "next"}, {"mode": "sample", "n": 2, "unit": "entry"}))[0])


def test_every_bundled_review_step_says_what_it_is_for() -> None:
    import glob
    for f in glob.glob(os.path.join(_ROOT, "paperlens", "workflows", "*.json")):
        spec = json.load(open(f))
        for st in spec["steps"]:
            if st.get("kind") == "review":
                assert st.get("purpose") in ws.PURPOSES, (f, st["id"])
                assert st["purpose"] == ("routed" if (st.get("where") or {}).get("paper_metadata.routed") else "audit")


# ── the stamp ─────────────────────────────────────────────────────────────────

def _run_with_papers(owner, ds: str, run_id: str, tags: tuple[str, ...]) -> dict[str, str]:
    docs = {}
    for t in tags:
        r = owner.post("/api/ingest-pdf", files={"pdf": (f"{t}.pdf", _pdf(f"{t}-{uuid.uuid4().hex[:4]}"), "application/pdf")},
                       data={"result": json.dumps(_result(t, n=2)), "run_id": run_id, "step_id": "tables"})
        assert r.status_code == 200, r.text
        docs[t] = r.json()["document_id"]
    return docs


def _verdicts(client, run_id: str, step: str, status_of) -> dict:
    q = client.get(f"/api/workflow-runs/{run_id}/queue/{step}").json()
    for it in q["items"]:
        if not it["checked"]:
            assert client.post(f"/api/records/{it['record_id']}/verify", json={"status": status_of(it)}).status_code == 200
    return client.get(f"/api/workflow-runs/{run_id}/queue/{step}").json()


def test_an_audit_by_two_coders_writes_the_stamps_record_when_its_gate_closes(monkeypatch) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    owner, owner_id, _ = _account("stamp-owner")
    ds = owner.post("/api/datasets", json={"title": "Stamped", "schema_id": PIN}).json()["id"]
    spec = ws.normalize(_spec(purpose="audit", asks={"tables": ["outcome_variable"]}))
    _declare(conn, ds, spec, monkeypatch)
    run = owner.post(f"/api/datasets/{ds}/workflow/runs", json={}).json()
    docs = _run_with_papers(owner, ds, run["id"], ("s0", "s1", "s2"))
    owner.post(f"/api/workflow-runs/{run['id']}/complete", json={"step_id": "tables"})
    cs = {}
    for tag in ("A", "B"):
        c, uid, email = _account(f"stamp-{tag}")
        assert owner.post(f"/api/datasets/{ds}/coders", json={"email": email}).status_code == 200
        cs[tag] = (c, uid)
    view = owner.get(f"/api/workflow-runs/{run['id']}").json()
    gate = next(s for s in view["steps"] if s["step_id"] == "coding")["review"]
    assert gate["total"] == 12 and gate["satisfied"] is False
    conn.rollback()
    assert validation.for_dataset(conn, ds) == []                              # nothing until the gate closes
    _verdicts(cs["A"][0], run["id"], "coding", lambda it: "verified")
    flagged_doc = docs["s1"]
    qb = _verdicts(cs["B"][0], run["id"], "coding", lambda it: "flagged" if it["document_id"] == flagged_doc else "verified")
    assert qb["satisfied"] is True
    conn.rollback()
    rows = validation.for_dataset(conn, ds)
    assert len(rows) == 1, rows
    v = rows[0]
    assert v["purpose"] == "audit" and v["step_id"] == "coding" and v["schema_id"] == PIN and v["dataset_id"] == ds and v["run_id"] == run["id"]
    assert v["workflow_id"] == ws.workflow_id(spec) and v["unit"] == "paper" and v["sampled"] is False
    assert v["n_papers"] == 3 and v["n_units"] == 6 and v["n_checked"] == 6 and v["n_disagreed"] == 2       # B flagged one paper's two records
    assert v["agreement"] == round(4 / 6, 4) and v["agreement_ci"] == records._wilson(4, 6)   # noqa: SLF001
    assert v["n_coders"] == 2 and v["human_n"] == 6 and v["human_agreement"] == round(4 / 6, 4)   # the coders answered alike on four of six
    assert v["asks"] == ["outcome_variable"] and v["template_id"] is None
    # once: a second look at the gate writes nothing more
    review_gate.state(conn, workflows.get_step_run(conn, conn.execute("SELECT id::text FROM workflow_step_run WHERE run_id = %s::uuid AND step_id = 'coding'", (run["id"],)).fetchone()[0]))
    assert len(validation.for_dataset(conn, ds)) == 1
    # read back: the preset's stamp at its current pin, lapsed at another; the workflow's per step; the Workflow card's line
    st = validation.for_preset(conn, "econ-regression-tables", current_schema_id=PIN)["stamp"]
    assert st["current"] is True and st["lapsed"] is False and st["n_papers"] >= 3 and st["audit"]["step_run_id"] == v["step_run_id"] or st["audit"]["created_at"] >= v["created_at"]
    assert "audited on" in st["label"] and "% agree" in st["label"] and "coders agree" in st["label"] and st["coders"] == 2
    moved = validation.for_preset(conn, "econ-regression-tables", current_schema_id="econ-regression-tables@deadbeef")["stamp"]
    assert moved["lapsed"] is True and moved["label"].endswith("at an earlier version")
    wf = validation.for_workflow(conn, spec["id"])
    assert "coding" in wf["steps"] and wf["steps"]["coding"]["audit"]["n_papers"] == 3
    panel = owner.get(f"/api/datasets/{ds}/workflow").json()
    item = next(s for s in panel["steps"] if s["id"] == "coding")
    assert item["purpose"] == "audit" and item["validation"]["n_checked"] == 6 and item["validation"]["agreement"] == round(4 / 6, 4)
    pend = owner.get(f"/api/datasets/{ds}/releases/pending").json()
    assert pend["credibility"]["validations"][0]["id"] == v["id"]


def test_a_monitor_holds_nothing_and_still_stamps(monkeypatch) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    owner, owner_id, _ = _account("monitor-owner")
    ds = owner.post("/api/datasets", json={"title": "Monitored", "schema_id": PIN}).json()["id"]
    spec = ws.normalize(_spec(purpose="monitor", mode="sample", n=2, unit="entry", coders=None))
    _declare(conn, ds, spec, monkeypatch)
    run = owner.post(f"/api/datasets/{ds}/workflow/runs", json={}).json()
    _run_with_papers(owner, ds, run["id"], ("m0", "m1"))
    view = owner.post(f"/api/workflow-runs/{run['id']}/complete", json={"step_id": "tables"}).json()
    gate = next(s for s in view["steps"] if s["step_id"] == "coding")["review"]
    assert view["status"] == "done" and gate["blocks"] == "none" and gate["total"] == 2 and gate["satisfied"] is False
    conn.rollback()
    assert workflows.gate_report(conn, ds)["open"] is True                     # two records await a verdict; the release is not held
    assert not review_gate.blocking_release(review_gate.open_gates(conn, ds))
    q = _verdicts(owner, run["id"], "coding", lambda it: "verified")
    assert q["satisfied"] is True
    conn.rollback()
    rows = validation.for_dataset(conn, ds)
    assert len(rows) == 1 and rows[0]["purpose"] == "monitor" and rows[0]["sampled"] is True and rows[0]["n_checked"] == 2 and rows[0]["agreement"] == 1.0
    assert rows[0]["frame_n"] == 4 and rows[0]["n_coders"] is None and rows[0]["human_agreement"] is None


def test_an_adjudication_opens_over_the_coders_disagreements(monkeypatch) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    owner, owner_id, _ = _account("adj-owner")
    ds = owner.post("/api/datasets", json={"title": "Settled", "schema_id": PIN}).json()["id"]
    d = _spec(purpose="calibrate", coders=2, blocks="next")
    d["steps"].append({"id": "settle", "kind": "review", "over": ["tables"], "mode": "all", "unit": "paper", "purpose": "adjudicate", "coders": 1, "blocks": "release"})
    spec = ws.normalize(d)
    assert ws.validate(spec)[0] == []
    _declare(conn, ds, spec, monkeypatch)
    run = owner.post(f"/api/datasets/{ds}/workflow/runs", json={}).json()
    docs = _run_with_papers(owner, ds, run["id"], ("a0", "a1"))
    view = owner.post(f"/api/workflow-runs/{run['id']}/complete", json={"step_id": "tables"}).json()
    assert view["status"] == "awaiting_review:coding"
    cs = {}
    for tag in ("A", "B", "C"):
        c, uid, email = _account(f"adj-{tag}")
        assert owner.post(f"/api/datasets/{ds}/coders", json={"email": email}).status_code == 200
        cs[tag] = (c, uid)
    # the two coders of each paper: one takes the model's values, the other flags the first paper's records
    for tag in ("A", "B", "C"):
        _verdicts(cs[tag][0], run["id"], "coding", lambda it, t=tag: "flagged" if (t == "B" and it["document_id"] == docs["a0"]) else "verified")
    view = owner.post(f"/api/workflow-runs/{run['id']}/resume", json={}).json()
    conn.rollback()
    settle = next(s for s in view["steps"] if s["step_id"] == "settle")
    disagreed = [rid for (rid,) in conn.execute("SELECT id::text FROM record WHERE document_id = %s::uuid", (docs["a0"],)).fetchall()]
    assert settle["outputs"]["settles"] == ["coding"] and settle["outputs"]["document_ids"] == [docs["a0"]]
    header = conn.execute("SELECT review FROM workflow_step_run WHERE run_id = %s::uuid AND step_id = 'settle'", (run["id"],)).fetchone()[0]
    assert settle["outputs"]["n_records"] == 2 and sorted(header.get("record_ids") or []) == sorted(disagreed)
    assert settle["review"]["total"] == 2 and settle["review"]["satisfied"] is False      # one adjudicator, two records, nothing yet
    adjudicator = {b["user_id"] for b in settle["review"]["by_coder"]}
    assert adjudicator == {cs["C"][1]}                                                   # the one coder who did not code that paper
    _verdicts(cs["C"][0], run["id"], "settle", lambda it: "verified")
    conn.rollback()
    assert next(s for s in owner.get(f"/api/workflow-runs/{run['id']}").json()["steps"] if s["step_id"] == "settle")["review"]["satisfied"] is True
    assert [r["purpose"] for r in validation.for_dataset(conn, ds)] == ["calibrate"]       # the calibration stamped; the adjudication never does
    cal = validation.for_dataset(conn, ds)[0]
    assert cal["human_n"] == 4 and cal["n_coders"] == 2
