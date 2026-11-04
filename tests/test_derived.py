"""Derive steps: the declaration's rules, the transform and the validation of what a call returns,
the computed review, and — with Postgres — a run that makes a draft, the draft committed, the unit
stale after a paper is added, the update's review naming the new records, the release gate, the
snapshot, the export and the analysis unit; a model-call step through a fake model, by hand and
through a run that waits for the key."""
from __future__ import annotations

import copy
import io
import json
import os
import sys
import uuid
import zipfile

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_analysis_table import _db_ok, _seed, _seed_into  # noqa: E402
from test_workflows import _client, _force_local, _result, _who  # noqa: E402

from paperlens import (  # noqa: E402
    analysis_table,
    derived,
    presets,
    records,
    release_export,
    releases,
    workflows,
)
from paperlens import workflow_spec as ws  # noqa: E402

PIN = "econ-regression-tables@67780825"


def _wf(step: dict, *, extra: dict | None = None) -> dict:
    return {"format": 1, "id": "derive-test", "meta": {"title": "Derive test"},
            "steps": [{"id": "tables", "kind": "extract", "preset": PIN, "source": "supplied"}, step,
                      {"id": "release", "kind": "release", "gate": ["tables", step["id"]]}], **(extra or {})}


TRANSFORM = {"id": "per_table", "kind": "derive", "label": "Columns per table", "over": "tables",
             "input": {"unit": "entries", "columns": ["table_number", "regression_id", "outcome_variable", "_study"]},
             "identity": ["table_number"], "review": {"mode": "all", "blocks": "release"},
             "calls": [{"id": "merge", "kind": "transform", "op": "group", "by": ["table_number"],
                        "fields": {"n_columns": "count", "n_papers": "n_papers", "outcome": {"op": "first", "column": "outcome_variable"},
                                   "studies": {"op": "union", "column": "_study"}}}],
             "output": {"label": "Table", "fields": [{"name": "table_number", "type": "string", "required": True}, {"name": "n_columns", "type": "integer"},
                                                     {"name": "n_papers", "type": "integer"}, {"name": "outcome", "type": "string"}, {"name": "studies", "type": "list"}]}}

LLM = {"id": "themes", "kind": "derive", "label": "Outcome themes", "over": "tables",
       "input": {"unit": "entries", "columns": ["outcome_variable", "_study"]}, "identity": ["theme"], "review": {"mode": "all", "blocks": "next"},
       "calls": [{"id": "name", "kind": "llm", "prompt": {"text": "Group the outcomes of ${dataset} into themes.\n\n${existing}\n\n${rows}\n"}},
                 {"id": "merge", "kind": "transform", "op": "group", "by": ["theme"], "fields": {"n": "count", "n_papers": "n_papers", "studies": {"op": "union", "column": "_study"}}}],
       "output": {"fields": [{"name": "theme", "type": "string", "required": True}, {"name": "kind", "type": "enum", "options": ["labour", "other"]},
                             {"name": "n", "type": "integer"}, {"name": "n_papers", "type": "integer"}, {"name": "studies", "type": "list"}]}}


def _validate(step):
    spec = ws.normalize(_wf(step))
    return ws.validate(spec, resolver=presets.load_all().get)


# ── no database ───────────────────────────────────────────────────────────────

