"""The checks registry: pure rules over records. No DB."""
from __future__ import annotations

import copy
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_register_presets import CLAIMS, TABLES
from test_regression_tables_preset import RESULT

from paperlens import checks, presets

ECON = presets.load_all()["econ-regression-tables"]


def _col(tag: str, **over) -> dict:
    reg = copy.deepcopy(RESULT["regressions"][0])
    reg["regression_id"] = f"{tag}::T3::_::C1"
    reg.update(over)
    return reg


def _input(cols: list[dict], *, doc="d1", paper=None) -> dict:
    recs = [{"id": f"r{i}", "document_id": doc, "paper_id": "p", "entry_index": i, "field_values": c, "paper": paper or {"doi": "10.1/t", "title": "T"}}
            for i, c in enumerate(cols)]
    return {"dataset_id": "ds", "schema_id": "econ-regression-tables@67780825", "spec": ECON, "records": recs,
            "documents": {doc: {"params": {}, "paper_metadata": {}}}}


def _codes(findings):
    return sorted((f["record_id"], f["code"], f["severity"], f.get("path")) for f in findings)


def test_a_consistent_column_passes_and_each_rule_fires_on_its_own_fault() -> None:
    P = checks.params_for({"check": "regression-cells"})
    assert checks.check_regression_cells([_input([_col("ok")])], P) == []
    # an se row that names no coefficient; one that names a string row; two se rows for one coefficient
    c = _col("a"); c["cells"][1]["refers_to"] = 7
    assert _codes(checks.check_regression_cells([_input([c])], P)) == [("r0", "se_dangling_ref", "error", "cells[1].refers_to")]
    # a test's p-value printed as a row of its own (joint significance, equality of treatments): no coefficient's — passes;
    # an unlabelled p row under a coefficient still needs its refers_to
    c = _col("p"); n = len(c["cells"])
    c["cells"].append({"row_index": n, "row_label": "Joint significance of treatments", "row_type": "p_value", "raw_text": "0.000", "numeric_value": 0.0, "refers_to": None, "significance_stars": 0})
    assert checks.check_regression_cells([_input([c])], P) == []
    c["cells"].append({"row_index": n + 1, "row_label": "", "row_type": "p_value", "raw_text": "[0.03]", "numeric_value": 0.03, "refers_to": None, "significance_stars": 0})
    assert _codes(checks.check_regression_cells([_input([c])], P)) == [("r0", "se_dangling_ref", "error", f"cells[{n + 1}].refers_to")]
    c = _col("b"); c["cells"][1]["refers_to"] = 2
    assert _codes(checks.check_regression_cells([_input([c])], P)) == [("r0", "se_refers_noncoef", "error", "cells[1].refers_to")]
    c = _col("c"); c["cells"].append({**c["cells"][1], "row_index": 5})
    assert ("r0", "coef_many_se", "error", "cells[5].refers_to") in _codes(checks.check_regression_cells([_input([c])], P))
    # the parsed number is not the printed one; the printed form may carry stars, parentheses, a unicode minus, commas
    c = _col("d"); c["cells"][0]["numeric_value"] = -1.3
    assert _codes(checks.check_regression_cells([_input([c])], P)) == [("r0", "numeric_mismatch", "error", "cells[0].numeric_value")]
    c = _col("e"); c["cells"][0]["raw_text"] = "−1.292***"; c["cells"][3]["raw_text"] = "12,410"
    assert checks.check_regression_cells([_input([c])], P) == []
    # stars against the stated level and against a p-value
    c = _col("f"); c["cells"][0]["significance_level"] = 0.05
    assert _codes(checks.check_regression_cells([_input([c])], P)) == [("r0", "stars_vs_level", "warn", "cells[0].significance_level")]
    c = _col("g"); c["cells"].append({"row_index": 5, "col_index": 0, "row_label": "", "column_label": "(1)", "row_type": "p_value", "raw_text": "[0.03]",
                                      "numeric_value": 0.03, "refers_to": 0, "significance_stars": 0, "significance_level": None, "is_string": False, "statistic_detail": None})
    assert _codes(checks.check_regression_cells([_input([c])], P)) == [("r0", "stars_vs_p", "warn", "cells[5].numeric_value")]
    # t against coefficient / se: -1.292 / 0.382 = -3.38
    c = _col("h"); c["cells"].append({"row_index": 5, "col_index": 0, "row_label": "", "column_label": "(1)", "row_type": "t_stat", "raw_text": "[-2.0]",
                                      "numeric_value": -2.0, "refers_to": 0, "significance_stars": 0, "significance_level": None, "is_string": False, "statistic_detail": None})
    out = checks.check_regression_cells([_input([c])], P)
    assert _codes(out) == [("r0", "t_vs_coef_se", "warn", "cells[5].numeric_value")] and abs(out[0]["detail"]["expected"] + 3.3822) < 1e-3
    c["cells"][5]["numeric_value"] = -3.4; c["cells"][5]["raw_text"] = "[-3.4]"   # within tolerance
    assert checks.check_regression_cells([_input([c])], P) == []
    # the stated N against the printed N; the columns of one table disagreeing (a note)
    c = _col("i"); c["sample_restrictions"] = "textile workers; N = 12,000"
    assert _codes(checks.check_regression_cells([_input([c])], P)) == [("r0", "n_obs_vs_sample", "warn", "cells[3].numeric_value")]
    c2 = _col("j", column="(2)", sample_restrictions=None); c2["cells"][3]["numeric_value"] = 9999; c2["cells"][3]["raw_text"] = "9,999"
    out = checks.check_regression_cells([_input([_col("j"), c2])], P)
    assert _codes(out) == [("r0", "n_obs_varies", "info", "cells"), ("r1", "n_obs_varies", "info", "cells")]
    # row indexes not from 0
    c = _col("k"); c["cells"][2]["row_index"] = 9
    assert ("r0", "row_index_gap", "warn", "cells") in _codes(checks.check_regression_cells([_input([c])], P))
    # the legend is a parameter
    c = _col("l"); c["cells"][0]["significance_level"] = 0.05
    assert checks.check_regression_cells([_input([c])], {**P, "star_legend": [[3, 0.05]]}) == []


