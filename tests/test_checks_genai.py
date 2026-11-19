"""The GenAI-at-work record checks (paperlens/checks_genai.py): a clean task yields nothing; each
rule fires once on a record built to trip it; the codes keep the pipeline's names and
severities. No database."""
from __future__ import annotations

import copy
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import checks
from paperlens.checks_genai import CODES, check_genai_record, number_in_text

DOC = "d1"


def _row(**kw) -> dict:
    base = {"arm": "T", "vs_arm": "C", "measure": "minutes", "subgroup": None, "horizon": None, "contrast_type": "ai_vs_no_ai",
            "estimand_type": "itt", "n": 50, "n_obs": 50, "mean": 20.0, "sd": 4.0, "effect_reported": "-5.0", "effect_value": -5.0,
            "effect_type": "mean_difference", "se": 0.8, "ci_low": -6.6, "ci_high": -3.4, "p_value": 0.001, "p_reported": "p < 0.01",
            "is_main": True, "source": "Table 1, col (1), row AI", "measure_kind": "duration"}
    base.update(kw)
    return base


def _ref(**kw) -> dict:
    base = {"arm": "C", "vs_arm": None, "measure": "minutes", "subgroup": None, "horizon": None, "n": 50, "mean": 25.0, "sd": 4.0, "measure_kind": "duration"}
    base.update(kw)
    return base


def _task(rows: list[dict], arms: list[dict] | None = None, task_id: str = "T1") -> dict:
    arms = arms if arms is not None else [{"arm_id": "C", "is_baseline": True, "control_type": "no_ai_concurrent", "n_arm": 50, "sample_population_type": "students"},
                                          {"arm_id": "T", "is_baseline": False, "n_arm": 50, "sample_population_type": "students"}]
    return {"task_id": task_id, "task_name": "Memo", "arms": arms, "results": rows}


def _inputs(tasks: list[dict], pm: dict | None = None, evidence: dict | None = None) -> list[dict]:
    recs = [{"id": f"r{i}", "document_id": DOC, "paper_id": "p", "entry_index": i, "field_values": t, "paper": {}} for i, t in enumerate(tasks)]
    return [{"dataset_id": "ds", "schema_id": "genai-work-extraction@x", "spec": {"entries": {"key": "tasks"}}, "records": recs,
             "documents": {DOC: {"params": {}, "paper_metadata": pm or {}}}, "evidence": evidence or {}}]


def _codes(tasks, **kw) -> dict[str, str]:
    out = check_genai_record(_inputs(tasks, **kw), {})
    return {f["code"]: f["severity"] for f in out}


def test_a_clean_task_yields_nothing() -> None:
    assert check_genai_record(_inputs([_task([_ref(), _row()])]), {}) == []


def test_every_code_has_the_pipelines_severity() -> None:
    assert set(CODES.values()) <= {"error", "warn", "info"}
    reg = checks.REGISTRY["genai-work-record"]
    assert reg["codes"] is CODES and "genai-work-extraction" in reg["applies_to"]["presets"] and reg["inputs"] == 1


