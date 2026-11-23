"""Rules: deterministic checks a workflow declares in `params.rules` — the grammar's validation
against a preset, the three-valued evaluation over entries, child rows, the paper and paper
tables, the suggestions a preset's field names imply, and a workflow run whose rules check flags
the records it finds (skips without Postgres)."""
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

from test_coder_gate import _declare
from test_coders import _account
from test_regression_tables_preset import RESULT
from test_workflows import _pdf, _result, _skip

from paperlens import checks, presets, records, rules
from paperlens import workflow_spec as ws

ECON = presets.load_all()["econ-regression-tables"]
PIN = "econ-regression-tables@67780825"


def _reg(tag: str, **over) -> dict:
    r = copy.deepcopy(RESULT["regressions"][0]); r["regression_id"] = f"{tag}::T3::_::C1"; r.update(over)
    return r


def _input(cols: list[dict], *, doc="d1", paper=None) -> dict:
    recs = [{"id": f"r{i}", "document_id": doc, "paper_id": "p", "entry_index": i, "field_values": c, "paper": {"doi": "10.1/t", "title": "T"}}
            for i, c in enumerate(cols)]
    return {"dataset_id": "ds", "schema_id": PIN, "spec": ECON, "records": recs,
            "documents": {doc: {"params": {}, "paper_metadata": paper or {"inventory_note": "two tables", "table_inventory": [{"table_number": "Table 3"}, {"table_number": "Table 4"}]}}}}


def _run(rules_, cols, **kw):
    return [(f["record_id"], f["code"], f["severity"], f.get("path")) for f in checks.check_rules([_input(cols, **kw)], {"rules": rules_})]


# ── the grammar ───────────────────────────────────────────────────────────────

def test_rules_validate_against_the_preset_they_read() -> None:
    good = [
        {"code": "page_number", "require": {"page": {"matches": r"^\d+$"}}},
        {"code": "star_level", "unit": "cells", "where": {"significance_stars": {"gte": 1}}, "require": {"significance_level": {"lte": 0.1}}},
        {"code": "rows_ordered", "unit": "cells", "require": {"row_index": {"gte": {"field": "col_index"}}}},
        {"code": "enough_cells", "require": {"count(cells)": {"gte": 1}}},
        {"code": "coef_sum", "severity": "info", "require": {"sum(cells.numeric_value)": {"between": [-1000, 1000]}}},
        {"code": "has_controls", "severity": "warn", "when": {"len(controls)": {"eq": 0}}},
        {"code": "inventory", "unit": "paper", "require": {"count(regressions)": {"lte": 40}, "paper.inventory_note": {"not_null": True}}},
        {"code": "listed", "unit": "paper.table_inventory", "require": {"table_number": {"matches": "Table"}}},
        {"code": "either", "require": {"$any": [{"model_type": "OLS"}, {"$not": {"se_type": {"in": ["clustered"]}}}]}},
        {"code": "from_row", "unit": "cells", "require": {"entry.page": {"not_null": True}, "paper.doi": {"not_null": True}}},
    ]
    assert rules.validate(good, ECON) == []
    bad = {
        "unknown op": [{"code": "a", "require": {"page": {"like": "x"}}}],
        "not a field": [{"code": "a", "require": {"pages": 3}}],
        "unknown unit": [{"code": "a", "unit": "rows", "require": {"x": 1}}],
        "require and when": [{"code": "a", "require": {"page": 1}, "when": {"page": 2}}],
        "neither": [{"code": "a"}],
        "code twice": [{"code": "a", "require": {"page": 1}}, {"code": "a", "require": {"page": 2}}],
        "bad code": [{"code": "A b", "require": {"page": 1}}],
        "bad regex": [{"code": "a", "require": {"page": {"matches": "("}}}],
        "between": [{"code": "a", "require": {"page": {"between": [1]}}}],
        "ref in in": [{"code": "a", "require": {"page": {"in": {"field": "column"}}}}],
        "all not a list": [{"code": "a", "require": {"$all": {"page": 1}}}],
        "entry from entry": [{"code": "a", "require": {"entry.page": 1}}],
        "sum needs column": [{"code": "a", "require": {"sum(cells)": {"gt": 0}}}],
        "no such table": [{"code": "a", "require": {"count(rows)": {"gt": 0}}}],
        "no such column": [{"code": "a", "require": {"sum(cells.weight)": {"gt": 0}}}],
        "unknown key": [{"code": "a", "require": {"page": 1}, "fix": True}],
        "severity": [{"code": "a", "severity": "loud", "require": {"page": 1}}],
        "tol": [{"code": "a", "require": {"page": {"eq": 1, "tol": -1}}}],
    }
    for why, rs in bad.items():
        assert rules.validate(rs, ECON), why
    assert rules.validate({"code": "a"}, ECON) == ["must be a list of rules"]
    assert rules.validate([{"code": "a", "require": {"anything": 1}}], None) == []       # without the preset only the shape is checked


