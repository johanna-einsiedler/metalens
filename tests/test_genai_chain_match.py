"""The matchers of the GenAI-at-work chain: the assignment, the alignment of two readings, the
estimate and design comparisons, the majority, the model's pairing of leftovers — the project's
selftests over the port, and the fixture paper's runs against its stored stability record."""
from __future__ import annotations

import copy
import itertools
import json
import os
import random
import sys
from collections import Counter

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens import chains  # noqa: E402
from paperlens.chains.genai_work import match as M  # noqa: E402
from test_genai_chain_replay import PROJECT, RID, FakeCtx, _project_ok, _stored  # noqa: E402


def _brute(S):
    n, m = len(S), len(S[0])
    best, arg = -1.0, []
    if n <= m:
        for cols in itertools.permutations(range(m), n):
            tot = sum(S[i][j] for i, j in enumerate(cols))
            if tot > best + 1e-12:
                best, arg = tot, list(enumerate(cols))
    else:
        for rows in itertools.permutations(range(n), m):
            tot = sum(S[i][j] for j, i in enumerate(rows))
            if tot > best + 1e-12:
                best, arg = tot, [(i, j) for j, i in enumerate(rows)]
    return best


def test_the_assignment_is_optimal_on_rectangular_matrices() -> None:
    rng = random.Random(7)
    for _ in range(200):
        n, m = rng.randint(1, 5), rng.randint(1, 5)
        S = [[round(rng.random(), 3) for _ in range(m)] for _ in range(n)]
        pairs = M.assignment(S)
        assert len(pairs) == min(n, m) and len({i for i, _ in pairs}) == len(pairs) and len({j for _, j in pairs}) == len(pairs)
        assert abs(sum(S[i][j] for i, j in pairs) - _brute(S)) < 1e-9
    assert M.assignment([]) == [] and M.assignment([[0.2, 0.9], [0.8, 0.1]]) == [(0, 1), (1, 0)]


def _P():
    return {"design": {"study": {"scale": {"value": "lab"}, "preregistered": {"value": True}}, "tasks": [{"task_id": "T1", "task_label": {"value": "writing"}, "arms": [{"arm_id": "C", "control_type": {"value": "no_ai"}}, {"arm_id": "T1", "ai_role": {"value": "open_ended_assistance"}}]}]},
            "estimands": {"tasks": [{"task_id": "T1", "name": "Email task", "description": "Improve an email", "sample": "244 students"}],
                          "arms": [{"arm_id": "C", "label": "Control", "is_reference": True, "treatment": "none"}, {"arm_id": "T1", "label": "ChatGPT", "is_reference": False, "treatment": "access to ChatGPT"}],
                          "outcomes": [{"outcome_id": "O1", "measure": "time to complete", "how_measured": "minutes"}, {"outcome_id": "O2", "measure": "quality score", "how_measured": "1-7 rating"}],
                          "estimands": [{"estimand_id": "E1", "task_id": "T1", "arm_id": "T1", "vs_arm_id": "C", "outcome_id": "O1", "subgroup": None, "horizon": None}, {"estimand_id": "E2", "task_id": "T1", "arm_id": "T1", "vs_arm_id": "C", "outcome_id": "O2", "subgroup": None, "horizon": None}]},
            "estimates": {"estimates": [{"estimate_id": "E1_main", "estimand_id": "E1", "role": "main", "source": {"exhibit": "Table 2", "row": "Used AI", "column": "(1)"}, "effect_value": -0.4, "se": 0.1},
                                        {"estimate_id": "E2_main", "estimand_id": "E2", "role": "main", "source": {"exhibit": "Table 2", "row": "Used AI", "column": "(2)"}, "effect_value": 0.3, "se": 0.12}]}}


