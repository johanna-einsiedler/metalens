"""The workflow format: loading, defaults, validation, the contract hash, the implicit workflow
of a preset. Stdlib-only; no DB."""
from __future__ import annotations

import copy
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import preset_spec as ps
from paperlens import presets, workflows
from paperlens import workflow_spec as ws

MASEM = ps.schema_id(presets.load_all()["masem-direct"])
ECON = ps.schema_id(presets.load_all()["econ-regression-tables"])

GOOD = {
    "format": 1, "id": "demo", "version": 2, "meta": {"title": "Demo"},
    "dataset": {"unit": "paper", "key": "pdf_sha256"},
    "steps": [
        {"id": "extract", "kind": "extract", "preset": MASEM},
        {"id": "review", "kind": "review", "over": ["extract"], "mode": "all", "unit": "entry"},
        {"id": "contract", "kind": "check", "check": "schema", "over": ["extract"]},
        {"id": "release", "kind": "release", "gate": ["extract", "review"]},
    ],
}
# the same workflow in the form before review steps stood on their own: `review` on the extract step
LEGACY = {
    "format": 1, "id": "demo", "version": 2, "meta": {"title": "Demo"},
    "dataset": {"unit": "paper", "key": "pdf_sha256"},
    "steps": [
        {"id": "extract", "kind": "extract", "preset": MASEM, "review": {"mode": "all"}},
        {"id": "contract", "kind": "check", "check": "schema", "over": ["extract"]},
        {"id": "release", "kind": "release", "gate": ["extract"]},
    ],
}
BUNDLED_IDS = {"econ": "econ@6e4eb2b5", "econ-claims": "econ-claims@45c8b5b5", "econ-regressions": "econ-regressions@4d38b5f4",
               "genai-work": "genai-work@861f2667", "genai-work-chain": "genai-work-chain@8bfcbee6", "masem": "masem@d13605f1"}


def _errors(mutate) -> list[str]:
    doc = copy.deepcopy(GOOD)
    mutate(doc)
    try:
        spec = ws.normalize(doc)
    except ws.WorkflowError as exc:
        return exc.errors
    return ws.validate(spec)[0]


def test_defaults_are_filled_and_the_good_workflow_validates() -> None:
    spec = ws.normalize(copy.deepcopy(GOOD))
    assert ws.validate(spec) == ([], [])
    ex = spec["steps"][0]
    assert ex["source"] is None or ex["source"] == "live"          # resolved by validate against the live preset
    ws.validate(spec)
    assert ex["source"] == "live" and ex["k"] == 1 and ex["input"] == {"previous": []} and ex["params"] == {}
    assert "review" not in ex                                        # an extract step is never reviewed in place
    rv = spec["steps"][1]
    assert rv["kind"] == "review" and rv["label"] == "Review" and {k: rv[k] for k in ("mode", "unit", "blocks", "oversample_flagged")} == {"mode": "all", "unit": "entry", "blocks": "release", "oversample_flagged": False}
    assert ws.review_of(rv) == {k: rv[k] for k in ("mode", "unit", "blocks", "oversample_flagged")}
    ck = spec["steps"][2]
    assert ck["on_fail"] == "note" and ck["severity"] == "error" and ck["params"] == {} and ck["label"] == "Contract"
    assert spec["steps"][-1]["kind"] == "release"
    # a workflow without a release step gets one, gated on its extract and review steps
    doc = copy.deepcopy(GOOD); doc["steps"].pop()
    spec2 = ws.normalize(doc)
    assert spec2["steps"][-1] == {"id": "release", "kind": "release", "label": "Release", "gate": ["extract", "review"]}
    assert ws.validate(spec2)[0] == []