def test_structure_rules() -> None:
    assert _codes([_task([])]) == {"no_results": "error"}
    assert _codes([_task([_ref(), _row()]), _task([_ref(), _row()])]) == {"duplicate_task_id": "error"}
    arms = [{"arm_id": "C", "is_baseline": True, "control_type": "no_ai_concurrent"}, {"arm_id": "C", "is_baseline": False}]
    assert "duplicate_arm_id" in _codes([_task([_ref(), _row()], arms=arms)])
    assert _codes([_task([_ref(), _row(arm="X")])]) == {"arm_unknown": "error"}
    assert _codes([_task([_ref(), _row(vs_arm="X")])]) == {"vs_arm_unknown": "error"}
    assert _codes([_task([_ref(), _row(vs_arm="T")])]) == {"vs_self": "error"}
    arms = [{"arm_id": "C", "is_baseline": False}, {"arm_id": "T", "is_baseline": False}]
    assert _codes([_task([_ref(), _row()], arms=arms)]) == {"no_baseline": "error"}
    assert _codes([_task([_ref(effect_value=-5.0), _row()])]) == {"baseline_with_effect": "error"}
    assert _codes([_task([_ref(contrast_type="ai_vs_no_ai"), _row()])]) == {"baseline_contrast": "warn"}
    assert _codes([_task([_ref(), _row(contrast_type=None)])]) == {"no_contrast_type": "warn"}
    assert _codes([_task([_ref(), _row(estimand_type=None)])]) == {"no_estimand_type": "warn"}
    assert _codes([_task([_ref(), _row(), _row(effect_value=-4.0, effect_reported="-4.0", ci_low=-5.6, ci_high=-2.4)])]) == {"duplicate_main": "error"}
    assert _codes([_task([_ref(), _row(n_obs=10)])]) == {"n_obs_below_n": "error"}
    assert _codes([_task([_ref(), _row(n=60, n_obs=60)])]) == {"n_exceeds_arm": "warn"}
    assert _codes([_task([_ref(), _row(ci_low=-3.4, ci_high=-6.6)])]) == {"ci_reversed": "error"}
    assert _codes([_task([_ref(), _row(ci_low=-4.0, ci_high=-3.4)])]) == {"effect_outside_ci": "error", "ci_se_inconsistent": "warn"}
    assert _codes([_task([_ref(), _row(p_value=1.5)])]) == {"p_out_of_range": "error"}
    arms = [{"arm_id": "C", "is_baseline": True, "n_arm": 50, "sample_population_type": "students"},
            {"arm_id": "T", "is_baseline": False, "control_type": "placebo_tool", "n_arm": 50}]
    c = _codes([_task([_ref(), _row()], arms=arms)])
    assert c == {"no_control_type": "warn", "control_type_on_treatment": "error", "arm_sample_missing": "warn"}
    assert _codes([_task([_ref(), _row()])], pm={"period_start_year": 1850, "period_end_month": 13}) == {"bad_date": "warn"}


def test_plausibility_and_scale_rules() -> None:
    assert _codes([_task([_ref(), _row(n=0, n_obs=0)])]) == {"n_nonpositive": "error"}
    assert _codes([_task([_ref(), _row(sd=-1.0)])]) == {"sd_negative": "error"}
    assert "se_nonpositive" in _codes([_task([_ref(), _row(se=-0.5)])])
    assert "se_rounds_to_zero" in _codes([_task([_ref(), _row(se=0.0)])])
    assert "ratio_nonpositive" in _codes([_task([_ref(), _row(effect_type="ratio", effect_value=-0.5, effect_reported="-0.5", ci_low=None, ci_high=None, p_value=None, p_reported=None)])])
    assert "percent_below_minus_100" in _codes([_task([_ref(), _row(effect_type="percent_change", effect_value=-150.0, effect_reported="-150", ci_low=None, ci_high=None, p_value=None, p_reported=None, mean=None)])])
    assert "implausible_smd" in _codes([_task([_ref(), _row(effect_type="standardized_difference", effect_value=4.0, effect_reported="4.0", ci_low=None, ci_high=None, p_value=None, p_reported=None, mean=None)])])
    # p against the interval: the interval excludes 0 but p says nothing
    assert "p_ci_inconsistent" in _codes([_task([_ref(), _row(p_value=0.4, p_reported=None)])])
    # a per-cent change the means do not support, and one printed as a proportion
    pc = _row(effect_type="percent_change", effect_value=-0.2, effect_reported="-0.2", ci_low=None, ci_high=None, p_value=None, p_reported=None, se=None)
    assert _codes([_task([_ref(mean=25.0), pc])]) == {"percent_as_proportion": "error"}
    pc["effect_value"], pc["effect_reported"] = -50.0, "-50"
    assert _codes([_task([_ref(mean=25.0), pc])]) == {"percent_vs_means": "warn"}
    pc["effect_value"], pc["effect_reported"] = 20.0, "20"
    assert _codes([_task([_ref(mean=25.0), pc])]) == {"sign_disagrees": "error"}
    md = _row(effect_value=-9.0, effect_reported="-9.0", ci_low=-10.6, ci_high=-7.4, se=0.8)
    assert _codes([_task([_ref(mean=25.0), md])]) == {"effect_vs_means": "warn"}
    md = _row(effect_value=5.0, effect_reported="5.0", ci_low=3.4, ci_high=6.6)
    assert _codes([_task([_ref(mean=25.0), md])]) == {"sign_disagrees": "error"}
    big = _row(mean=60.0, sd=4.0, effect_value=-35.0, effect_reported="-35.0", ci_low=-36.6, ci_high=-33.4, p_value=None, p_reported=None, se=None)
    big["effect_value"] = 35.0; big["effect_reported"] = "35.0"; big["ci_low"], big["ci_high"] = 33.4, 36.6
    assert _codes([_task([_ref(mean=25.0), big])]) == {"implausible_g": "warn"}
    # GRIM: a mean of integer counts that no n gives at this rounding
    assert _codes([_task([_ref(measure_kind="output_count"), _row(measure_kind="output_count", n=10, n_obs=10, mean=20.03, se=None, ci_low=None, ci_high=None, p_value=None, p_reported=None, effect_value=None, effect_reported=None)])]) == {"grim_inconsistent": "warn"}


