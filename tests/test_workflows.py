"""Workflow runs on a dataset: the plan over staged papers, a run that fans the papers out and
advances as they land, the check step, the review gate, the release gate, cancel and
reconcile, and the provenance a release and an export carry. Skips without Postgres."""
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

from test_analysis_table import _db_ok, _seed
from test_regression_tables_preset import RESULT

from paperlens import app as appmod
from paperlens import checks, extract, records, releases, review_gate, workflows
from paperlens import workflow_spec as ws
from paperlens.principal import Principal


def _skip():
    if not _db_ok():
        pytest.skip("no Postgres")


def _pdf(tag: str) -> bytes:
    import fitz
    d = fitz.open(); d.new_page().insert_text((72, 90), f"workflow paper {tag}"); b = d.tobytes(); d.close()
    return b


def _sha(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


def _result(tag: str, *, n: int = 2) -> dict:
    r = copy.deepcopy(RESULT)
    r["paper_metadata"]["title"] = f"Paper {tag}"; r["paper_metadata"]["doi"] = f"10.1/{tag}"
    r["regressions"] = r["regressions"][:1] * 1
    r["regressions"] = [copy.deepcopy(r["regressions"][0]) for _ in range(n)]
    for i, reg in enumerate(r["regressions"]):
        reg["regression_id"] = f"{tag}::T3::_::C{i + 1}"; reg["column"] = f"({i + 1})"
    r["evidence"] = [{"snippet": "Table 3: Import competition and occupations", "page": 1, "source": "Table 3",
                      "field": [f"regressions[{i}]", f"regressions[{i}].cells"]} for i in range(n)]
    return r


def _who(sess: str) -> Principal:
    return Principal(user_id=None, session_id=sess)


def _client(sess: str):
    from fastapi.testclient import TestClient
    return TestClient(appmod.app), {"X-Session-Id": sess}


def _force_local(monkeypatch):
    """A run needs a signed-in owner or local mode; the tests run as the local user."""
    monkeypatch.setenv("PAPERLENS_LOCAL_MODE", "1")
    from paperlens import localmode
    monkeypatch.setattr(localmode, "enabled", lambda: True)
    monkeypatch.setattr(localmode, "inline_jobs", lambda: True)
    monkeypatch.setattr(appmod.worker, "enqueue", lambda *a, **k: None)


def test_the_econ_dataset_runs_its_supplied_workflow_end_to_end(monkeypatch) -> None:
    _skip()
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    sess = f"wf-{uuid.uuid4().hex[:6]}"
    ds, _doc0 = _seed(conn, _result("p0"), "econ-regression-tables", sess)
    c, H = _client(sess)
    # declare the workflow
    r = c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "econ-regressions"}); assert r.status_code == 200, r.text
    conn.rollback()
    assert (records.get_dataset(conn, ds) or {})["workflow"].startswith("econ-regressions@")
    # the panel: one paper present, the extract step fresh, the check never run, no release yet
    panel = c.get(f"/api/datasets/{ds}/workflow", headers=H).json()
    assert panel["workflow"]["id"] == "econ-regressions" and panel["workflow"]["implicit"] is False and panel["owner"] is True
    by = {s["id"]: s for s in panel["steps"]}
    assert by["tables"]["state"] == "fresh" and by["tables"]["source"] == "supplied"
    assert by["contract"]["state"] == "missing" and by["release"]["state"] == "missing"
    assert panel["gate"]["open"] is True and panel["current_run"] is None          # nothing stale, nothing to review yet
    # plan over two staged papers: supplied → the run waits for their uploads, no model calls
    pdf1, pdf2 = _pdf("p1"), _pdf("p2")
    staged = [{"sha256": _sha(pdf1), "filename": "p1.pdf"}, {"sha256": _sha(pdf2), "filename": "p2.pdf"}, {"sha256": "ffff", "filename": "ignored"}]
    plan = c.post(f"/api/datasets/{ds}/workflow/plan", headers=H, json={"new_pdfs": staged[:2]}).json()
    assert plan["workflow"]["problems"] == [] and plan["n_calls"] == 0
    tables = next(s for s in plan["steps"] if s["id"] == "tables")
    assert tables["state"] == "stale" and tables["work"] == {"extract": 0, "attach": 0, "wait_for": 2, "present": 0, "old_contract": 0, "foreign": 0}
    assert [i["action"] for i in tables["items"]] == ["wait", "wait"]
    # start: a run with two pending jobs, waiting for the pipeline
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged[:2]}).json()
    assert run["status"] == "running" and run["current_step"] == "tables" and run["jobs"] == {**run["jobs"], "total": 2, "pending": 2}
    rid = run["id"]
    assert c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged[:2]}).status_code == 409   # one run at a time
    # the pipeline uploads one paper with the run and step
    r = c.post("/api/ingest-pdf", headers=H, files={"pdf": ("p1.pdf", pdf1, "application/pdf")},
               data={"result": json.dumps(_result("p1")), "run_id": rid, "step_id": "tables", "produced_by": "pipeline"})
    assert r.status_code == 200, r.text
    doc1 = r.json()["document_id"]
    conn.rollback()
    row = conn.execute("SELECT run_id::text, step_id FROM extraction_document WHERE id = %s::uuid", (doc1,)).fetchone()
    assert row == (rid, "tables")                                                    # the document remembers its run
    assert conn.execute("SELECT count(*) FROM record WHERE document_id = %s::uuid AND dataset_id = %s::uuid", (doc1, ds)).fetchone()[0] == 2
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "running" and view["jobs"]["done"] == 1 and view["jobs"]["pending"] == 1
    # a paper the plan did not list is accepted by a supplied step; a repeat upload is refused
    pdf3 = _pdf("p3")
    r = c.post("/api/ingest-pdf", headers=H, files={"pdf": ("p3.pdf", pdf3, "application/pdf")},
               data={"result": json.dumps(_result("p3")), "run_id": rid, "step_id": "tables"})
    assert r.status_code == 200, r.text
    r = c.post("/api/ingest-pdf", headers=H, files={"pdf": ("p1.pdf", pdf1, "application/pdf")},
               data={"result": json.dumps(_result("p1")), "run_id": rid, "step_id": "tables"})
    assert r.status_code == 409
    # the owner closes the uploads: the paper never uploaded is dropped, the step completes, the
    # check runs, the gate opens (review all, blocks release), the run finishes
    view = c.post(f"/api/workflow-runs/{rid}/complete", headers=H, json={"step_id": "tables"}).json()
    assert view["status"] == "done", view
    steps = {s["step_id"]: s for s in view["steps"]}
    assert steps["tables"]["status"] == "done" and steps["tables"]["outputs"]["n_papers"] == 2 and steps["tables"]["outputs"]["n_records"] == 4
    # the econ workflow draws thirty columns; two papers of two columns are fewer, so every column is drawn
    assert steps["tables"]["review"]["mode"] == "sample" and steps["tables"]["review"]["total"] == 4 and steps["tables"]["review"]["satisfied"] is False
    assert steps["tables"]["review"]["exhaustive"] is True and steps["tables"]["review"]["stratify"] == ["table_number"]
    assert steps["contract"]["status"] == "done" and steps["contract"]["outputs"]["n_checked"] == 6    # the whole dataset, three papers
    assert view["jobs"]["cancelled"] == 1 and next(p for p in view["papers"] if p["status"] == "cancelled")["error"] == "not uploaded"
    conn.rollback()
    # the release gate holds: the gate step awaits review
    p = c.get(f"/api/datasets/{ds}/releases/pending", headers=H).json()
    assert p["gate"]["open"] is False and p["gate"]["gates_blocking"] == ["tables"]
    r = c.post(f"/api/datasets/{ds}/releases", headers=H, json={"notes": "try"})
    assert r.status_code == 409 and r.json()["detail"]["gate"]["gates_blocking"] == ["tables"]
    # verdicts on the four new records satisfy the gate; the release goes through and remembers the run
    for (rec_id,) in conn.execute("SELECT r.id FROM record r JOIN extraction_document d ON d.id = r.document_id WHERE d.run_id = %s::uuid", (rid,)).fetchall():
        records.verify_record(conn, str(rec_id), status="verified")
    conn.commit()
    panel = c.get(f"/api/datasets/{ds}/workflow", headers=H).json()
    by = {s["id"]: s for s in panel["steps"]}
    assert by["tables"]["state"] == "fresh" and by["contract"]["state"] == "fresh" and panel["gate"]["open"] is True
    r = c.post(f"/api/datasets/{ds}/releases", headers=H, json={"notes": "first"})
    assert r.status_code == 200, r.text
    assert r.json()["workflow_run_id"] == rid and r.json()["gates"]["gates_overridden"] is False
    rel = releases.latest(conn, ds, with_snapshot=True)
    wf = rel["snapshot"]["workflow"]
    assert wf["id"] == "econ-regressions" and wf["run_id"] == rid and wf["implicit"] is False
    assert [s["id"] for s in wf["steps"]] == ["tables", "contract", "numbers", "release"] and wf["steps"][0]["review"]["mode"] == "sample"
    # the recipe, the release files and the JSON export say which process made the data
    ov = records.dataset_overview(conn, ds)
    assert ov["recipe"]["workflow"]["run_id"] == rid and ov["recipe"]["workflow"]["contract"].startswith("econ-regressions@")
    from paperlens import exporter, release_export
    files = release_export.build(conn, rel)
    meta = json.loads(files["release.json"])
    assert meta["workflow"]["run_id"] == rid and meta["workflow"]["steps"][0]["id"] == "tables" and "Built with workflow" in files["README.md"].decode()
    assert exporter.materialize_dataset(conn, ds)["metadata"]["workflow"]["run_id"] == rid
    # a verdict after the check ran makes the check stale; the fingerprint moved with the run
    rec_id = conn.execute("SELECT id::text FROM record WHERE dataset_id = %s::uuid LIMIT 1", (ds,)).fetchone()[0]
    records.verify_record(conn, rec_id, status="corrected", patch=[{"field_path": "notes", "value": "edited"}]); conn.commit()
    by = {s["id"]: s for s in c.get(f"/api/datasets/{ds}/workflow", headers=H).json()["steps"]}
    assert by["contract"]["state"] == "stale" and "corrections after" in by["contract"]["because"][0]
    conn.close()


