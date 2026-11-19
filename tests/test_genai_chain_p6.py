"""Seeding the chain from the review project's files: a paper staged without dispatch with its own
page text, the seeder's supplies (the readings' annotations lifted onto the pass outputs), and the
whole ``genai-work-chain`` run end to end on supplied outputs — the document Metalens assembles
compared with the project's import file. Needs Postgres (and the project, for the last two)."""
from __future__ import annotations

import json
import os
import sys
import uuid

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE, os.path.join(_ROOT, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import compare_chain as CC  # noqa: E402
import seed_genai_chain as SEED  # noqa: E402
from test_analysis_table import _db_ok  # noqa: E402
from test_genai_chain_record import RID2  # noqa: E402
from test_genai_chain_replay import PROJECT, RID, _project_ok  # noqa: E402
from test_passes import _Fake, _dataset, _workflow  # noqa: E402
from test_workflows import _client, _force_local, _pdf, _sha  # noqa: E402

from paperlens import chains, passes, presets, providers, records  # noqa: E402


def test_a_paper_is_staged_without_dispatch_with_its_own_page_text(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    fake = _Fake(); monkeypatch.setattr(providers, "generate_json", fake)
    conn = records.connect(); records.init_db(conn)
    _workflow(tmp_path, monkeypatch)
    sess = f"stage-{uuid.uuid4().hex[:6]}"
    ds = _dataset(conn, sess)
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "demo-pass"}).status_code == 200
    pdf = _pdf("sigma"); sha = _sha(pdf)
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": sha, "filename": "sigma.pdf"}]}).json(); rid = run["id"]
    pages = ["The first page, as the pipeline read it.", "The second page."]
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("sigma.pdf", pdf, "application/pdf")},
               data={"dispatch": "false", "pages": json.dumps(pages), "title": "Sigma", "abstract": "An abstract.", "doi": "10.1/sigma"})
    assert r.status_code == 200 and r.json() == {"queued": False, "staged": True, "pdf_sha256": sha, "n_pages": 2}, r.text
    assert passes.page_texts_of(conn, sha) == pages and fake.calls == []                      # the supplied text stands, nothing ran
    paper = passes.paper_row(conn, rid, sha)
    assert paper["title"] == "Sigma" and paper["abstract"] == "An abstract." and paper["doi"] == "10.1/sigma" and paper["n_pages"] == 2
    assert c.get(f"/api/workflow-runs/{rid}", headers=H).json()["status"] == "running"
    bad = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("sigma.pdf", pdf, "application/pdf")}, data={"dispatch": "false", "pages": "not json"})
    assert bad.status_code == 422
    # the outputs supplied afterwards complete the chain without a call
    r = c.post(f"/api/workflow-runs/{rid}/passes/title/{sha}", headers=H, json={"output": {"title": "Sigma", "n_pages": 2, "words": [{"id": "w1", "word": "s"}]}})
    assert r.status_code == 200
    r = c.post(f"/api/workflow-runs/{rid}/passes/words/{sha}", headers=H, json={"output": {"title_len": 5, "words": [{"title_id": "w1", "word": "p1"}]}})
    assert r.status_code == 200
    assert c.get(f"/api/workflow-runs/{rid}", headers=H).json()["status"] == "done" and fake.calls == []
    conn.close()


@pytest.mark.skipif(not _project_ok(), reason="the review project is not on this machine")
def test_the_seeder_lifts_the_readings_onto_the_pass_outputs() -> None:
    sup = SEED.supplies(PROJECT, RID)
    steps = [(s["step"], s["role"], s["pass_index"]) for s in sup]
    assert steps[-6:] == [(s, "primary", 0) for s in SEED.STEP_ORDER]
    assert ("design", "run", 1) in steps and ("design", "run", 2) in steps and ("exhibits", "run", 1) in steps
    by = {s["step"]: s for s in sup if s["role"] == "primary"}
    rec = json.loads((PROJECT / "labeling" / "split" / RID / "record.json").read_text(encoding="utf-8"))
    assert by["design"]["output"]["study"]["_runs"] == rec["record"]["study"]["_runs"] and {k: v for k, v in by["design"]["stability"]["design_majority"].items() if k != "runs"} == {k: v for k, v in rec["stability"]["design_majority"].items() if k != "runs"}
    assert by["design"]["output"]["tasks"][0]["_runs"] == rec["record"]["tasks"][0]["_runs"] and by["estimands"]["output"]["tasks"][0]["_runs"]["found"] == rec["record"]["tasks"][0]["_runs"]["found"]
    rows = {r.get("estimate_id") for t in rec["record"]["tasks"] for r in t["results"] if r.get("vs_arm") and r.get("_runs")}
    assert rows and all(e.get("_runs") for e in by["estimates"]["output"]["estimates"] if e["estimate_id"] in rows)
    assert by["exhibits"]["prompt_sha256"] == rec["prompt_hash"] and by["design"]["prompt_sha256"] == "afbb8492d2"
    assert set(by["claims"]["output"]) == set(SEED.CLAIMS_KEYS) and by["claims"]["output"]["status"] == "done"
    assert set(by["headline_plan"]["output"]) == set(SEED.PLAN_KEYS) and by["headline_plan"]["output"]["decisions"]
    meta = SEED.paper_meta(PROJECT, RID)
    assert meta["title"].startswith("Cognitive offloading") and len(meta["abstract"]) > 100
    assert len(SEED.pages_for(PROJECT, RID)) > 5