def test_statistics_within_the_rounding_and_sd_vs_se() -> None:
    assert _codes([_task([_ref(), _row(effect_reported="-4.0")])]) == {"effect_reported_mismatch": "error"}
    assert _codes([_task([_ref(), _row(ci_low=-5.5, ci_high=-4.5)])]) == {"ci_se_inconsistent": "warn"}
    assert _codes([_task([_ref(), _row(p_value=0.6, p_reported=None, ci_low=None, ci_high=None)])]) == {"p_se_inconsistent": "warn"}
    assert _codes([_task([_ref(), _row(p_reported="n.s.", p_value=None, ci_low=None, ci_high=None)])]) == {"stars_se_inconsistent": "warn"}
    assert _codes([_task([_ref(), _row(effect_value=-0.5, effect_reported="-0.5", p_reported="***", p_value=None, ci_low=None, ci_high=None, mean=None)])]) == {"stars_se_inconsistent": "warn"}
    # the arms' "sd" combine to the standard error: standard errors recorded as standard deviations
    assert "sd_looks_like_se" in _codes([_task([_ref(sd=0.6), _row(sd=0.53, se=0.8, ci_low=None, ci_high=None, p_value=None, p_reported=None)])])
    assert "se_vs_sd_inconsistent" in _codes([_task([_ref(sd=4.0), _row(sd=4.0, se=5.0, ci_low=None, ci_high=None, p_value=None, p_reported=None)])])
    assert "sd_below_se" in _codes([_task([_ref(sd=0.1), _row(sd=0.1, se=0.8, ci_low=None, ci_high=None, p_value=None, p_reported=None)])])


def test_duplicates_and_quotes() -> None:
    assert _codes([_task([_ref(), _row(), _row(is_main=False)])]) == {"duplicate_estimate": "warn"}
    assert _codes([_task([_ref(), _row(), _row(measure="hours", source="Table 2, col (1), row AI")])]) == {"duplicate_numbers_across_estimands": "warn"}
    assert _codes([_task([_ref(), _row(), _row(measure="hours", source="Table 1, col (1), row AI")])]) == {}        # one shared cell
    ev = {"r0": [{"field_path": "results[1].effect_value", "snippet": "AI −5.0 (0.8)", "page": 3, "located": True},
                 {"field_path": "results[1].n", "snippet": "n = 48", "page": 3, "located": True},
                 {"field_path": "results[1].se", "snippet": "AI −5.0 (0.9)", "page": 3, "located": False},
                 {"field_path": "results[1].mean", "snippet": "20.0 minutes", "page": 3, "located": True}]}
    c = check_genai_record(_inputs([_task([_ref(), _row()])], evidence=ev), {})
    assert {(f["code"], f["severity"], f["path"]) for f in c} == {("n_not_in_quote", "warn", "results[1].n"), ("quote_not_found", "warn", "results[1].se"),
                                                                   ("number_not_in_quote", "warn", "results[1].se")}
    ev["r0"][0]["snippet"] = "AI −4.9 (0.8)"
    c = check_genai_record(_inputs([_task([_ref(), _row()])], evidence=ev), {})
    assert ("number_not_in_quote", "error", "results[1].effect_value") in {(f["code"], f["severity"], f["path"]) for f in c}


def test_number_rendering() -> None:
    assert number_in_text(0.45, "a share of .45 of the sample") and number_in_text(1234.0, "N = 1,234") and number_in_text(-5.0, "−5.0 (0.8)")
    assert number_in_text(0.12, "12% of the time") and number_in_text(12.5, "12,5 Minuten")
    assert not number_in_text(5.0, "15.0 and 50.0") and not number_in_text(0.3, "nothing here")