def test_a_live_workflow_runs_the_worker_path_inline_and_refunds_what_never_ran(monkeypatch) -> None:
    _skip()
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    sess = f"wf-{uuid.uuid4().hex[:6]}"
    c, H = _client(sess)
    ds = records.create_dataset(conn, title="Live WF", schema_id="masem-direct@eb5f0287", session_id=sess)["id"]
    conn.commit()
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "masem"}).status_code == 200
    calls = []

    def fake_complete(pdf, prompt, *, model="", api_key="", base_url=None, use_text=False):
        calls.append(model)
        if len(calls) == 2:
            raise ValueError("Error code: 400 - the model refused")
        return extract.LLMResult(text=json.dumps({"paper_metadata": {"title": f"Live {len(calls)}"},
                                                  "samples": [{"sample_id": "S1", "n": 10}], "evidence": []}),
                                 finish_reason="stop", usage={"total": 1}, resolved_model="fake")
    real = extract.run_extraction
    monkeypatch.setattr(appmod.extract, "run_extraction", lambda cn, pdf, prompt, **kw: real(cn, pdf, prompt, complete=fake_complete, **kw))
    pdfs = [_pdf("l1"), _pdf("l2")]
    staged = [{"sha256": _sha(b), "filename": f"l{i}.pdf"} for i, b in enumerate(pdfs)]
    plan = c.post(f"/api/datasets/{ds}/workflow/plan", headers=H, json={"new_pdfs": staged}).json()
    tables = plan["steps"][0]
    assert tables["source"] == "live" and tables["work"]["extract"] == 2 and plan["n_calls"] == 2
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json()
    rid = run["id"]
    # a paper not in the plan is refused by a live step; planned ones go through /api/extract with the run
    r = c.post("/api/extract", headers=H, files={"pdf": ("x.pdf", _pdf("zz"), "application/pdf")},
               data={"prompt": "x", "run_id": rid, "step_id": "extract", "api_key": "k", "model": "m"})
    assert r.status_code == 409
    r = c.post("/api/extract", headers=H, files={"pdf": ("l0.pdf", pdfs[0], "application/pdf")},
               data={"prompt": "x", "run_id": rid, "step_id": "extract", "api_key": "k", "model": "m"})
    assert r.status_code == 200, r.text
    r = c.post("/api/extract", headers=H, files={"pdf": ("l1.pdf", pdfs[1], "application/pdf")},
               data={"prompt": "x", "run_id": rid, "step_id": "extract", "api_key": "k", "model": "m"})
    assert r.status_code == 422                                                       # the model refused: the run learns it
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "done" and view["jobs"]["done"] == 1 and view["jobs"]["failed"] == 1
    steps = {s["step_id"]: s for s in view["steps"]}
    assert steps["extract"]["outputs"]["failed"][0]["pdf_sha256"] == staged[1]["sha256"] and steps["extract"]["outputs"]["n_papers"] == 1
    conn.rollback()
    assert conn.execute("SELECT count(DISTINCT document_id) FROM record WHERE dataset_id = %s::uuid", (ds,)).fetchone()[0] == 1
    # the failed paper can be retried from the PDF the run kept — the run is done, so retry refuses
    r = c.post(f"/api/workflow-runs/{rid}/retry", headers=H, json={"api_key": "k"})
    assert r.status_code == 409
    conn.close()