def test_the_registry_validates_a_rules_step_with_its_preset() -> None:
    errs = []
    E, W = errs.append, (lambda m: None)
    st = {"id": "plaus", "kind": "check", "check": "rules", "over": ["tables"], "params": {"rules": [{"code": "page_number", "require": {"page": {"matches": r"^\d+$"}}}]}}
    checks.validate_step(st, "$.steps[1]", E, W, presets_of={"tables": ECON})
    assert errs == []
    st["params"]["rules"].append({"code": "nope", "require": {"pages": 1}})
    checks.validate_step(st, "$.steps[1]", E, W, presets_of={"tables": ECON})
    assert errs and errs[0].startswith("$.steps[1].params.rules[1]") and "not a field" in errs[0]
    spec = ws.normalize({"format": 1, "id": "ruled", "meta": {"title": "Ruled"},
                         "steps": [{"id": "tables", "kind": "extract", "preset": PIN, "source": "supplied"}, st]})
    assert any("params.rules[1]" in e for e in ws.validate(spec)[0])


# ── evaluation ────────────────────────────────────────────────────────────────

def test_each_operator_fires_on_its_own_fault_and_a_missing_value_never_does() -> None:
    ok, bad = _reg("ok"), _reg("bad", page="p14", model_type="IV", controls=[], iv_endogenous=None)
    bad["cells"] = copy.deepcopy(bad["cells"]); bad["cells"][0].update(significance_stars=3, significance_level=0.5); bad["cells"][1].update(row_index=0, col_index=3)
    rs = [
        {"code": "page_number", "require": {"page": {"matches": r"^\d+$"}}},
        {"code": "ols_only", "severity": "warn", "require": {"model_type": {"in": ["OLS", "WLS"]}}},
        {"code": "not_iv", "severity": "info", "when": {"model_type": "IV"}},
        {"code": "has_controls", "severity": "warn", "when": {"len(controls)": {"eq": 0}}},
        {"code": "iv_named", "require": {"iv_endogenous": {"matches": "."}}},                    # None: undecided, no finding
        {"code": "star_level", "unit": "cells", "where": {"significance_stars": {"gte": 1}}, "require": {"significance_level": {"lte": 0.1}}},
        {"code": "rows_ordered", "unit": "cells", "require": {"row_index": {"gte": {"field": "col_index"}}}},
        {"code": "coef_close", "unit": "cells", "where": {"row_type": "coefficient"}, "require": {"numeric_value": {"eq": -1.29, "tol": 0.01}}},
        {"code": "coef_exact", "unit": "cells", "severity": "info", "where": {"row_type": "coefficient"}, "require": {"numeric_value": {"eq": -1.29, "tol": 0.001}}},
        {"code": "enough_cells", "require": {"count(cells)": {"gte": 5}}},
        {"code": "sum_sane", "require": {"sum(cells.numeric_value)": {"between": [-100000, 100000]}, "max(cells.significance_stars)": {"lte": 3}, "min(cells.row_index)": {"gte": 0}}},
        {"code": "either", "require": {"$any": [{"model_type": "OLS"}, {"$not": {"se_type": {"ne": "clustered"}}}]}},
        {"code": "clustered_or_robust", "require": {"$all": [{"se_type": {"not_in": ["none"]}}, {"cluster_level": {"not_null": True}}]}},
        {"code": "weights_named", "require": {"weights_variable": {"null": True}}},
    ]
    assert rules.validate(rs, ECON) == []
    out = _run(rs, [ok, bad])
    assert ("r0", "page_number", "error", "page") not in out and ("r1", "page_number", "error", "page") in out
    assert ("r1", "ols_only", "warn", "model_type") in out and ("r1", "not_iv", "info", "model_type") in out and ("r1", "has_controls", "warn", "len(controls)") not in out
    assert any(c == "has_controls" and r == "r1" for r, c, _s, _p in out) and not any(c == "iv_named" for _r, c, _s, _p in out)
    assert ("r1", "star_level", "error", "cells[0].significance_level") in out and not any(c == "star_level" and r == "r0" for r, c, _s, _p in out)
    assert ("r1", "rows_ordered", "error", "cells[1].row_index") in out
    assert not any(c == "coef_close" for _r, c, _s, _p in out) and [r for r, c, _s, _p in out if c == "coef_exact"] == ["r0", "r1"]
    assert not any(c in ("enough_cells", "sum_sane", "either", "clustered_or_robust", "weights_named") for _r, c, _s, _p in out)
    msg = next(f for f in checks.check_rules([_input([ok, bad])], {"rules": rs}) if f["code"] == "page_number")
    assert msg["message"].startswith("page_number: page = 'p14' matches") and msg["detail"]["values"] == {"page": "p14"} and msg["document_id"] == "d1"


