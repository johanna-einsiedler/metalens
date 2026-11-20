"""The GenAI-at-work review as a workflow: the bundled `genai-work` workflow over the pipeline's
extraction record — three review tasks, each a bundled template, the judgment by two coders —
and the seeder's workflow mode that feeds it as the owner, end to end over the API: the record
checks, the tasks assigned, the coders answering and closing them, the export with every
coding's answers. Skips without Postgres."""
from __future__ import annotations

import importlib.util
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
from test_workflows import _pdf, _skip

from paperlens import presets, records, review_gate, templates as tp, workflows
from paperlens import workflow_spec as ws



def _seeder():
    spec = importlib.util.spec_from_file_location("seed_accounts", os.path.join(_ROOT, "scripts", "seed_accounts.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod




def _extraction_for(spec: dict, tag: str, *, broken: bool = False) -> dict:
    """An import file of one paper under the extraction preset: one task, two arms, a reference
    row and a contrast row; ``broken`` makes the contrast row's n_obs fall below its n."""
    pm = {"title": f"Paper {tag}", "doi": f"10.1/{tag}", "year": 2024}
    for f in spec["paper"]["fields"]:
        pm[f["name"]] = [] if f["type"] in ("table", "list", "multi") else None
    pm["period_start_year"] = 2023
    ref = {"arm": "C", "vs_arm": None, "measure": "minutes", "n": 50, "mean": 25.0, "sd": 4.0, "measure_kind": "duration"}
    row = {"arm": "T", "vs_arm": "C", "measure": "minutes", "contrast_type": "ai_vs_no_ai", "estimand_type": "itt", "n": 50, "n_obs": 10 if broken else 50,
           "mean": 20.0, "sd": 4.0, "effect_reported": "-5.0", "effect_value": -5.0, "effect_type": "mean_difference", "se": 0.8,
           "ci_low": -6.6, "ci_high": -3.4, "p_value": 0.001, "p_reported": "p < 0.01", "is_main": True, "source": "Table 1, col (1), row AI",
           "measure_kind": "duration", "estimate": f"E1_{tag}"}
    task = {"task_id": "T1", "task_name": "Write a memo", "outcomes": [], "results": [ref, row],
            "arms": [{"arm_id": "C", "arm_label": "no AI", "is_baseline": True, "control_type": "no_ai_concurrent", "n_arm": 50, "sample_population_type": "students"},
                     {"arm_id": "T", "arm_label": "AI", "is_baseline": False, "n_arm": 50, "sample_population_type": "students"}]}
    return {"paper_metadata": pm, "tasks": [task],
            "evidence": [{"snippet": "AI −5.0 (0.8)", "page": 1, "source": "Table 1", "field": "tasks[0].results[1].effect_value"}]}


def test_the_presets_and_the_workflow_are_bundled_and_validate() -> None:
    allp = presets.load_all()
    assert not ({"genai-work-judgment", "genai-work-extraction-check", "genai-work-routed"} & set(allp))   # retired: templates over the record
    assert allp["genai-work-extraction"]["meta"]["mode"] == "import" and not (allp["genai-work-extraction"].get("prompt") or {}).get("text")
    wf = workflows.load_all()["genai-work"]
    assert ws.validate(wf)[0] == [] and workflows.later_problems(wf) == []
    by = ws.steps_by_id(wf)
    assert [s["id"] for s in wf["steps"]] == ["extraction", "record", "review_judgment", "review_check", "review_routed", "contract", "release"]
    assert by["extraction"]["source"] == "supplied" and by["extraction"]["dataset"] == "extraction" and by["record"]["check"] == "genai-work-record"
    assert by["contract"]["check"] == "schema" and by["contract"]["over"] == ["extraction"]
    assert all(by[s]["over"] == ["extraction"] and by[s]["template"] == t for s, t in (("review_judgment", "genai-work-judgment"), ("review_check", "genai-work-check"), ("review_routed", "genai-work-routed")))
    assert by["review_judgment"]["coders"] == 2 and by["review_judgment"]["unit"] == "paper" and by["review_judgment"]["mode"] == "all"
    assert by["review_check"]["unit"] == "row" and by["review_check"]["table"] == "exhibits" and by["review_check"]["per_paper"] == 1 and by["review_check"]["where"] == {"n_cited": {"gt": 0}}
    assert by["review_routed"]["where"] == {"paper_metadata.routed": True} and by["review_routed"]["coders"] == 1
    assert by["release"]["gate"] == ["extraction", "review_judgment", "review_check", "review_routed"]
    assert ws.roles(wf) == {"extraction": {"step": "extraction", "preset": by["extraction"]["preset"], "label": "Extraction record"}}
    assert ws.workflow_id(wf) == "genai-work@861f2667" and wf["version"] == 5
    assert ws.has_card(wf) and "## Steps" in wf["card"]["text"]
    assert by["extraction"]["preset"] == presets.emit_schema_row("genai-work-extraction")["schema_id"]


def _answer_everything(client, doc: str, task: str, tpl: dict, pspec: dict) -> int:
    """Answer every required question of a task on a document with its first option (or a word),
    as a coder would, until nothing is owed; returns how many answers it took."""
    n = 0
    for _ in range(6):
        v = client.get(f"/api/documents/{doc}/view?task={task}").json()
        sampled = {k: set(vals) for k, vals in (v.get("task") or {}).get("sampled", {}).items()} or None
        comp = tp.completeness(tpl, v, pspec, v["answers"], sampled=sampled)
        if comp["complete"]:
            return n
        blocks = {(st["id"], i): bl for st in tpl["stages"] for i, bl in enumerate(st["blocks"])}
        for m in comp["missing"]:
            q = next(q for q in tp.question_defs(blocks[(m["stage"], m["block"])], pspec) if q["name"] == m["question"])
            val = (q["options"][0]["value"] if isinstance(q["options"][0], dict) else q["options"][0]) if q.get("options") else ("x" if q.get("type") in ("string", "text") else 1)
            r = client.post(f"/api/documents/{doc}/answers", json={"task": task, "record_id": m["record_id"], "unit": m["unit"], "answers": {m["question"]: val}})
            assert r.status_code == 200, r.text
            n += 1
    raise AssertionError("still owed after six rounds")


def test_the_seeder_feeds_the_workflow_as_the_owner_and_the_coders_work_the_three_tasks(tmp_path) -> None:
    _skip()
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    allp = presets.load_all()
    ext = allp["genai-work-extraction"]
    owner, owner_id, o_email = _account("gw-owner")
    a, a_id, a_email = _account("gw-coder-a")
    b, b_id, b_email = _account("gw-coder-b")
    # two papers of the extraction record: the second trips a record check, is routed, has a cited exhibit and a flag
    tag = uuid.uuid4().hex[:6]
    files = [f"x1-{tag}", f"x2-{tag}"]
    xf = tmp_path / "import_extraction"; xf.mkdir()
    for k, rid in enumerate(files):
        obj = _extraction_for(ext, rid, broken=(k == 1))
        pm = obj["paper_metadata"]
        pm["exhibits"] = [{"exhibit_id": "X1", "kind": "table", "page": 2, "caption": "Table 1", "panels": [], "n_cells": 2, "n_cited": 1},
                          {"exhibit_id": "X2", "kind": "figure", "page": 3, "caption": "Figure 1", "panels": [], "n_cells": 1, "n_cited": 0}]
        pm["exhibit_cells"] = [{"exhibit_id": "X1", "panel": None, "row_label": "AI", "column": "(1)", "value_as_printed": "-5.0", "se_as_printed": "0.8",
                                "is_se": False, "arm_stats": False, "estimate_id": f"E1_{rid}", "estimand_id": None, "estimand_text": "memo minutes", "role": "contrast"},
                               {"exhibit_id": "X1", "panel": None, "row_label": "N", "column": "(1)", "value_as_printed": "100", "se_as_printed": None,
                                "is_se": False, "arm_stats": True, "estimate_id": None, "estimand_id": None, "estimand_text": None, "role": "other"}]
        pm["routed"] = k == 1
        pm["route_reasons"] = ["check:n_obs_below_n"] if k == 1 else []
        pm["triage_score"] = 0.9 if k == 1 else 0.1
        pm["flags"] = [{"flag_id": "F1", "group": "checks", "estimate_id": f"E1_{rid}", "kind": "check_fail", "code": "n_obs_below_n", "message": "n_obs 10 < n 50",
                        "source": "Table 1", "extracted": "10", "page": 2}] if k == 1 else []
        (xf / f"{rid}.json").write_text(json.dumps(obj))
        (xf / f"{rid}.pdf").write_bytes(_pdf(rid))
    manifest = {"workflow": "genai-work", "title": f"GenAI at work — {tag}", "owner": {"email": o_email, "password": "pw123456"},
                "coders": [a_email, {"email": b_email, "password": "pw123456"}], "produced_by": "pipeline (test)",
                "papers": {"extraction": "import_extraction"}, "tasks": {"review_routed": {"coders": [b_email]}}}
    seed = _seeder()
    assert seed.seed_workflow(TestClient(appmod.app), manifest, tmp_path, True) == {"dry": True}
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM dataset WHERE title = %s", (manifest["title"],)).fetchone()[0] == 0        # a dry run touches nothing
    out = seed.seed_workflow(TestClient(appmod.app), manifest, tmp_path, False, client_factory=lambda: TestClient(appmod.app))
    assert out["status"] == "done" and out["sides"] == {"extraction": {"imported": 2, "skipped": 0, "failed": 0}}, out
    pid, rid = out["workflow_dataset"], out["run"]
    # the project: one dataset, two people; the record checks found the broken row
    g = owner.get(f"/api/datasets/{pid}/group").json()
    assert [r["role"] for r in g["roles"]] == ["extraction"] and g["roles"][0]["dataset"]
    xds = g["roles"][0]["dataset"]["dataset_id"]
    issues = owner.get(f"/api/workflow-runs/{rid}/issues?step=record").json()
    found = [f for f in issues.get("findings") or [] if f["step_id"] == "record"]
    assert [(f["code"], f["path"]) for f in found if f["severity"] == "error"] == [("n_obs_below_n", "results[1].n_obs")], found
    assert [c["email"] for c in owner.get(f"/api/projects/{pid}/people").json()["people"]] == [a_email, b_email]
    # the three tasks: judgment two per paper (both coders), check one exhibit per paper, routed the routed paper only
    ts = {t["step_id"]: t for t in owner.get(f"/api/projects/{pid}/tasks").json()["tasks"]}
    assert set(ts) == {"review_judgment", "review_check", "review_routed"} and all(t["datasets"] == [xds] for t in ts.values())
    jd, ck, rt = ts["review_judgment"], ts["review_check"], ts["review_routed"]
    assert jd["template"] == "genai-work-judgment" and jd["gate"]["assigned"] and jd["gate"]["total"] == 4 and sorted(b_["total"] for b_ in jd["gate"]["by_coder"]) == [2, 2]
    assert ck["unit"] == "row" and ck["gate"]["total"] == 2 and ck["gate"]["drawn"] == 2 and all(it["unit_key"].startswith("exhibits#") for it in owner.get(f"/api/workflow-runs/{rid}/queue/review_check").json()["items"])
    assert rt["gate"]["total"] == 1 and rt["gate"]["assigned"]
    docs = {d["title"]: d["document_id"] for d in owner.get("/api/documents", params={"dataset": xds}).json()["documents"]}
    routed_doc = docs[f"Paper x2-{tag}"]
    assert [x["document_id"] for x in rt["assignments"]] == [routed_doc]
    assert rt["config"] == {"coders": [b_id]} and rt["assignments"][0]["coders"][0]["user_id"] == b_id      # the manifest's `tasks` named coder B
    # a second seeding changes nothing
    again = seed.seed_workflow(TestClient(appmod.app), manifest, tmp_path, False, client_factory=lambda: TestClient(appmod.app))
    assert again["workflow_dataset"] == pid and again["sides"]["extraction"] == {"imported": 0, "skipped": 2, "failed": 0}
    assert again["run"] == rid and len(owner.get(f"/api/projects/{pid}").json()["runs"]) == 1      # nothing to upload: no new run
    # access follows the task: both coders read the extraction dataset, the owner sees every task on the project page
    for c in (a, b):
        assert [d["id"] for d in c.get("/api/datasets?coder=1").json()["datasets"]] == [xds]
        assert {t["step_id"] for t in c.get("/api/me/tasks").json()["tasks"]} >= {"review_judgment"}
    # the judgment: each coder answers the template's questions on their papers, blind, and marks them done
    jt = tp.get("genai-work-judgment")
    for c, uid in ((a, a_id), (b, b_id)):
        q = c.get(f"/api/workflow-runs/{rid}/queue/review_judgment").json()
        assert q["viewer_coder"] == uid and len(q["items"]) == 2 and q["template"] == "genai-work-judgment"
        for d in {it["document_id"] for it in q["items"]}:
            v = c.get(f"/api/documents/{d}/view?task={q['task']}").json()
            assert "task_label" not in v["records"][0]["field_values"] and v["template"]["blind"] is True
            assert c.post(f"/api/records/{v['records'][0]['id']}/verify", json={"status": "verified", "task": q["task"]}).status_code == 422   # answers first
            assert _answer_everything(c, d, q["task"], jt, ext) >= 8
            assert c.post(f"/api/records/{v['records'][0]['id']}/verify", json={"status": "verified", "task": q["task"]}).status_code == 200
    conn.rollback()
    judg = review_gate.state(conn, workflows.get_step_run(conn, conn.execute(
        "SELECT id::text FROM workflow_step_run WHERE run_id = %s::uuid AND step_id = 'review_judgment'", (rid,)).fetchone()[0]))
    assert judg["satisfied"] is True and judg["checked"] == 4
    # the check: the assigned coder sees the drawn exhibit and its cells alone, answers them, marks the paper done
    ct = tp.get("genai-work-check")
    for x in ck["assignments"]:
        c = a if x["coders"][0]["user_id"] == a_id else b
        v = c.get(f"/api/documents/{x['document_id']}/view?task={rid}:review_check").json()
        assert v["task"]["sampled"]["_keys"] and len(v["task"]["sampled"]["_keys"]) == 1 and v["task"]["sampled"]["exhibits.exhibit_id"] == ["X1"]
        assert _answer_everything(c, x["document_id"], f"{rid}:review_check", ct, ext) >= 3       # value_ok twice, mapping_ok once, full_review
        assert c.post(f"/api/records/{v['records'][0]['id']}/verify", json={"status": "verified", "task": f"{rid}:review_check"}).status_code == 200
    # the routed paper: its flag resolved by its coder
    rtt = tp.get("genai-work-routed")
    c = a if rt["assignments"][0]["coders"][0]["user_id"] == a_id else b
    v = c.get(f"/api/documents/{routed_doc}/view?task={rid}:review_routed").json()
    assert _answer_everything(c, routed_doc, f"{rid}:review_routed", rtt, ext) >= 2
    assert c.post(f"/api/records/{v['records'][0]['id']}/verify", json={"status": "verified", "task": f"{rid}:review_routed"}).status_code == 200
    conn.rollback()
    ts2 = {t["step_id"]: t for t in owner.get(f"/api/projects/{pid}/tasks").json()["tasks"]}
    assert all(t["gate"]["satisfied"] for t in ts2.values()), {k: t["gate"]["pending"] for k, t in ts2.items()}
    assert owner.get(f"/api/datasets/{pid}/releases/pending").json()["gate"]["open"] is True
    # the export of the record carries both codings with their answers; the records as stored stay unverified
    ex = owner.get(f"/api/datasets/{xds}/export").json()
    assert [c_["label"] for c_ in ex["coders"]] == ["coder_1", "coder_2"]
    for p in ex["papers"]:
        rec = p["records"][0]
        assert rec["verification_status"] == "unverified" and {c_["coder"] for c_ in rec["codings"]} == {"coder_1", "coder_2"}
        assert all("genai-work-judgment" in (c_.get("answers") or {}) for c_ in rec["codings"])
        assert any("genai-work-judgment" in (x.get("answers") or {}) for x in p["paper_codings"])
    conn.close()
