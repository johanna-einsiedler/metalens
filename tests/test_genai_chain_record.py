"""The record assembly, the checks, the headline rule and the Metalens document of the
GenAI-at-work chain: the project's own selftests over the port, the Student t tail against
scipy's values, and — when the review project is on this machine — two papers' stored pass
files reproducing their record.json, headlines.json and v20 import file."""
from __future__ import annotations

import copy
import json
import os
import sys
from collections import Counter

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens import chains  # noqa: E402
from paperlens.chains.genai_work import checks as K  # noqa: E402
from paperlens.chains.genai_work import document as D  # noqa: E402
from paperlens.chains.genai_work import headlines as H  # noqa: E402
from paperlens.chains.genai_work import record as R  # noqa: E402
from paperlens.chains.genai_work import text as X  # noqa: E402
from test_genai_chain_replay import PROJECT, _project_ok  # noqa: E402

RID2 = "0e925a5517f8"      # a paper whose import file carries the v20 review fields and whose pass files match its record


def test_the_student_t_tail_matches_scipy() -> None:
    for z, df, want in [(1.96, 10, 0.07843624024769974), (2.5, 30, 0.018115649068066706), (0.5, 5, 0.638298871640929), (3.2, 100, 0.0018419425158830525),
                        (1.0, 3, 0.39100221895577053), (0.0, 8, 1.0), (6.0, 2, 0.02667147321542477), (2.0, 1, 0.2951672353008664)]:
        assert abs(K.t_two_sided(z, df) - want) < 1e-9, (z, df)
    assert abs(K.implied_p(2.0, 1.0) - 0.04550026389635842) < 1e-9 and abs(K.implied_p(2.0, 1.0, 12) - K.t_two_sided(2.0, 10)) < 1e-12