def test_plan_attaches_what_the_owner_already_has_and_refuses_what_cannot_run(monkeypatch) -> None:
    _skip()
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    sess = f"wf-{uuid.uuid4().hex[:6]}"
    who = _who(sess)
    # a document of this paper under the same contract lives in another dataset of the owner
    _ds_a, doc_a = _seed(conn, _result("a"), "econ-regression-tables", sess)
    ds_b = records.create_dataset(conn, title="B", schema_id="econ-regression-tables@67780825", session_id=sess)["id"]
    conn.commit()
    sha = conn.execute("SELECT pdf_sha256 FROM extraction_document WHERE id = %s::uuid", (doc_a,)).fetchone()[0]
    assert sha is None                                            # seeded without a PDF: give it one so the hash exists
    conn.execute("UPDATE extraction_document SET pdf_sha256 = %s WHERE id = %s::uuid", ("a" * 64, doc_a)); conn.commit()
    plan = workflows.plan(conn, ds_b, who=who, new_pdfs=[{"sha256": "a" * 64, "filename": "a.pdf"}])
    ex = plan["steps"][0]
    assert ex["work"]["wait_for"] == 1 and ex["items"][0]["action"] == "wait"       # a supplied step never attaches on its own
    # a live step attaches without a model call
    ds_c = records.create_dataset(conn, title="C", schema_id="masem-direct@eb5f0287", session_id=sess)["id"]
    _ds_d, doc_d = _seed(conn, {"paper_metadata": {"title": "M"}, "samples": [{"sample_id": "S1", "n": 5}], "evidence": []}, "masem-direct", sess)
    conn.execute("UPDATE extraction_document SET pdf_sha256 = %s WHERE id = %s::uuid", ("b" * 64, doc_d)); conn.commit()
    plan = workflows.plan(conn, ds_c, who=who, new_pdfs=[{"sha256": "b" * 64, "filename": "b.pdf"}, {"sha256": "c" * 64, "filename": "c.pdf"}])
    ex = plan["steps"][0]
    assert ex["work"]["attach"] == 1 and ex["work"]["extract"] == 1 and plan["n_calls"] == 1
    run = workflows.start(conn, ds_c, who=who, new_pdfs=[{"sha256": "b" * 64, "filename": "b.pdf"}])
    assert run["jobs"]["attached"] == 1 and run["status"] == "done"                 # nothing to wait for: attached and finished
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM record WHERE dataset_id = %s::uuid AND document_id = %s::uuid", (ds_c, doc_d)).fetchone()[0] == 1
    # what this release refuses: a sampled review, k > 1
    spec = ws.normalize({"format": 1, "id": "later", "meta": {"title": "Later"},
                         "steps": [{"id": "x", "kind": "extract", "preset": "masem-direct@eb5f0287", "k": 2, "vote": {"identity": ["sample_id"]},
                                    "review": {"mode": "sample", "n": 3}}]})
    monkeypatch.setattr(workflows, "get_workflow", lambda wid: spec if wid.startswith("later") else None)
    conn.execute("UPDATE dataset SET workflow = %s WHERE id = %s::uuid", (ws.workflow_id(spec), ds_c)); conn.commit()
    with pytest.raises(workflows.RunRefused) as info:
        workflows.start(conn, ds_c, who=who, new_pdfs=[{"sha256": "d" * 64, "filename": "d.pdf"}])
    assert "vote engine" in str(info.value) and "sampled review" not in str(info.value)   # the sampler runs now
    conn.close()