@pytest.mark.parametrize("label, mutate, needle", [
    ("format", lambda d: d.__setitem__("format", 2), "format"),
    ("id", lambda d: d.__setitem__("id", "Bad Id"), "$.id"),
    ("title", lambda d: d["meta"].pop("title"), "title"),
    ("unknown root key", lambda d: d.__setitem__("zzz", 1), "unknown key"),
    ("dataset key", lambda d: d["dataset"].__setitem__("key", "title"), "dataset.key"),
    ("duplicate id", lambda d: d["steps"].insert(1, {"id": "extract", "kind": "check", "check": "schema", "over": ["extract"]}), "declared twice"),
    ("unknown kind", lambda d: d["steps"][0].__setitem__("kind", "magic"), "kind must be"),
    ("unknown step key", lambda d: d["steps"][0].__setitem__("prompt", "x"), "unknown key"),
    ("release not last", lambda d: d["steps"].insert(0, {"id": "rel0", "kind": "release", "gate": []}), "last step"),
    ("two releases", lambda d: d["steps"].append({"id": "rel2", "kind": "release", "gate": []}), "exactly one release"),
    ("gate names a check", lambda d: d["steps"][3].__setitem__("gate", ["contract"]), "not an earlier extract"),
    ("pin shape", lambda d: d["steps"][0].__setitem__("preset", "masem-direct"), "pin a data contract"),
    ("unknown preset", lambda d: d["steps"][0].__setitem__("preset", "nope-preset@deadbeef"), "unknown preset"),
    ("live on an import preset", lambda d: d["steps"][0].update({"preset": ECON, "source": "live"}), "has no prompt"),
    ("k out of range", lambda d: d["steps"][0].__setitem__("k", 9), "k must be"),
    ("k without vote", lambda d: d["steps"][0].__setitem__("k", 2), "needs vote.identity"),
    ("vote identity not a field", lambda d: d["steps"][0].update({"k": 2, "vote": {"identity": ["zzz"]}}), "scalar entry field"),
    ("previous names a later step", lambda d: d["steps"][0].__setitem__("input", {"previous": ["contract"]}), "not an earlier extract"),
    ("check unknown", lambda d: d["steps"][2].__setitem__("check", "zzz"), "check must be one of"),
    ("check over a check", lambda d: d["steps"].insert(3, {"id": "c2", "kind": "check", "check": "schema", "over": ["contract"]}), "not an earlier extract"),
    ("check too many inputs", lambda d: d["steps"][2].__setitem__("over", ["extract", "extract"]), "reads 1 step"),
    ("check bad on_fail", lambda d: d["steps"][2].__setitem__("on_fail", "explode"), "on_fail"),
    ("check unknown param", lambda d: d["steps"][2].__setitem__("params", {"zzz": 1}), "no parameter"),
    ("check param type", lambda d: d["steps"][2].__setitem__("params", {"ignore": "uncited_value"}), "must be a list"),
    ("review on a check", lambda d: d["steps"][2].__setitem__("review", {"mode": "all"}), "declares no review"),
    ("review mode", lambda d: d["steps"][1].__setitem__("mode", "some"), "mode must be"),
    ("review mode none", lambda d: d["steps"][1].__setitem__("mode", "none"), "a review step reviews"),
    ("review unit", lambda d: d["steps"][1].__setitem__("unit", "column"), "unit must be"),
    ("review unit row needs a table", lambda d: d["steps"][1].__setitem__("unit", "row"), "table must name"),
    ("review template unknown", lambda d: d["steps"][1].__setitem__("template", "x"), "unknown template"),
    ("review new_only", lambda d: d["steps"][1].__setitem__("new_only", "yes"), "new_only must be"),
    ("review blocks", lambda d: d["steps"][1].__setitem__("blocks", "everything"), "blocks must be"),
    ("review over nothing", lambda d: d["steps"][1].__setitem__("over", []), "over must list"),
    ("review over a check", lambda d: d["steps"][1].__setitem__("over", ["contract"]), "not an earlier extract"),
    ("review unknown key", lambda d: d["steps"][1].__setitem__("zzz", 1), "unknown key"),
    ("legacy review mode", lambda d: d["steps"].pop(1) and d["steps"][0].__setitem__("review", {"mode": "some"}), "mode must be"),
    ("sample needs n or share", lambda d: d["steps"][1].__setitem__("mode", "sample"), "exactly one of n"),
    ("sample both", lambda d: d["steps"][1].update({"mode": "sample", "n": 3, "share": 0.1}), "exactly one of n"),
    ("sample share range", lambda d: d["steps"][1].update({"mode": "sample", "share": 1.5}), "share must be"),
    ("sample strata not a field", lambda d: d["steps"][1].update({"mode": "sample", "n": 3, "stratify": ["zzz"]}), "stratify"),
    ("coders on a draft", lambda d: d["steps"].insert(2, {"id": "d", "kind": "derive", "over": "extract", "identity": ["a"], "calls": [{"id": "c", "kind": "transform", "op": "group", "by": ["a"], "fields": {}}], "output": {"fields": [{"name": "a"}]}}) or d["steps"].insert(3, {"id": "dr", "kind": "review", "over": ["d"], "mode": "all", "unit": "entry", "coders": 2}), "committed by the owner"),
    ("sample of a draft", lambda d: d["steps"].insert(2, {"id": "d", "kind": "derive", "over": "extract", "identity": ["a"], "calls": [{"id": "c", "kind": "transform", "op": "group", "by": ["a"], "fields": {}}], "output": {"fields": [{"name": "a"}]}}) or d["steps"].insert(3, {"id": "dr", "kind": "review", "over": ["d"], "mode": "sample", "n": 2, "unit": "entry"}), "reviewed whole"),
    ("vocabulary without column", lambda d: d["steps"].insert(2, {"id": "v", "kind": "vocabulary"}), "names its column"),
    ("pages without group", lambda d: d["steps"].insert(2, {"id": "pg", "kind": "pages", "keyed_by": "x"}), "names its group"),
    ("derive without calls", lambda d: d["steps"].insert(2, {"id": "d", "kind": "derive", "over": "extract", "identity": ["a"], "output": {"fields": [{"name": "a"}]}}), "calls"),
    ("loop until not a check", lambda d: d["steps"].insert(2, {"id": "l", "kind": "loop", "steps": ["extract"], "until": {"check": "extract"}}), "until.check"),
])
def test_validation_rejects(label, mutate, needle) -> None:
    errs = _errors(mutate)
    assert errs, f"{label}: expected an error"
    assert any(needle in e for e in errs), f"{label}: {errs}"


