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


def test_designs_group_result_claims_by_the_taxonomy_units() -> None:
    """The causal designs: a result-claims document (v0.5, value/confidence wrappers) and the
    taxonomy placements → one entry per design unit with its records as rows; records the
    taxonomy did not place form one flagged design per strategy; the first quote of a design
    identifies it; a quote that prints the estimate is linked to the value."""
    vc = lambda x: {"value": x, "confidence": "high"}   # noqa: E731
    def claim(cid, strategy, stmt, pt, quote, headline=False):
        return {"claim_id": cid, "headline": headline, "statement": stmt, "treatment": {"label": "net-of-tax rate", "ontology_node": "income.taxes"},
                "outcome": {"label": "labor income", "ontology_node": "labor.earnings"}, "relationship": {"sign": vc("positive"), "magnitude": {"point_estimate": vc(pt), "se_or_ci": "(0.011)", "units": "elasticity"}},
                "estimand": {"type": "elasticity", "bound": "point", "target_population": "tax filers"}, "population": {"description": "balanced panel", "sex": "all", "period": "1982-1993"},
                "identification": {"strategy": strategy, "source_of_variation": "the 1987 reform", "instrument": "simulated rate change",
                                   "key_assumptions": [{"assumption": "parallel trends", "stated_or_implicit": "stated"}], "threats_discussed": ["mean reversion"], "robustness_checks": []},
                "estimation": {"estimator": vc("2SLS"), "source_table": "Table 2, col. 1"}, "provenance": {"quotes": [{"page": 286, "text": quote}]},
                "extraction_confidence": "high", "ambiguities": []}
    doc = {"artid": "dk_X", "paper": {"title": "T", "code_data_availability": "No statement."},
           "data": {"registers": [{"name_in_paper": "Income Tax Register", "dst_code": "SKAT", "years": "1980-2005", "role": "treatment", "variables_mentioned": ["labor income"]}],
                    "linkages": ["person id"], "sample": {"universe": "all residents", "unit_of_observation": "individual-year", "selection_rules": ["age 15-70"], "period": "1984-2005", "n_observations": 49000000, "n_individuals": None},
                    "construction_notes": ["tax rates simulated"]},
           "claims": [claim("dk_X_c1", "instrumental_variables", "A higher net-of-tax rate raises labor income.", 0.214, "DD elasticity = 0.214 (0.011)", True),
                      claim("dk_X_c2", "instrumental_variables", "The effect is larger for the self-employed.", 0.46, "about 0.5 for the self-employed"),
                      claim("dk_X_c3", "descriptive", "Top earners pay most of the tax.", None, "the top decile pays 60 percent")]}
    tax = {"units": [{"unit": "u1", "family": "instrumental_variables", "records": ["dk_X_c1", "dk_X_c2"], "headline": True, "instrument": "simulated rate change",
                      "source_of_variation": "mechanical reform changes", "sub_design": "instrumental_variables.simulated_mechanical_policy", "pattern": "pattern.mechanical",
                      "source": {"id": "source.tax_reform", "label": "tax reform rate changes", "kind": "policy_reform"}, "treatment": "net-of-tax rate", "outcome": "labor income", "summary": "Reforms shift rates.", "flags": []}]}
    labels = {"instrumental_variables.simulated_mechanical_policy": "Simulated (mechanical) policy instrument", "pattern.mechanical": "A mechanical change in X"}
    out, warns = imp.convert_designs(doc, tax, labels, {"title": "T", "doi": None, "year": 2014, "authors": None, "journal": "AEJ"}, 0)
    assert warns == []
    assert [u["unit_id"] for u in out["designs"]] == ["dk_X::u1", "dk_X::descriptive"]
    u1, u2 = out["designs"]
    assert u1["family"] == "instrumental_variables" and u1["sub_design_label"] == "Simulated (mechanical) policy instrument" and u1["pattern"] == "A mechanical change in X"
    assert u1["source_kind"] == "policy_reform" and u1["carries_headline"] is True and u1["n_records"] == 2 and u1["confidence"]["design"]["level"] == "high"
    assert u2["family"] == "descriptive" and u2["flags"] == ["descriptive: no identification strategy claimed"] and u2["confidence"]["design"]["level"] == "medium"
    r = u1["records"][0]
    assert r["record_id"] == "dk_X_c1" and r["sign"] == "positive" and r["point_estimate"] == 0.214 and r["estimator"] == "2SLS"   # wrappers unwrapped
    assert r["key_assumptions"] == ["parallel trends (stated)"] and r["estimand_type"] == "elasticity" and r["headline"] is True
    ev = out["evidence"]
    assert ev[0]["field"] == ["designs[0]", "designs[0].records[0]", "designs[0].records[0].point_estimate"] and ev[0]["page"] == 286
    assert ev[1]["field"] == ["designs[0].records[1]"]                                          # 0.5 is not 0.46: no value link
    assert ev[2]["field"] == ["designs[1]", "designs[1].records[0]"]
    pm = out["paper_metadata"]
    assert pm["registers"][0]["variables_mentioned"] == "labor income" and pm["n_observations"] == "49000000" and pm["code_data_availability"] == "No statement."
    real = [i for i in preset_spec.validate_result(copy.deepcopy(out), presets.load_all()["register-designs"], {}) if i["code"] != "missing_confidence"]
    assert real == [], real