def test_the_record_checks_selftest_holds_over_the_port() -> None:
    rec = {"study": {"period_start_year": {"value": 2023, "quote": "q", "page": 1}, "period_start_month": 14,
                     "sample_size": {"value": 100, "quote": "100 participants", "page": 2},
                     "experimental_design": {"value": "across_person", "quote": "q", "page": 1},
                     "confidence": {"design": {"level": "low", "notes": "guess"}}},
           "tasks": [{"task_id": "T1",
                      "arms": [{"arm_id": "C", "is_baseline": True, "n_arm": {"value": 60, "quote": "60", "page": 1}, "control_type": {"value": "no_ai_concurrent", "items": [], "proposed": None}},
                               {"arm_id": "T1", "is_baseline": False, "n_arm": {"value": 60, "quote": "60", "page": 1}, "control_type": {"value": "other_ai", "items": [], "proposed": None}}],
                      "results": [
                          {"arm": "C", "vs_arm": None, "measure": "time (min)", "subgroup": "full sample", "horizon": None, "n": 60, "mean": 30.0, "sd": 5.0, "stats_quote": "30.0 (5.0) n=60", "stats_page": 3,
                           "effect_value": None, "se": None, "ci_low": None, "ci_high": None, "p_value": None, "effect_reported": None, "is_main": False, "contrast_type": None, "effect_type": None},
                          {"arm": "T1", "vs_arm": "C", "measure": "time (min)", "subgroup": "full sample", "horizon": None, "n": 70, "mean": 20.0, "sd": 5.0, "stats_quote": "20.0 (5.0) n=70", "stats_page": 3,
                           "effect_reported": "-10 min", "effect_value": 10.0, "effect_type": {"value": "mean_difference", "proposed": None},
                           "se": 1.0, "ci_low": 8.0, "ci_high": 12.0, "p_value": 1.5, "effect_quote": "difference of 10 minutes (SE 1.0), CI [8, 12]", "effect_page": 3,
                           "n_obs": 50, "is_main": True, "contrast_type": {"value": "ai_vs_ai", "proposed": None}},
                          {"arm": "T1", "vs_arm": "C", "measure": "time (min)", "subgroup": "full sample", "horizon": None, "n": None, "mean": None, "sd": None, "effect_reported": "-9", "effect_value": -9.0,
                           "effect_type": {"value": "mean_difference", "proposed": None}, "effect_quote": "-9 with controls", "effect_page": 4, "is_main": True, "contrast_type": {"value": "other", "proposed": ""},
                           "se": None, "ci_low": None, "ci_high": None, "p_value": None},
                          {"arm": "T9", "vs_arm": "C", "measure": "quality", "subgroup": "full sample", "horizon": None, "n": 70, "mean": 4.0, "sd": None, "stats_quote": "mean 4.1", "stats_page": 5, "effect_value": None,
                           "se": None, "ci_low": None, "ci_high": None, "p_value": None, "effect_reported": None, "is_main": False, "contrast_type": {"value": "ai_vs_no_ai", "proposed": None}, "effect_type": None}]}]}
    codes = sorted(i["code"] for i in K.numbers_in_quotes(rec) + K.structure(rec) + K.categories(rec) + K.confidence(rec))
    want = ["arm_unknown", "arm_sample_missing", "bad_date", "contrast_mismatch", "control_type_on_treatment", "duplicate_main", "low_confidence", "n_arm_exceeds_sample",
            "n_exceeds_arm", "n_obs_below_n", "no_baseline_row", "no_estimand_type", "number_not_in_quote", "other_without_term", "p_out_of_range", "sign_disagrees"]
    assert not [w for w in want if w not in codes], codes
    rec2 = {"study": {}, "tasks": [{"task_id": "T2", "arms": [{"arm_id": "C", "is_baseline": True}, {"arm_id": "T", "is_baseline": False}], "results": [
        {"arm": "C", "vs_arm": None, "measure": "commits", "subgroup": "full sample", "horizon": None, "measure_kind": "output_count", "n": 50, "mean": 10.0, "sd": 0.2, "stats_quote": "10.0 (0.2)", "effect_type": None},
        {"arm": "T", "vs_arm": "C", "measure": "commits", "subgroup": "full sample", "horizon": None, "measure_kind": "output_count", "n": 20, "mean": 3.37, "sd": 0.2, "stats_quote": "3.37 (0.2)", "effect_value": 5.0,
         "effect_type": {"value": "mean_difference", "proposed": None}, "se": 0.0, "ci_low": -1.0, "ci_high": 1.0, "p_value": 0.01},
        {"arm": "T", "vs_arm": "C", "measure": "commits", "subgroup": "full sample", "horizon": None, "n": 50, "mean": 12.0, "sd": 0.2, "stats_quote": "12.0 (0.2)", "effect_value": 0.2, "effect_type": {"value": "percent_change", "proposed": None}},
        {"arm": "T", "vs_arm": "C", "measure": "commits", "subgroup": "men", "horizon": None, "effect_value": -0.5, "effect_type": {"value": "ratio", "proposed": None}},
        {"arm": "T", "vs_arm": "C", "measure": "commits", "subgroup": "women", "horizon": None, "effect_value": 4.5, "effect_type": {"value": "standardized_difference", "proposed": None}},
        {"arm": "T", "vs_arm": "C", "measure": "commits", "subgroup": "old", "horizon": None, "effect_value": -120, "effect_type": {"value": "percent_change", "proposed": None}}]}]}
    codes2 = sorted(i["code"] for i in K.plausibility(rec2))
    for w in ("se_nonpositive", "p_ci_inconsistent", "grim_inconsistent", "effect_vs_means", "implausible_g", "percent_as_proportion", "ratio_nonpositive", "implausible_smd", "percent_below_minus_100"):
        assert w in codes2, f"missing {w}; got {codes2}"
    assert K.grim_ok(3.35, 20, 2) and not K.grim_ok(3.37, 20, 2) and K.grim_ok(3.37, 1000, 2)


