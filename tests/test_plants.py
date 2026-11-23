"""Planted errors (paperlens/plants.py): the alterations, the transcription cell they are written
into, the draw, and the path through a task — shown beside the data, never in it; caught or missed
by the coder's first answer; told on a catch; read by the owner alone. The DB part skips without
Postgres."""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import uuid

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import plants as P  # noqa: E402
from paperlens import templates as tp  # noqa: E402

TABLE = """**Table 1. The Effect**

**Panel A: Pre**

**A: Pre**
|  | Events (1) | Release (2) |
|---|---|---|
| Treat×Post | 0.115 | -0.012** |
| Treat×Post (SE) | (0.109) | (0.005) |

**Panel B: Post**

**B: Post**
|  | Events (1) | Release (2) |
|---|---|---|
| Treat×Post | 0.048 | -0.003 |
| N | 31983 | 31983 |"""


def test_a_value_is_made_plausibly_wrong_and_never_left_as_it_was() -> None:
    for s in ("0.115", "-0.012**", "(0.109)", "18332", "0.453***", "-0.217***", "12.5"):
        seen = {P.perturb_value(s, h / 20) for h in range(20)}
        assert None not in seen and s not in seen, (s, seen)
        for out in seen:                                                    # still a number as printed: same shape, same parentheses
            assert re.match(r"^\(?-?\d+(\.\d+)?\**\)?$", out), (s, out)
            assert out.startswith("(") == s.startswith("(") and ("." in out) == ("." in s)
    assert P.perturb_value("-0.000", 0.99) in {"-0.001"}                      # no sign flip on a zero; the last digit moves
    assert {P.perturb_value("10", h / 10) for h in range(10)} <= {"11", "-10"}  # no "01"
    assert P.perturb_value("n/a", 0.3) is None and P.perturb_value(None, 0.3) is None
    assert P.perturb_value("0.115", 0.42) == P.perturb_value("0.115", 0.42)  # the same draw, the same error


def test_a_mapping_part_is_swapped_for_one_another_cell_of_the_exhibit_has() -> None:
    me = "T1 · AI vs none · Gollum events · with · h0"
    others = ["T1 · AI vs none · Member events · with · h0", "T1 · AI vs none · Gollum events · without · h0"]
    outs = {P.perturb_mapping(me, others, " · ", h / 10) for h in range(10)}
    assert outs == {"T1 · AI vs none · Member events · with · h0", "T1 · AI vs none · Gollum events · without · h0"}
    assert P.perturb_mapping(me, [me, me], " · ", 0.5) is None                # every cell agrees: nothing plausible to swap in


def test_the_transcription_cell_is_found_as_the_grid_places_it() -> None:
    assert P.locate_cell(TABLE, "B: Post", "Treat×Post", "Release (2)") is not None
    line, ci = P.locate_cell(TABLE, "Panel B: Post", "treat×post", "release (2)")
    out = P.replace_cell(TABLE, line, ci, "-0.030")
    assert "| Treat×Post | 0.048 | -0.030 |" in out and "| Treat×Post | 0.115 | -0.012** |" in out   # panel A untouched
    assert P.locate_cell(TABLE, "C: nowhere", "Treat×Post", "Events (1)") is None   # an unknown panel is never guessed
    assert P.locate_cell(TABLE, "A: Pre", "Treat×Post", "Output (3)") is None


def test_the_draw_picks_a_cited_cell_and_a_kind_the_template_allows() -> None:
    tpl = tp.get("genai-work-check")
    cfg, block = P.config(tpl)
    assert cfg["share"] == 0.33 and block["layout"] == "flags"
    units = [{"key": f"exhibit_cells#o{i}", "row": {"value_as_printed": v, "estimand_text": f"T1 · AI vs none · {o} · all · h0"}}
             for i, (v, o) in enumerate((("0.115", "events"), ("-0.012**", "release"), ("0.3", "output")))]
    kinds = set()
    for n in range(40):
        pl = P.choose({**cfg, "kinds": ["value", "mapping"]}, block, units, ("s", "run", "check", f"doc{n}", "coder"))
        assert pl and pl["planted"] != pl["original"] and pl["unit"] in {u["key"] for u in units}
        assert pl["question"] == ("value_ok" if pl["kind"] == "value" else "mapping_ok") and pl["field"] == ("value_as_printed" if pl["kind"] == "value" else "estimand_text")
        kinds.add(pl["kind"])
    assert kinds == {"value", "mapping"}
    key = ("s", "run", "check", "docX", "coder")
    assert P.choose(cfg, block, units, key) == P.choose(cfg, block, units, key)
    assert P.choose({**cfg, "kinds": ["mapping"]}, block, [units[0]], key) is None         # one cell: no other mapping to swap in


