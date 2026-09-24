"""The two register-data presets (claims chain, regression tables): they load, they are SCHEMAS
for data produced outside Metalens rather than extraction recipes, and the analysis layer gives
one row per claim × result and one row per table cell. No database needed."""
from __future__ import annotations

import copy
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import analysis_table as at, preset_spec, presets, records  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402

CLAIMS = {
    "paper_metadata": {"title": "International trade and job polarization", "doi": "10.1/t", "year": 2023, "authors": ["Keller W", "Utar H"],
                       "journal": "J", "abstract_source": "printed"},
    "claims": [
        {"claim_id": "S1", "type": "edge", "statement": "Import competition decreases employment in mid-wage occupations.",
         "cause": "import competition", "effect": "employment in mid-wage occupations", "sign": "-",
         "cause_construct": "trade exposure", "effect_construct": "job polarization",
         "anchor_quote": "rising import competition has led to reduced employment in mid-wage occupations", "intro_sentence": "Our difference-in-differences strategy compares occupation trajectories of workers",
         "support": "explicit", "refined_by_introduction": False, "scope_setting": "Denmark", "scope_period": "1999-2009",
         "scope_population": "textile workers", "scope_identification": "difference-in-differences", "magnitude_stated": None,
         "qualifies": [], "moderator": None, "relation": None, "notes": None,
         "results": [
             {"result_id": "R1", "role": "main", "exhibit": "table", "source_table": "Table 3", "panel": None, "column": "(1)", "row_label": "Import Competition",
              "estimand": "S1/men/years-0-1", "subgroup": "men", "horizon": "years 0-1",
              "outcome_variable": "Mid-wage employment", "regressor": "Import Competition", "point_estimate": -1.292, "estimate_se": 0.382,
              "treatment_relation": "direct", "outcome_relation": "direct", "sign_consistent": True, "why": "the headline DiD coefficient"},
             {"result_id": "R2", "role": "main", "exhibit": "table", "source_table": "Table 4", "panel": "A", "column": "(1)", "row_label": "Import Competition",
              "estimand": "S1/women/years-0-1", "subgroup": "women", "horizon": "years 0-1",
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


def test_presets_are_import_schemas_with_no_prompt() -> None:
    all_ = presets.load_all()
    claims, tables = all_["register-claims"], all_["register-tables"]
    assert claims["entries"]["key"] == "claims" and claims["entries"]["children"][0]["key"] == "results"
    assert tables["entries"]["key"] == "regressions" and [f["name"] for f in tables["entries"]["fields"] if f["type"] == "table"] == ["cells"]
    # the records come from an agent pipeline that runs outside; a prompt here would claim
    # authorship of data it never produced
    for spec in (claims, tables):
        assert spec["meta"]["mode"] == "import"
        assert not (spec.get("prompt") or {}).get("text")
        assert "OUTSIDE Metalens" in spec["meta"]["description"]
    # …and a prompt is then a validation error, not merely unused
    import copy as _c
    bad = _c.deepcopy(claims); bad["prompt"] = {"text": "Extract the claims."}
    errs, _ = preset_spec.validate(bad)
    assert any("import" in e for e in errs), errs

    assert claims["display"]["review"]["constructs"] == {"from": "cause_construct", "to": "effect_construct", "basis": "construct_basis", "note": "construct_note"}
    assert claims["display"]["review"]["results"]["subgroup"] == "subgroup"
    rv = claims["display"]["review"]
    assert rv["layout"] == "chain" and rv["edge"] == {"from": "cause", "to": "effect", "sign": "sign", "signs": {"+": "increases", "-": "decreases", "0": "no effect", "mixed": "mixed"}}
    broken = copy.deepcopy(claims); broken["display"]["review"]["results"]["value"] = "no_such_field"; broken["display"]["review"]["quotes"].append({"label": "X", "field": "nope"})
    errs, _ = preset_spec.validate(broken)
    real = lambda issues: [i for i in issues if i["code"] != "missing_confidence"]   # noqa: E731  (the fixtures carry no ratings)
    assert claims["display"]["review"]["constructs"] == {"from": "cause_construct", "to": "effect_construct", "basis": "construct_basis", "note": "construct_note"}
    assert claims["display"]["review"]["results"]["subgroup"] == "subgroup"
    rv = claims["display"]["review"]
    assert rv["layout"] == "chain" and rv["edge"] == {"from": "cause", "to": "effect", "sign": "sign", "signs": {"+": "increases", "-": "decreases", "0": "no effect", "mixed": "mixed"}}
    broken = copy.deepcopy(claims); broken["display"]["review"]["results"]["value"] = "no_such_field"; broken["display"]["review"]["quotes"].append({"label": "X", "field": "nope"})
    errs, _ = preset_spec.validate(broken)
    assert any("results.value" in e for e in errs) and any("quotes" in e for e in errs)
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
    # the claim stays general; the stratum lives on the estimates, so men and women stay apart
    assert [r["vals"]["subgroup"] for r in rows[:2]] == ["men", "women"] and rows[0]["vals"]["horizon"] == "years 0-1"
    assert rows[0]["vals"]["effect_construct"] == "job polarization" and rows[0]["vals"]["estimand"] == "S1/men/years-0-1"

    assert rows[0]["vals"]["estimand"] == "S1/men/years-0-1"
    assert rows[2]["vals"]["claim_id"] == "S2" and rows[2]["vals"].get("point_estimate") is None   # no result: the child columns are absent
    cols = {c["name"]: c for c in at.catalogue(claims, at._unit(claims, None), rows)}
    assert cols["point_estimate"]["roles"] == ["measure"] and cols["estimate_se"]["roles"] == ["dispersion"]
    assert "dimension" in cols["sign"]["roles"] and "identifier" in cols["claim_id"]["roles"] and cols["claim_id"]["scope"] == "entry"
    assert cols["subgroup"]["roles"] == ["dimension"] and cols["effect_construct"]["roles"] == ["dimension"]

    trecs = ingest(copy.deepcopy(TABLES), entries_key="regressions").records
    trows = at.flatten(tables, at._unit(tables, None), [{"idx": 0, "field_values": trecs[0].field_values, "entry_index": 0, "sys": {}}])
    assert [r["p"] for r in trows] == ["cells[0]", "cells[1]", "cells[2]"]
    assert trows[0]["vals"]["numeric_value"] == -1.292 and trows[0]["vals"]["row_type"] == "coefficient" and trows[1]["vals"]["refers_to"] == 0
    assert trows[0]["vals"]["regression_id"] == "T3::_::C1" and trows[0]["vals"]["outcome_variable"] == "Mid-wage employment"
    tcols = {c["name"]: c for c in at.catalogue(tables, at._unit(tables, None), trows)}
    assert tcols["numeric_value"]["roles"] == ["measure"] and "dimension" in tcols["row_type"]["roles"]


def test_a_verdict_and_its_note_are_stored_on_the_entry() -> None:
    """What the chain review's OK / Flag write: a verification event on the claim (status, who,
    why), the entry's status, and the note the card shows back on the next load."""
    import uuid
    from test_analysis_table import _db_ok, _seed
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    ds, doc = _seed(conn, CLAIMS, "register-claims", f"rev-{uuid.uuid4().hex[:6]}")
    rid = records.dataset_records(conn, ds)[0]["id"]
    records.verify_record(conn, rid, status="flagged", notes="the evidence does not support it — the quote is from the conclusion",
                          verifier_kind="maintainer")
    view = records.document_view(conn, doc)
    rec = next(r for r in view["records"] if r["id"] == rid)
    assert rec["verification_status"] == "flagged"
    assert rec["note"]["text"].startswith("the evidence does not support it —") and rec["note"]["status"] == "flagged"
    ev = records.record_events(conn, rid)
    assert ev[0]["status"] == "flagged" and "conclusion" in ev[0]["notes"] and ev[0]["verifier_kind"] == "maintainer"
    records.verify_record(conn, rid, status="verified", notes="", verifier_kind="maintainer")   # checked after all
    rec2 = next(r for r in records.document_view(conn, doc)["records"] if r["id"] == rid)
    assert rec2["verification_status"] == "verified" and rec2["note"]["text"].startswith("the evidence")   # the last REASONED note stays visible
    assert len(records.record_events(conn, rid)) == 2                                            # both verdicts are kept
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()

def test_a_stated_magnitude_no_estimate_reproduces_is_flagged() -> None:
    """The paper says 'earnings decline by 2 percent' and every extracted estimate is something
    else: usually a different quantity (another horizon), which belongs to its own estimand."""
    spec = presets.load_all()["register-claims"]
    obj = copy.deepcopy(CLAIMS)
    obj["claims"][0]["magnitude_stated"] = "earnings decline by 5 percent"          # the estimates are -1.292 / -1.991
    flagged = [i for i in preset_spec.validate_result(obj, spec, {}) if i["code"] == "magnitude_unmatched"]
    assert len(flagged) == 1 and flagged[0]["path"] == "claims[0].magnitude_stated" and "reproduces 5" in flagged[0]["message"]
    # one coincidental match must not hide the other number's absence
    obj["claims"][0]["magnitude_stated"] = "men decline by 2 percent, women by 5 percent"
    assert [i["message"] for i in preset_spec.validate_result(obj, spec, {}) if i["code"] == "magnitude_unmatched"][0].endswith(
        "give it its own estimand") 
    obj["claims"][0]["magnitude_stated"] = "a 2 percent decline in 2019"             # a year is not a magnitude
    assert not [i for i in preset_spec.validate_result(obj, spec, {}) if i["code"] == "magnitude_unmatched"]
    obj["claims"][0]["magnitude_stated"] = "a decline of 1.292"                     # the same quantity, stated
    assert not [i for i in preset_spec.validate_result(obj, spec, {}) if i["code"] == "magnitude_unmatched"]
    obj["claims"][0]["magnitude_stated"] = "a decline of 129.2 percent of a point"  # the percent/proportion scale gap
    assert not [i for i in preset_spec.validate_result(obj, spec, {}) if i["code"] == "magnitude_unmatched"]
    obj["claims"][0]["magnitude_stated"] = None
    assert not [i for i in preset_spec.validate_result(obj, spec, {}) if i["code"] == "magnitude_unmatched"]


def test_two_preferred_estimates_of_one_estimand_are_flagged() -> None:
    """The invariant the preset states: several estimates may measure one quantity, but exactly
    one of them is the preferred one. Two 'main' rows under one estimand is an extraction issue."""
    spec = presets.load_all()["register-claims"]
    obj = copy.deepcopy(CLAIMS)
    obj["claims"][0]["results"][1]["estimand"] = "S1/men/years-0-1"          # the same quantity, twice preferred
    dup = [i for i in preset_spec.validate_result(obj, spec, {}) if i["code"] == "duplicate_main"]
    assert len(dup) == 1 and dup[0]["path"] == "claims[0].results[1]" and "split the estimand" in dup[0]["message"]
    assert not [i for i in preset_spec.validate_result(copy.deepcopy(CLAIMS), spec, {}) if i["code"] == "duplicate_main"]