def test_paper_rules_read_the_paper_its_tables_and_the_entries_as_one_unit() -> None:
    rs = [
        {"code": "too_many", "unit": "paper", "when": {"count(regressions)": {"gt": 1}}, "message": "more than one column"},
        {"code": "note", "unit": "paper", "require": {"paper.inventory_note": {"not_null": True}, "inventory_note": {"matches": "tables"}}},
        {"code": "listed", "unit": "paper.table_inventory", "require": {"table_number": {"matches": "^Table"}}},
        {"code": "inventory_size", "unit": "paper", "require": {"count(table_inventory)": {"eq": {"field": "count(paper.table_inventory)"}}, "count(paper.table_inventory)": {"gte": 2}}},
    ]
    assert rules.validate(rs, ECON) == []
    out = checks.check_rules([_input([_reg("a"), _reg("b")])], {"rules": rs})
    assert [(f["record_id"], f["code"], f["path"], f["message"]) for f in out] == [(None, "too_many", None, "more than one column")]
    out = checks.check_rules([_input([_reg("a")], paper={"inventory_note": None, "table_inventory": [{"table_number": "Fig 1"}]})], {"rules": rs})
    assert sorted((f["code"], f["path"]) for f in out) == [("inventory_size", None), ("listed", "paper.table_inventory[0].table_number"), ("note", "paper.inventory_note")]   # not_null decides on a missing value
    assert all(f["record_id"] is None and f["document_id"] == "d1" for f in out)