def test_the_projects_selftest_holds_over_the_port() -> None:
    P = _P()
    R = copy.deepcopy(P)
    R["estimands"]["tasks"][0].update(task_id="T9", name="E-mail writing task")
    R["estimands"]["arms"][1].update(arm_id="T7")
    for x in R["estimands"]["estimands"]:
        x["task_id"] = "T9"; x["arm_id"] = "T7"
    R["estimands"]["estimands"][0]["estimand_id"] = "E5"; R["estimands"]["estimands"][1]["estimand_id"] = "E6"
    R["estimates"]["estimates"][0].update(estimand_id="E5", estimate_id="E5_main", se=0.11)
    R["estimates"]["estimates"][1].update(estimand_id="E6", estimate_id="E6_main")
    R["design"]["study"]["scale"] = {"value": "field"}
    R["design"]["tasks"][0]["task_id"] = "T9"; R["design"]["tasks"][0]["arms"][1]["arm_id"] = "T7"
    al = M.align(copy.deepcopy(P["estimands"]), copy.deepcopy(R["estimands"]))
    assert al["tid_map"] == {"T9": "T1"} and al["aid_map"] == {"C": "C", "T7": "T1"} and al["oid_map"] == {"O1": "O1", "O2": "O2"} and al["estimands"] == {"E1": "E5", "E2": "E6"}
    est, extras = M.compare_estimates(P["estimates"], R["estimates"], al)
    assert est["E1_main"]["status"] == "numbers_differ" and est["E1_main"]["diff"] == {"se": 0.11} and est["E2_main"]["status"] == "stable" and extras == 0
    des = M.compare_design(P["design"], R["design"], al)
    assert M.design_agreement(des)["study"] == [1, 2]
    # a third run: scale agrees with r2 (the primary is overruled), preregistered differs everywhere (unsettled)
    R3 = copy.deepcopy(R); R3["design"]["study"]["preregistered"] = {"value": "not_stated"}; R["design"]["study"]["preregistered"] = {"value": False}
    des2 = M.compare_design(P["design"], R["design"], al); des3 = M.compare_design(P["design"], R3["design"], M.align(copy.deepcopy(P["estimands"]), copy.deepcopy(R3["estimands"])))
    maj = M.majority(P["design"], [("r2", des2), ("r3", des3)])
    assert maj["study"]["scale"]["status"] == "settled" and M.value_of(maj["study"]["scale"]["adopted"]) == "field" and maj["study"]["scale"]["from"] == "r2"
    assert maj["study"]["preregistered"]["status"] == "unsettled" and maj["tasks"]["T1"]["task_label"]["status"] == "agree"
    de = copy.deepcopy(P["design"])
    issues, summ = M.apply_majority(de, maj, 2)
    assert de["study"]["scale"] == {"value": "field", "settled_by": "r2"} and de["study"]["_runs"]["found"] == 1 and de["study"]["_runs"]["of"] == 3
    assert Counter(i["code"] for i in issues) == {"runs_settled": 1, "runs_unsettled": 1} and dict(summ) == {"agree": 3, "settled": 1, "unsettled": 1}
    assert de["tasks"][0]["_runs"] == {"found": 3, "of": 3, "alt": {}}


def test_the_pairing_answer_is_validated_and_applied() -> None:
    A = [{"estimand_id": "E1"}, {"estimand_id": "E2"}]; B = [{"estimand_id": "F1"}, {"estimand_id": "F2"}, {"estimand_id": "F3"}]
    ans = {"pairs": [{"a": "E1", "b": "F2", "why": "same"}, {"a": "E1", "b": "F3", "why": "dup a"}, {"a": "E9", "b": "F1", "why": "unknown a"}],
           "b_unpaired": [{"b": "F1", "reason": "not_in_primary", "why": "x"}, {"b": "F3", "reason": "nonsense", "why": "y"}]}
    pairs, rejected, run_only = M.validate(ans, A, B)
    assert [p["a"] + p["b"] for p in pairs] == ["E1F2"] and len(rejected) == 2
    assert {x["estimand_id"]: x["reason"] for x in run_only} == {"F1": "not_in_primary", "F3": "other"}
    al = {"estimands": {"E0": "F0"}, "estimands_only_primary": ["E1", "E2"], "estimands_only_run": ["F1", "F2", "F3"]}
    assert M.apply_match(al, {"E1": "F2", "E2": "F0"}) == 1 and al["estimands"] == {"E0": "F0", "E1": "F2"} and al["estimands_only_primary"] == ["E2"] and al["estimands_only_run"] == ["F1", "F3"]
    E = {"tasks": [{"task_id": "T1", "name": "Email task"}], "arms": [{"arm_id": "C", "label": "Control"}, {"arm_id": "T", "label": "AI", "treatment": "ChatGPT"}],
         "outcomes": [{"outcome_id": "O1", "measure": "time", "unit": "min"}]}
    s = M.describe(E, {"estimand_id": "E1", "task_id": "T1", "arm_id": "T", "vs_arm_id": "C", "outcome_id": "O1", "subgroup": None, "horizon": None, "statement": "AI vs control on time"})
    assert s.startswith("E1 | task: Email task | arms: AI (ChatGPT) vs Control | outcome: time (min) | subgroup: full sample | horizon: post-treatment |")