def test_the_pass_checks_selftest_holds_over_the_port() -> None:
    ex = {"tables": [{"id": "Table 2", "page": 12, "panels": [{"panel": "Panel A", "header": ["", "(1) Time", "(2) Score"],
          "rows": [["Used AI", "0.12", "0.30"], ["Used AI (SE)", "(0.05)", "(0.10)"], ["Treatment x High", "0.20", "0.11"], ["Treatment x High (SE)", "(0.07)", "(0.09)"], ["Observations", "100", "100"]]}],
          "check": "table_cells", "cells_checked": 10, "cells_found": 10}], "figures": [], "text_numbers": []}
    es = {"tasks": [{"task_id": "T1"}], "arms": [{"arm_id": "C", "is_reference": True, "task_ids": ["T1"]}, {"arm_id": "T1", "is_reference": False, "task_ids": ["T1"]}],
          "outcomes": [{"outcome_id": "O1"}, {"outcome_id": "O2"}],
          "estimands": [{"estimand_id": "E1", "task_id": "T1", "arm_id": "T1", "vs_arm_id": "C", "outcome_id": "O1"}, {"estimand_id": "E2", "task_id": "T1", "arm_id": "T1", "vs_arm_id": "C", "outcome_id": "O2"}]}
    em = {"estimates": [
        {"estimate_id": "E1_main", "estimand_id": "E1", "role": "main", "source": {"exhibit": "Table 2", "panel": "Panel A", "column": "(1) Time", "row": "Treatment x High", "page": 12}, "effect_value": 0.20, "se": 0.07, "n_obs": 100, "effect_reported": "0.20 (0.07)"},
        {"estimate_id": "E2_main", "estimand_id": "E2", "role": "main", "source": {"exhibit": "Table 2", "panel": "Panel A", "column": "(1) Time", "row": "Treatment x High", "page": 12}, "effect_value": 0.99, "se": 0.07, "ci_low": 0.1, "ci_high": 0.2, "p_value": 0.9},
        {"estimate_id": "E3", "estimand_id": "E9", "role": "main", "source": {"exhibit": "Table 7"}, "effect_value": 1}], "unmatched_estimands": [], "orphan_numbers": []}
    issues, summary = K.pass_issues(ex, es, em)
    codes = Counter(i["code"] for i in issues)
    want = {"exhibit_row_unreferenced": 1, "exhibit_column_unreferenced": 1, "source_cell_mismatch": 1, "duplicate_source_cell": 1, "ci_se_inconsistent": 1, "p_se_inconsistent": 1,
            "source_exhibit_unknown": 1, "estimate_unknown_estimand": 1, "number_not_in_source_row": 2}
    assert {k: codes.get(k, 0) for k in want} == want, dict(codes)
    assert summary["exhibit_coverage"]["ratio"] == 0.5
    assert K.star_convention("*, **, *** denote significance at the 10%, 5%, and 1% levels") == {1: 0.1, 2: 0.05, 3: 0.01}
    assert K.star_convention("Standard errors in parentheses. *** p<0.01, ** p<0.05, * p<0.1.") == {3: 0.01, 2: 0.05, 1: 0.1} and K.star_convention("Standard errors in parentheses.") is None
    assert K.parse_p("<0.01") == ("lt", 0.01) and K.parse_p("p = .03") == ("eq", 0.03) and K.parse_p("***") == ("stars", 3) and K.parse_p("n.s.") == ("ns", None)
    ex2 = {"tables": [
        {"id": "Table 3", "page": 5, "caption": "Effects", "notes": "Standard errors in parentheses. *** p<0.01, ** p<0.05, * p<0.1.",
         "panels": [{"panel": None, "header": ["", "(1) Output", "(2) Errors"], "rows": [["AI", "0.50***", "0.40"], ["AI (SE)", "(0.10)", "(0.30)"], ["Observations", "200", "200"]]}]},
        {"id": "Table 1", "page": 3, "caption": "Descriptives", "notes": "Standard deviations in parentheses.",
         "panels": [{"panel": None, "header": ["", "Control", "Treatment"], "rows": [["Output", "10.0 (2.0)", "12.0 (2.5)"], ["N", "100", "100"]]}]},
        {"id": "Table 4", "page": 4, "caption": "Means", "notes": "", "panels": [{"panel": None, "header": ["", "Mean"], "rows": [["Output", "12.0 ± 2.5"]]}]}], "figures": [], "text_numbers": []}
    es2 = {"tasks": [{"task_id": "T1"}], "arms": [{"arm_id": "C", "is_reference": True, "task_ids": ["T1"]}, {"arm_id": "T", "is_reference": False, "task_ids": ["T1"]}],
           "outcomes": [{"outcome_id": "O1"}, {"outcome_id": "O2"}, {"outcome_id": "O3"}],
           "estimands": [{"estimand_id": f"E{i}", "task_id": "T1", "arm_id": "T", "vs_arm_id": "C", "outcome_id": f"O{i}"} for i in (1, 2, 3)]}
    t3 = {"exhibit": "Table 3", "panel": None, "column": "(1) Output", "row": "AI", "page": 5}
    t1 = {"exhibit": "Table 1", "panel": None, "column": "Treatment", "row": "Output", "page": 3}
    em2 = {"estimates": [
        {"estimate_id": "A", "estimand_id": "E1", "role": "main", "source": t3, "effect_value": 0.5, "se": 0.10, "p_reported": "***", "statistic": "t = 5.0", "text_quote": "AI increased output.", "effect_type": "regression_coefficient"},
        {"estimate_id": "B", "estimand_id": "E2", "role": "main", "source": dict(t3, column="(2) Errors"), "effect_value": 0.4, "se": 0.30, "cell_as_printed": "0.40", "se_cell_as_printed": "(0.30)", "p_reported": "**", "statistic": "t = 3.0", "text_quote": "AI reduced errors.", "effect_type": "regression_coefficient"},
        {"estimate_id": "Cc", "estimand_id": "E3", "role": "main", "source": t1, "mean_treat": 12.0, "sd_treat": 2.5, "n_treat": 100, "mean_ref": 10.0, "sd_ref": 2.0, "n_ref": 100, "effect_value": 2.0, "effect_type": "mean_difference", "se": 3.2016},
        {"estimate_id": "D", "estimand_id": "E3", "role": "supporting", "source": t1, "mean_treat": 12.0, "sd_treat": 2.5, "effect_value": 2.0, "se": 2.5},
        {"estimate_id": "Ee", "estimand_id": "E2", "role": "robustness", "source": {"exhibit": "text", "panel": None, "column": None, "row": None, "page": 6}, "effect_value": 0.5, "se": 0.10},
        {"estimate_id": "F", "estimand_id": "E1", "role": "robustness", "source": {"exhibit": "text", "panel": None, "column": None, "row": None, "page": 6}, "effect_type": "ratio", "effect_value": 1.5, "ci_low": 1.2, "ci_high": 1.875, "se": 0.1138},
        {"estimate_id": "H", "estimand_id": "E3", "role": "robustness", "source": {"exhibit": "Table 4", "panel": None, "column": "Mean", "row": "Output", "page": 4}, "mean_treat": 12.0, "sd_treat": 2.5, "cell_as_printed": "12.0 ± 2.5"},
        {"estimate_id": "I", "estimand_id": "E1", "role": "supporting", "source": t3, "effect_value": 0.5, "sd_treat": 0.10}], "unmatched_estimands": [], "orphan_numbers": []}
    issues2, _ = K.pass_issues(ex2, es2, em2)
    codes2 = Counter(i["code"] for i in issues2)
    by_path = {}
    for i in issues2:
        by_path.setdefault(i["path"], set()).add(i["code"])
    want2 = {"stars_se_inconsistent": 1, "t_se_inconsistent": 1, "sign_vs_text": 1, "sd_looks_like_se": 1, "sd_below_se": 1, "se_from_sd_parentheses": 1,
             "duplicate_numbers_across_estimands": 1, "pm_unlabelled": 1, "sd_from_se_parentheses": 1}
    assert {k: codes2.get(k, 0) for k in want2} == want2, dict(codes2)
    assert "ci_se_inconsistent" not in by_path.get("estimates[F]", set())
    assert K.z_bounds({"cell_as_printed": "0.40", "se_cell_as_printed": "(0.30)"}, 0.4, 0.3) == (0.395 / 0.305, 0.405 / 0.295)
    assert {"stars_se_inconsistent", "t_se_inconsistent", "sign_vs_text"} <= by_path["estimates[B]"] and not by_path.get("estimates[A]", set()) & {"stars_se_inconsistent", "t_se_inconsistent", "sign_vs_text"}