def test_a_gate_that_blocks_the_next_step_pauses_the_run_and_resume_needs_the_verdicts(monkeypatch) -> None:
    _skip()
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    sess = f"wf-{uuid.uuid4().hex[:6]}"
    ds, _doc = _seed(conn, _result("q0"), "econ-regression-tables", sess)
    spec = ws.normalize({"format": 1, "id": "strict", "meta": {"title": "Strict"},
                         "steps": [{"id": "tables", "kind": "extract", "preset": "econ-regression-tables@67780825",
                                    "review": {"mode": "all", "blocks": "next"}},
                                   {"id": "contract", "kind": "check", "check": "schema", "over": ["tables"], "on_fail": "flag"}]})
    monkeypatch.setattr(workflows, "get_workflow", lambda wid: spec if wid.startswith("strict") else None)
    conn.execute("UPDATE dataset SET workflow = %s WHERE id = %s::uuid", (ws.workflow_id(spec), ds)); conn.commit()
    c, H = _client(sess)
    pdf = _pdf("q1")
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": _sha(pdf), "filename": "q1.pdf"}]}).json()
    rid = run["id"]
    # the paper lands without a required field → the contract check finds it later
    bad = _result("q1", n=1); bad["regressions"][0]["outcome_variable"] = None
    r = c.post("/api/ingest-pdf", headers=H, files={"pdf": ("q1.pdf", pdf, "application/pdf")},
               data={"result": json.dumps(bad), "run_id": rid, "step_id": "tables"})
    assert r.status_code == 200, r.text
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "awaiting_review:tables" and view["current_step"] == "tables"
    assert c.post(f"/api/workflow-runs/{rid}/resume", headers=H, json={}).status_code == 409           # one record unchecked
    conn.rollback()
    rec = conn.execute("SELECT r.id::text FROM record r JOIN extraction_document d ON d.id = r.document_id WHERE d.run_id = %s::uuid", (rid,)).fetchone()[0]
    records.verify_record(conn, rec, status="flagged"); conn.commit()
    view = c.post(f"/api/workflow-runs/{rid}/resume", headers=H, json={}).json()
    assert view["status"] == "done", view
    steps = {s["step_id"]: s for s in view["steps"]}
    assert steps["contract"]["outputs"]["findings"] >= 1 and steps["contract"]["review"]["total"] >= 1   # the check put the record in front of a reviewer
    findings = c.get(f"/api/workflow-runs/{rid}/issues", headers=H).json()["findings"]
    assert any(f["code"] == "missing_required" and f["record_id"] == rec and f["path"] == "outcome_variable" and f["severity"] == "error" for f in findings), findings
    assert checks.for_document(conn, view["papers"][0]["document_id"])
    # the check's gate is already satisfied: the record carries a verdict
    gates = review_gate.open_gates(conn, ds)
    assert all(g["satisfied"] for g in gates)
    # an override is recorded when the owner resumes past unchecked records
    pdf2 = _pdf("q2")
    run2 = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": _sha(pdf2), "filename": "q2.pdf"}]}).json()
    assert c.post("/api/ingest-pdf", headers=H, files={"pdf": ("q2.pdf", pdf2, "application/pdf")},
                  data={"result": json.dumps(_result("q2", n=1)), "run_id": run2["id"], "step_id": "tables"}).status_code == 200
    view = c.post(f"/api/workflow-runs/{run2['id']}/resume", headers=H, json={"override": True}).json()
    assert view["status"] == "done" and view["steps"][0]["review"]["overridden_by"]
    # a stranger owns none of the runs (outside local mode, where one user owns everything);
    # cancel refuses a finished run gracefully
    from paperlens import localmode
    monkeypatch.setattr(localmode, "enabled", lambda: False)
    assert workflows.is_run_owner(workflows.get_run(conn, rid), Principal(session_id="someone-else", user_id=None)) is False
    monkeypatch.setattr(localmode, "enabled", lambda: True)
    assert c.post(f"/api/workflow-runs/{rid}/cancel", headers=H).json()["status"] == "done"
    conn.close()


