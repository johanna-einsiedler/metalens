"""The two register-data presets (claims chain, regression tables): they load, their prompts
carry the rules the chain rests on, and the analysis layer gives one row per claim × result
and one row per table cell. No database needed."""
from __future__ import annotations

import copy
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import analysis_table as at, preset_spec, presets  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402

CLAIMS = {
    "paper_metadata": {"title": "International trade and job polarization", "doi": "10.1/t", "year": 2023, "authors": ["Keller W", "Utar H"],
                       "journal": "J", "abstract_source": "printed"},
    "claims": [
        {"claim_id": "S1", "type": "edge", "statement": "Import competition decreases employment in mid-wage occupations.",
         "cause": "import competition", "effect": "employment in mid-wage occupations", "sign": "-",
         "anchor_quote": "rising import competition has led to reduced employment in mid-wage occupations", "intro_sentence": "Our difference-in-differences strategy compares occupation trajectories of workers",
         "support": "explicit", "refined_by_introduction": False, "scope_setting": "Denmark", "scope_period": "1999-2009",
         "scope_population": "textile workers", "scope_identification": "difference-in-differences", "magnitude_stated": None,
         "qualifies": [], "moderator": None, "relation": None, "notes": None,
         "results": [
             {"result_id": "R1", "role": "main", "exhibit": "table", "source_table": "Table 3", "panel": None, "column": "(1)", "row_label": "Import Competition",
              "outcome_variable": "Mid-wage employment", "regressor": "Import Competition", "point_estimate": -1.292, "estimate_se": 0.382,
              "treatment_relation": "direct", "outcome_relation": "direct", "sign_consistent": True, "why": "the headline DiD coefficient"},
             {"result_id": "R2", "role": "supporting", "exhibit": "table", "source_table": "Table 4", "panel": "A", "column": "(1)", "row_label": "Import Competition",
              "outcome_variable": "Mid-wage employment", "regressor": "Import Competition", "point_estimate": -1.991, "estimate_se": None,
              "treatment_relation": "direct", "outcome_relation": "direct", "sign_consistent": True, "why": "same finding, extended sample"}]},
        {"claim_id": "S2", "type": "not_causal", "statement": "The paper uses employer-employee matched data.", "cause": None, "effect": None, "sign": None,
         "anchor_quote": "We employ employer-employee matched data from Denmark", "intro_sentence": None, "support": "explicit",
         "refined_by_introduction": False, "scope_setting": None, "scope_period": None, "scope_population": None, "scope_identification": None,
         "magnitude_stated": None, "qualifies": [], "moderator": None, "relation": None, "notes": None, "results": []}],
    "evidence": [   # one quote may support several paths: the anchor identifies the claim, quotes its anchor and justifies its sign
        {"snippet": "rising import competition has led to reduced employment in mid-wage occupations", "page": 1, "source": "abstract",
         "field": ["claims[0]", "claims[0].anchor_quote", "claims[0].sign"]},
        {"snippet": "Our difference-in-differences strategy compares occupation trajectories of workers", "page": 2, "source": "introduction", "field": "claims[0].intro_sentence"},
        {"snippet": "Import Competition -1.292*** (0.382)", "page": 14, "source": "Table 3, column (1)", "field": ["claims[0].results[0]", "claims[0].results[0].point_estimate"]},
        {"snippet": "Import Competition -1.991*** (0.501)", "page": 15, "source": "Table 4, column (1)", "field": ["claims[0].results[1]", "claims[0].results[1].point_estimate"]},
        {"snippet": "We employ employer-employee matched data from Denmark", "page": 1, "source": "abstract", "field": ["claims[1]", "claims[1].anchor_quote"]}],
}

TABLES = {
    "paper_metadata": {"title": "International trade and job polarization", "doi": "10.1/t", "year": 2023, "authors": ["Keller W"], "journal": "J"},
    "regressions": [
        {"regression_id": "T3::_::C1", "table_number": "Table 3", "table_title": "Import competition and occupations", "panel": None, "column": "(1)",
         "column_label": None, "outcome_variable": "Mid-wage employment", "target_regressors": ["Import Competition"], "model_type": "diff_in_diff",
         "fixed_effects": ["worker", "year"], "controls": ["age", "age squared"], "iv": None, "weights_type": "none", "weights_variable": None,
         "cluster_level": "firm", "se_type": "clustered", "sample_restrictions": "textile workers in 1999; N = 12,410", "unit_of_observation": "worker-year",
         "time_period": "1999-2009", "outcome_construction": None, "data_construction_steps": [], "reproduction_notes": None, "notes": None,
         "cells": [{"row_label": "Import Competition", "row_index": 0, "column_label": "(1)", "raw_text": "-1.292***", "numeric_value": -1.292, "row_type": "coefficient", "refers_to": None, "significance_stars": 3},
                   {"row_label": "Import Competition", "row_index": 1, "column_label": "(1)", "raw_text": "(0.382)", "numeric_value": 0.382, "row_type": "se", "refers_to": 0, "significance_stars": 0},
                   {"row_label": "Observations", "row_index": 2, "column_label": "(1)", "raw_text": "12,410", "numeric_value": 12410, "row_type": "statistic_n_obs", "refers_to": None, "significance_stars": 0}]}],
    "evidence": [{"snippet": "Table 3: Import competition and occupations", "page": 14, "source": "Table 3", "field": ["regressions[0]", "regressions[0].cells"]}],
}