def test_a_preset_suggests_the_rules_its_field_names_imply() -> None:
    spec = {"id": "x", "entries": {"key": "results", "fields": [
        {"name": "ci_low", "type": "number"}, {"name": "ci_high", "type": "number"}, {"name": "p_value", "type": "number"},
        {"name": "n_obs", "type": "integer"}, {"name": "share", "type": "number", "range": [0, 1]}, {"name": "label", "type": "string"}],
        "children": [{"key": "arms", "fields": [{"name": "n_participants", "type": "integer"}, {"name": "start_year", "type": "integer"}]}]},
        "paper": {"fields": [{"name": "pub_year", "type": "integer"}]}}
    out = rules.suggest(spec)
    assert rules.validate(out, spec) == []
    by = {r["code"]: r for r in out}
    assert by["ci_order"]["require"] == {"ci_low": {"lte": {"field": "ci_high"}}} and by["ci_order"]["unit"] == "entry"
    assert by["p_value_in_unit_interval"]["require"] == {"p_value": {"between": [0, 1]}} and "share_in_unit_interval" not in by   # a declared range is the schema's
    assert by["n_obs_positive"]["severity"] == "warn" and by["n_participants_positive"]["unit"] == "arms"
    assert by["pub_year_plausible"]["unit"] == "paper" and by["start_year_plausible"]["unit"] == "arms"
    assert rules.suggest(ECON) == []                                               # nothing its names imply beyond the contract (the paper's identity year is the engine's)


# ── a run ─────────────────────────────────────────────────────────────────────

SPEC = {"format": 1, "id": "ruled", "meta": {"title": "Ruled"},
        "steps": [{"id": "tables", "kind": "extract", "preset": PIN, "source": "supplied"},
                  {"id": "plaus", "kind": "check", "check": "rules", "over": ["tables"], "on_fail": "flag",
                   "params": {"rules": [{"code": "page_number", "require": {"page": {"matches": r"^\d+$"}}},
                                        {"code": "star_level", "unit": "cells", "where": {"significance_stars": {"gte": 1}}, "require": {"significance_level": {"lte": 0.1}}}]}}]}


def test_a_rules_check_runs_in_a_workflow_and_flags_the_records_it_finds(monkeypatch, no_queue) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    owner, owner_id, _ = _account("rules-owner")
    ds = owner.post("/api/datasets", json={"title": "Ruled", "schema_id": PIN}).json()["id"]
    spec = ws.normalize(copy.deepcopy(SPEC))
    assert ws.validate(spec)[0] == []
    _declare(conn, ds, spec, monkeypatch)
    run = owner.post(f"/api/datasets/{ds}/workflow/runs", json={}).json()
    docs = {}
    for t in ("fine", "odd"):
        res = _result(t, n=2)
        if t == "odd":
            for reg in res["regressions"]:
                reg["page"] = "p14"
        r = owner.post("/api/ingest-pdf", files={"pdf": (f"{t}.pdf", _pdf(f"{t}-{uuid.uuid4().hex[:4]}"), "application/pdf")},
                       data={"result": json.dumps(res), "run_id": run["id"], "step_id": "tables"})
        assert r.status_code == 200, r.text
        docs[t] = r.json()["document_id"]
    view = owner.post(f"/api/workflow-runs/{run['id']}/complete", json={"step_id": "tables"}).json()
    step = next(s for s in view["steps"] if s["step_id"] == "plaus")
    assert step["status"] == "done" and step["outputs"]["by_code"] == {"page_number": 2} and step["outputs"]["n_checked"] == 4
    assert step["review"]["total"] == 2 and step["review"]["satisfied"] is False           # the two odd records, in front of a reviewer
    conn.rollback()
    found = checks.for_run(conn, run["id"], "plaus")
    assert sorted((f["document_id"], f["code"], f["path"], f["severity"]) for f in found) == [(docs["odd"], "page_number", "page", "error")] * 2
    panel = owner.get(f"/api/datasets/{ds}/workflow").json()
    item = next(s for s in panel["steps"] if s["id"] == "plaus")
    assert item["findings"]["findings"] == 2 and item["findings"]["n_checked"] == 4
    from paperlens import workflow_cards
    codes = next(s for s in workflow_cards._steps(conn, spec) if s["id"] == "plaus")["check_info"]["codes"]   # noqa: SLF001
    assert codes == {"page_number": "error", "star_level": "error"}
