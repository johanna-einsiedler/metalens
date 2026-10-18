"""The econ-regression-tables preset: the import schema of the Robustness-at-Scale transcription
(one entry per estimated column, every printed cell, the coefficient link). Stdlib-only."""
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

RESULT = {
    "paper_metadata": {"title": "Trade and jobs", "doi": "10.1/t", "year": 2021, "authors": ["Author A"], "journal": "AER",
                       "table_inventory": [{"table_number": "Table 1", "table_title": "Summary statistics", "page": "12", "is_regression_table": False, "n_columns_estimated": None},
                                           {"table_number": "Table 3", "table_title": "Import competition and occupations", "page": "14", "is_regression_table": True, "n_columns_estimated": 2}],
                       "inventory_note": None},
    "regressions": [
        {"regression_id": "aer_111_2_5::T3::_::C1", "table_number": "Table 3", "table_title": "Import competition and occupations", "panel": None, "column": "(1)",
         "column_label": None, "page": "14", "outcome_variable": "Mid-wage employment",
         "target_regressors": [{"name": "Import Competition", "row_index": 0, "why": "the abstract's headline effect"}],
         "model_type": "OLS", "fixed_effects": ["worker", "year"], "controls": ["age", "age squared"], "iv_endogenous": None, "iv_instruments": [],
         "weights_type": "none", "weights_variable": None, "cluster_level": "firm", "se_type": "clustered",
         "sample_restrictions": "textile workers in 1999; N = 12,410", "unit_of_observation": "worker-year", "time_period": "1999-2009",
         "outcome_construction": None, "data_construction_steps": [], "reproduction_notes": None, "notes": None,
         "cells": [{"row_index": 0, "col_index": 0, "row_label": "Import Competition", "column_label": "(1)", "row_type": "coefficient", "raw_text": "-1.292***", "numeric_value": -1.292, "refers_to": None, "significance_stars": 3, "significance_level": 0.01, "is_string": False, "statistic_detail": None},
                   {"row_index": 1, "col_index": 0, "row_label": "", "column_label": "(1)", "row_type": "se", "raw_text": "(0.382)", "numeric_value": 0.382, "refers_to": 0, "significance_stars": 0, "significance_level": None, "is_string": False, "statistic_detail": None},
                   {"row_index": 2, "col_index": 0, "row_label": "Worker FE", "column_label": "(1)", "row_type": "string", "raw_text": "Yes", "numeric_value": None, "refers_to": None, "significance_stars": 0, "significance_level": None, "is_string": True, "statistic_detail": None},
                   {"row_index": 3, "col_index": 0, "row_label": "Observations", "column_label": "(1)", "row_type": "statistic_n_obs", "raw_text": "12,410", "numeric_value": 12410, "refers_to": None, "significance_stars": 0, "significance_level": None, "is_string": False, "statistic_detail": None},
                   {"row_index": 4, "col_index": 0, "row_label": "Mean of dep. var.", "column_label": "(1)", "row_type": "statistic_other", "raw_text": "0.31", "numeric_value": 0.31, "refers_to": None, "significance_stars": 0, "significance_level": None, "is_string": False, "statistic_detail": "mean of dependent variable"}]}],
    "evidence": [{"snippet": "Table 3: Import competition and occupations", "page": 14, "source": "Table 3", "field": ["regressions[0]", "regressions[0].cells"]}],
}


def test_it_is_an_import_schema_produced_outside_metalens() -> None:
    spec = presets.load_all()["econ-regression-tables"]
    assert spec["meta"]["mode"] == "import" and spec["meta"]["brands"] == ["metalens"]
    assert not (spec.get("prompt") or {}).get("text") and "OUTSIDE Metalens" in spec["meta"]["description"]
    errs, warns = preset_spec.validate(spec)
    assert errs == [] and not any("in no tab" in w for w in warns), (errs, warns)
    assert spec["entries"]["key"] == "regressions" and spec["entries"]["id_field"] == "regression_id"
    cells = next(f for f in spec["entries"]["fields"] if f["name"] == "cells")
    assert [c["name"] for c in cells["columns"]] == ["row_index", "col_index", "row_label", "column_label", "row_type", "raw_text", "numeric_value",
                                                        "refers_to", "significance_stars", "significance_level", "is_string", "statistic_detail"]
    tr = next(f for f in spec["entries"]["fields"] if f["name"] == "target_regressors")
    assert tr["type"] == "table" and [c["name"] for c in tr["columns"]] == ["name", "row_index", "why"]
    assert [f["name"] for f in spec["paper"]["fields"]] == ["table_inventory", "inventory_note"]
    assert preset_spec.has_card(spec) and preset_spec.schema_id(spec) == "econ-regression-tables@67780825"


def test_a_transcribed_column_round_trips() -> None:
    spec = presets.load_all()["econ-regression-tables"]
    issues = [i for i in preset_spec.validate_result(copy.deepcopy(RESULT), spec) if i["code"] != "missing_confidence"]
    assert issues == [], issues
    res = ingest(copy.deepcopy(RESULT), entries_key="regressions")
    assert len(res.records) == 1
    fv = res.records[0].field_values
    assert fv["regression_id"] == "aer_111_2_5::T3::_::C1" and fv["target_regressors"][0]["row_index"] == 0
    units = [u["id"] for u in at.units(spec)]
    assert {"entries", "target_regressors", "cells"} <= set(units)
    rec = [{"idx": 0, "field_values": fv, "entry_index": 0, "sys": {"_study": "Author (2021)", "_status": "unverified"}}]
    rows = at.flatten(spec, at._unit(spec, "cells"), rec)   # noqa: SLF001
    assert len(rows) == 5
    se = next(r for r in rows if r["vals"]["row_type"] == "se")
    assert se["vals"]["refers_to"] == 0 and se["vals"]["numeric_value"] == 0.382 and se["vals"]["col_index"] == 0
    assert next(r for r in rows if r["vals"]["row_type"] == "statistic_other")["vals"]["statistic_detail"] == "mean of dependent variable"
    cols = {c["name"]: c for c in at.catalogue(spec, at._unit(spec, "cells"), rows)}   # noqa: SLF001
    assert cols["numeric_value"]["roles"] == ["measure"] and "dimension" in cols["row_type"]["roles"]