def test_a_pin_older_than_the_live_contract_warns_and_an_unknown_one_errors() -> None:
    doc = copy.deepcopy(GOOD); doc["steps"][0]["preset"] = "masem-direct@00000000"
    spec = ws.normalize(doc)
    errs, warns = ws.validate(spec)                       # no database: only the shape is checked
    assert errs == [] and any("older contract" in w for w in warns)
    errs, _ = ws.validate(spec, pin_known=lambda pin: False)
    assert any("unknown contract" in e for e in errs)
    errs, _ = ws.validate(spec, pin_known=lambda pin: True)
    assert errs == []


def test_the_contract_hashes_steps_pins_and_review_but_not_wording() -> None:
    base = ws.normalize(copy.deepcopy(GOOD)); ws.validate(base)
    wid = ws.workflow_id(base)
    assert wid.startswith("demo@") and len(wid) == len("demo@") + 8
    relabel = copy.deepcopy(GOOD); relabel["steps"][0]["label"] = "Something else"; relabel["steps"][1]["help"] = "why"
    r = ws.normalize(relabel); ws.validate(r)
    assert ws.workflow_id(r) == wid and ws.spec_sha(r) != ws.spec_sha(base)      # wording: same contract, a changed document
    for mutate in (lambda d: (d["steps"].pop(1), d["steps"][2].__setitem__("gate", ["extract"])),     # no review at all
                   lambda d: d["steps"][1].__setitem__("blocks", "next"),
                   lambda d: d["steps"][1].__setitem__("coders", 2),
                   lambda d: d["steps"][1].__setitem__("new_only", False),
                   lambda d: d["steps"][2].__setitem__("on_fail", "flag"),
                   lambda d: d["steps"][3].__setitem__("gate", [])):
        other = copy.deepcopy(GOOD); mutate(other)
        o = ws.normalize(other); ws.validate(o)
        assert ws.workflow_id(o) != wid
    # the review step's id, label and the declared defaults are outside the contract
    for mutate in (lambda d: d["steps"][1].update({"id": "look", "label": "Look"}) or d["steps"][3].__setitem__("gate", ["extract", "look"]),
                   lambda d: d["steps"][1].__setitem__("new_only", True),
                   lambda d: d["steps"][3].__setitem__("gate", ["review"])):
        other = copy.deepcopy(GOOD); mutate(other)
        o = ws.normalize(other); ws.validate(o)
        assert ws.workflow_id(o) == wid
    # a derive prompt's wording is outside the contract too
    d1 = copy.deepcopy(GOOD); d1["steps"].insert(3, {"id": "d", "kind": "derive", "over": "extract", "identity": ["a"],
                                                      "calls": [{"id": "c", "kind": "llm", "prompt": {"text": "one"}}], "output": {"fields": [{"name": "a"}]}})
    d2 = copy.deepcopy(d1); d2["steps"][3]["calls"][0]["prompt"]["text"] = "two"
    assert ws.workflow_id(ws.normalize(d1)) == ws.workflow_id(ws.normalize(d2))