def test_the_template_says_how_many_and_what_kinds() -> None:
    from paperlens import presets
    ext = presets.load_all()["genai-work-extraction"]
    base = copy.deepcopy(tp.get("genai-work-check"))
    for bad, msg in (({"share": 0, "seed": "s"}, "share must be a number above 0"), ({"share": 0.5}, "seed must be a string"),
                     ({"share": 0.5, "seed": "s", "kinds": ["typo"]}, "kinds must list"), ({"share": 0.5, "seed": "s", "x": 1}, "unknown key")):
        t = {**base, "plants": bad}
        errs = tp.validate(tp.normalize(t), ext)[0]
        assert any(msg in e for e in errs), (bad, errs)
    no_flags = copy.deepcopy(base)
    for st in no_flags["stages"]:
        for bl in st["blocks"]:
            if bl.get("layout") == "flags":
                bl["layout"] = "rows"; bl.pop("confirm"); bl["grid"] = {k: v for k, v in bl["grid"].items() if k not in ("flag", "mapping", "value")}
    assert any("needs a block with layout 'flags'" in e for e in tp.validate(tp.normalize(no_flags), ext)[0])
    assert tp.template_id(base) == tp.template_id({k: v for k, v in base.items() if k != "plants"})   # outside the id


# ── through a task (Postgres) ──────────────────────────────────────────────────────────────────

