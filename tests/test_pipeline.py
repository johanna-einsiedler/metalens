"""Pipelines inside a workflow run: a live step run k times and voted into one reviewed
document (its runs hidden, read-only, deleted with their final), a follow-up step reading the
runs by reference, and a loop that re-runs the papers a check still finds problems in, with the
findings fed back. Model calls are faked. Skips without Postgres."""
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

from test_analysis_table import _db_ok  # noqa: E402
from test_chain_vote import BASE  # noqa: E402
from test_workflows import _client, _force_local, _pdf, _sha, _who  # noqa: E402

from paperlens import extract, presets, providers, records, workflows  # noqa: E402
from paperlens import preset_spec as ps
from paperlens import workflow_spec as ws  # noqa: E402


def _presets(tmp_path, monkeypatch):
    """Two live presets in a directory of their own: the claims extraction and a follow-up
    that adjudicates its runs. Returns their pins."""
    claims = copy.deepcopy(BASE)
    claims["prompt"] = {"text": "Extract every causal claim the abstract states. ${output_schema} ${evidence} ${return_format}"}
    graph = copy.deepcopy(BASE)
    graph["id"] = "graph-test"; graph["meta"]["title"] = "Graph test"
    graph["prompt"] = {"text": "Adjudicate the runs into one set of claims; keep the orientation.\n\n${previous}\n\n${output_schema} ${evidence} ${return_format}"}
    (tmp_path / "claims-test.json").write_text(json.dumps(claims))
    (tmp_path / "graph-test.json").write_text(json.dumps(graph))
    monkeypatch.setenv("PAPERLENS_PRESET_DIRS", str(tmp_path))
    allp = presets.load_all()
    return ps.schema_id(allp["claims-test"]), ps.schema_id(allp["graph-test"])


def _workflow(tmp_path, monkeypatch, steps: list[dict], gate: list[str]) -> dict:
    wdir = tmp_path / "wf"; wdir.mkdir(exist_ok=True)
    (wdir / "pipe-test.json").write_text(json.dumps({"format": 1, "id": "pipe-test", "meta": {"title": "Pipeline test"},
                                                     "steps": [*steps, {"id": "release", "kind": "release", "gate": gate}]}))
    monkeypatch.setenv("PAPERLENS_WORKFLOW_DIRS", str(wdir))
    return workflows.load_all()["pipe-test"]


def _dataset(conn, sess: str, schema_id: str) -> str:
    run = presets.resolve_run(conn, schema_id=schema_id, params={})
    with conn.transaction():
        records.upsert_schema(conn, run.schema_id, run.field_defs)
    ds = records.create_dataset(conn, title="Pipeline set", schema_id=schema_id, session_id=sess)
    conn.commit()
    return ds["id"]


def _claims_doc(sign_b: str, *, extra: list | None = None, cause0: str | None = "Parental death") -> dict:
    return {"paper_metadata": {"title": "Paper P", "doi": "10.1/p", "year": 2020, "authors": ["A"], "journal": None},
            "claims": [{"cause": cause0, "effect": "earnings", "sign": "-", "statement": "Losing a parent lowers earnings.", "tags": ["health"], "results": [{"estimate": -0.1}]},
                       {"cause": "import competition", "effect": "employment", "sign": sign_b, "statement": "Imports move jobs.", "tags": ["trade"], "results": [{"estimate": -1.3}]},
                       *(extra or [])],
            "evidence": [{"snippet": "workflow paper", "page": 1, "source": None, "field": ["claims[0]", "claims[1]"]}]}


def _fake_complete(answers: list[dict], prompts: list[str]):
    def complete(pdf_bytes, prompt, **kw):
        prompts.append(prompt)
        i = min(len(prompts) - 1, len(answers) - 1)
        return extract.LLMResult(text=json.dumps(answers[i]), finish_reason="stop", usage={"total_tokens": 10}, resolved_model="fake-1")
    return complete