def test_claims_against_the_companion_tables() -> None:
    claims = copy.deepcopy(CLAIMS["claims"])
    recs = {"dataset_id": "ds", "schema_id": "register-claims@x", "spec": None,
            "records": [{"id": "c0", "document_id": "dc", "paper_id": "p", "entry_index": i, "field_values": c, "paper": {"doi": "10.1/t", "title": "T"}}
                        for i, c in enumerate(claims)],
            "documents": {}, "companion": {"dataset_id": "tables", "regressions": [
                {"record_id": "x", "paper": "10.1/t", "regression_id": "T3::_::C1", "table": "3", "panel": "", "column": "1", "coefficients": [-1.292, 0.4]}]}}
    out = checks.check_claims_vs_tables([recs], {})
    # S1 cites Table 3 col (1) = -1.292 (exact) and Table 4 col (1) = -1.991 (no such column: a warning)
    assert _codes(out) == [("c0", "exhibit_unlinked", "warn", "results[1].source_table")]
    assert out[0]["detail"]["status"] == "unlinked" and "Table 4" in out[0]["message"]
    recs["companion"]["regressions"].append({"record_id": "y", "paper": "10.1/t", "regression_id": "T4::_::C1", "table": "4", "panel": "", "column": "1", "coefficients": [-1.5]})
    out = checks.check_claims_vs_tables([recs], {})
    assert _codes(out) == [("c0", "estimate_mismatch", "error", "results[1].point_estimate")]
    assert out[0]["companion_record_id"] == "y" and out[0]["detail"]["regression_id"] == "T4::_::C1" and out[0]["detail"]["table_coefficients"] == [-1.5]
    # no companion rows at all: every cited exhibit is unlinked, nothing is an error
    recs["companion"] = {"dataset_id": "tables", "regressions": []}
    assert {f["severity"] for f in checks.check_claims_vs_tables([recs], {})} == {"warn"}


def test_step_validation_knows_the_registry() -> None:
    errs = []
    E, W = errs.append, (lambda m: None)
    presets_of = {"tables": ECON, "claims": presets.load_all()["register-claims"]}
    checks.validate_step({"id": "n", "check": "regression-cells", "over": ["tables"], "params": {"t_tolerance": 0.2}}, "$.steps[1]", E, W, presets_of=presets_of)
    assert errs == []
    checks.validate_step({"id": "n", "check": "regression-cells", "over": ["claims"]}, "$.steps[1]", E, W, presets_of=presets_of)
    assert any("does not apply to 'register-claims'" in e for e in errs); errs.clear()
    checks.validate_step({"id": "n", "check": "claims-vs-tables", "over": ["claims"]}, "$.steps[1]", E, W, presets_of=presets_of)
    assert errs == []
    checks.validate_step({"id": "n", "check": "regression-cells", "over": ["tables"], "params": {"star_legend": "***"}}, "$.steps[1]", E, W, presets_of=presets_of)
    assert any("must be a table" in e for e in errs); errs.clear()
    checks.validate_step({"id": "n", "check": "regression-cells", "over": ["tables", "tables"]}, "$.steps[1]", E, W, presets_of=presets_of)
    assert any("reads 1 step" in e for e in errs)
    assert checks.REGISTRY["claims-vs-tables"]["needs_companion"] and set(checks.REGISTRY["regression-cells"]["codes"]) >= {"se_dangling_ref", "numeric_mismatch"}
    assert TABLES["regressions"][0]["cells"][1]["refers_to"] == 0        # the fixtures the DB tests build on are consistent