def test_the_headline_rule_selftest_holds_over_the_port() -> None:
    rec = {"tasks": [{"task_id": "T1", "outcomes": [{"outcome_id": "O1", "measure": "Overall quality index", "how_measured": "average of the three ratings"},
                                                    {"outcome_id": "O2", "measure": "Accuracy", "how_measured": "rating"}, {"outcome_id": "O3", "measure": "Time", "how_measured": "minutes"}],
                      "results": [
                          {"estimate_id": "e1", "arm": "T", "vs_arm": "C", "outcome": "O2", "measure": "Accuracy", "finding_type": "productivity_quality", "subgroup": "full sample", "horizon": None, "role": "main", "estimand_type": "itt", "source": "Table 2"},
                          {"estimate_id": "e2", "arm": "T", "vs_arm": "C", "outcome": "O1", "measure": "Overall quality index", "finding_type": "productivity_quality", "subgroup": "full sample", "horizon": None, "role": "supporting", "estimand_type": "itt", "source": "Table 2"},
                          {"estimate_id": "e3", "arm": "T", "vs_arm": "C", "outcome": "O1", "measure": "Overall quality index", "finding_type": "productivity_quality", "subgroup": "novices", "horizon": None, "role": "main", "estimand_type": "itt", "source": "Table 3"},
                          {"estimate_id": "e4", "arm": "T", "vs_arm": "C", "outcome": "O3", "measure": "Time", "finding_type": "productivity_time", "subgroup": "full sample", "horizon": "task 1 (pre-treatment)", "role": "main", "estimand_type": "itt", "source": "Table 1"},
                          {"estimate_id": "e5", "arm": "T", "vs_arm": "C", "outcome": "O3", "measure": "Time", "finding_type": "productivity_time", "subgroup": "full sample", "horizon": "task 2", "role": "main", "estimand_type": "tot", "source": "Table A1"},
                          {"estimate_id": "e6", "arm": "T", "vs_arm": "C", "outcome": "O3", "measure": "Time", "finding_type": "productivity_time", "subgroup": "full sample", "horizon": "task 2", "role": "supporting", "estimand_type": "itt", "source": "text"},
                          {"estimate_id": "e7", "arm": "T", "vs_arm": "C", "outcome": "O3", "measure": "Time", "finding_type": "productivity_time", "subgroup": "full sample", "horizon": "task 2", "role": "main", "estimand_type": "itt", "contrast_type": "interaction", "source": "Table 2"}]}]}
    plan = {"units": [{"id": "U1", "task": "T1", "category": "productivity_time", "arm_pairs": [["T", "C"]]}], "decisions": [{"unit": "U1", "main": ["e5", "e6"], "branch": "one claim, numeric"}]}
    units = {u["kind"]: u for u in H.units_of(rec, plan)}
    q, t = units["productivity_quality"], units["productivity_time"]
    assert q["headline"] == "e2" and q["rule"] == "composite" and q["excluded"] == ["e1"] and q["composite"] == "O1"
    assert t["headline"] == "e6" and t["rule"] == "claim"
    res = H.headlines(rec, plan)
    assert res["headlines"] == ["e2", "e6"] and res["excluded"] == ["e1"]
    es = {"outcomes": [{"outcome_id": "O1", "measure": "Overall quality index", "how_measured": "the average of accuracy and clarity ratings", "measure_kind": "rating", "outcome_class": "performance"},
                       {"outcome_id": "O2", "measure": "Accuracy rating", "measure_kind": "rating", "outcome_class": "performance"},
                       {"outcome_id": "O3", "measure": "Clarity rating", "measure_kind": "rating", "outcome_class": "performance"}, {"outcome_id": "O4", "measure": "Time", "measure_kind": "duration", "outcome_class": "performance"}],
          "estimands": [{"estimand_id": f"E{i}", "task_id": "T1", "outcome_id": f"O{i}"} for i in (1, 2, 3, 4)]}
    groups, part_of = H.composite_groups(es)
    assert groups == {"O1": ["O2", "O3"]} and dict(part_of) == {"O2": ["O1"], "O3": ["O1"]}