def test_k_runs_are_voted_into_one_reviewed_document(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    pin_c, _pin_g = _presets(tmp_path, monkeypatch)
    spec = _workflow(tmp_path, monkeypatch, [{"id": "claims", "kind": "extract", "preset": pin_c, "source": "live", "k": 3,
                                             "vote": {"identity": ["cause", "effect"], "fields": {"statement": "first"}},
                                             "review": {"mode": "all", "blocks": "release"}}], ["claims"])
    errs, _w = workflows.validate_for(conn, spec)
    assert errs == [] and workflows.later_problems(spec) == []
    assert workflows.pipeline_for(spec, "claims") == [{"step_id": "claims", "schema_id": pin_c, "k": 3, "vote": spec["steps"][0]["vote"], "previous": False, "params": {}}]
    assert workflows.calls_per_paper(spec, "claims") == 3
    sess = f"pipe-{uuid.uuid4().hex[:6]}"
    ds = _dataset(conn, sess, pin_c)
    conn.execute("UPDATE dataset SET workflow = %s WHERE id = %s::uuid", (ws.workflow_id(spec), ds)); conn.commit()
    who = _who(sess)
    pdf = _pdf("p1")
    plan = workflows.plan(conn, ds, who=who, new_pdfs=[{"sha256": _sha(pdf), "filename": "p1.pdf"}])
    st = next(s for s in plan["steps"] if s["id"] == "claims")
    assert st["n_calls"] == 3 and st["calls_per_paper"] == 3 and plan["n_calls"] == 3
    run = workflows.start(conn, ds, who=who, new_pdfs=[{"sha256": _sha(pdf), "filename": "p1.pdf"}])
    prompts: list[str] = []
    answers = [_claims_doc("+"), _claims_doc("-"), _claims_doc("-", extra=[{"cause": "tax rate", "effect": "evasion", "sign": "+", "statement": "x", "tags": [], "results": []}])]
    monkeypatch.setattr(extract, "_default_complete", _fake_complete(answers, prompts))
    c, H = _client(sess)
    r = c.post("/api/extract", headers=H, files={"pdf": ("p1.pdf", pdf, "application/pdf")},
               data={"schema_id": pin_c, "model": "fake-1", "api_key": "k", "run_id": run["id"], "step_id": "claims"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(prompts) == 3 and out["n_calls"] == 3 and len(out["run_document_ids"]) == 3 and out["group_id"]
    final = out["document_id"]
    conn.rollback()
    view = records.document_view(conn, final)
    assert view["role"] == "final" and view["chain"]["k"] == 3 and view["chain"]["n_runs"] == 3 and not view["read_only"]
    recs = sorted(view["records"], key=lambda x: x["entry_index"])
    assert [r_["field_values"]["cause"] for r_ in recs] == ["Parental death", "import competition", "tax rate"]
    assert recs[1]["field_values"]["sign"] == "-" and recs[1]["chain"]["support"] == {"n": 3, "k": 3}
    assert [x["value"] for x in recs[1]["chain"]["candidates"]["sign"]] == ["+", "-"] and "contested:sign" in recs[1]["chain"]["flags"]
    assert recs[2]["chain"]["support"] == {"n": 1, "k": 3} and "minority" in recs[2]["chain"]["flags"]
    assert any("(r1, r2, r3)" in (e["source"] or "") for e in view["evidence"])
    # the runs: hidden from every listing, read-only through the final's pages, never a dataset member, refused on edit
    run_ids = out["run_document_ids"]
    assert all(d["document_id"] != run_ids[0] for d in records.list_documents(conn, session_id=sess))
    rv = records.document_view(conn, run_ids[0])
    assert rv["read_only"] and rv["role"] == "run" and rv["pages_document_id"] == final and rv["pass_index"] == 0
    with pytest.raises(ValueError):
        records.assign_document_to_dataset(conn, ds, run_ids[0])
    conn.rollback()
    rec_of_run = conn.execute("SELECT id::text FROM record WHERE document_id = %s::uuid LIMIT 1", (run_ids[0],)).fetchone()[0]
    assert c.post(f"/api/records/{rec_of_run}/verify", headers=H, json={"status": "verified"}).status_code == 409
    assert c.delete(f"/api/documents/{run_ids[0]}", headers=H).status_code == 409
    ch = c.get(f"/api/documents/{final}/chain", headers=H).json()
    assert ch["final_id"] == final and [x["label"] for x in ch["runs"]] == ["r1", "r2", "r3"] and ch["runs"][0]["records"]
    # the run landed in the dataset and finished; the review gate asks for the final's records only
    conn.rollback()
    rv_ = workflows.run_view(conn, workflows.get_run(conn, run["id"]))
    assert rv_["status"] == "done" and rv_["papers"][0]["document_id"] == final
    n_ds = conn.execute("SELECT count(*) FROM record WHERE dataset_id = %s::uuid", (ds,)).fetchone()[0]
    assert n_ds == 3
    # adopting a candidate is a correction; deleting the final takes the runs with it
    assert c.post(f"/api/records/{recs[1]['id']}/verify", headers=H, json={"status": "corrected", "patch": [{"field_path": "sign", "value": "+"}],
                                                                           "diff": [{"field_path": "sign", "original_value": "-", "final_value": "+"}]}).status_code == 200
    assert c.delete(f"/api/documents/{final}", headers=H).json()["deleted"] >= 1
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM extraction_document WHERE id = ANY(%s::uuid[])", (run_ids,)).fetchone()[0] == 0
    monkeypatch.delenv("PAPERLENS_WORKFLOW_DIRS"); monkeypatch.delenv("PAPERLENS_PRESET_DIRS"); conn.close()


def test_a_follow_up_step_reads_the_runs_by_reference(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    pin_c, pin_g = _presets(tmp_path, monkeypatch)
    spec = _workflow(tmp_path, monkeypatch, [
        {"id": "claims", "kind": "extract", "preset": pin_c, "source": "live", "k": 2, "vote": {"identity": ["cause", "effect"]}},
        {"id": "graph", "kind": "extract", "preset": pin_g, "source": "live", "input": {"previous": ["claims"]}, "review": {"mode": "all", "blocks": "release"}}], ["graph"])
    errs, _w = workflows.validate_for(conn, spec)
    assert errs == [] and workflows.later_problems(spec) == [], errs
    assert workflows.consumed_steps(spec) == {"claims": "graph"}
    stages = workflows.pipeline_for(spec, "graph")
    assert [s["step_id"] for s in stages] == ["claims", "graph"] and stages[0]["k"] == 2 and stages[1]["previous"] and workflows.calls_per_paper(spec, "graph") == 3
    # the grammar: a follow-up preset without input.previous, a plain preset with it, a supplied source
    bad = copy.deepcopy(spec); bad["steps"][1]["input"] = {"previous": []}
    assert any("declare input.previous" in e for e in ws.validate(bad, resolver=presets.load_all().get)[0])
    bad = copy.deepcopy(spec); bad["steps"][1]["preset"] = pin_c
    assert any("must read ${previous}" in e for e in ws.validate(bad, resolver=presets.load_all().get)[0])
    sess = f"pipe-{uuid.uuid4().hex[:6]}"
    ds = _dataset(conn, sess, pin_g)
    conn.execute("UPDATE dataset SET workflow = %s WHERE id = %s::uuid", (ws.workflow_id(spec), ds)); conn.commit()
    who = _who(sess)
    pdf = _pdf("p2")
    plan = workflows.plan(conn, ds, who=who, new_pdfs=[{"sha256": _sha(pdf), "filename": "p2.pdf"}])
    by = {s["id"]: s for s in plan["steps"]}
    assert by["claims"]["pipeline_of"] == "graph" and by["claims"]["n_calls"] == 0 and by["graph"]["n_calls"] == 3 and plan["n_calls"] == 3
    run = workflows.start(conn, ds, who=who, new_pdfs=[{"sha256": _sha(pdf), "filename": "p2.pdf"}])
    assert [j["step_id"] for j in workflows.jobs(conn, run["id"])] == ["graph"]
    prompts: list[str] = []
    monkeypatch.setattr(extract, "_default_complete", _fake_complete([_claims_doc("+"), _claims_doc("-")], prompts))
    seen: list[str] = []
    def fake_generate(model, api_key, prompt, **kw):
        seen.append(prompt)
        return json.dumps({"claims": [{"cause": "parental death", "effect": "earnings", "sign": "-", "statement": "s", "tags": ["health"], "results": [{"estimate": -0.1}]},
                                      {"cause": "import competition", "effect": "employment", "sign": "-", "statement": "s", "tags": [], "results": []}],
                           "evidence": [{"ref": ["r1.claims[0]", "r2.claims[0]"], "field": "claims[0]"},
                                        {"ref": ["r1.claims[1].sign", "r2.claims[1].sign"], "field": "claims[1].sign"},
                                        {"ref": "r2.claims[1]", "field": "claims[1]"}]})
    monkeypatch.setattr(providers, "generate_text", fake_generate)
    # the step's own preset cannot run alone
    c, H = _client(sess)
    r = c.post("/api/extract", headers=H, files={"pdf": ("p2.pdf", pdf, "application/pdf")}, data={"schema_id": pin_g, "model": "fake-1", "api_key": "k"})
    assert r.status_code == 422 and "input.previous" in r.text
    r = c.post("/api/extract", headers=H, files={"pdf": ("p2.pdf", pdf, "application/pdf")},
               data={"schema_id": pin_g, "model": "fake-1", "api_key": "k", "run_id": run["id"], "step_id": "graph"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(prompts) == 2 and len(seen) == 1 and out["n_calls"] == 3
    assert "r1.claims[0]:" in seen[0] and "r2.claims[1]:" in seen[0] and "by reference" in seen[0]
    conn.rollback()
    view = records.document_view(conn, out["document_id"])
    assert view["schema_id"] == pin_g and view["chain"]["stages"][1]["previous"] is True and view["chain"]["aggregate"] == {"kind": "prompt"}
    recs = sorted(view["records"], key=lambda x: x["entry_index"])
    assert recs[0]["chain"]["support"] == {"n": 2, "k": 2} and recs[0]["chain"]["trace"][0]["ref"] == "r1.claims[0]"
    assert [x["value"] for x in recs[1]["chain"]["candidates"]["sign"]] == ["+", "-"]
    assert {e["field_path"] for e in view["evidence"]} >= {"claims[0]", "claims[1]"} and all(e["snippet"] == "workflow paper" for e in view["evidence"])
    run_docs = conn.execute("SELECT schema_id, role, step_id FROM extraction_document WHERE group_id = %s::uuid AND role = 'run'", (out["group_id"],)).fetchall()
    assert len(run_docs) == 2 and {r_[0] for r_ in run_docs} == {pin_c} and {r_[2] for r_ in run_docs} == {"claims"}
    rv_ = workflows.run_view(conn, workflows.get_run(conn, run["id"]))
    assert rv_["status"] == "done" and {s["step_id"]: s["status"] for s in rv_["steps"]}["claims"] == "done"
    monkeypatch.delenv("PAPERLENS_WORKFLOW_DIRS"); monkeypatch.delenv("PAPERLENS_PRESET_DIRS"); conn.close()


def test_a_loop_re_runs_the_papers_a_check_still_finds_problems_in(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    pin_c, _pin_g = _presets(tmp_path, monkeypatch)
    spec = _workflow(tmp_path, monkeypatch, [
        {"id": "claims", "kind": "extract", "preset": pin_c, "source": "live", "review": {"mode": "none"}},
        {"id": "contract", "kind": "check", "check": "schema", "over": ["claims"], "on_fail": "note", "severity": "error"},
        {"id": "fix", "kind": "loop", "steps": ["claims", "contract"], "until": {"check": "contract"}, "max_iterations": 2}], ["claims"])
    errs, _w = workflows.validate_for(conn, spec)
    assert errs == [] and workflows.later_problems(spec) == [], errs
    bad = copy.deepcopy(spec); bad["steps"][2]["steps"] = ["contract"]
    assert any("must include the extract step" in e for e in ws.validate(bad, resolver=presets.load_all().get)[0])
    sess = f"pipe-{uuid.uuid4().hex[:6]}"
    ds = _dataset(conn, sess, pin_c)
    conn.execute("UPDATE dataset SET workflow = %s WHERE id = %s::uuid", (ws.workflow_id(spec), ds)); conn.commit()
    who = _who(sess)
    pdf = _pdf("p3")
    run = workflows.start(conn, ds, who=who, new_pdfs=[{"sha256": _sha(pdf), "filename": "p3.pdf"}])
    prompts: list[str] = []
    # the first extraction leaves a required field empty (a schema finding); the second fixes it
    monkeypatch.setattr(extract, "_default_complete", _fake_complete([_claims_doc("-", cause0=None), _claims_doc("-")], prompts))
    c, H = _client(sess)
    r = c.post("/api/extract", headers=H, files={"pdf": ("p3.pdf", pdf, "application/pdf")},
               data={"schema_id": pin_c, "model": "fake-1", "api_key": "k", "run_id": run["id"], "step_id": "claims"})
    assert r.status_code == 200, r.text
    first_doc = r.json()["document_id"]
    conn.rollback()
    view = workflows.run_view(conn, workflows.get_run(conn, run["id"]))
    steps = {s["step_id"]: s for s in view["steps"]}
    assert steps["contract"]["outputs"]["findings"] >= 1 and view["status"] == "needs_key:claims", view["status"]   # the loop wants the key for the second attempt
    loop_out = steps["fix"]["outputs"]
    assert loop_out["iteration"] == 1 and loop_out["papers_rerun"] == [_sha(pdf)] and _sha(pdf) in loop_out["feedback"]
    r = c.post(f"/api/workflow-runs/{run['id']}/resume", headers=H, json={"api_key": "k", "model": "fake-1"})
    assert r.status_code == 200, r.text
    assert len(prompts) == 2 and "PREVIOUS ATTEMPT" in prompts[1] and "missing_required" in prompts[1]
    view = r.json()
    assert view["status"] == "done", view
    steps = {s["step_id"]: s for s in view["steps"]}
    assert steps["fix"]["outputs"]["stopped"] == "clean" and steps["fix"]["outputs"]["iteration"] == 1 and steps["contract"]["outputs"]["findings"] == 0
    conn.rollback()
    job = workflows.jobs(conn, run["id"], "claims")[0]
    assert job["tries"] == 2 and job["status"] == "done" and job["document_id"] != first_doc        # the second attempt
    assert conn.execute("SELECT count(*) FROM record WHERE dataset_id = %s::uuid AND document_id = %s::uuid", (ds, first_doc)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM record WHERE dataset_id = %s::uuid", (ds,)).fetchone()[0] == 2
    monkeypatch.delenv("PAPERLENS_WORKFLOW_DIRS"); monkeypatch.delenv("PAPERLENS_PRESET_DIRS"); conn.close()