def test_the_estimands_matcher_sends_the_leftovers_to_the_model_once() -> None:
    P = _P()["estimands"]
    R = copy.deepcopy(P)
    R["estimands"] += [{"estimand_id": "E3", "task_id": "T1", "arm_id": "T1", "vs_arm_id": "C", "outcome_id": "O2", "subgroup": "novices", "horizon": None},
                       {"estimand_id": "E4", "task_id": "T1", "arm_id": "T1", "vs_arm_id": "C", "outcome_id": "O2", "subgroup": "experts", "horizon": None}]
    P["estimands"].append({"estimand_id": "E7", "task_id": "T1", "arm_id": "T1", "vs_arm_id": "C", "outcome_id": "O2", "subgroup": "participants without prior experience", "horizon": None})
    ctx = FakeCtx({"match_runs": {"pairs": [{"a": "E7", "b": "E3", "why": "the same subgroup worded differently"}], "b_unpaired": [{"b": "E4", "reason": "not_in_primary", "why": "the first reading has no experts subgroup"}]}}, ["p"], {}, "T")
    res = M.match_estimands(ctx, [P, R])
    assert ctx.calls == ["match_runs"]                                                        # the alignment left one and two: one request
    run = res["stability"]["runs"][0]
    assert run["alignment"]["estimands"] == {"E1": "E1", "E2": "E2", "E7": "E3"} and run["alignment"]["estimands_only_run"] == ["E4"]
    assert run["pairing"]["model_pairs"][0]["a"] == "E7" and run["pairing"]["run_only"] == [{"estimand_id": "E4", "reason": "not_in_primary", "why": "the first reading has no experts subgroup"}]
    assert run["metrics"]["estimands_model_paired"] == 1 and run["metrics"]["estimands_run_only_missed"] == 1
    assert res["primary"]["tasks"][0]["_runs"] == {"found": 2, "of": 2, "alt": {}} and res["stability"]["issues"] == []
    assert "_i" not in res["primary"]["tasks"][0] and "_s" not in res["primary"]["estimands"][0]
    # the same number left on both sides: paired by order, no request; a run with a task the primary has not: the count differs
    R2 = copy.deepcopy(P); R2["estimands"][-1]["subgroup"] = "novices"; R2["tasks"].append({"task_id": "T2", "name": "Coding task", "description": "", "sample": ""})
    ctx = FakeCtx({}, ["p"], {}, "T")
    res = M.match_estimands(ctx, [P, R2])
    assert ctx.calls == [] and res["stability"]["runs"][0]["alignment"]["estimands"] == {"E1": "E1", "E2": "E2", "E7": "E7"}
    assert [i["code"] for i in res["stability"]["issues"]] == ["task_count_differs"]