def test_the_assembly_normalises_codes_and_carries_the_readings() -> None:
    assert R.normalise_code("result", "effect_type", "other (rank-biserial correlation)") == ("other", "rank-biserial correlation")
    assert R.normalise_code("result", "effect_type", "difference") == ("mean_difference", None) and R.normalise_code("result", "effect_type", "Mean Difference") == ("mean_difference", None)
    assert R.cell("result", "effect_type", "coefficient") == {"value": "regression_coefficient", "proposed": None}
    es = {"tasks": [{"task_id": "T1", "name": "Email task", "_runs": {"found": 1, "of": 2, "alt": {}}}],
          "arms": [{"arm_id": "C", "label": "Control", "is_reference": True, "control_type": "no_ai_concurrent", "task_ids": ["T1"], "quote": "q", "page": 1},
                   {"arm_id": "T1", "label": "AI", "is_reference": False, "treatment": "ChatGPT", "task_ids": ["T1"], "quote": "q", "page": 1}],
          "outcomes": [{"outcome_id": "O1", "measure": "time", "outcome_class": "performance", "measure_kind": "duration", "finding_type": "productivity_time"}],
          "estimands": [{"estimand_id": "E1", "task_id": "T1", "arm_id": "T1", "vs_arm_id": "C", "outcome_id": "O1", "subgroup": None, "horizon": None, "contrast_type": "ai_vs_no_ai"}]}
    em = {"estimates": [{"estimate_id": "x1", "estimand_id": "E1", "role": "main", "source": {"exhibit": "text", "page": 2}, "effect_value": -3.0, "effect_type": "mean_difference", "quote": "AI cut the time by 3 minutes", "page": 2,
                         "_runs": {"found": 2, "of": 2, "alt": {"se": [["0.5", 1]]}}}]}
    de = {"study": {"scale": {"value": "micro", "quote": "", "page": 0}, "_runs": {"found": 2, "of": 3, "alt": {"scale": [["macro", 1]]}}},
          "tasks": [{"task_id": "T1", "task_label": {"value": "writing", "quote": "", "page": 0}, "_runs": {"found": 3, "of": 3, "alt": {}}, "arms": [{"arm_id": "T1", "ai_role": {"value": "open_ended_assistance", "quote": "", "page": 0}, "_runs": {"found": 3, "of": 3, "alt": {"ai_role": [["copilot", 1]]}}}]}]}
    rec = R.assemble(copy.deepcopy(es), copy.deepcopy(em), copy.deepcopy(de), {"tables": [], "figures": []})
    t = rec["tasks"][0]
    assert t["_runs"] == {"found": 1, "of": 3, "alt": {}} and t["arms"][1]["_runs"]["alt"] == {"ai_role": [["copilot", 1]]}
    rows = t["results"]
    assert [r["role"] for r in rows] == ["reference", "main"] and rows[1]["_runs"]["alt"] == {"se": [["0.5", 1]]} and "_runs" not in rows[0]
    ch = D._chain_entry(t)
    assert ch["support"] == {"n": 1, "k": 3} and ch["kind"] == "match" and set(ch["flags"]) == {"contested:arms[1].ai_role", "contested:results[1].se", "not_in_all_runs"}
    assert ch["candidates"]["arms[1].ai_role"][0]["value"] == "open_ended_assistance" and ch["candidates"]["arms[1].ai_role"][1] == {"value": "copilot", "runs": [], "n": 1}
    assert D._runs_note(rec["study"]) == 'runs: returned by 2 of 3 runs; scale: "macro" (1)'