def test_a_planted_error_is_shown_beside_the_data_caught_or_missed_and_read_by_the_owner(tmp_path, monkeypatch) -> None:
    from test_answers import EXT_PIN, _declare
    from test_coders import _account
    from test_row_sampling import _record
    from test_workflows import _pdf, _skip

    from paperlens import presets, records, review_gate, workflows
    from paperlens import workflow_spec as ws
    _skip()
    conn = records.connect(); records.init_db(conn)
    # the check template with a plant on every paper, values only (the draw is then sure)
    tdir = tmp_path / "tpl"; tdir.mkdir()
    t = copy.deepcopy(json.load(open(os.path.join(_ROOT, "paperlens", "templates", "genai-work-check.json"), encoding="utf-8")))
    t["id"] = "check-planted"; t["plants"] = {"share": 1.0, "seed": "t", "kinds": ["value"]}
    (tdir / "check-planted.json").write_text(json.dumps(t))
    monkeypatch.setenv("PAPERLENS_TEMPLATE_DIRS", str(tdir))
    assert tp.get("check-planted")["plants"]["share"] == 1.0
    spec = ws.normalize({"format": 1, "id": "planted", "meta": {"title": "Planted"},
                         "steps": [{"id": "extraction", "kind": "extract", "preset": EXT_PIN, "source": "supplied"},
                                   {"id": "check", "kind": "review", "over": ["extraction"], "template": "check-planted", "mode": "sample", "unit": "row",
                                    "table": "exhibits", "where": {"n_cited": {"gt": 0}}, "per_paper": 1, "seed": "check-v1", "coders": 1}]})
    spec_ext = presets.load_all()["genai-work-extraction"]
    owner, _oid, _ = _account("plant-owner")
    a, a_id, a_email = _account("plant-a")
    ds = owner.post("/api/datasets", json={"title": "Planted", "schema_id": EXT_PIN, "visibility": "private"}).json()["id"]
    _declare(conn, ds, spec, monkeypatch)
    assert owner.post(f"/api/datasets/{ds}/coders", json={"email": a_email}).status_code == 200
    run = owner.post(f"/api/datasets/{ds}/workflow/runs", json={}).json(); rid = run["id"]
    tag = uuid.uuid4().hex[:6]
    ex = {"exhibit_id": "Table 1", "kind": "table", "page": 1, "caption": "The Effect", "panels": ["A: Pre", "B: Post"], "n_cells": 8, "n_cited": 2}
    cells = [{"exhibit_id": "Table 1", "panel": "A: Pre", "row_label": "Treat×Post", "column": "Events (1)", "value_as_printed": "0.115", "se_as_printed": "(0.109)",
              "is_se": False, "arm_stats": False, "estimate_id": "E1", "estimand_id": "E1", "estimand_text": "T1 · AI vs none · events · all · A", "role": "contrast"},
             {"exhibit_id": "Table 1", "panel": "B: Post", "row_label": "Treat×Post", "column": "Release (2)", "value_as_printed": "-0.003", "se_as_printed": None,
              "is_se": False, "arm_stats": False, "estimate_id": "E2", "estimand_id": "E2", "estimand_text": "T1 · AI vs none · release · all · B", "role": "contrast"}]
    docs = {}
    for name in ("A", "B"):
        rec = _record(spec_ext, f"{name}{tag}", exhibits=[dict(ex)], cells=copy.deepcopy(cells), routed=False)
        rec["paper_metadata"]["evidence_items"] = [{"n": 1, "page": 1, "kind": "table", "text": TABLE, "note": "", "bears_on": ["exhibits", "exhibit_cells"]}]
        r = owner.post("/api/ingest-pdf", files={"pdf": (f"{name}.pdf", _pdf(f"plant-{name}-{tag}"), "application/pdf")},
                       data={"result": json.dumps(rec), "run_id": rid, "step_id": "extraction"})
        assert r.status_code == 200, r.text
        docs[name] = r.json()["document_id"]
    view = owner.post(f"/api/workflow-runs/{rid}/complete", json={"step_id": "extraction"}).json()
    assert view["status"] == "done", view
    task = f"{rid}:check"
    # the coder's view: the plant beside the data — the record's values as stored, the transcription altered at the same cell
    v = a.get(f"/api/documents/{docs['A']}/view?task={task}").json()
    shown = v["task"]["shown"]
    assert len(shown) == 1 and shown[0]["field"] == "value_as_printed"
    unit = shown[0]["unit"]
    cell = next(c for c in v["paper_metadata"]["exhibit_cells"] if f"exhibit_cells#{c['_rid']}" == unit)
    assert shown[0]["value"] != cell["value_as_printed"] and cell["value_as_printed"] in ("0.115", "-0.003")   # the data stays true
    text = v["paper_metadata"]["evidence_items"][0]["text"]
    assert shown[0]["value"] in text and text != TABLE
    assert a.get(f"/api/documents/{docs['A']}/view?task={task}").json()["task"]["shown"] == shown   # the same plant on every view
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM review_plant WHERE run_id = %s::uuid", (rid,)).fetchone()[0] == 1
    # the owner reads it; the coder does not see the checks' tally anywhere
    ov = owner.get(f"/api/documents/{docs['A']}/view?task={task}").json()
    assert [(p["unit"], p["status"]) for p in ov["plants"]] == [(unit, "open")] and "shown" not in ov["task"]
    assert "plants" not in (a.get(f"/api/workflow-runs/{rid}/queue/check").json().get("gate") or {})
    # caught: the coder's first answer on the planted question is the flag — told at once; then the view is true again
    r = a.post(f"/api/documents/{docs['A']}/answers", json={"task": task, "unit": unit, "answers": {"value_ok": "no"}})
    assert r.status_code == 200 and r.json()["caught"] == [{"unit": unit, "field": "value_as_printed", "kind": "value", "shown": shown[0]["value"]}]
    v2 = a.get(f"/api/documents/{docs['A']}/view?task={task}").json()
    assert "shown" not in v2["task"] and v2["task"]["caught"][0]["unit"] == unit and v2["paper_metadata"]["evidence_items"][0]["text"] == TABLE
    a.post(f"/api/documents/{docs['A']}/answers", json={"task": task, "unit": unit, "answers": {"value_ok": "yes"}})   # a later answer does not undo the catch
    # missed: on paper B the coder confirms everything — the plant's first answer is the confirm's
    vb = a.get(f"/api/documents/{docs['B']}/view?task={task}").json()
    ub = vb["task"]["shown"][0]["unit"]
    cells_b = [u for u in tp.unit_rows(tp.get("check-planted")["stages"][0]["blocks"][1], vb, spec_ext, sampled={k: set(x) for k, x in vb["task"]["sampled"].items()})]
    r = a.post(f"/api/documents/{docs['B']}/answers", json={"task": task, "via": "confirm", "items": [{"unit": u["key"], "answers": {"value_ok": "yes", "mapping_ok": "yes"}} for u in cells_b]})
    assert r.status_code == 200 and "caught" not in r.json() and ub in {u["key"] for u in cells_b}
    assert "shown" not in a.get(f"/api/documents/{docs['B']}/view?task={task}").json()["task"]   # a miss is not told; the view is true
    # the owner's list and the gate's line
    pl = owner.get(f"/api/workflow-runs/{rid}/plants").json()["plants"]
    assert sorted((p["filename"], p["status"]) for p in pl) == [("A.pdf", "caught"), ("B.pdf", "missed")]
    assert a.get(f"/api/workflow-runs/{rid}/plants").status_code in (403, 404)
    conn.rollback()
    sr = workflows.get_step_run(conn, conn.execute("SELECT id::text FROM workflow_step_run WHERE run_id = %s::uuid AND step_id = 'check'", (rid,)).fetchone()[0])
    st = review_gate.state(conn, sr)
    assert [(c["planted"], c["caught"], c["missed"], c["open"]) for c in st["plants"]] == [(2, 1, 1, 0)]
    # a paper the coder had answered on before any plant: none is drawn there afterwards
    conn.execute("DELETE FROM review_plant WHERE run_id = %s::uuid AND document_id = %s::uuid", (rid, docs["B"])); conn.commit()
    assert "shown" not in a.get(f"/api/documents/{docs['B']}/view?task={task}").json()["task"]
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM review_plant WHERE run_id = %s::uuid AND document_id = %s::uuid", (rid, docs["B"])).fetchone()[0] == 0
    conn.close()
