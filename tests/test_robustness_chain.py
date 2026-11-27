"""The robustness chain: a project's deterministic steps (rendered from its package) run inside a
workflow over three supplied datasets — the headline rule from the claims and the grading of the
re-estimation, both written on the tables through the audited path; a compute's ``reads`` naming
data steps; chains installed beside the engine. The run skips without Postgres."""
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

from test_analysis_table import _db_ok, _seed                   # noqa: E402
from test_register_presets import CLAIMS                         # noqa: E402
from test_workflows import _client, _force_local, _pdf, _result, _sha   # noqa: E402

from paperlens import chains, records, workflows                 # noqa: E402
from paperlens import workflow_spec as ws                        # noqa: E402
from paperlens.chains.robustness import computes as RC           # noqa: E402


# ── the shared code, on plain dicts ────────────────────────────────────────────────────────

def test_grading_at_printed_precision_sign_never_ignored_scale_recorded() -> None:
    assert abs(RC.printed_tolerance("0.033", 0.033) - 0.0005) < 1e-15 and RC.printed_tolerance("1,234", 1234) == 0.5
    assert RC.detect_scale([(0.033, 3.3), (0.041, 4.1), (0.12, 12.0), (0.07, 7.0)])[0] == 100.0
    assert RC.detect_scale([(0.033, 3.3), (0.041, 4.1)]) == (1.0, "")            # too few cells to infer a scale
    truth = [{"regression_id": "T1::_::C1", "cells": [
        {"row_index": 0, "row_type": "coefficient", "row_label": "x", "raw_text": "0.033", "numeric_value": 0.033},
        {"row_index": 1, "row_type": "se", "row_label": "", "raw_text": "(0.010)", "numeric_value": 0.010},
        {"row_index": 2, "row_type": "coefficient", "row_label": "y", "raw_text": "-0.114", "numeric_value": -0.114},
        {"row_index": 3, "row_type": "statistic_n_obs", "row_label": "N", "raw_text": "1,234", "numeric_value": 1234},
        {"row_index": 4, "row_type": "string", "row_label": "FE", "raw_text": "Yes", "numeric_value": None}]}]
    targets = [{"regression_id": "T1::_::C1", "status": "ok", "method": "stata", "reason": None, "n_obs": 1230,
                "cells": [{"row_index": 0, "numeric_value": 0.0334}, {"row_index": 2, "numeric_value": 0.1139}]}]
    g = RC.grade_regressions(truth, targets)
    reg = g["per_reg"]["T1::_::C1"]
    assert (reg["n_cells"], reg["n_match"], reg["n_sign_flip"], reg["n_missing"]) == (3, 1, 1, 1)   # N and Yes are not graded
    assert reg["sample_mismatch"] is True and reg["n_obs_published"] == 1234
    assert [r["verdict"] for r in g["rows"]] == ["match", "missing", "sign_flip"]
    assert RC.reproduction_of(reg) == "sign_flip" and RC.reproduction_of(None) == "not_returned"
    assert RC.reproduction_of({"status": "failed", "method": "not_attempted"}) == "not_attempted"
    assert RC.reproduction_of({"status": "ok", "n_cells": 4, "n_match": 4, "n_sign_flip": 0}) == "match"
    assert RC.reproduction_of({"status": "ok", "n_cells": 4, "n_match": 2, "n_sign_flip": 0}) == "partial"
    assert RC.closeness(g["rows"])["within_5pct"] == 1