def test_the_compute_is_registered_and_builds_a_document() -> None:
    chains.load()
    e = chains.get("genai_work.assemble")
    assert e["kind"] == "compute" and e["produces"] == "document" and e["reads"] == ["exhibits", "estimands", "estimates", "design"]
    es = {"tasks": [{"task_id": "T1", "name": "Email task", "description": "Improve an email", "sample": "244 students"}],
          "arms": [{"arm_id": "C", "label": "Control", "is_reference": True, "control_type": "no_ai_concurrent", "task_ids": ["T1"], "quote": "Participants in the control group wrote the email alone", "page": 1, "check": "page"},
                   {"arm_id": "T1", "label": "AI", "is_reference": False, "treatment": "access to ChatGPT", "task_ids": ["T1"], "quote": "the treatment group had access to ChatGPT", "page": 1, "check": "page"}],
          "outcomes": [{"outcome_id": "O1", "measure": "time to complete", "how_measured": "minutes", "outcome_class": "performance", "measure_kind": "duration", "finding_type": "productivity_time", "quote": "time to complete in minutes", "page": 1, "check": "page"}],
          "estimands": [{"estimand_id": "E1", "task_id": "T1", "arm_id": "T1", "vs_arm_id": "C", "outcome_id": "O1", "subgroup": None, "horizon": None, "contrast_type": "ai_vs_no_ai", "statement": "s"}]}
    em = {"estimates": [{"estimate_id": "x1", "estimand_id": "E1", "role": "main", "source": {"exhibit": "text", "page": 1}, "effect_value": -3.0, "effect_type": "mean_difference", "se": 1.0,
                         "quote": "AI cut the time by 3 minutes (SE 1.0)", "page": 1, "estimand_type": "itt"}], "unmatched_estimands": [], "orphan_numbers": []}
    de = {"study": {"scale": {"value": "micro", "quote": "a lab study", "page": 1, "proposed": None}}, "tasks": [{"task_id": "T1", "arms": []}]}
    ex = {"tables": [{"id": "Table 1", "page": 1, "caption": "Means", "notes": "", "panels": [{"panel": None, "header": ["", "AI", "Control"], "rows": [["Time", "10", "13"]], "markdown": "| | AI | Control |"}], "check": "table_cells", "cells_checked": 2, "cells_found": 2}], "figures": [], "text_numbers": []}
    page = "A lab study. Participants in the control group wrote the email alone; the treatment group had access to ChatGPT. We measured time to complete in minutes. AI cut the time by 3 minutes (SE 1.0). Table 1. Means"

    class Ctx:
        pages, paper, params, reads = [page], {"title": "T", "doi": "10/x", "year": 2025}, {}, {"exhibits": ex, "estimands": es, "estimates": em, "design": de}
        log_lines: list = []

        def read(self, n):
            return self.reads[n]

        def log(self, line):
            self.log_lines.append(line)
    obj = e["fn"](Ctx())
    meta = obj["paper_metadata"]
    assert meta["title"] == "T" and meta["scale"] == "micro" and meta["claims"] == [] and meta["flags"] == [] and meta["routed"] is None
    assert meta["exhibits"] == [{"exhibit_id": "Table 1", "kind": "table", "page": 1, "caption": "Means", "panels": [], "n_cells": 2, "n_cited": 0}] and meta["exhibit_cells"] == [] and meta["evidence_items"] == []
    t = obj["tasks"][0]
    assert t["task_id"] == "T1" and t["task_description"] == "Improve an email" and t["arms"][1]["treatment"] == "access to ChatGPT" and t["outcomes"][0]["composite_of"] is None
    assert [r["role"] for r in t["results"]] == ["reference", "main"] if "role" in t["results"][0] else [r["arm"] for r in t["results"]] == ["C", "T1"]
    assert t["results"][1]["effect_value"] == -3.0 and t["results"][1]["estimate"] == "x1"
    fields = [x["field"] for x in obj["evidence"]]
    assert "paper_metadata.scale" in fields and "tasks[0].arms[1].treatment" in fields and "tasks[0].outcomes[0]" in fields and "tasks[0].results[1]" in fields and "paper_metadata.exhibits[0]" in fields
    assert obj["_record_chain"] == {"0": {"kind": "match", "support": {"n": 1, "k": 1}, "flags": [], "candidates": {}, "runs": [], "trace": None}}
    assert Ctx.log_lines and "1 tasks, 2 rows" in Ctx.log_lines[0]


