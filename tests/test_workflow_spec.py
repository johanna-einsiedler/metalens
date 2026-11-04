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
        {"id": "extract", "kind": "extract", "preset": MASEM, "review": {"mode": "all"}},
        {"id": "contract", "kind": "check", "check": "schema", "over": ["extract"]},
        {"id": "release", "kind": "release", "gate": ["extract"]},
    ],
}


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
    assert ex["review"] == {"mode": "all", "unit": "entry", "blocks": "release", "oversample_flagged": False}
    ck = spec["steps"][1]
    assert ck["on_fail"] == "note" and ck["severity"] == "error" and ck["params"] == {} and ck["label"] == "Contract"
    assert spec["steps"][-1]["kind"] == "release"
    # a workflow without a release step gets one, gated on its extract steps
    doc = copy.deepcopy(GOOD); doc["steps"].pop()
    spec2 = ws.normalize(doc)
    assert spec2["steps"][-1] == {"id": "release", "kind": "release", "label": "Release", "gate": ["extract"]}
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
    ("gate names a check", lambda d: d["steps"][2].__setitem__("gate", ["contract"]), "not an earlier extract"),
    ("pin shape", lambda d: d["steps"][0].__setitem__("preset", "masem-direct"), "pin a data contract"),
    ("unknown preset", lambda d: d["steps"][0].__setitem__("preset", "nope-preset@deadbeef"), "unknown preset"),
    ("live on an import preset", lambda d: d["steps"][0].update({"preset": ECON, "source": "live"}), "has no prompt"),
    ("k out of range", lambda d: d["steps"][0].__setitem__("k", 9), "k must be"),
    ("k without vote", lambda d: d["steps"][0].__setitem__("k", 2), "needs vote.identity"),
    ("vote identity not a field", lambda d: d["steps"][0].update({"k": 2, "vote": {"identity": ["zzz"]}}), "scalar entry field"),
    ("previous names a later step", lambda d: d["steps"][0].__setitem__("input", {"previous": ["contract"]}), "not an earlier extract"),
    ("check unknown", lambda d: d["steps"][1].__setitem__("check", "zzz"), "check must be one of"),
    ("check over a check", lambda d: d["steps"].insert(2, {"id": "c2", "kind": "check", "check": "schema", "over": ["contract"]}), "not an earlier extract"),
    ("check too many inputs", lambda d: d["steps"][1].__setitem__("over", ["extract", "extract"]), "reads 1 step"),
    ("check bad on_fail", lambda d: d["steps"][1].__setitem__("on_fail", "explode"), "on_fail"),
    ("check unknown param", lambda d: d["steps"][1].__setitem__("params", {"zzz": 1}), "no parameter"),
    ("check param type", lambda d: d["steps"][1].__setitem__("params", {"ignore": "uncited_value"}), "must be a list"),
    ("review on a check", lambda d: d["steps"][1].__setitem__("review", {"mode": "all"}), "declares no review"),
    ("review mode", lambda d: d["steps"][0].__setitem__("review", {"mode": "some"}), "mode must be"),
    ("review unit", lambda d: d["steps"][0].__setitem__("review", {"mode": "all", "unit": "column"}), "unit must be"),
    ("review blocks", lambda d: d["steps"][0].__setitem__("review", {"mode": "all", "blocks": "everything"}), "blocks must be"),
    ("sample needs n or share", lambda d: d["steps"][0].__setitem__("review", {"mode": "sample"}), "exactly one of n"),
    ("sample both", lambda d: d["steps"][0].__setitem__("review", {"mode": "sample", "n": 3, "share": 0.1}), "exactly one of n"),
    ("sample share range", lambda d: d["steps"][0].__setitem__("review", {"mode": "sample", "share": 1.5}), "share must be"),
    ("sample strata not a field", lambda d: d["steps"][0].__setitem__("review", {"mode": "sample", "n": 3, "stratify": ["zzz"]}), "stratify"),
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
    for mutate in (lambda d: d["steps"][0].__setitem__("review", {"mode": "none"}),
                   lambda d: d["steps"][0].__setitem__("review", {"mode": "all", "blocks": "next"}),
                   lambda d: d["steps"][1].__setitem__("on_fail", "flag"),
                   lambda d: d["steps"][2].__setitem__("gate", [])):
        other = copy.deepcopy(GOOD); mutate(other)
        o = ws.normalize(other); ws.validate(o)
        assert ws.workflow_id(o) != wid
    # a derive prompt's wording is outside the contract too
    d1 = copy.deepcopy(GOOD); d1["steps"].insert(2, {"id": "d", "kind": "derive", "over": "extract", "identity": ["a"],
                                                      "calls": [{"id": "c", "kind": "llm", "prompt": {"text": "one"}}], "output": {"fields": [{"name": "a"}]}})
    d2 = copy.deepcopy(d1); d2["steps"][2]["calls"][0]["prompt"]["text"] = "two"
    assert ws.workflow_id(ws.normalize(d1)) == ws.workflow_id(ws.normalize(d2))


def test_what_this_release_refuses_at_plan_time() -> None:
    doc = copy.deepcopy(GOOD)
    doc["steps"][0].update({"k": 2, "vote": {"identity": ["sample_id"]}})
    spec = ws.normalize(doc); assert ws.validate(spec)[0] == []
    later = workflows.later_problems(spec)
    assert len(later) == 1 and "vote engine" in later[0]
    doc = copy.deepcopy(GOOD); doc["steps"][0]["review"] = {"mode": "sample", "n": 5}
    assert workflows.later_problems(ws.normalize(doc)) == []                     # a sampled review runs
    doc = copy.deepcopy(GOOD); doc["steps"].insert(1, {"id": "second", "kind": "extract", "preset": ECON})
    assert any("linked datasets" in m for m in workflows.later_problems(ws.normalize(doc)))


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
    assert ws.pins(econ) == [("tables", ECON)] and ws.gate_steps(econ) == ["tables"]
    assert ws.validate(all_wf["masem"])[0] == [] and all_wf["masem"]["steps"][0]["source"] == "live"


def test_a_card_file_outside_the_directory_is_refused(tmp_path) -> None:
    (tmp_path / "w.json").write_text('{"format": 1, "id": "w", "meta": {"title": "W"}, "card": {"file": "../secret.md"}, "steps": []}', encoding="utf-8")
    with pytest.raises(ws.WorkflowError) as info:
        ws.load_file(tmp_path / "w.json")
    assert "outside" in str(info.value)
    assert ws.load_dir(tmp_path) == {}                   # skipped, never fatal
