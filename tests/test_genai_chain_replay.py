"""The ported passes replayed on a paper of the review project: the model's cached answers in,
the project's stored pass files out — the post-processing (quote checks, snapping, derived
cells, parts, canonical ids) reproduced. Needs the project on this machine (skipped otherwise)."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens.chains.genai_work import hashes, passes, text  # noqa: E402

PROJECT = Path(os.environ.get("GENAI_REVIEW_PROJECT", "/Users/einsie0004/Documents/research/49_AI_review"))
RID = "ad557585ae81"
STORE = PROJECT / "labeling" / "llm" / "split_810861b1ac_gemini-3.1-pro-preview" / f"{RID}.jsonl"   # the answers the stored files were made from


def _project_ok() -> bool:
    return STORE.exists() and (PROJECT / "labeling" / "split" / RID / "exhibits.json").exists() and (PROJECT / "data" / "pdfs" / "txt" / f"{RID}.txt").exists()


class FakeCtx:
    """The engine's pass context over the project's files: the cached answer for each pass in
    place of the model, the paper's page text as the engine would hand it."""
    def __init__(self, answers: dict, pages: list[str], reads: dict, title: str):
        self.answers, self.pages, self.reads, self.paper = answers, pages, reads, {"title": title, "abstract": None}
        self.params, self.log_lines, self.calls, self.short = {}, [], [], False

    @property
    def title(self):
        return self.paper["title"]

    def read(self, name):
        return self.reads[name]

    def log(self, line):
        self.log_lines.append(line)

    def call(self, system, user, schema, *, max_tokens=None):
        for name in ("exhibits", "estimands", "estimates", "design"):
            if system == hashes.prompt(name):
                self.calls.append(name)
                return copy.deepcopy(self.answers[name])
        if system == hashes.prompt("match_runs"):
            self.calls.append("match_runs")
            return copy.deepcopy(self.answers.get("match_runs") or {"pairs": [], "b_unpaired": []})
        raise AssertionError("an unknown prompt")


def _stored(name: str) -> dict:
    return json.loads((PROJECT / "labeling" / "split" / RID / f"{name}.json").read_text(encoding="utf-8"))


def _without_check(o):
    """The project's finish marks the record's wrapped cells with ``_check`` and, the cells being
    shared, the stored design file with them: the record assembly's doing, not the pass's."""
    if isinstance(o, dict):
        return {k: _without_check(v) for k, v in o.items() if k != "_check"}
    return [_without_check(x) for x in o] if isinstance(o, list) else o


def _answers() -> dict:
    out = {}
    for line in STORE.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        k = r["key"]
        if k in ("exhibits:1", "estimands:1", "estimates:1"):
            out[k.split(":")[0]] = r["answer"]
        elif k.startswith("design:") and ":run=" not in k:
            out["design"] = r["answer"]
    return out


@pytest.mark.skipif(not _project_ok(), reason="the review project is not on this machine")
def test_the_four_passes_reproduce_the_projects_stored_outputs() -> None:
    answers = _answers()
    raw = (PROJECT / "data" / "pdfs" / "txt" / f"{RID}.txt").read_text(encoding="utf-8", errors="ignore")
    pages = text.Pages.from_raw(raw).list
    title = _stored("record")["title"]
    ex = passes.exhibits(FakeCtx(answers, pages, {}, title))
    es = passes.estimands(FakeCtx(answers, pages, {"exhibits": ex}, title))
    em = passes.estimates(FakeCtx(answers, pages, {"exhibits": ex, "estimands": es}, title))
    de = passes.design(FakeCtx(answers, pages, {"estimands": es}, title))
    passes.derive_roles(em)                                   # the finish step's idempotent derivations before the files are written
    passes.canon_ids(es, em, de)
    for name, got in (("exhibits", ex), ("estimands", es), ("estimates", em), ("design", de)):
        want = _without_check(_stored(name)) if name == "design" else _stored(name)
        got = {k: v for k, v in got.items() if k != "parts"}             # the project's finish keeps `parts` out of the stored file
        assert json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True), name
    assert em["parts"] == [{"key": "estimates:1", "estimands": len(es["estimands"]), "short": False}]
    assert len(ex["tables"]) >= 1 and all("cells_checked" in t and "markdown" in t["panels"][0] for t in ex["tables"] if t.get("panels"))
    assert all(x.get("check") in text.FOUND_ALL | {"not_found", "empty"} for x in es["estimands"])