# ── the project's papers ────────────────────────────────────────────────────────────────────

def _split(rid, name):
    return json.loads((PROJECT / "labeling" / "split" / rid / f"{name}.json").read_text(encoding="utf-8"))


def _pages(rid):
    return X.Pages.from_raw((PROJECT / "data" / "pdfs" / "txt" / f"{rid}.txt").read_text(encoding="utf-8", errors="ignore"))


def _strip(o, keys=("_runs", "settled_by")):
    if isinstance(o, dict):
        return {k: _strip(v, keys) for k, v in o.items() if k not in keys}
    return [_strip(x, keys) for x in o] if isinstance(o, list) else o


def _plan(rid):
    f = PROJECT / "labeling" / "headline_plan" / f"trial_{rid}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


@pytest.mark.skipif(not _project_ok(), reason="the review project is not on this machine")
@pytest.mark.parametrize("rid", ["ad557585ae81", RID2])
def test_the_stored_pass_files_reproduce_the_projects_record_and_headlines(rid) -> None:
    want = _split(rid, "record.json"[:-5])
    fin = R.finish(_split(rid, "exhibits"), _split(rid, "estimands"), _split(rid, "estimates"), _split(rid, "design"), _pages(rid))
    assert json.dumps(_strip(fin["record"]), sort_keys=True) == json.dumps(_strip(want["record"]), sort_keys=True)
    assert fin["quotes"] == want["quotes"]
    runs_codes = {"runs_disagree", "not_in_all_runs", "task_count_differs", "runs_settled", "runs_unsettled", "runs_wording"}
    mine = Counter((i["code"], i["path"], i["severity"]) for i in fin["issues"])
    theirs = Counter((i["code"], i["path"], i["severity"]) for i in want["issues"] if i["code"] not in runs_codes)
    assert mine == theirs, {"only_mine": mine - theirs, "only_theirs": theirs - mine}
    assert fin["exhibit_coverage"] == want["exhibit_coverage"] and fin["unmatched_reasons"] == want["unmatched_reasons"]
    heads = H.headlines(fin["record"], _plan(rid))
    want_h = _split(rid, "headlines")
    assert heads == {k: v for k, v in want_h.items() if k != "record_id"}


