"""A review of ROWS (``unit: row``; the sampler itself is tests/test_review_sampling.py): the grammar, the draw of a paper table's rows per paper,
the gate's items with their unit keys, a task's projection to the drawn rows, and a review step's
``where`` over the papers. Skips without Postgres."""
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

from test_answers import EXT_PIN, _declare
from test_coders import _account
from test_genai_work_workflow import _extraction_for
from test_workflows import _pdf, _skip

from paperlens import presets, records, review_gate, templates as tp, workflows
from paperlens import workflow_spec as ws

SPEC = {"format": 1, "id": "rowed", "meta": {"title": "Rowed"},
        "steps": [{"id": "extraction", "kind": "extract", "preset": EXT_PIN, "source": "supplied"},
                  {"id": "check", "kind": "review", "over": ["extraction"], "template": "genai-work-check", "mode": "sample", "unit": "row",
                   "table": "exhibits", "where": {"n_cited": {"gt": 0}}, "per_paper": 1, "seed": "check-v1", "coders": 1},
                  {"id": "routed", "kind": "review", "over": ["extraction"], "template": "genai-work-routed", "mode": "all", "unit": "paper",
                   "where": {"paper_metadata.routed": True}, "coders": 1}]}


def _errors(mutate) -> list[str]:
    d = copy.deepcopy(SPEC); mutate(d)
    return ws.validate(ws.normalize(d))[0]


def test_the_grammar_of_a_review_of_rows_and_of_a_where() -> None:
    spec = ws.normalize(copy.deepcopy(SPEC))
    assert ws.validate(spec) == ([], [])
    rv = ws.review_of(spec["steps"][1])
    assert rv["unit"] == "row" and rv["table"] == "exhibits" and rv["per_paper"] == 1 and rv["where"] == {"n_cited": {"gt": 0}}
    for label, mutate, needle in [
        ("row needs a table", lambda d: d["steps"][1].pop("table"), "table must name a paper table"),
        ("table not a paper table", lambda d: d["steps"][1].__setitem__("table", "scale"), "not a paper table"),
        ("sample of rows needs per_paper", lambda d: d["steps"][1].pop("per_paper"), "per_paper must be"),
        ("n on a sample of rows", lambda d: d["steps"][1].__setitem__("n", 3), "per_paper says how many"),
        ("table on a paper unit", lambda d: d["steps"][2].__setitem__("table", "exhibits"), "only applies to unit 'row'"),
        ("where column of the rows", lambda d: d["steps"][1].__setitem__("where", {"zzz": 1}), "not a field of the unit"),
        ("where paper field", lambda d: d["steps"][2].__setitem__("where", {"paper_metadata.zzz": True}), "not a paper field"),
        ("where operator", lambda d: d["steps"][2].__setitem__("where", {"paper_metadata.routed": {"like": 1}}), "unknown operator"),
        ("stratify a column", lambda d: d["steps"][1].__setitem__("stratify", ["zzz"]), "column of the table"),
    ]:
        errs = _errors(mutate)
        assert any(needle in e for e in errs), (label, errs)
    assert _errors(lambda d: d["steps"][1].__setitem__("stratify", ["kind"])) == []
    # every matching row (mode all) needs no per_paper; a where over the papers may use a bare paper field
    every = copy.deepcopy(SPEC); every["steps"][1].update({"mode": "all"}); every["steps"][1].pop("per_paper"); every["steps"][1].pop("seed")
    assert ws.validate(ws.normalize(every))[0] == []
    bare = copy.deepcopy(SPEC); bare["steps"][2]["where"] = {"routed": True}
    assert ws.validate(ws.normalize(bare))[0] == []
    # the where and the table are in the contract
    assert ws.workflow_id(spec) != ws.workflow_id(ws.normalize(bare)) != ws.workflow_id(ws.normalize(every))