def test_the_attribute_form_is_read_as_a_review_step_and_hashes_the_same() -> None:
    flat = ws.normalize(copy.deepcopy(GOOD)); ws.validate(flat)
    legacy = ws.normalize(copy.deepcopy(LEGACY)); ws.validate(legacy)
    assert [s["id"] for s in legacy["steps"]] == ["extract", "extract_review", "contract", "release"]
    rs = legacy["steps"][1]
    assert rs["kind"] == "review" and rs["over"] == ["extract"] and rs["label"] == "Review of Extract"
    assert {k: rs[k] for k in ("mode", "unit", "blocks", "oversample_flagged")} == {"mode": "all", "unit": "entry", "blocks": "release", "oversample_flagged": False}
    assert "review" not in legacy["steps"][0] and legacy["steps"][-1]["gate"] == ["extract", "extract_review"]
    assert ws.workflow_id(legacy) == ws.workflow_id(flat)                 # one contract, two spellings
    assert ws.normalize(legacy) == legacy and ws.normalize(flat) == flat   # idempotent
    # a nested `review` on a review step is hoisted
    nested = copy.deepcopy(GOOD); nested["steps"][1] = {"id": "review", "kind": "review", "over": ["extract"], "review": {"mode": "all", "unit": "entry"}}
    n = ws.normalize(nested); ws.validate(n)
    assert "review" not in n["steps"][1] and n["steps"][1]["mode"] == "all" and ws.workflow_id(n) == ws.workflow_id(flat)
    # what the contract hashes: the form before, the review folded onto its step, the gate repointed
    u = ws.unshim(flat)
    assert [s["id"] for s in u["steps"]] == ["extract", "contract", "release"] and u["steps"][-1]["gate"] == ["extract"]
    assert u["steps"][0]["review"] == {"mode": "all", "unit": "entry", "blocks": "release", "oversample_flagged": False}
    # mode none in the old form: no review step, the none attribute in the contract
    none = copy.deepcopy(LEGACY); none["steps"][0]["review"] = {"mode": "none"}
    n2 = ws.normalize(none); ws.validate(n2)
    assert [s["kind"] for s in n2["steps"]] == ["extract", "check", "release"] and "review" not in n2["steps"][0]
    assert ws.unshim(n2)["steps"][0]["review"]["mode"] == "none" and ws.review_of(n2["steps"][0])["mode"] == "none"
    # what only a step can say keeps the step in the contract
    two = copy.deepcopy(GOOD); two["steps"][1]["new_only"] = False
    t = ws.normalize(two); ws.validate(t)
    assert ws.unshim(t)["steps"][1]["kind"] == "review" and ws.unshim(t)["steps"][1]["new_only"] is False and ws.workflow_id(t) != ws.workflow_id(flat)
    # two review steps over one data step: the second keeps its place
    twice = copy.deepcopy(GOOD); twice["steps"].insert(2, {"id": "again", "kind": "review", "over": ["extract"], "mode": "all", "unit": "paper"})
    tw = ws.normalize(twice); ws.validate(tw)
    assert [s["kind"] for s in ws.unshim(tw)["steps"]] == ["extract", "review", "check", "release"] and ws.workflow_id(tw) != ws.workflow_id(flat)
    # the helpers: what covers a data step
    assert ws.review_steps_over(flat, "extract") == [flat["steps"][1]] and ws.review_for(flat, "extract")["step"] == "review"
    assert ws.review_for(n2, "extract")["mode"] == "none"


def test_the_bundled_workflows_keep_their_ids_across_the_two_spellings() -> None:
    all_wf = workflows.load_all()
    assert {wid: ws.workflow_id(spec) for wid, spec in all_wf.items() if wid in BUNDLED_IDS} == BUNDLED_IDS
    for wid, spec in all_wf.items():
        assert ws.validate(spec)[0] == [] and ws.normalize(spec) == spec, wid
        assert not any(s.get("kind") in ("extract", "agent", "derive") and "review" in s for s in spec["steps"]), wid