@pytest.mark.skipif(not _project_ok() or not (PROJECT / "labeling" / "metalens" / "import_pilot" / f"{RID2}.json").exists(), reason="the review project's import file is not on this machine")
def test_the_document_reproduces_the_projects_v20_import_file() -> None:
    want = json.loads((PROJECT / "labeling" / "metalens" / "import_pilot" / f"{RID2}.json").read_text(encoding="utf-8"))
    wm = want["paper_metadata"]
    P = _pages(RID2)
    fin = R.finish(_split(RID2, "exhibits"), _split(RID2, "estimands"), _split(RID2, "estimates"), _split(RID2, "design"), P)
    # the readings' annotations the project's record carries (stability.py) are not in the pass files: take them from the stored record
    fin["record"] = _split(RID2, "record")["record"]
    heads = H.headlines(fin["record"], _plan(RID2))
    paper = {k: wm.get(k) for k in ("title", "doi", "year", "authors", "journal", "peer_reviewed")}
    obj = D.build(paper, fin, heads, _split(RID2, "claims"), P.pages)
    route = {"flags", "routed", "route_reasons", "triage_score"}
    mine = {k: v for k, v in obj["paper_metadata"].items() if k not in route}
    theirs = {k: v for k, v in wm.items() if k not in route}
    assert set(mine) == set(theirs)
    for k in theirs:
        assert json.dumps(mine[k], sort_keys=True) == json.dumps(theirs[k], sort_keys=True), k
    assert json.dumps(obj["tasks"], sort_keys=True) == json.dumps(want["tasks"], sort_keys=True)
    keep = lambda e: not e["field"].startswith("paper_metadata.flags")  # noqa: E731
    assert obj["evidence"] == [e for e in want["evidence"] if keep(e)]
    assert set(obj["_record_chain"]) == {str(i) for i in range(len(want["tasks"]))}
