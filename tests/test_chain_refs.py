"""Evidence by reference: a follow-up step cites the runs' outputs (``r2.claims[3].sign``);
the engine validates every ref, inherits the runs' quotes onto the follow-up's paths
(exact → row → table → entry), drops direct quotes, matches unreferenced entries by identity,
and the resolved document still satisfies the round-trip invariant. The follow-up prompt
itself carries the by-reference rules and the runs' outputs under stable ids."""
from __future__ import annotations

import copy
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_chain_vote import BASE, SPEC, _runs  # noqa: E402

from paperlens import chain, contract, preset_spec  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402
from paperlens.reconstruct import reconstruct_publishable  # noqa: E402


def test_ref_grammar_and_validation_codes() -> None:
    runs = _runs()
    sources = {r.label: r for r in runs}
    obj = {"claims": [{"cause": "parental death", "effect": "earnings", "sign": "-", "statement": "s", "tags": [], "results": []},
                      {"cause": "imports", "effect": "employment", "sign": "0", "statement": "s", "tags": [], "results": []},
                      {"cause": "orphan", "effect": "x", "sign": "+", "statement": "s", "tags": [], "results": []}],
           "evidence": [
               {"ref": "r1.claims[0]", "field": "claims[0]"},
               {"ref": ["r1.claims[1].sign", "r2.claims[1].sign"], "field": "claims[1].sign"},
               {"ref": "r9.claims[0]", "field": "claims[1]"},
               {"ref": "r1.claims[7]", "field": "claims[1]"},
               {"ref": "claims[0]", "field": "claims[1]"},
               {"ref": "r1.claims[0]", "field": "claims[9]"},
               {"ref": "r1.claims[0]"},
               {"snippet": "quoted directly", "page": 3, "field": "claims[2]"}]}
    issues, clean = chain.validate_refs(obj, SPEC, sources)
    codes = sorted(i["code"] for i in issues)
    assert codes == sorted(["unknown_source", "dangling_ref", "bad_ref", "dangling_path", "ref_without_field", "quoted_directly", "unreferenced_row"])
    assert [c["field"] for c in clean] == ["claims[0]", "claims[1].sign"]
    assert clean[1]["refs"] == [("r1", "claims[1].sign"), ("r2", "claims[1].sign")]
    assert {i["path"] for i in issues if i["code"] == "unreferenced_row"} == {"claims[2]"}


def test_resolution_walks_exact_row_table_entry_and_inherits() -> None:
    runs = _runs()
    sources = {r.label: r for r in runs}
    obj = {"claims": [{"cause": "parental death", "effect": "earnings", "sign": "-", "statement": "s", "tags": [], "results": [{"estimate": -0.1}]},
                      {"cause": "import competition", "effect": "employment", "sign": "-", "statement": "s", "tags": [], "results": [{"estimate": -1.3}]}],
           "evidence": [{"ref": "r1.claims[0]", "field": "claims[0]"},
                        {"ref": ["r1.claims[1].sign", "r3.claims[1].sign"], "field": "claims[1].sign"},
                        {"ref": "r1.claims[1].results[0].estimate", "field": "claims[1].results[0].estimate"}]}
    _, clean = chain.validate_refs(obj, SPEC, sources)
    doc, facts, resolved = chain.resolve(obj, SPEC, clean, sources, k=3, identity=["cause", "effect"], runs=runs,
                                         fields_for_candidates=SPEC["entries"]["fields"])
    fields = {(e["field"], e["snippet"]) for e in doc["evidence"]}
    assert ("claims[0]", "A parent's death reduces earnings") in fields
    assert ("claims[0].sign", "reduces earnings by 4 percent") in fields
    assert ("claims[1].sign", "import competition lowered employment") in fields
    assert ("claims[1].results[0].estimate", "−1.3") in fields
    assert all(e["page"] for e in doc["evidence"]) and "snippet" in doc["evidence"][0]
    assert facts[0]["support"] == {"n": 1, "k": 3} and "minority" in facts[0]["flags"]
    assert facts[1]["support"] == {"n": 2, "k": 3}
    assert [c["value"] for c in facts[1]["candidates"]["sign"]] == ["+", "-"]
    assert "contested:sign" in facts[1]["flags"] and "rewritten:sign" not in facts[1]["flags"]
    kinds = {(t["ref"], t["kind"]) for t in facts[1]["trace"]}
    assert ("r1.claims[1].sign", "entry") in kinds and ("r1.claims[1].results[0].estimate", "exact") in kinds
    assert resolved and all(isinstance(it["runs"], set) for it in resolved)


def test_resolved_document_round_trips_and_an_unreferenced_entry_is_matched_by_identity() -> None:
    runs = _runs()
    sources = {r.label: r for r in runs}
    obj = {"claims": [{"cause": "tax rate", "effect": "evasion", "sign": "+", "statement": "x", "tags": [], "results": []}], "evidence": []}
    _, clean = chain.validate_refs(obj, SPEC, sources)
    doc, facts, _ = chain.resolve(obj, SPEC, clean, sources, k=3, identity=["cause", "effect"], runs=runs,
                                  fields_for_candidates=SPEC["entries"]["fields"])
    assert facts[0]["support"]["n"] == 1 and "by_identity" in facts[0]["flags"]
    assert doc["paper_metadata"]["title"] == "T"
    text = json.dumps(doc)
    assert reconstruct_publishable(ingest(text, entries_key="claims")) == contract.strip_to_publishable(text, entries_key="claims")


def test_a_followup_preset_renders_the_runs_and_the_by_reference_rules() -> None:
    fu = copy.deepcopy(BASE)
    fu["id"] = "graph-test"
    fu["prompt"] = {"text": "Adjudicate the runs below into one set of claims.\n\n${previous}\n"}
    spec = preset_spec.normalize(fu)
    assert preset_spec.is_followup(spec) and not preset_spec.is_followup(SPEC)
    errs, _ = preset_spec.validate(spec)
    assert errs == [], errs
    assert "${previous}" in preset_spec.render_prompt(spec)                       # left literal until run time
    prev, truncated = chain.render_previous(_runs(), SPEC)
    assert not truncated and "r1.claims[0]:" in prev and "r3.claims[2]:" in prev and "quoted" in prev
    text = preset_spec.render_followup(spec, None, prev)
    assert "r2.claims[1]:" in text and "# EVIDENCE (generated — by reference)" in text and '"ref": "r1.claims[0]' in text
    assert "# PAPER METADATA" not in text and "snippet" in text and "never a quote" not in text
    bad = copy.deepcopy(BASE); bad["prompt"] = {"text": "x ${previous} ${nope}"}
    try:
        preset_spec.normalize(bad); errs = []
    except preset_spec.SpecError as e:
        errs = e.errors
    assert any("${nope}" in e for e in errs)