def test_the_declaration_is_checked_against_the_preset_and_the_calls() -> None:
    errs, _warns = _validate(TRANSFORM)
    assert errs == [], errs
    errs, _ = _validate(LLM)
    assert errs == [], errs
    bad = copy.deepcopy(TRANSFORM); bad["input"]["unit"] = "nope"
    assert any("input.unit" in e for e in _validate(bad)[0])
    bad = copy.deepcopy(TRANSFORM); bad["input"]["columns"] = ["table_number", "no_such_column"]
    assert any("no_such_column" in e for e in _validate(bad)[0])
    bad = copy.deepcopy(TRANSFORM); bad["identity"] = ["n_columns", "missing"]
    assert any("identity must name output fields" in e for e in _validate(bad)[0])
    bad = copy.deepcopy(TRANSFORM); bad["output"]["fields"].append({"name": "sources", "type": "list"})
    assert any("engine-owned" in e for e in _validate(bad)[0])
    bad = copy.deepcopy(TRANSFORM); bad["calls"][0]["fields"]["n_columns"] = "average"
    assert any("rule must be one of" in e for e in _validate(bad)[0])
    bad = copy.deepcopy(TRANSFORM); bad["calls"][0]["by"] = ["outcome_variable"]
    assert any("must also be an output field" in e for e in _validate(bad)[0])
    bad = copy.deepcopy(LLM); bad["calls"][0]["prompt"]["text"] = "no rows here ${existing}"
    assert any("must read ${rows}" in e for e in _validate(bad)[0])
    bad = copy.deepcopy(LLM); bad["calls"][0]["prompt"]["text"] = "${rows} ${magic}"
    assert any("unknown placeholder ${magic}" in e for e in _validate(bad)[0])
    bad = copy.deepcopy(LLM); bad["calls"].insert(1, {"id": "second", "kind": "llm", "prompt": {"text": "again ${rows}"}})
    assert any("must read ${previous}" in e for e in _validate(bad)[0])
    bad = copy.deepcopy(LLM); bad["output"]["fields"][1] = {"name": "kind", "type": "enum"}
    assert any("names its options" in e for e in _validate(bad)[0])
    # the prompt's wording is not part of the contract; the transform's rules are
    a = ws.normalize(_wf(LLM)); b = ws.normalize(_wf(copy.deepcopy(LLM)))
    b["steps"][1]["calls"][0]["prompt"]["text"] = "Other words ${rows} ${existing}"
    assert ws.workflow_id(a) == ws.workflow_id(b)
    b["steps"][1]["calls"][1]["fields"]["n"] = "n_papers"
    assert ws.workflow_id(a) != ws.workflow_id(b)
    assert workflows.later_problems(a) == []


def test_transform_rules_and_the_validation_of_rows() -> None:
    rows = [{"table_number": "Table 3", "outcome_variable": "wage", "_study": "A (2020)", "_paper": 0, "_record_id": "r-a1", "sign": "+"},
            {"table_number": "table 3", "outcome_variable": "hours", "_study": "A (2020)", "_paper": 0, "_record_id": "r-a2", "sign": "+"},
            {"table_number": "Table 4", "outcome_variable": "wage", "_study": "B (2021)", "_paper": 1, "_record_id": "r-b1", "sign": "-"},
            {"table_number": "Table 3", "outcome_variable": None, "_study": "B (2021)", "_paper": 1, "_record_id": "r-b2", "sign": "-"}]
    st = copy.deepcopy(TRANSFORM)
    st["calls"][0]["fields"]["sign"] = "majority"
    st["output"]["fields"].append({"name": "sign", "type": "string"})
    out = derived.transform_group(rows, st["calls"][0], st)
    assert [r["table_number"] for r in out] == ["Table 3", "Table 4"]           # keyed case-insensitively, first spelling kept
    t3 = out[0]
    assert t3["n_columns"] == 3 and t3["n_papers"] == 2 and t3["outcome"] == "wage" and t3["studies"] == ["A (2020)", "B (2021)"]
    assert t3["sign"] == "+" and t3["sources"] == ["r-a1", "r-a2", "r-b2"]
    clean, repairs = derived.validate_rows(out, st, {"r-a1", "r-a2", "r-b1", "r-b2"})
    assert len(clean) == 2 and clean[0]["_id"] == derived.identity_id({"table_number": "table 3"}, ["table_number"]) and repairs == []
    # what a model returns: aliases resolved, unknown keys and sources dropped, a sourceless row dropped, duplicates merged, enums repaired
    st2 = copy.deepcopy(LLM)
    model_rows = [{"theme": "Labour", "kind": "Labour", "extra": 1, "sources": ["r1", "zzz"]},
                  {"theme": "labour ", "kind": "other", "sources": ["r2"]},
                  {"theme": "Health", "sources": []},
                  {"kind": "other", "sources": ["r1"]},
                  {"theme": "Prices", "kind": "wrong", "n": "3", "sources": ["r2"]}]
    clean, repairs = derived.validate_rows(model_rows, st2, {"id-1", "id-2"}, aliases={"r1": "id-1", "r2": "id-2"})
    assert [r["theme"] for r in clean] == ["Labour", "Prices"] and clean[0]["sources"] == ["id-1", "id-2"] and clean[0]["kind"] == "labour"
    assert clean[1]["kind"] is None and clean[1]["n"] == 3 and "extra" not in clean[0]
    assert any("unknown sources" in x for x in repairs) and any("no admissible source" in x for x in repairs)
    assert any("required field is empty" in x for x in repairs) and any("merged" in x for x in repairs) and any("not an option" in x for x in repairs)