def test_what_this_release_refuses_at_plan_time() -> None:
    doc = copy.deepcopy(GOOD)
    doc["steps"][0].update({"k": 2, "vote": {"identity": ["sample_id"]}})
    spec = ws.normalize(doc); assert ws.validate(spec)[0] == []
    assert workflows.later_problems(spec) == []                                   # k runs and a vote run (the pipeline)
    assert workflows.calls_per_paper(spec, spec["steps"][0]["id"]) == 2
    doc = copy.deepcopy(GOOD); doc["steps"][0].update({"k": 2})
    assert any("k > 1 needs vote" in e for e in ws.validate(ws.normalize(doc))[0])
    doc = copy.deepcopy(GOOD); doc["steps"].insert(1, {"id": "bot", "kind": "agent", "preset": ECON})
    assert any("agent" in m for m in workflows.later_problems(ws.normalize(doc)))
    doc = copy.deepcopy(GOOD); doc["steps"][0]["review"] = {"mode": "sample", "n": 5}
    assert workflows.later_problems(ws.normalize(doc)) == []                     # a sampled review runs
    doc = copy.deepcopy(GOOD); doc["steps"].insert(1, {"id": "second", "kind": "extract", "preset": ECON})
    assert any("distinct dataset role" in m for m in workflows.later_problems(ws.normalize(doc)))   # two sides need roles (a group)
    doc["steps"][0]["dataset"] = "a"; doc["steps"][1]["dataset"] = "b"
    assert workflows.later_problems(ws.normalize(doc)) == []


def test_every_bundled_preset_has_a_valid_implicit_workflow() -> None:
    for pid, pspec in presets.load_all().items():
        spec = ws.implicit(pspec, ps.schema_id(pspec))
        errs, _ = ws.validate(spec)
        assert errs == [], (pid, errs)
        assert spec["implicit"] is True and spec["steps"][0]["kind"] == "extract" and spec["steps"][-1]["kind"] == "release"
        assert spec["steps"][0]["source"] == ("supplied" if pspec["meta"].get("mode") == "import" else "live")
        assert spec["steps"][-1]["gate"] == ["extract"]
    # a preset's update steps become steps of the implicit workflow
    pspec = copy.deepcopy(presets.load_all()["register-claims"])
    pspec["meta"]["update"] = [{"type": "vocabulary", "unit": "entries", "column": "cause"}, {"type": "pages", "group": "design", "keyed_by": "design"}, {"type": "crosscheck"}]
    spec = ws.implicit(pspec, ps.schema_id(pspec))
    assert [s["id"] for s in spec["steps"]] == ["extract", "vocabulary_cause", "pages_design", "crosscheck", "release"]
    assert ws.validate(spec)[0] == []


def test_the_bundled_workflows_validate_and_the_sibling_card_is_inlined() -> None:
    all_wf = workflows.load_all()
    assert {"econ-regressions", "masem"} <= set(all_wf)
    econ = all_wf["econ-regressions"]
    errs, warns = ws.validate(econ)
    assert errs == [] and warns == [] and workflows.later_problems(econ) == []
    assert ws.has_card(econ) and "## Steps" in econ["card"]["text"] and "file" not in econ["card"]
    assert econ["steps"][0]["source"] == "supplied" and econ["steps"][0]["preset"] == ECON
    assert ws.pins(econ) == [("tables", ECON)] and ws.gate_steps(econ) == ["tables", "review"]
    assert ws.steps_by_id(econ)["review"]["over"] == ["tables"] and ws.review_of(ws.steps_by_id(econ)["review"])["n"] == 30
    assert ws.validate(all_wf["masem"])[0] == [] and all_wf["masem"]["steps"][0]["source"] == "live"
    assert [s["id"] for s in all_wf["masem"]["steps"]] == ["extract", "contract", "review", "release"]


def test_a_card_file_outside_the_directory_is_refused(tmp_path) -> None:
    (tmp_path / "w.json").write_text('{"format": 1, "id": "w", "meta": {"title": "W"}, "card": {"file": "../secret.md"}, "steps": []}', encoding="utf-8")
    with pytest.raises(ws.WorkflowError) as info:
        ws.load_file(tmp_path / "w.json")
    assert "outside" in str(info.value)
    assert ws.load_dir(tmp_path) == {}                   # skipped, never fatal
