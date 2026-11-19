"""The GenAI-at-work review as a workflow: the three bundled validation presets, the bundled
`genai-work` workflow over them (three supplied sides, the judgment pass by two coders), and the
seeder's workflow mode that feeds it as the owner — end to end over the API. Skips without
Postgres."""
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

from paperlens import presets, records, review_gate, workflows
from paperlens import workflow_spec as ws

PRESETS = ("genai-work-judgment", "genai-work-extraction-check", "genai-work-routed")


def _seeder():
    spec = importlib.util.spec_from_file_location("seed_accounts", os.path.join(_ROOT, "scripts", "seed_accounts.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def _result_for(spec: dict, tag: str) -> dict:
    """A minimal import file of one paper under one of the validation presets: every declared
    paper field present (the first unit table with one row), the one `review` entry, one quote."""
    pm = {"title": f"Paper {tag}", "doi": f"10.1/{tag}", "year": 2024}
    first = None
    for f in spec["paper"]["fields"]:
        pm[f["name"]] = [] if f["type"] in ("table", "list", "multi") else None
        if f["type"] == "table" and f["name"] != "evidence_items" and first is None:
            first = f
    row = {c["name"]: None for c in first["columns"]}
    row[first["columns"][0]["name"]] = f"Unit of {tag}"
    pm[first["name"]] = [row]
    return {"paper_metadata": pm, spec["entries"]["key"]: [{"review_id": "paper", "full_review": None, "full_review_why": None}],
            "evidence": [{"snippet": f"Unit of {tag}", "page": 1, "source": "p. 1", "field": f"paper_metadata.{first['name']}[0]"}]}


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
    for pid in PRESETS:
        assert allp[pid]["meta"]["mode"] == "import" and not (allp[pid].get("prompt") or {}).get("text")
    wf = workflows.load_all()["genai-work"]
    assert ws.validate(wf)[0] == [] and workflows.later_problems(wf) == []
    by = ws.steps_by_id(wf)
    assert [s["id"] for s in wf["steps"]] == ["extraction", "judgment", "check", "routed", "record", "contract_judgment", "contract_check", "contract_routed", "release"]
    assert by["extraction"]["source"] == "supplied" and by["extraction"]["review"]["mode"] == "none" and by["record"]["check"] == "genai-work-record" and by["record"]["over"] == ["extraction"]
    assert all(by[f"contract_{s}"]["check"] == "schema" and by[f"contract_{s}"]["over"] == [s] for s in ("judgment", "check", "routed"))
    assert all(by[s]["source"] == "supplied" and by[s]["dataset"] == s for s in ("judgment", "check", "routed"))
    assert by["judgment"]["review"]["coders"] == 2 and by["check"]["review"]["coders"] == 1 and by["routed"]["review"]["coders"] == 1
    assert all(by[s]["review"] == {**by[s]["review"], "mode": "all", "unit": "paper", "blocks": "release"} for s in ("judgment", "check", "routed"))
    assert by["release"]["gate"] == ["judgment", "check", "routed"]
    assert ws.has_card(wf) and "## Steps" in wf["card"]["text"]
    for s in ("extraction", "judgment", "check", "routed"):   # the pins are the bundled files' contracts
        base = by[s]["preset"].split("@")[0]
        assert by[s]["preset"] == presets.emit_schema_row(base)["schema_id"], (s, by[s]["preset"])


def test_the_seeder_feeds_the_workflow_as_the_owner_and_the_coders_code_it(tmp_path) -> None:
    _skip()
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    allp = presets.load_all()
    owner, owner_id, o_email = _account("gw-owner")
    a, a_id, a_email = _account("gw-coder-a")
    b, b_id, b_email = _account("gw-coder-b")
    # the import files: two judgment papers, one check paper, no routed paper yet
    tag = uuid.uuid4().hex[:6]
    files = {"judgment": [f"j1-{tag}", f"j2-{tag}"], "check": [f"c1-{tag}"], "routed": [], "extraction": [f"x1-{tag}", f"x2-{tag}"]}
    for role, pid in (("judgment", "genai-work-judgment"), ("check", "genai-work-extraction-check"), ("routed", "genai-work-routed")):
        folder = tmp_path / f"import_{role}"; folder.mkdir()
        for rid in files[role]:
            (folder / f"{rid}.json").write_text(json.dumps(_result_for(allp[pid], rid)))
            (folder / f"{rid}.pdf").write_bytes(_pdf(rid))
    xf = tmp_path / "import_extraction"; xf.mkdir()
    for k, rid in enumerate(files["extraction"]):                                     # the second record trips a rule
        (xf / f"{rid}.json").write_text(json.dumps(_extraction_for(allp["genai-work-extraction"], rid, broken=(k == 1))))
        (xf / f"{rid}.pdf").write_bytes(_pdf(rid))
    manifest = {"workflow": "genai-work", "title": f"GenAI at work — {tag}", "owner": {"email": o_email, "password": "pw123456"},
                "coders": [a_email, {"email": b_email, "password": "pw123456"}], "produced_by": "pipeline (test)",
                "papers": {"extraction": "import_extraction", "judgment": "import_judgment", "check": "import_check", "routed": "import_routed"},
                "only": {"judgment": files["judgment"]}}
    seed = _seeder()
    assert seed.seed_workflow(TestClient(appmod.app), manifest, tmp_path, True) == {"dry": True}
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM dataset WHERE title = %s", (manifest["title"],)).fetchone()[0] == 0        # a dry run touches nothing
    out = seed.seed_workflow(TestClient(appmod.app), manifest, tmp_path, False, client_factory=lambda: TestClient(appmod.app))
    assert out["status"] == "done" and out["sides"] == {"extraction": {"imported": 2, "skipped": 0, "failed": 0}, "judgment": {"imported": 2, "skipped": 0, "failed": 0},
                                                       "check": {"imported": 1, "skipped": 0, "failed": 0}, "routed": {"imported": 0, "skipped": 0, "failed": 0}}, out
    wd, rid = out["workflow_dataset"], out["run"]
    # the owner's view: three sides linked, two coders, the gates assigned — judgment two per paper, check one, routed empty and satisfied
    g = owner.get(f"/api/datasets/{wd}/group").json()
    assert {r["role"] for r in g["roles"]} == {"extraction", "judgment", "check", "routed"} and all(r["dataset"] for r in g["roles"])
    # the record checks ran over the extraction side: one finding, on the broken paper's contrast row
    issues = owner.get(f"/api/workflow-runs/{rid}/issues?step=record").json()
    found = [f for f in issues.get("findings") or [] if f["step_id"] == "record"]
    assert [(f["code"], f["path"]) for f in found if f["severity"] == "error"] == [("n_obs_below_n", "results[1].n_obs")], found
    assert {f["code"] for f in found if f["severity"] != "error"} == {"quote_not_found"}       # the test PDF does not print the quote
    steps_done = {s["step_id"]: s["status"] for s in owner.get(f"/api/workflow-runs/{rid}").json()["steps"]}
    assert all(steps_done[s] == "done" for s in ("record", "contract_judgment", "contract_check", "contract_routed")), steps_done
    assert [c["email"] for c in owner.get(f"/api/datasets/{wd}/coders").json()["coders"]] == [a_email, b_email]
    view = owner.get(f"/api/workflow-runs/{rid}").json()
    gates = {s["step_id"]: s["review"] for s in view["steps"] if s.get("review")}
    assert gates["judgment"]["assigned"] and gates["judgment"]["total"] == 4 and sorted(b["total"] for b in gates["judgment"]["by_coder"]) == [2, 2]
    assert gates["check"]["assigned"] and gates["check"]["total"] == 1 and len(gates["check"]["by_coder"]) == 1
    assert gates["routed"]["total"] == 0 and gates["routed"]["satisfied"] is True
    assert "extraction" not in gates or gates["extraction"]["mode"] == "none"
    # a second seeding changes nothing: the papers are there, no new run is needed for them
    again = seed.seed_workflow(TestClient(appmod.app), manifest, tmp_path, False, client_factory=lambda: TestClient(appmod.app))
    assert again["workflow_dataset"] == wd and again["sides"]["judgment"] == {"imported": 0, "skipped": 2, "failed": 0} and again["sides"]["extraction"]["skipped"] == 2
    # the coders find the sides under "Coding for" and code the judgment papers; the gate closes on both verdicts
    coded = {d["group_role"] for d in a.get("/api/datasets?coder=1").json()["datasets"]}
    assert coded == {"extraction", "judgment", "check", "routed"} and all(d["workflow_title"] == manifest["title"] for d in a.get("/api/datasets?coder=1").json()["datasets"])
    for c, uid in ((a, a_id), (b, b_id)):
        q = c.get(f"/api/workflow-runs/{rid}/queue/judgment").json()
        assert q["viewer_coder"] == uid and len(q["items"]) == 2 and q["progress"]["pending"] == 2
        for it in q["items"]:
            assert c.post(f"/api/records/{it['record_id']}/verify", json={"status": "verified"}).status_code == 200
    conn.rollback()
    judg = review_gate.state(conn, workflows.get_step_run(conn, conn.execute(
        "SELECT id::text FROM workflow_step_run WHERE run_id = %s::uuid AND step_id = 'judgment'", (rid,)).fetchone()[0]))
    assert judg["satisfied"] is True and judg["checked"] == 4
    chk = owner.get(f"/api/workflow-runs/{rid}/queue/check").json()
    assert chk["satisfied"] is False and chk["progress"]["pending"] == 1 and chk["by_coder"][0]["label"] in ("coder_1", "coder_2")
    # the export of the judgment side carries both codings; the records as stored stay unverified
    jds = next(r["dataset"]["dataset_id"] for r in g["roles"] if r["role"] == "judgment")
    ex = owner.get(f"/api/datasets/{jds}/export").json()
    assert [c["label"] for c in ex["coders"]] == ["coder_1", "coder_2"]
    for p in ex["papers"]:
        assert p["records"][0]["verification_status"] == "unverified" and {c["coder"] for c in p["records"][0]["codings"]} == {"coder_1", "coder_2"}
    conn.close()