def test_the_headline_rule_reads_the_same_on_the_pipeline_files_and_on_the_import() -> None:
    stated = {"claims": [{"id": "S1", "type": "edge"}, {"id": "S2", "type": "not_causal"}]}
    mp = {"claims": [{"id": "S1", "matches": [{"regression_id": "p::T3::_::C1", "role": "main", "pointed_to": "reference"},
                                             {"regression_id": "p::T3::_::C2", "role": "supporting", "pointed_to": "none"}]},
                     {"id": "S2", "matches": [{"regression_id": "p::T4::_::C1", "role": "main", "pointed_to": "number"}]}]}
    links = RC.headline_links(stated, mp)
    assert set(links) == {"p::T3::_::C1"} and links["p::T3::_::C1"] == {"n_main": 1, "claims": {"S1"}, "pointed": True}
    assert RC.headline_links(stated, mp, excl={"p::T3::_::C1"}) == {}
    tables = [{"regression_id": "T3::_::C1", "table_number": "Table 3", "panel": None, "column": "(1)"},
              {"regression_id": "T3::_::C2", "table_number": "Table 3", "panel": "", "column": "(2)"}]
    claims = [{"claim_id": "S1", "type": "edge", "results": [
                  {"result_id": "R1", "role": "main", "source_table": "Table 3", "panel": None, "column": "(1)", "carries_stated_number": None,
                   "why": 'Tests it. The text points to this estimate by reference: "column 1 of Table 3"'},
                  {"result_id": "R2", "role": "supporting", "source_table": "Table 3", "panel": None, "column": "(2)"},
                  {"result_id": "R3", "role": "main", "source_table": "Table 9", "panel": None, "column": "(7)"}]},
              {"claim_id": "S2", "type": "not_causal", "results": [{"result_id": "R1", "role": "main", "source_table": "Table 3", "column": "(2)"}]}]
    head, unlinked = RC.headline_links_import(claims, tables)
    assert set(head) == {"T3::_::C1"} and head["T3::_::C1"] == {"n_main": 1, "claims": {"S1"}, "pointed": True}
    assert [(u["claim_id"], u["result_id"]) for u in unlinked] == [("S1", "R3")]


