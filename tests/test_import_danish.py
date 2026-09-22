"""The danish-register-econ importer's pure converters on a trimmed real paper (one stated
claim, its three matched results, the two regressions they cite). No server, no database."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE, os.path.join(_ROOT, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import import_danish as imp  # noqa: E402
from paperlens import preset_spec, presets  # noqa: E402

SNAP = Path(_HERE) / "snapshots" / "danish"
_load = lambda n: json.loads((SNAP / f"{n}.json").read_text(encoding="utf-8"))   # noqa: E731
META = {"title": "International trade and job polarization", "doi": "10.1016/j.jinteco.2023.103810", "year": 2023, "authors": None, "journal": "Journal of International Economics"}


def test_exhibit_grammar() -> None:
    assert imp.norm_table_token("Table II") == "2" and imp.norm_table_token("A.I") == "A1" and imp.norm_table_token("(3)") == "3"
    assert imp.parse_source_table("Table 3, column (1)") == {"exhibit": "table", "source_table": "Table 3", "panel": None, "column": "(1)"}
    assert imp.parse_source_table("Table 4, Panel A, columns (1)-(2)") == {"exhibit": "table", "source_table": "Table 4", "panel": "A", "column": "(1)"}
    assert imp.parse_source_table("Figure 4, Panel A. Labor income (annotation 'DD elasticity = 0.214 (0.011)')") == {"exhibit": "figure", "source_table": "Figure 4", "panel": "A", "column": None}


def test_claims_chain_converts_with_three_quotes_per_result_claim() -> None:
    stated, cmap, results, tables = _load("stated_claims"), _load("claim_map"), _load("results"), _load("tables")
    assert imp.page_offset(stated) == 0                                    # this journal prints the PDF's own page numbers
    out, warns = imp.convert_claims(stated, cmap, {c["claim_id"]: c for c in results["claims"]}, tables, META, 0)
    assert warns == [] and out["paper_metadata"]["abstract_source"] == "printed" and len(out["claims"]) == 1
    c = out["claims"][0]
    assert c["claim_id"] == "S1" and c["sign"] == "-" and c["cause"] == "import competition" and c["support"] == "explicit"
    assert c["anchor_quote"].startswith("We employ employer-employee matched data")
    assert c["intro_sentence"].startswith("First, workers affected by import competition")     # the claim's own introduction quote comes first
    assert c["scope_setting"] == "Denmark" and c["scope_period"] == "1999-2009"
    by = {r["result_id"]: r for r in c["results"]}
    assert set(by) == {f"dk_W2213841331_c{n}" for n in (1, 6, 7, 42, 84)} and c["results"][0]["result_id"] == "dk_W2213841331_c1"
    assert by["dk_W2213841331_c42"]["source_table"] == "Table 5" and by["dk_W2213841331_c42"]["panel"] == "A"   # cited by prose only: parsed, no cell
    assert by["dk_W2213841331_c84"]["exhibit"] == "figure" and by["dk_W2213841331_c84"]["source_table"] == "Figure 3"
    r1 = c["results"][0]
    assert r1["role"] == "main" and r1["source_table"] == "Table 3" and r1["column"] == "(1)" and r1["point_estimate"] == -1.292 and r1["estimate_se"] == 0.382
    assert r1["row_label"] == "Import Competition" and r1["sign_consistent"] is True and r1["exhibit"] == "table"
    # evidence: the abstract anchor (identifying the claim, quoting the anchor, justifying the sign), the introduction sentence, one table line per result
    ev = out["evidence"]
    anchor = next(e for e in ev if e["source"] == "abstract")
    assert set(anchor["field"]) >= {"claims[0]", "claims[0].anchor_quote", "claims[0].sign"} and anchor["page"] == 1
    intro = next(e for e in ev if e["source"] == "introduction")
    assert intro["field"] == ["claims[0].intro_sentence"] and intro["page"] == 2
    line = next(e for e in ev if "claims[0].results[0].point_estimate" in e["field"])
    assert line["snippet"] == "Import Competition -1.292***" and line["source"] == "Table 3, column (1)" and line["page"] == 8
    real = [i for i in preset_spec.validate_result(copy.deepcopy(out), presets.load_all()["register-claims"], {}) if i["code"] != "missing_confidence"]
    assert real == [{"path": "claims[0].results[4].point_estimate", "code": "uncited_value", "message": "value has no evidence item"}], real   # a figure value no quote prints


def test_tables_convert_cell_by_cell() -> None:
    tables = _load("tables")
    out, warns = imp.convert_tables(tables, META, 0)
    assert warns == [] and [r["regression_id"] for r in out["regressions"]] == ["T3::_::C1", "T4::A::C1"]
    r = out["regressions"][0]
    assert r["table_number"] == "Table 3" and r["column"] == "(1)" and r["target_regressors"] == ["Import Competition"] and r["model_type"] == "diff_in_diff"
    assert r["cells"][0] == {"row_label": "Import Competition", "row_index": 0, "column_label": "(1)", "raw_text": "-1.292***", "numeric_value": -1.292,
                             "row_type": "coefficient", "refers_to": None, "significance_stars": 3}
    assert r["cells"][1]["row_type"] == "se" and r["cells"][1]["refers_to"] == 0
    assert out["evidence"][0]["field"] == ["regressions[0]", "regressions[0].cells"] and out["evidence"][0]["page"] == 8
    real = [i for i in preset_spec.validate_result(copy.deepcopy(out), presets.load_all()["register-tables"], {}) if i["code"] != "missing_confidence"]
    assert real == [], real