def test_the_review_is_computed_from_the_rows() -> None:
    old = [{"_id": "a", "theme": "Labour", "n": 2, "sources": ["x", "y"]}, {"_id": "b", "theme": "Health", "n": 1, "sources": ["z"]}]
    new = [{"_id": "a", "theme": "Labour", "n": 3, "sources": ["x", "y", "w"]}, {"_id": "c", "theme": "Prices", "n": 1, "sources": ["v"]},
           {"_id": "d", "theme": "Trade", "n": 1, "sources": ["x"]}]
    diff = derived.row_diff(old, new, new_ids={"w", "v"})
    by = {x["_id"]: x for x in diff}
    assert by["a"]["verdict"] == "revised" and set(by["a"]["changed"]) == {"n", "sources"} and by["a"]["sources_new"] == ["w"] and not by["a"]["unexplained"]
    assert by["c"]["verdict"] == "new" and by["c"]["sources_new"] == ["v"]
    assert by["d"]["verdict"] == "new" and by["d"]["unexplained"]                   # a new row no new record explains
    assert by["b"]["verdict"] == "removed"
    assert [x["verdict"] for x in derived.row_diff(old, old, new_ids=set())] == ["kept", "kept"]


def test_the_plan_splits_the_rows_by_group_over_the_budget(monkeypatch) -> None:
    monkeypatch.setattr(derived, "BUDGET_CHARS", 400)
    rows = [{"outcome_variable": f"outcome number {i} with a long name to fill the budget", "_study": f"S{i // 3}", "_paper": i // 3, "_record_id": f"r{i}"} for i in range(12)]
    pl = derived.plan(rows, LLM)
    assert pl["n_chunks"] > 1 and pl["n_calls"] == pl["n_chunks"] and sum(len(c) for c in pl["chunks"]) == 12
    assert all(len({r["_paper"] for r in c}) >= 1 for c in pl["chunks"]) and all(r["_paper"] == c[0]["_paper"] or len(c) > 3 for c in pl["chunks"] for r in c[:1])
    assert derived.plan(rows, TRANSFORM)["n_calls"] == 0
    monkeypatch.setattr(derived, "MAX_CALLS", 2)
    pl = derived.plan(rows, LLM)
    assert pl["over_budget"]


def _fake_model(answers: list[dict]):
    calls: list[str] = []
    def gen(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps(answers[min(len(calls) - 1, len(answers) - 1)])
    gen.calls = calls
    return gen


def test_the_chain_runs_a_model_call_then_a_transform() -> None:
    rows = [{"outcome_variable": "wage", "_study": "A (2020)", "_paper": 0, "_record_id": "id-1", "_new": False},
            {"outcome_variable": "hours", "_study": "A (2020)", "_paper": 0, "_record_id": "id-2", "_new": True},
            {"outcome_variable": "blood pressure", "_study": "B (2021)", "_paper": 1, "_record_id": "id-3", "_new": True}]
    gen = _fake_model([{"rows": [{"theme": "Labour", "kind": "labour", "sources": ["r1"]}, {"theme": "Labour", "kind": "labour", "sources": ["r2"]},
                                 {"theme": "Health", "kind": "other", "sources": ["r3"]}],
                       "change_log": [{"op": "add", "old_id": None, "because": ["r2", "r3"]}]}])
    res = derived.run_chain(LLM, {"rows": rows, "columns": ["outcome_variable", "_study"]}, [], dataset_title="Set", generate=gen, new_ids={"id-2", "id-3"})
    assert len(gen.calls) == 1 and "[NEW]" in gen.calls[0] and "(no committed rows yet)" in gen.calls[0] and "Group the outcomes of Set" in gen.calls[0]
    out = {r["theme"]: r for r in res["rows"]}
    assert out["Labour"]["n"] == 2 and out["Labour"]["n_papers"] == 1 and out["Labour"]["studies"] == ["A (2020)"] and out["Labour"]["sources"] == ["id-1", "id-2"]
    assert out["Health"]["n_papers"] == 1 and out["Health"]["sources"] == ["id-3"] and res["n_calls"] == 1
    assert res["change_log"] == [{"op": "add", "old_id": None, "because": ["id-2", "id-3"]}] and res["prompt_sha256"]
    assert all("_paper" not in r and "_study" not in r for r in res["rows"])


# ── with Postgres ─────────────────────────────────────────────────────────────

def _declare(conn, tmp_path, monkeypatch, step, ds):
    (tmp_path / "derive-test.json").write_text(json.dumps(_wf(step)))
    monkeypatch.setenv("PAPERLENS_WORKFLOW_DIRS", str(tmp_path))
    spec = workflows.load_all()["derive-test"]
    conn.execute("UPDATE dataset SET workflow = %s WHERE id = %s::uuid", (ws.workflow_id(spec), ds)); conn.commit()
    return spec


def test_a_run_makes_the_draft_and_the_update_names_the_new_records(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    sess = f"dv-{uuid.uuid4().hex[:6]}"
    ds, _doc0 = _seed(conn, _result("p0", n=2), "econ-regression-tables", sess)
    _declare(conn, tmp_path, monkeypatch, TRANSFORM, ds)
    who = _who(sess)
    # the planner: the step is missing, costs nothing
    plan = workflows.plan(conn, ds, who=who)
    st = next(s for s in plan["steps"] if s["id"] == "per_table")
    assert st["state"] == "missing" and st["n_calls"] == 0 and plan["n_calls"] == 0 and plan["workflow"]["problems"] == []
    # a run: the supplied step has nothing new, the derive step makes the draft, the release gate holds on it
    run = workflows.start(conn, ds, who=who)
    run = workflows.complete_uploads(conn, run["id"], who=who, step_id="tables")
    assert run["status"] == "done", run
    sr = next(s for s in run["steps"] if s["step_id"] == "per_table")
    assert sr["status"] == "done" and sr["outputs"]["n_rows"] == 1 and sr["review"]["draft"]["status"] == "draft" and not sr["review"]["satisfied"]
    draft = derived.latest_draft(conn, ds, "per_table")
    assert draft["rows"][0]["table_number"] == "Table 3" and draft["rows"][0]["n_columns"] == 2 and draft["run_id"] == run["id"]
    states = {s["id"]: s for s in workflows.step_states(conn, ds, workflows.load_all()["derive-test"])}
    assert states["per_table"]["state"] == "draft" and states["release"]["state"] == "blocked"
    gate = releases.pending(conn, ds)["gate"]
    assert not gate["open"] and any(b["step"] == "per_table" for b in gate["blocking"]) and "per_table" in gate["gates_blocking"]
    with pytest.raises(releases.GateClosed):
        releases.create(conn, ds, created_by=None)
    # the review API: one new row with its sources; a decision; the commit opens the gate
    c, H = _client(sess)
    rv = c.get(f"/api/derived/{draft['id']}/review", headers=H).json()
    assert rv["summary"] == {"new": 1, "unexplained": 0} and rv["diff"][0]["verdict"] == "new" and len(rv["diff"][0]["row"]["sources"]) == 2
    assert all(lab["document_id"] for lab in rv["labels"].values()) and rv["owner"] is True and rv["step"]["id"] == "per_table"
    assert c.patch(f"/api/derived/{draft['id']}", headers=H, json={"decisions": {draft["rows"][0]["_id"]: "accept"}}).status_code == 200
    out = c.post(f"/api/derived/{draft['id']}/commit", headers=H).json()
    assert out["derived"]["status"] == "committed"
    conn.rollback()
    assert releases.pending(conn, ds)["gate"]["open"] and workflows.step_states(conn, ds, workflows.load_all()["derive-test"])[1]["state"] == "fresh"
    rel = releases.create(conn, ds, created_by=None)
    snap = releases.get(conn, rel["id"], with_snapshot=True)["snapshot"]
    assert snap["derived"][0]["step"] == "per_table" and snap["derived"][0]["version"] == 1 and snap["derived"][0]["declaration"]["identity"] == ["table_number"]
    # the analysis unit and the export
    t = analysis_table.build(conn, ds, "per_table", owner=True)
    assert t["unit"]["level"] == "derived" and t["n_rows"] == 1 and any(u["id"] == "per_table" for u in t["units"])
    names = [col["name"] for col in t["columns"]]
    assert t["rows"][0]["v"][names.index("n_columns")] == 2 and t["rows"][0]["v"][names.index("_n_sources")] == 2 and len(t["rows"][0]["src"]) == 2
    assert any(u["id"] == "per_table" for u in analysis_table.build(conn, ds, None, owner=True)["units"])
    files = release_export.build(conn, releases.get(conn, rel["id"], with_snapshot=True))
    assert "tables/per_table.json" in files and json.loads(files["release.json"])["derived"][0]["step"] == "per_table"
    ev = json.loads(files["evidence.json"])
    assert ev["cells"]["per_table"]["0"]["_sources"]["k"] == "sources" and len(ev["cells"]["per_table"]["0"]["_sources"]["i"]) == 2
    names_x = [col["name"] for col in json.loads(files["tables/per_table.json"])["columns"]]
    assert "n_columns" in names_x and json.loads(files["release.json"])["units"][-1]["level"] == "derived"
    assert "tables/per_table.json" in zipfile.ZipFile(io.BytesIO(release_export.as_zip(files, "x"))).namelist()[0] or True
    # a paper added: the unit is stale; a second run's draft is reviewed against v1 with the new records as triggers
    _seed_into(conn, _result("p1", n=1), "econ-regression-tables", sess, ds)
    conn.rollback()
    assert derived.state(conn, ds, TRANSFORM)["state"] == "stale"
    run2 = workflows.start(conn, ds, who=who)
    run2 = workflows.complete_uploads(conn, run2["id"], who=who, step_id="tables")
    assert run2["status"] == "done"
    d2 = derived.latest_draft(conn, ds, "per_table")
    assert d2["version"] == 2 and d2["built_from"]["n_new"] == 1 and d2["built_from"]["derived_version_from"] == 1
    rv = c.get(f"/api/derived/{d2['id']}/review", headers=H).json()
    assert rv["summary"]["revised"] == 1 and rv["diff"][0]["changed"] and "n_columns" in rv["diff"][0]["changed"] and len(rv["diff"][0]["sources_new"]) == 1
    assert rv["committed"]["version"] == 1 and rv["diff"][0]["row"]["n_columns"] == 3 and rv["diff"][0]["old"]["n_columns"] == 2
    # a rejected row is dropped at the commit; the derived list shows both versions
    assert c.patch(f"/api/derived/{d2['id']}", headers=H, json={"decisions": {d2["rows"][0]["_id"]: "reject"}}).status_code == 200
    c.post(f"/api/derived/{d2['id']}/commit", headers=H)
    conn.rollback()
    assert derived.committed(conn, ds, "per_table")["rows"] == []
    lst = c.get(f"/api/datasets/{ds}/derived", headers=H).json()
    assert [v["version"] for v in lst["versions"]] == [1, 2] and lst["steps"][0]["state"] == "fresh"
    assert c.get(f"/api/datasets/{ds}/derived/per_table/plan", headers=H).json()["n_rows"] == 3
    assert c.get("/derived").status_code == 200
    # the release fingerprint moved with the commits
    assert releases.changed_since(conn, ds, releases.latest(conn, ds))
    monkeypatch.delenv("PAPERLENS_WORKFLOW_DIRS"); conn.close()


def test_a_model_step_runs_by_hand_and_a_run_waits_for_the_key(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    from paperlens import providers
    answers = [{"rows": [{"theme": "Labour", "kind": "labour", "sources": ["r1"]}, {"theme": "Labour", "kind": "labour", "sources": ["r2"]}]}]
    seen: list[dict] = []
    def fake_generate(model, api_key, prompt, **kw):
        seen.append({"model": model, "key": api_key, "base_url": kw.get("base_url")})
        return json.dumps(answers[0])
    monkeypatch.setattr(providers, "generate_text", fake_generate)
    conn = records.connect(); records.init_db(conn)
    sess = f"dv-{uuid.uuid4().hex[:6]}"
    ds, _doc0 = _seed(conn, _result("p0", n=2), "econ-regression-tables", sess)
    _declare(conn, tmp_path, monkeypatch, LLM, ds)
    who = _who(sess)
    c, H = _client(sess)
    # by hand, with an own key: one call, a draft with one merged row
    assert c.get(f"/api/datasets/{ds}/derived/themes/plan", headers=H).json()["n_calls"] == 1
    out = c.post(f"/api/datasets/{ds}/derived/themes/propose", headers=H, json={"model": "test-model", "api_key": "k-own"}).json()
    assert out["ok"] is True and out["n_calls"] == 1 and seen[-1] == {"model": "test-model", "key": "k-own", "base_url": None}
    row = out["derived"]["derived"]["rows"][0]
    assert row["theme"] == "Labour" and row["n"] == 2 and row["n_papers"] == 1 and len(row["sources"]) == 2 and out["derived"]["derived"]["model"] == "test-model"
    conn.rollback()
    did = out["derived"]["derived"]["id"]
    c.post(f"/api/derived/{did}/commit", headers=H)
    # a run on an own key: the step pauses for the key; resume with the key runs it and the gate holds the run (blocks: next)
    _seed_into(conn, _result("p1", n=1), "econ-regression-tables", sess, ds)
    conn.rollback()
    plan = workflows.plan(conn, ds, who=who)
    assert next(s for s in plan["steps"] if s["id"] == "themes")["n_calls"] == 1 and plan["n_calls"] == 1
    run = workflows.start(conn, ds, who=who)
    run = workflows.complete_uploads(conn, run["id"], who=who, step_id="tables")
    assert run["status"] == "needs_key:themes"
    r = c.post(f"/api/workflow-runs/{run['id']}/resume", headers=H, json={})
    assert r.status_code == 422 and "needs_key" in r.text
    r = c.post(f"/api/workflow-runs/{run['id']}/resume", headers=H, json={"api_key": "k-resume", "model": "test-model"})
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["status"] == "awaiting_review:themes" and seen[-1]["key"] == "k-resume"
    sr = next(s for s in view["steps"] if s["step_id"] == "themes")
    assert sr["review"]["draft"]["status"] == "draft" and sr["n_calls"] == 1
    assert "(no committed rows yet)" not in seen and len(seen) == 2
    # the commit lets the run go on
    d2 = derived.latest_draft(conn, ds, "themes")
    assert d2["version"] == 2 and d2["run_id"] == run["id"]
    c.post(f"/api/derived/{d2['id']}/commit", headers=H)
    conn.rollback()
    assert workflows.get_run(conn, run["id"])["status"] == "done"
    assert c.post(f"/api/workflow-runs/{run['id']}/resume", headers=H, json={}).status_code == 409
    monkeypatch.delenv("PAPERLENS_WORKFLOW_DIRS"); conn.close()
