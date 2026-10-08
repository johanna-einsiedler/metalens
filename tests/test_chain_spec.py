"""The ``chain`` block of a preset: k runs of the extraction, follow-up calls that read
earlier outputs by reference, and how the runs are merged when no call does it. The
declaration never enters the schema id; the follow-up prompt gets its own generated
sections (by-reference evidence rules, no paper metadata, ``${previous}``)."""
from __future__ import annotations

import copy
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import preset_spec  # noqa: E402

BASE = {
    "format": 2, "id": "chain-test", "version": 1,
    "meta": {"title": "Chain test", "mode": "extraction"},
    "prompt": {"text": "Extract claims. ${output_schema} ${evidence} ${return_format}"},
    "entries": {"key": "claims", "fields": [
        {"name": "cause", "type": "string", "evidence": "row"},
        {"name": "effect", "type": "string", "evidence": "row"},
        {"name": "sign", "type": "enum", "options": ["+", "-", "0"], "evidence": "value"},
        {"name": "statement", "type": "text", "evidence": "none"},
        {"name": "tags", "type": "list", "evidence": "none"}],
        "children": [{"key": "results", "fields": [{"name": "estimate", "type": "number", "evidence": "value"}]}]},
}


def _with(chain: dict, **over) -> dict:
    d = copy.deepcopy(BASE); d["chain"] = chain; d.update(over); return d


def _errors(doc: dict) -> list[str]:
    try:
        preset_spec.normalize(doc)
        return []
    except preset_spec.SpecError as e:
        return e.errors


def test_memo_shapes_are_accepted_and_normalised() -> None:
    vote = preset_spec.normalize(_with({"k": 3, "identity": ["cause", "effect"],
                                        "aggregate": {"kind": "vote", "fields": {"sign": "majority", "statement": "first", "tags": "union"}}}))
    plan = preset_spec.chain_plan(vote)
    assert plan == {"k": 3, "identity": ["cause", "effect"], "calls": [],
                    "aggregate": {"kind": "vote", "fields": {"sign": "majority", "statement": "first", "tags": "union"}}}
    # the memo's aggregate.kind "prompt" is one follow-up call named "aggregate"; vote stays the fallback
    sugar = preset_spec.normalize(_with({"k": 2, "identity": ["cause", "effect"],
                                         "aggregate": {"kind": "prompt", "prompt": {"text": "Adjudicate. ${previous}"}}}))
    assert [c["id"] for c in sugar["chain"]["calls"]] == ["aggregate"]
    assert sugar["chain"]["aggregate"]["kind"] == "vote"
    assert sugar["chain"]["calls"][0]["prompt"]["generate"] == list(preset_spec.FOLLOWUP_SECTIONS)
    assert preset_spec.chain_plan(sugar)["calls"][0]["id"] == "aggregate"
    ext = preset_spec.normalize(_with({"k": None, "identity": ["cause", "effect"], "aggregate": {"kind": "external"}},
                                      meta={"title": "x", "mode": "import"}, prompt={}))
    assert preset_spec.chain_plan(ext) is None and preset_spec.chain_external(ext) == {}
    # k = 1 without calls is today's single extraction
    assert preset_spec.chain_plan(preset_spec.normalize(_with({"k": 1}))) is None
    assert preset_spec.chain_plan(preset_spec.normalize(copy.deepcopy(BASE))) is None


def test_chain_rejections() -> None:
    assert any("k must be an integer" in e for e in _errors(_with({"k": 0})))
    assert any("k must be an integer" in e for e in _errors(_with({"k": 6, "identity": ["cause"]})))
    assert any("identity is required" in e for e in _errors(_with({"k": 2})))
    assert any("not an entry field" in e for e in _errors(_with({"k": 2, "identity": ["nope"]})))
    assert any("final call keeps the preset" in e for e in _errors(_with({"k": 1, "calls": [
        {"id": "a", "prompt": {"text": "x ${previous}"}, "entries": {"key": "variables", "fields": [{"name": "name"}]}}]})))
    assert any("must reference ${previous}" in e for e in _errors(_with({"k": 1, "calls": [{"id": "a", "prompt": {"text": "no previous here"}}]})))
    assert any("neither a declared parameter nor a generated section" in e
               for e in _errors(_with({"k": 1, "calls": [{"id": "a", "prompt": {"text": "x ${previous}"}}]},
                                      prompt={"text": "Extract. ${previous} ${output_schema}"})))
    assert any("'union' only applies" in e for e in _errors(_with({"k": 2, "identity": ["cause"], "aggregate": {"kind": "vote", "fields": {"sign": "union"}}})))
    assert any("needs meta.mode 'import'" in e for e in _errors(_with({"k": None, "aggregate": {"kind": "external"}})))
    assert any("used twice" in e for e in _errors(_with({"k": 1, "calls": [
        {"id": "a", "prompt": {"text": "x ${previous}"}}, {"id": "a", "prompt": {"text": "y ${previous}"}}]})))
    assert any("not look like a run" in e for e in _errors(_with({"k": 1, "calls": [{"id": "r2", "prompt": {"text": "x ${previous}"}}]})))
    # an intermediate call's own entries are validated with the same grammar
    errs = _errors(_with({"k": 1, "calls": [
        {"id": "part", "prompt": {"text": "x ${previous}"}, "entries": {"key": "variables", "fields": [{"name": "evidence"}]}},
        {"id": "last", "prompt": {"text": "y ${previous}"}}]}))
    assert any("$.chain.calls[0].entries.fields[0].name 'evidence' is reserved" in e for e in errs)


def test_chain_is_not_part_of_the_schema_id() -> None:
    plain = preset_spec.normalize(copy.deepcopy(BASE))
    chained = preset_spec.normalize(_with({"k": 3, "identity": ["cause", "effect"], "aggregate": {"kind": "vote"}}))
    assert preset_spec.schema_id(plain) == preset_spec.schema_id(chained)


def test_followup_prompt_sections() -> None:
    spec = preset_spec.normalize(_with({"k": 2, "identity": ["cause", "effect"], "calls": [
        {"id": "adjudicate", "prompt": {"text": "Decide between the runs.\n${previous}\n${output_schema}\n${evidence}"}}]}))
    call = spec["chain"]["calls"][0]
    text = preset_spec.render_call_prompt(spec, call, None, "# PREVIOUS CALLS\nr1.claims[0]: {...}")
    assert "r1.claims[0]: {...}" in text
    assert "by reference" in text and '"ref"' in text
    assert "paper_metadata" not in text.split("# EVIDENCE")[0]        # no paper metadata asked of a follow-up
    assert '"confidence"' not in text.split("# EVIDENCE")[0]
    assert "# RETURN FORMAT" in text                                  # the unreferenced enabled section is appended
    recipe = preset_spec.call_recipe_sha(spec, call, None)
    assert recipe == preset_spec.call_recipe_sha(spec, call, None) and len(recipe) == 64
    assert "${previous}" in preset_spec.render_call_prompt(spec, call, None, "${previous}")