def test_an_installed_chain_registers_through_the_environment_and_never_shadows_a_bundled_one(tmp_path, monkeypatch) -> None:
    ext = tmp_path / "ext_chain"; ext.mkdir()
    (ext / "__init__.py").write_text('CHAIN_ID = "ext"\n\ndef _touch(ctx):\n    return {"records": {}, "paper": {}, "findings": []}\n\n'
                                     'def register():\n    return {"touch": {"kind": "compute", "label": "Touch", "produces": "fields", "fn": _touch}}\n')
    dup = tmp_path / "dup_demo"; dup.mkdir()
    (dup / "__init__.py").write_text('CHAIN_ID = "_demo"\n\ndef register():\n    raise AssertionError("a bundled id is never shadowed")\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PAPERLENS_CHAINS", os.pathsep.join(["ext_chain", "dup_demo"]))
    chains.reset()
    try:
        reg = chains.load()
        assert "ext.touch" in reg and reg["ext.touch"]["base_dir"] == ext and reg["ext.touch"]["produces"] == "fields"
        assert "_demo.mark" in reg and "robustness.grade" in reg and "robustness.headlines" in reg
    finally:
        monkeypatch.delenv("PAPERLENS_CHAINS"); chains.reset(); chains.load()


def test_the_robustness_workflows_validate_and_a_compute_may_read_a_data_step() -> None:
    wf = workflows.load_all()
    for wid in ("robustness", "robustness-regressions", "robustness-claims"):
        errs, _ = ws.validate(wf[wid]); assert errs == [], (wid, errs)
    spec = wf["robustness"]
    by = ws.steps_by_id(spec)
    assert by["headlines"]["reads"] == ["claims"] and by["grade"]["reads"] == ["reproduced"] and by["grade"]["edits"] == "tables"
    bad = json.loads(json.dumps(spec)); next(s for s in bad["steps"] if s["id"] == "grade")["reads"] = ["joint"]
    assert any("is not an earlier pass step or data step" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); next(s for s in bad["steps"] if s["id"] == "grade")["reads"] = ["claims"]
    assert any("does not read 'claims'" in e for e in ws.validate(bad)[0])          # the registration says what it reads


# ── the run ────────────────────────────────────────────────────────────────────────────────

def _reproduced(tables: dict) -> dict:
    """Re-estimation results for the two columns of ``tables``: the first reproduces every withheld
    cell exactly, the second flips the sign of its coefficient and leaves the other statistic out."""
    targets = []
    for i, r in enumerate(tables["regressions"]):
        cells, n_obs = [], None
        for c in r["cells"]:
            if c.get("row_type") == "statistic_n_obs":
                n_obs = c.get("numeric_value")
            if c.get("numeric_value") is None or not RC.BLIND_POLICY.get(c.get("row_type"), True):
                continue
            v = c["numeric_value"]
            if i == 1 and c["row_type"] == "coefficient":
                v = -v
            elif i == 1 and c["row_type"] == "statistic_other":
                continue
            cells.append({"row_index": c["row_index"], "col_index": 0, "raw_text": repr(v), "numeric_value": v})
        targets.append({"regression_id": r["regression_id"], "status": "ok", "method": "stata", "reason": None, "detail": None,
                        "n_obs": n_obs, "do_file": "main.do", "command": "regress y x", "spec_id": f"spec{i}", "cells": cells})
    return {"paper_metadata": {**tables["paper_metadata"], "run_id": "test-run", "model": "m", "stata_version": "19", "n_targets": len(targets), "n_changes": 0},
            "targets": targets, "evidence": []}


def test_the_robustness_workflow_writes_headlines_and_grades_on_the_tables(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    sess = f"rob-{uuid.uuid4().hex[:6]}"
    c, H = _client(sess)
    ds_t, _doc0 = _seed(conn, _result("p0", n=1), "robustness-regressions", sess)
    r = c.post("/api/workflow-datasets", headers=H, json={"workflow": "robustness", "datasets": {"tables": ds_t}})
    assert r.status_code == 200, r.text
    wd = r.json(); assert wd["workflow"].startswith("robustness@")
    roles = {}
    for role in ("claims", "reproduced"):
        g = c.post(f"/api/datasets/{wd['id']}/group/link", headers=H, json={"role": role, "create": True}).json()
        roles[role] = g["roles"][role]["dataset_id"]
    panel = c.get(f"/api/datasets/{wd['id']}/workflow", headers=H).json()
    by = {s["id"]: s for s in panel["steps"]}
    assert by["reproduced"]["dataset_id"] == roles["reproduced"] and by["headlines"]["kind"] == "compute" and by["grade"]["kind"] == "compute"
    # the run: one new paper, three uploads, the computes run as the uploads close
    pdf = _pdf("w1"); sha = _sha(pdf)
    run = c.post(f"/api/datasets/{wd['id']}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": sha, "filename": "w1.pdf"}]}).json()
    assert run["status"] == "running" and run["now"]["phase"] == "waiting for uploads"
    tables = _result("w1", n=2)
    claims = copy.deepcopy(CLAIMS); claims["paper_metadata"]["doi"] = "10.1/w1"; claims["paper_metadata"]["title"] = "Paper w1"
    cl = claims["claims"][0]; cl["type"] = "edge"
    first = tables["regressions"][0]
    cl["results"] = [dict(cl["results"][0], result_id="R1", role="main", source_table=first["table_number"], panel=first.get("panel"),
                          column=first["column"], carries_stated_number=True)]
    for other in claims["claims"][1:]:
        other["results"] = []
    rep = _reproduced(tables)
    for step, doc in (("tables", tables), ("claims", claims), ("reproduced", rep)):
        r = c.post("/api/ingest-pdf", headers=H, files={"pdf": ("w1.pdf", pdf, "application/pdf")},
                   data={"result": json.dumps(doc), "run_id": run["id"], "step_id": step})
        assert r.status_code == 200, (step, r.text)
    view = c.get(f"/api/workflow-runs/{run['id']}", headers=H).json()
    s_by = {s["step_id"]: s for s in view["steps"]}
    # the computes ran as the third upload landed; the joint review holds only the release, so the run is done
    assert view["status"] == "done", (view["status"], view.get("error"), [(k, v["status"], v.get("error")) for k, v in s_by.items()],
                                      [(p["step_id"], p["status"], p["error"]) for p in view["papers"] if p.get("error")])
    assert s_by["headlines"]["status"] == "done" and s_by["grade"]["status"] == "done"
    assert s_by["headlines"]["outputs"]["n_papers"] == 1 and s_by["headlines"]["outputs"]["edited_records"] == 2 and s_by["headlines"]["outputs"]["findings"] == 0
    assert s_by["grade"]["outputs"]["n_papers"] == 1 and s_by["grade"]["outputs"]["edited_records"] == 2 and s_by["grade"]["outputs"]["findings"] == 2
    # the tables document of the new paper carries the headline and the reproduction verdicts, the paper its totals
    docs = c.get("/api/documents", headers=H, params={"dataset": ds_t}).json()["documents"]
    did = next(d["document_id"] for d in docs if (d.get("title") or "").endswith("w1") or d.get("filename") == "w1.pdf")
    v = c.get(f"/api/documents/{did}/view", headers=H).json()
    fv = {r_["field_values"]["regression_id"]: r_["field_values"] for r_ in v["records"]}
    c1, c2 = fv["w1::T3::_::C1"], fv["w1::T3::_::C2"]
    assert (c1["headline"], c1["n_main"], c1["main_of"], c1["pointed"]) == (True, 1, [cl["claim_id"]], True)
    assert (c2["headline"], c2["n_main"], c2["main_of"]) == (False, 0, [])
    assert c1["reproduction"] == "match" and c1["cells_graded"] == c1["cells_matched"] > 0 and c1["sign_flips"] == 0
    assert c2["reproduction"] == "sign_flip" and c2["sign_flips"] == 1 and c2["cells_matched"] == c2["cells_graded"] - 2
    conn.rollback()
    pm = conn.execute("SELECT paper_metadata_raw FROM extraction_document WHERE id = %s::uuid", (did,)).fetchone()[0]
    assert pm["n_headline"] == 1 and pm["n_main_links"] == 1 and pm["n_main_unlinked"] == 0
    assert pm["reproduced_total"] == 2 and pm["reproduced_ok"] == 2 and pm["n_sign_flips"] == 1 and pm["sample_mismatches"] == 0
    assert pm["n_cells_graded"] == c1["cells_graded"] + c2["cells_graded"] and 0 < pm["match_rate"] < 1
    # every edit is an audited correction by the engine; the cells that did not reproduce are findings
    ev = conn.execute("""SELECT DISTINCT ve.verifier_kind, ve.status, ve.notes FROM verification_event ve JOIN record r ON r.id = ve.record_id
                         WHERE r.document_id = %s::uuid""", (did,)).fetchall()
    assert set(ev) == {("engine", "corrected", "compute:robustness.headlines"), ("engine", "corrected", "compute:robustness.grade")}
    codes = conn.execute('SELECT "check", code, count(*) FROM workflow_finding WHERE run_id = %s::uuid GROUP BY 1, 2', (run["id"],)).fetchall()
    by_code = {(ck, code): n for ck, code, n in codes}
    assert by_code.get(("robustness.grade", "cell_sign_flip")) == 1 and by_code.get(("robustness.grade", "cell_missing")) == 1
    # the joint review holds the release (the new paper's two columns and two claims); the paper with a sign
    # flip is routed to the flips review (its two columns), which never holds it
    joint, flips = s_by["joint"]["review"], s_by["flips"]["review"]
    assert joint["total"] == 4 and joint["satisfied"] is False and joint["blocks"] == "release", joint
    assert s_by["flips"]["status"] == "done" and flips["total"] == 2 and flips["blocks"] == "none", flips
    conn.close()