def test_cancel_and_reconcile(monkeypatch) -> None:
    _skip()
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    sess = f"wf-{uuid.uuid4().hex[:6]}"
    who = _who(sess)
    ds = records.create_dataset(conn, title="Live WF 2", schema_id="masem-direct@eb5f0287", session_id=sess)["id"]
    conn.execute("UPDATE dataset SET workflow = %s WHERE id = %s::uuid", (ws.workflow_id(workflows.get_workflow("masem")), ds)); conn.commit()
    pdfs = [_pdf("r1"), _pdf("r2")]
    staged = [{"sha256": _sha(b), "filename": f"r{i}.pdf"} for i, b in enumerate(pdfs)]
    run = workflows.start(conn, ds, who=who, new_pdfs=staged)
    rid = run["id"]
    # the page claimed one paper and the queue took it; then the queue forgets it
    claim = workflows.claim_job(conn, rid, "extract", staged[0]["sha256"], who, filename="r0.pdf")
    workflows.mark_queued(conn, claim["job"]["id"], "arq-job-1")
    conn.execute("UPDATE workflow_run SET updated_at = now() - interval '1 hour' WHERE id = %s::uuid", (rid,)); conn.commit()
    cands = workflows.reconcile_candidates(conn)
    assert [(r["id"], j["job_id"]) for r, j in cands] == [(rid, "arq-job-1")]
    assert workflows.apply_job_status(conn, cands[0][0], cands[0][1], None) == "lost"
    view = workflows.run_view(conn, workflows.get_run(conn, rid))
    assert view["jobs"]["failed"] == 1 and view["status"] == "running"             # the other paper is still pending
    # the worker landed a document before the queue forgot: reconcile recovers it
    claim = workflows.claim_job(conn, rid, "extract", staged[1]["sha256"], who, filename="r1.pdf")
    workflows.mark_queued(conn, claim["job"]["id"], "arq-job-2")
    from paperlens.ingest import ingest
    doc = records.persist(conn, ingest({"paper_metadata": {"title": "R2"}, "samples": [{"sample_id": "S1"}], "evidence": []}, entries_key="samples"),
                          schema_id="masem-direct@eb5f0287", source_job_id="arq-job-2", session_id=sess, pdf_sha256=staged[1]["sha256"],
                          run_id=rid, step_id="extract")
    conn.commit()
    job2 = next(j for j in workflows.jobs(conn, rid, "extract") if j["pdf_sha256"] == staged[1]["sha256"])
    assert workflows.apply_job_status(conn, workflows.get_run(conn, rid), job2, None) == "recovered"
    view = workflows.run_view(conn, workflows.get_run(conn, rid))
    assert view["status"] == "done" and view["jobs"]["done"] == 1
    assert next(p for p in view["papers"] if p["pdf_sha256"] == staged[1]["sha256"])["document_id"] == doc
    # cancel a fresh run: pending jobs are cancelled, the run is cancelled, the gate does not count
    run = workflows.start(conn, ds, who=who, new_pdfs=[{"sha256": "e" * 64, "filename": "e.pdf"}])
    view = workflows.cancel(conn, run["id"], who=who)
    assert view["status"] == "cancelled" and view["jobs"]["cancelled"] == 1
    assert all(g["run_id"] != run["id"] for g in review_gate.open_gates(conn, ds))
    # the session's runs go with its data
    from paperlens import retention
    retention.forget_session(conn, sess)
    assert conn.execute("SELECT count(*) FROM workflow_run WHERE dataset_id = %s::uuid", (ds,)).fetchone()[0] == 0
    conn.close()