@pytest.mark.skipif(not _project_ok() or not (PROJECT / "labeling" / "metalens" / "import_pilot" / f"{RID2}.pdf").exists(), reason="the review project is not on this machine")
def test_the_seeded_chain_reproduces_the_projects_import_file(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    monkeypatch.setattr(providers, "generate_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call expected")))
    conn = records.connect(); records.init_db(conn)
    chains.load()
    sess = f"seed-{uuid.uuid4().hex[:6]}"
    pin = presets.emit_schema_row("genai-work-extraction")["schema_id"]
    run_spec = presets.resolve_run(conn, schema_id=pin, params={})
    with conn.transaction():
        records.upsert_schema(conn, run_spec.schema_id, run_spec.field_defs)
    ds = records.create_dataset(conn, title="GenAI chain seed", schema_id=pin, session_id=sess)["id"]; conn.commit()
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "genai-work-chain"}).status_code == 200
    pdf = (PROJECT / "labeling" / "metalens" / "import_pilot" / f"{RID2}.pdf").read_bytes(); sha = _sha(pdf)
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": sha, "filename": f"{RID2}.pdf"}], "use_credits": False}).json()
    rid = run["id"]
    meta = SEED.paper_meta(PROJECT, RID2)
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": (f"{RID2}.pdf", pdf, "application/pdf")},
               data={"dispatch": "false", "pages": json.dumps(SEED.pages_for(PROJECT, RID2), ensure_ascii=False), "title": meta["title"], "abstract": meta["abstract"], "doi": meta["doi"] or "",
                     "journal": meta["journal"] or "", "year": meta["year"], "authors": json.dumps(meta["authors"]), "peer_reviewed": meta["peer_reviewed"] or ""})
    assert r.status_code == 200 and r.json()["staged"] is True, r.text
    for s in SEED.supplies(PROJECT, RID2, stale_plan=True):                 # the project's own import used this paper's (stale) plan file
        r = c.post(f"/api/workflow-runs/{rid}/passes/{s['step']}/{sha}", headers=H,
                   json={"output": s["output"], "role": s["role"], "pass_index": s["pass_index"], "prompt_sha256": s["prompt_sha256"], "stability": s["stability"]})
        assert r.status_code == 200, (s["step"], s["role"], r.text)
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    s_by = {s["step_id"]: s for s in view["steps"]}
    problems = [(s["step_id"], s["status"], s.get("error")) for s in view["steps"] if s.get("error")] + [(p["step_id"], p["error"]) for p in view.get("papers") or [] if p.get("error")]
    assert view["status"] == "done", (view["status"], problems)
    assert s_by["extraction"]["status"] == "done" and s_by["extraction"]["outputs"]["n_papers"] == 1
    assert s_by["passes_check"]["status"] == "done" and s_by["passes_check"]["outputs"]["n_checked"] == 1 and s_by["passes_check"]["outputs"]["findings"] > 0
    assert s_by["record"]["status"] == "done" and s_by["route"]["status"] == "done" and s_by["route"]["outputs"]["edited_fields"] == 4
    assert all(s_by[k]["review"]["total"] == 1 for k in ("review_judgment", "review_check")) and s_by["review_judgment"]["review"]["satisfied"] is False
    # the document equals the project's import file, field for field
    export = c.get(f"/api/datasets/{ds}/export", headers=H).json()
    paper = next(p for p in export["papers"] if p["filename"] == f"{RID2}.pdf")
    theirs = json.loads((PROJECT / "labeling" / "metalens" / "import_pilot" / f"{RID2}.json").read_text(encoding="utf-8"))
    res = CC.compare_paper(theirs, paper)
    assert res["diffs"] == [], res["diffs"][:30]
    assert res["routed"] == (theirs["paper_metadata"]["routed"], theirs["paper_metadata"]["routed"]) and res["flags"][0] == res["flags"][1]
    docs = c.get("/api/documents", headers=H, params={"dataset": ds}).json()["documents"]
    v = c.get(f"/api/documents/{docs[0]['document_id']}/view", headers=H).json()
    assert v["chain"]["kind"] == "compute" and [st_["step"] for st_ in v["chain"]["stages"]] == list(SEED.STEP_ORDER) and all(st_["supplied"] for st_ in v["chain"]["stages"])
    assert v["records"][0]["chain"]["kind"] == "match" and v["records"][0]["chain"]["support"]["k"] >= 1
    conn.close()