def _record(spec: dict, tag: str, *, exhibits: list[dict], cells: list[dict], routed: bool) -> dict:
    obj = _extraction_for(spec, tag)
    pm = obj["paper_metadata"]
    pm["exhibits"] = exhibits
    pm["exhibit_cells"] = cells
    pm["routed"] = routed
    pm["route_reasons"] = ["criterion:no_reference_arm"] if routed else []
    pm["triage_score"] = 0.8 if routed else 0.1
    pm["flags"] = [{"flag_id": "F1", "group": "criteria", "kind": "criterion", "code": "no_reference_arm", "message": "no no-AI arm", "source": "Table 1", "extracted": "—", "page": 2}] if routed else []
    pm["evidence_items"] = []
    return obj


def test_a_review_of_rows_draws_one_exhibit_per_paper_and_a_where_keeps_the_routed_papers(monkeypatch) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    spec_ext = presets.load_all()["genai-work-extraction"]
    owner, _oid, _ = _account("rows-owner")
    a, a_id, a_email = _account("rows-a")
    ds = owner.post("/api/datasets", json={"title": "Rowed", "schema_id": EXT_PIN, "visibility": "private"}).json()["id"]
    spec = ws.normalize(copy.deepcopy(SPEC))
    _declare(conn, ds, spec, monkeypatch)
    assert owner.post(f"/api/datasets/{ds}/coders", json={"email": a_email}).status_code == 200
    run = owner.post(f"/api/datasets/{ds}/workflow/runs", json={}).json(); rid = run["id"]
    tag = uuid.uuid4().hex[:6]
    ex = lambda i, cited: {"exhibit_id": f"X{i}", "kind": "table", "page": i, "caption": f"Table {i}", "panels": [], "n_cells": 4, "n_cited": cited}   # noqa: E731
    cell = lambda x, j, est: {"exhibit_id": x, "panel": None, "row_label": f"row {j}", "column": "(1)", "value_as_printed": f"0.{j}", "se_as_printed": None,   # noqa: E731
                              "is_se": False, "arm_stats": False, "estimate_id": est, "estimand_id": None, "estimand_text": None, "role": "contrast" if est else "other"}
    docs = {}
    for name, exhibits, cells, routed in (("A", [ex(1, 2), ex(2, 0), ex(3, 1)], [cell("X1", 1, "E1"), cell("X1", 2, None), cell("X2", 1, None), cell("X3", 1, "E2")], True),
                                          ("B", [ex(1, 1)], [cell("X1", 1, "E1")], False)):
        pdf = _pdf(f"rows-{name}-{tag}")
        r = owner.post("/api/ingest-pdf", files={"pdf": (f"{name}.pdf", pdf, "application/pdf")},
                       data={"result": json.dumps(_record(spec_ext, f"{name}{tag}", exhibits=exhibits, cells=cells, routed=routed)), "run_id": rid, "step_id": "extraction"})
        assert r.status_code == 200, r.text
        docs[name] = r.json()["document_id"]
    view = owner.post(f"/api/workflow-runs/{rid}/complete", json={"step_id": "extraction"}).json()
    assert view["status"] == "done", view
    gates = {s["step_id"]: s["review"] for s in view["steps"] if s.get("review")}
    # the rows: three cited exhibits in the frame, one drawn per paper, each a cited one
    chk = gates["check"]
    assert chk["mode"] == "sample" and chk["unit"] == "row" and chk["table"] == "exhibits" and chk["per_paper"] == 1
    assert chk["frame_n"] == 3 and chk["n_target"] == 2 and chk["drawn"] == 2 and chk["exhaustive"] is False and chk["total"] == 2
    q = owner.get(f"/api/workflow-runs/{rid}/queue/check").json()
    keys = {it["document_id"]: it["unit_key"] for it in q["items"]}
    assert set(keys) == set(docs.values()) and q["draw"]["table"] == "exhibits" and q["draw"]["per_paper"] == 1
    conn.rollback()
    pm_a = conn.execute("SELECT paper_metadata_raw FROM extraction_document WHERE id = %s::uuid", (docs["A"],)).fetchone()[0]
    cited = {f"exhibits#{x['_rid']}" for x in pm_a["exhibits"] if x["n_cited"] > 0}
    assert keys[docs["A"]] in cited and keys[docs["B"]] == f"exhibits#{pm_a and 'o0'}"
    # the draw is a function of the seed and the paper: the same again, whatever the order
    sr = workflows.get_step_run(conn, conn.execute("SELECT id::text FROM workflow_step_run WHERE run_id = %s::uuid AND step_id = 'check'", (rid,)).fetchone()[0])
    again = review_gate.draw_rows(conn, [docs["B"], docs["A"]], table="exhibits", where={"n_cited": {"gt": 0}}, per_paper=1, seed=sr["review"]["seed"])
    assert {it["document_id"]: it["unit_key"] for it in again["items"]} == keys and again["frame_n"] == 3
    # the routed review keeps paper A only (its where over the papers)
    rt = gates["routed"]
    assert rt["total"] == 1 and rt["mode"] == "all"
    rsr = workflows.get_step_run(conn, conn.execute("SELECT id::text FROM workflow_step_run WHERE run_id = %s::uuid AND step_id = 'routed'", (rid,)).fetchone()[0])
    assert rsr["outputs"]["document_ids"] == [docs["A"]] and rsr["outputs"]["where"] == {"paper_metadata.routed": True}
    # a draw of rows never enters the dataset's sample estimate
    assert review_gate.dataset_sample(conn, ds) is None
    # the coder's task view of paper A: the drawn exhibit alone, its cells alone; the required answers follow the draw
    sampled = review_gate.sampled_units(conn, sr, docs["A"])
    assert sampled["_keys"] == {keys[docs["A"]]} and len(sampled["exhibits.exhibit_id"]) == 1
    v = a.get(f"/api/documents/{docs['A']}/view?task={rid}:check").json()
    assert sorted(v["task"]["sampled"]["_keys"]) == [keys[docs["A"]]] and v["task"]["template"] == "genai-work-check"
    tpl = tp.get("genai-work-check")
    blocks = tpl["stages"][0]["blocks"]
    s_in = {k: set(vals) for k, vals in v["task"]["sampled"].items()}
    heads = tp.unit_rows(blocks[0], v, spec_ext, sampled=s_in)
    assert [u["key"] for u in heads] == [keys[docs["A"]]]
    xid = heads[0]["row"]["exhibit_id"]
    cells_u = tp.unit_rows(blocks[1], v, spec_ext, sampled=s_in)
    assert cells_u and all(u["row"]["exhibit_id"] == xid for u in cells_u) and len(cells_u) == sum(1 for c in pm_a["exhibit_cells"] if c["exhibit_id"] == xid)
    comp = tp.completeness(tpl, v, spec_ext, {}, sampled=s_in)
    n_mapping = sum(1 for u in cells_u if u["row"].get("estimate_id"))
    assert comp["required"] == len(cells_u) + n_mapping + 1                   # value_ok per cell, mapping_ok where an estimate, the paper's full_review
    # answer them; the paper is done; the gate closes on paper A, paper B still waits
    for u in cells_u:
        vals = {"value_ok": "yes", **({"mapping_ok": "yes"} if u["row"].get("estimate_id") else {})}
        assert a.post(f"/api/documents/{docs['A']}/answers", json={"task": f"{rid}:check", "unit": u["key"], "answers": vals}).status_code == 200
    rec = v["records"][0]["id"]
    r = a.post(f"/api/records/{rec}/verify", json={"status": "verified", "task": f"{rid}:check"})
    assert r.status_code == 422 and r.json()["detail"]["missing"][0]["question"] == "full_review"
    assert a.post(f"/api/documents/{docs['A']}/answers", json={"task": f"{rid}:check", "unit": "", "answers": {"full_review": "no"}}).status_code == 200
    assert a.post(f"/api/records/{rec}/verify", json={"status": "verified", "task": f"{rid}:check"}).status_code == 200
    conn.rollback()
    st = review_gate.state(conn, workflows.get_step_run(conn, sr["id"]))
    assert st["checked"] == 1 and st["pending"] == 1 and st["satisfied"] is False
    conn.close()
