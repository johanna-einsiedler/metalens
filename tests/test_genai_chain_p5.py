"""The bundled ``genai-work-chain`` workflow: the chain's passes with their readings and matchers,
the assemble and route computes, the two checks, and the three review tasks of ``genai-work`` v4
unchanged — validated, pinned, and consistent with the chain's registry."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens import chains, workflows  # noqa: E402
from paperlens import workflow_spec as ws  # noqa: E402

ORDER = ["exhibits", "estimands", "estimates", "design", "claims", "headline_plan", "extraction", "record", "passes_check", "route",
         "review_judgment", "review_check", "review_routed", "contract", "release"]


def _chain():
    return workflows.load_all()["genai-work-chain"]


def test_the_chain_workflow_validates_and_runs_the_chain_in_order() -> None:
    spec = _chain()
    errs, warns = ws.validate(spec)
    assert errs == [] and warns == [] and workflows.later_problems(spec) == []
    assert [s["id"] for s in spec["steps"]] == ORDER
    kinds = {s["id"]: s["kind"] for s in spec["steps"]}
    assert [kinds[i] for i in ORDER] == ["pass"] * 6 + ["compute", "check", "check", "compute", "review", "review", "review", "check", "release"]
    assert spec["dataset"] == {"unit": "paper", "key": "pdf_sha256"} and ws.has_card(spec) and "## Steps" in spec["card"]["text"]
    assert ws.pins(spec) == [("extraction", "genai-work-extraction@cea73929")] and ws.gate_steps(spec) == ["extraction", "review_judgment", "review_check", "review_routed"]


def test_the_passes_are_pinned_to_the_registry_and_read_what_it_declares() -> None:
    chains.load()
    by = ws.steps_by_id(_chain())
    for sid in ORDER[:6]:
        st = by[sid]
        assert st["pass"] == chains.pin(f"genai_work.{sid}"), sid
        assert list(st.get("reads") or []) == list(chains.get(f"genai_work.{sid}")["reads"]), sid
    assert {sid: (by[sid].get("k") or 1, by[sid].get("match")) for sid in ORDER[:6]} == {
        "exhibits": (2, "genai_work.match_exhibits"), "estimands": (2, "genai_work.match_estimands"), "estimates": (2, "genai_work.match_estimates"),
        "design": (3, "genai_work.match_design"), "claims": (1, None), "headline_plan": (1, None)}
    assert {sid: workflows.calls_per_paper(_chain(), sid) for sid in ORDER[:6]} == {"exhibits": 2, "estimands": 2, "estimates": 2, "design": 3, "claims": 2, "headline_plan": 4}
    ext, route = by["extraction"], by["route"]
    assert ext["fn"] == "genai_work.assemble" and ext["reads"] == chains.get("genai_work.assemble")["reads"] and ext["dataset"] == "extraction"
    assert route["fn"] == "genai_work.route" and route["reads"] == chains.get("genai_work.route")["reads"] and route["edits"] == "extraction"
    assert route["writes"] == ["paper.flags", "paper.routed", "paper.route_reasons", "paper.triage_score"] and route["params"] == {"threshold": 6}
    assert by["passes_check"]["check"] == "genai-work-passes" and by["passes_check"]["over"] == ORDER[:4] and by["record"]["over"] == ["extraction"]


def test_the_review_tasks_are_those_of_the_supplied_record_workflow() -> None:
    all_wf = workflows.load_all()
    chain, v4 = ws.steps_by_id(all_wf["genai-work-chain"]), ws.steps_by_id(all_wf["genai-work"])
    strip = lambda st: {k: v for k, v in st.items() if k != "help"}  # noqa: E731
    for sid in ("review_judgment", "review_check", "contract", "release"):
        assert strip(chain[sid]) == strip(v4[sid]), sid
    assert strip(chain["review_routed"]) == strip(v4["review_routed"])
    assert ws.workflow_id(all_wf["genai-work"]) == "genai-work@e62c0d84"            # the supplied-record workflow keeps its id


def test_a_prompt_change_moves_the_workflows_id(monkeypatch) -> None:
    import json
    spec = json.loads(json.dumps(_chain()))
    before = ws.workflow_id(spec)
    st = next(s for s in spec["steps"] if s["id"] == "exhibits")
    st["pass"] = st["pass"].partition("@")[0] + "@00000000"
    assert ws.workflow_id(spec) != before and any("not the pass's current pin" in e for e in ws.validate(spec)[0])
    spec2 = json.loads(json.dumps(_chain())); spec2["steps"][0]["help"] = "other words"
    assert ws.workflow_id(spec2) == before