def test_presets_load_and_their_prompts_carry_the_rules() -> None:
    all_ = presets.load_all()
    claims, tables = all_["register-claims"], all_["register-tables"]
    assert claims["entries"]["key"] == "claims" and claims["entries"]["children"][0]["key"] == "results"
    assert tables["entries"]["key"] == "regressions" and [f["name"] for f in tables["entries"]["fields"] if f["type"] == "table"] == ["cells"]
    flat = lambda t: " ".join(t.split())   # noqa: E731  (the prompts are line-wrapped)
    p = flat(preset_spec.render_prompt(claims, {}))
    for must in ("The abstract decides WHICH findings exist", "is NOT a claim", "Refinement never adds what the anchor does not announce",
                 "Sign agreement is NOT a criterion for matching", "Do not force a match", "THE THREE QUOTES",
                 'claims[i].anchor_quote, claims[i].intro_sentence', "claims[i].results[j].point_estimate"):
        assert must in p, must
    q = flat(preset_spec.render_prompt(tables, {}))
    for must in ("T<table>::<panel>::C<column>", "refers_to", "Enumerate every control individually", "regressions[i].cells"):
        assert must in q, must
    real = lambda issues: [i for i in issues if i["code"] != "missing_confidence"]   # noqa: E731  (the fixtures carry no ratings)
    assert not real(preset_spec.validate_result(copy.deepcopy(CLAIMS), claims, {})), real(preset_spec.validate_result(copy.deepcopy(CLAIMS), claims, {}))
    assert not real(preset_spec.validate_result(copy.deepcopy(TABLES), tables, {})), real(preset_spec.validate_result(copy.deepcopy(TABLES), tables, {}))


def test_units_and_rows() -> None:
    claims, tables = presets.load_all()["register-claims"], presets.load_all()["register-tables"]
    assert [u["id"] for u in at.units(claims)] == ["entries", "results"] and at.default_unit(claims) == "results"
    assert [u["id"] for u in at.units(tables)] == ["entries", "cells"] and at.default_unit(tables) == "cells"

    recs = ingest(copy.deepcopy(CLAIMS), entries_key="claims").records
    rows = at.flatten(claims, at._unit(claims, None), [{"idx": k, "field_values": r.field_values, "entry_index": k, "sys": {"_study": "Keller (2023)"}} for k, r in enumerate(recs)])
    assert [r["p"] for r in rows] == ["results[0]", "results[1]", ""]                    # claim × result; a claim without results still has a row
    assert rows[0]["vals"]["point_estimate"] == -1.292 and rows[0]["vals"]["claim_id"] == "S1" and rows[0]["vals"]["sign"] == "-"
    assert rows[2]["vals"]["claim_id"] == "S2" and rows[2]["vals"].get("point_estimate") is None   # no result: the child columns are absent
    cols = {c["name"]: c for c in at.catalogue(claims, at._unit(claims, None), rows)}
    assert cols["point_estimate"]["roles"] == ["measure"] and cols["estimate_se"]["roles"] == ["dispersion"]
    assert "dimension" in cols["sign"]["roles"] and "identifier" in cols["claim_id"]["roles"] and cols["claim_id"]["scope"] == "entry"

    trecs = ingest(copy.deepcopy(TABLES), entries_key="regressions").records
    trows = at.flatten(tables, at._unit(tables, None), [{"idx": 0, "field_values": trecs[0].field_values, "entry_index": 0, "sys": {}}])
    assert [r["p"] for r in trows] == ["cells[0]", "cells[1]", "cells[2]"]
    assert trows[0]["vals"]["numeric_value"] == -1.292 and trows[0]["vals"]["row_type"] == "coefficient" and trows[1]["vals"]["refers_to"] == 0
    assert trows[0]["vals"]["regression_id"] == "T3::_::C1" and trows[0]["vals"]["outcome_variable"] == "Mid-wage employment"
    tcols = {c["name"]: c for c in at.catalogue(tables, at._unit(tables, None), trows)}
    assert tcols["numeric_value"]["roles"] == ["measure"] and "dimension" in tcols["row_type"]["roles"]