def test_the_estimates_and_design_matchers_annotate_the_primary() -> None:
    P = _P()
    R = copy.deepcopy(P); R["estimates"]["estimates"][0]["se"] = 0.11; del R["estimates"]["estimates"][1]
    ctx = FakeCtx({}, ["p"], {"estimands": P["estimands"]}, "T")
    res = M.match_estimates(ctx, [P["estimates"], R["estimates"]])
    e1, e2 = res["primary"]["estimates"]
    assert e1["_runs"] == {"found": 2, "of": 2, "alt": {"se": [["0.11", 1]]}} and e2["_runs"] == {"found": 1, "of": 2, "alt": {}}
    assert [i["code"] for i in res["stability"]["issues"]] == ["runs_disagree", "not_in_all_runs"] and res["stability"]["runs"][0]["metrics"]["estimates_missing"] == 1
    assert "_runs" not in P["estimates"]["estimates"][0]                                      # the inputs are left alone
    # two design readings: a warning per differing cell; three: the majority
    R["design"]["study"]["scale"] = {"value": "field"}
    res = M.match_design(ctx, [P["design"], R["design"]])
    assert res["primary"]["study"]["scale"] == {"value": "lab"} and res["primary"]["study"]["_runs"]["alt"] == {"scale": [["field", 1]]}
    assert [i["code"] for i in res["stability"]["issues"]] == ["runs_disagree"] and "majority" not in res["stability"]
    res = M.match_design(ctx, [P["design"], R["design"], copy.deepcopy(R["design"])])
    assert res["primary"]["study"]["scale"] == {"value": "field", "settled_by": "run1"} and res["stability"]["design_majority"] == {"runs": ["run1", "run2"], "agree": 4, "settled": 1}
    assert [i["code"] for i in res["stability"]["issues"]] == ["runs_settled"]


def test_the_matchers_are_registered_with_the_chain() -> None:
    chains.load()
    for name in ("match_exhibits", "match_estimands", "match_estimates", "match_design"):
        assert chains.get(f"genai_work.{name}")["kind"] == "match"


def _run(name, run):
    return json.loads((PROJECT / "labeling" / "split" / RID / "runs" / run / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.skipif(not _project_ok(), reason="the review project is not on this machine")
def test_the_fixture_papers_runs_reproduce_its_stability_record() -> None:
    want = json.loads((PROJECT / "labeling" / "split" / RID / "stability.json").read_text(encoding="utf-8"))
    es, em, de = _stored("estimands"), _stored("estimates"), _stored("design")
    al = M.align(copy.deepcopy(es), copy.deepcopy(_run("estimands", "r2")))
    assert M._public(al) == want["alignment"]
    est, extras = M.compare_estimates(em, _run("estimates", "r2"), al)
    assert est == want["estimates"] and extras == want["metrics"]["estimates_extra_in_run"]
    des = M.compare_design(de, _run("design", "r2"), al)
    assert M.design_agreement(des) == want["metrics"]["design_agreement"]
    assert {k: [list(x) if isinstance(x, tuple) else x for x in v] for k, v in des["study"].items()} == want["design"]["study"]
    # the matchers over the primary and its runs, as the engine would call them
    ctx = FakeCtx({}, ["p"], {"estimands": es}, _stored("record")["title"])
    r = M.match_estimands(ctx, [es, _run("estimands", "r2")])
    assert ctx.calls == [] and r["stability"]["runs"][0]["alignment"] == want["alignment"]
    r = M.match_estimates(ctx, [em, _run("estimates", "r2")])
    assert r["stability"]["runs"][0]["estimates"] == want["estimates"] and all(e["_runs"] == {"found": 2, "of": 2, "alt": {}} for e in r["primary"]["estimates"])
    r = M.match_design(ctx, [de, _run("design", "r2"), _run("design", "r3")])
    assert {k: v for k, v in r["stability"]["design_majority"].items() if k != "runs"} == want["design_majority"]
    settled = [i for i in r["stability"]["issues"] if i["code"] == "runs_settled"]                 # r3 read a second data source; the primary and r2 agree: the primary's value stays
    assert [i["path"] for i in settled] == ["study.data_source"] and r["primary"]["study"]["data_source"]["value"] == ["experimental_data"] and "settled_by" not in r["primary"]["study"]["data_source"]
    assert r["primary"]["study"]["_runs"] == {"found": 2, "of": 3, "alt": {"data_source": [["('experimental_data', 'worker_household_survey')", 1]]}}
    assert all(t["_runs"] == {"found": 3, "of": 3, "alt": {}} for t in r["primary"]["tasks"]) and all(a["_runs"]["found"] == 3 for t in r["primary"]["tasks"] for a in t["arms"])