def test_findings_reach_the_document_view_and_the_claims_check_reads_the_companion(monkeypatch) -> None:
    _skip()
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    sess = f"wf-{uuid.uuid4().hex[:6]}"
    c, H = _client(sess)
    # a tables dataset on the econ workflow (the fixture paper, DOI 10.1/t, Table 3 col (1) = -1.292):
    # the number check finds a dangling standard error in the paper added next
    ds_t, _d = _seed(conn, copy.deepcopy(RESULT), "econ-regression-tables", sess)
    assert c.patch(f"/api/datasets/{ds_t}", headers=H, json={"workflow": "econ-regressions"}).status_code == 200
    pdf = _pdf("t1")
    run = c.post(f"/api/datasets/{ds_t}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": _sha(pdf), "filename": "t1.pdf"}]}).json()
    bad = _result("t1", n=1); bad["regressions"][0]["cells"][1]["refers_to"] = 9
    r = c.post("/api/ingest-pdf", headers=H, files={"pdf": ("t1.pdf", pdf, "application/pdf")},
               data={"result": json.dumps(bad), "run_id": run["id"], "step_id": "tables"})
    assert r.status_code == 200, r.text
    doc = r.json()["document_id"]
    view = c.get(f"/api/workflow-runs/{run['id']}", headers=H).json()
    assert view["status"] == "done"
    steps = {s["step_id"]: s for s in view["steps"]}
    assert steps["numbers"]["outputs"]["findings"] == 1 and steps["numbers"]["outputs"]["by_code"] == {"se_dangling_ref": 1}
    dv = c.get(f"/api/documents/{doc}/view", headers=H).json()
    assert [(f["check"], f["code"], f["path"]) for f in dv["findings"]] == [("regression-cells", "se_dangling_ref", "cells[1].refers_to")]
    conn.rollback()
    by = {s["id"]: s for s in c.get(f"/api/datasets/{ds_t}/workflow", headers=H).json()["steps"]}
    assert by["numbers"]["findings"]["findings"] == 1 and by["numbers"]["state"] == "fresh"
    # a claims dataset on the claims workflow: the cross-check needs the companion
    from test_register_presets import CLAIMS
    ds_c, _dc = _seed(conn, copy.deepcopy(CLAIMS), "register-claims", sess)
    assert c.patch(f"/api/datasets/{ds_c}", headers=H, json={"workflow": "econ-claims"}).status_code == 200
    by = {s["id"]: s for s in c.get(f"/api/datasets/{ds_c}/workflow", headers=H).json()["steps"]}
    assert by["links"]["state"] == "blocked" and "no companion" in by["links"]["because"][0]
    assert c.patch(f"/api/datasets/{ds_c}", headers=H, json={"companion_dataset_id": ds_t}).status_code == 200, "the econ tables are a registered companion"
    run = c.post(f"/api/datasets/{ds_c}/workflow/runs", headers=H, json={}).json()
    view = c.post(f"/api/workflow-runs/{run['id']}/complete", headers=H, json={"step_id": "claims"}).json()   # nothing new to upload: run the checks
    # the corpus-edges step makes model calls: on an own key the run pauses for it, then goes on
    assert view["status"] == "needs_key:edges", view
    from paperlens import derived, providers
    monkeypatch.setattr(providers, "generate_text", lambda model, key, prompt, **kw: json.dumps(
        {"rows": [{"cause_concept": "import_competition", "effect_concept": "employment", "sign": "-", "sources": ["r1"]}]}))
    view = c.post(f"/api/workflow-runs/{run['id']}/resume", headers=H, json={"api_key": "k-test", "model": "test-model"}).json()
    assert view["status"] == "done", view
    steps = {s["step_id"]: s for s in view["steps"]}
    assert steps["edges"]["status"] == "done" and steps["edges"]["n_calls"] == 1 and steps["edges"]["review"]["draft"]["status"] == "draft"
    # S1 cites Table 3 col (1) = -1.292 (the seeded econ table has it) and Table 4 col (1) (no such column)
    assert steps["links"]["outputs"]["by_code"] == {"exhibit_unlinked": 1}, steps["links"]["outputs"]
    findings = c.get(f"/api/workflow-runs/{run['id']}/issues", headers=H).json()["findings"]
    assert findings[0]["path"] == "results[1].source_table" and findings[0]["severity"] == "warn"
    # a release of the claims dataset carries the findings file
    for (rec_id,) in conn.execute("SELECT id FROM record WHERE dataset_id = %s::uuid", (ds_c,)).fetchall():
        records.verify_record(conn, str(rec_id), status="verified")
    conn.commit()
    r = c.post(f"/api/datasets/{ds_c}/releases", headers=H, json={"notes": "with findings"})
    assert r.status_code == 409 and "edges" in r.text                       # the edges draft holds the release until its commit
    draft = derived.latest_draft(conn, ds_c, "edges")
    assert c.post(f"/api/derived/{draft['id']}/commit", headers=H).status_code == 200
    r = c.post(f"/api/datasets/{ds_c}/releases", headers=H, json={"notes": "with findings"})
    assert r.status_code == 200, r.text
    from paperlens import release_export
    files = release_export.build(conn, releases.latest(conn, ds_c, with_snapshot=True))
    assert "findings.json" in files and json.loads(files["findings.json"])["findings"][0]["code"] == "exhibit_unlinked"
    assert json.loads(files["release.json"])["findings"]["by_severity"] == {"warn": 1}
    conn.close()