def test_the_estimates_pass_halves_a_request_the_answer_cannot_fit_and_fills_the_gaps() -> None:
    from paperlens.passes import Truncated
    es = {"tasks": [{"task_id": "T1", "name": "t", "description": "d", "sample": "s"}], "arms": [], "outcomes": [],
          "estimands": [{"estimand_id": f"E{i}", "task_id": "T1", "arm_id": "T1", "vs_arm_id": "C", "outcome_id": "O1", "subgroup": None, "horizon": None,
                         "contrast_type": "ai_vs_no_ai", "exhibits": [], "statement": "s"} for i in range(1, 11)]}
    ex = {"tables": [], "figures": [], "text_numbers": []}

    class Ctx(FakeCtx):
        def __init__(self):
            super().__init__({}, ["p1"], {"exhibits": ex, "estimands": es}, "T")
            self.n = 0

        def call(self, system, user, schema, *, max_tokens=None):
            self.n += 1
            active = [line.split("► ")[1].split(":")[0] for line in user.splitlines() if line.startswith("- ► ")]
            if not active and "# This request" not in user:
                raise Truncated("")                                        # ten estimands at once: too long
            if active == ["E1", "E2", "E3", "E4", "E5"]:
                return {"estimates": [{"estimate_id": f"x{e}", "estimand_id": e, "role": "main"} for e in active[:4]], "unmatched_estimands": [], "orphan_numbers": [{"exhibit": "T1", "where": "r1"}]}
            if active == ["E6", "E7", "E8", "E9", "E10"]:
                return {"estimates": [{"estimate_id": "x", "estimand_id": "E6", "role": "main"}, {"estimate_id": "x", "estimand_id": "E7", "role": "main"}],
                        "unmatched_estimands": [{"estimand_id": "E8", "reason": "other", "why": "w"}], "orphan_numbers": [{"exhibit": "T1", "where": "r1"}]}
            return {"estimates": [{"estimate_id": "y", "estimand_id": "E5", "role": "main"}], "unmatched_estimands": [], "orphan_numbers": []}   # the completion: E5, E9, E10 asked; E5 answered
    ctx = Ctx()
    em = passes.run_estimates(ctx, "T", es, ex, "body")
    assert ctx.n == 4 and [p["key"] for p in em["parts"]] == ["estimates:1a", "estimates:1b", "estimates:1:fill=1/1"]
    assert [e["estimate_id"] for e in em["estimates"]] == ["xE1", "xE2", "xE3", "xE4", "x", "x.2", "y"]            # ids made unique
    assert len(em["orphan_numbers"]) == 1
    assert sorted(u["estimand_id"] for u in em["unmatched_estimands"]) == ["E10", "E8", "E9"] and all("not answered" in u["why"] for u in em["unmatched_estimands"] if u["estimand_id"] != "E8")
    assert any("asked in two halves" in l for l in ctx.log_lines) and any("not answered; asked once more" in l for l in ctx.log_lines)


def test_derived_cells_follow_the_taxonomy_and_the_reference_arm() -> None:
    es = {"outcomes": [{"outcome_id": "O1", "outcome_class": "performance", "measure_kind": "duration", "finding_type": "productivity_quality"},
                       {"outcome_id": "O2", "outcome_class": "wellbeing", "measure_kind": "duration", "finding_type": "wellbeing"}],
          "arms": [{"arm_id": "C", "is_reference": True, "control_type": "no_ai_concurrent"}, {"arm_id": "T1", "is_reference": False}],
          "estimands": [{"estimand_id": "E1", "vs_arm_id": "C", "contrast_type": "pre_vs_post"}, {"estimand_id": "E2", "vs_arm_id": "C", "contrast_type": "association"},
                        {"estimand_id": "E3", "vs_arm_id": "T1", "contrast_type": "ai_vs_ai"}]}
    passes.derive_findings(es); passes.derive_contrast(es)
    o1, o2 = es["outcomes"]
    assert o1["finding_type"] == "productivity_time" and o1["finding_type_model"] == "productivity_quality" and o2["measure_kind"] is None
    e1, e2, e3 = es["estimands"]
    assert e1["contrast_type"] == "ai_vs_no_ai" and e1["contrast_type_model"] == "pre_vs_post" and e2["contrast_type"] == "association" and e3["contrast_type"] == "ai_vs_ai"
    em = {"estimates": [{"estimand_id": "E1", "role": "supporting"}, {"estimand_id": "E2", "role": "supporting"}, {"estimand_id": "E2", "role": "main"}]}
    assert passes.derive_roles(em) == 1 and em["estimates"][0]["role"] == "main" and em["estimates"][0]["role_model"] == "supporting"
