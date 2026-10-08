"""Evidence by reference: a follow-up call cites earlier outputs (``r2.claims[3].sign``);
the engine validates every ref, inherits the runs' quotes onto the follow-up's paths
(exact → row → table → entry), walks through intermediate calls, drops direct quotes, and
the resolved document still satisfies the round-trip invariant."""
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

from paperlens import chain, contract, preset_spec  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402
from paperlens.reconstruct import reconstruct_publishable  # noqa: E402
from test_chain_spec import BASE  # noqa: E402
from test_chain_vote import SPEC, _runs  # noqa: E402


def test_ref_grammar_and_validation_codes() -> None:
    runs = _runs()
    sources = {r.label: r for r in runs}
    obj = {"claims": [{"cause": "parental death", "effect": "earnings", "sign": "-", "statement": "s", "tags": [], "results": []},
                      {"cause": "imports", "effect": "employment", "sign": "0", "statement": "s", "tags": [], "results": []},
                      {"cause": "orphan", "effect": "x", "sign": "+", "statement": "s", "tags": [], "results": []}],
           "evidence": [
               {"ref": "r1.claims[0]", "field": "claims[0]"},
               {"ref": ["r1.claims[1].sign", "r2.claims[1].sign"], "field": "claims[1].sign"},
               {"ref": "r9.claims[0]", "field": "claims[1]"},                 # unknown source
               {"ref": "r1.claims[7]", "field": "claims[1]"},                 # dangling ref
               {"ref": "claims[0]", "field": "claims[1]"},                    # bad ref (no source)
               {"ref": "r1.claims[0]", "field": "claims[9]"},                 # dangling field
               {"ref": "r1.claims[0]"},                                       # ref without field
               {"snippet": "quoted directly", "page": 3, "field": "claims[2]"}]}
    issues, clean = chain.validate_refs(obj, SPEC, sources)
    codes = sorted(i["code"] for i in issues)
    assert codes == sorted(["unknown_source", "dangling_ref", "bad_ref", "dangling_path", "ref_without_field",
                            "quoted_directly", "unreferenced_row"])
    assert [c["field"] for c in clean] == ["claims[0]", "claims[1].sign"]
    assert clean[1]["refs"] == [("r1", "claims[1].sign"), ("r2", "claims[1].sign")]
    assert {i["path"] for i in issues if i["code"] == "unreferenced_row"} == {"claims[2]"}   # the directly quoted entry


def test_resolution_walks_exact_row_table_entry_and_inherits() -> None:
    runs = _runs()
    sources = {r.label: r for r in runs}
    obj = {"claims": [{"cause": "parental death", "effect": "earnings", "sign": "-", "statement": "s", "tags": [], "results": [{"estimate": -0.1}]},
                      {"cause": "import competition", "effect": "employment", "sign": "-", "statement": "s", "tags": [], "results": [{"estimate": -1.3}]}],
           "evidence": [{"ref": "r1.claims[0]", "field": "claims[0]"},                              # entry-level: inherits every quote
                        {"ref": ["r1.claims[1].sign", "r3.claims[1].sign"], "field": "claims[1].sign"},   # no exact quote → entry
                        {"ref": "r1.claims[1].results[0].estimate", "field": "claims[1].results[0].estimate"}]}
    _, clean = chain.validate_refs(obj, SPEC, sources)
    doc, facts, resolved = chain.resolve(obj, SPEC, clean, sources, k=3, identity=["cause", "effect"], runs=runs,
                                         fields_for_candidates=SPEC["entries"]["fields"])
    fields = {(e["field"], e["snippet"]) for e in doc["evidence"]}
    assert ("claims[0]", "A parent's death reduces earnings") in fields
    assert ("claims[0].sign", "reduces earnings by 4 percent") in fields        # inherited onto the final's own field
    assert ("claims[1].sign", "import competition lowered employment") in fields  # entry quote covers the field ref
    assert ("claims[1].results[0].estimate", "−1.3") in fields                  # exact hit
    assert all(e["page"] for e in doc["evidence"]) and "snippet" in doc["evidence"][0]
    assert facts[0]["support"] == {"n": 1, "k": 3} and "minority" in facts[0]["flags"]
    assert facts[1]["support"] == {"n": 2, "k": 3}
    assert [c["value"] for c in facts[1]["candidates"]["sign"]] == ["+", "-"]       # r1 said +, r3 said −
    assert "contested:sign" in facts[1]["flags"] and "rewritten:sign" not in facts[1]["flags"]
    kinds = {(t["ref"], t["kind"]) for t in facts[1]["trace"]}
    assert ("r1.claims[1].sign", "entry") in kinds and ("r1.claims[1].results[0].estimate", "exact") in kinds
    assert resolved and all(isinstance(it["runs"], set) for it in resolved)


def test_transitive_resolution_through_an_intermediate_call() -> None:
    runs = _runs()
    sources = {r.label: r for r in runs}
    # an intermediate call with its own shape: variables grouping the runs' wordings
    mid_spec = {**SPEC, "entries": {"key": "variables", "fields": [{"name": "name", "type": "string", "evidence": "row"}], "children": []}}
    mid = {"variables": [{"name": "parental death"}], "evidence": [{"ref": ["r1.claims[0]", "r2.claims[0]"], "field": "variables[0]"}]}
    _, clean = chain.validate_refs(mid, mid_spec, sources)
    _, _, resolved = chain.resolve(mid, mid_spec, clean, sources, k=3)
    mid_src = chain.Source(label="group", obj=mid, spec=mid_spec, items=resolved, is_run=False)
    sources["group"] = mid_src
    final = {"claims": [{"cause": "parental death", "effect": "earnings", "sign": "-", "statement": "s", "tags": [], "results": []}],
             "evidence": [{"ref": "group.variables[0]", "field": "claims[0]"}]}
    _, clean = chain.validate_refs(final, SPEC, sources)
    doc, facts, _ = chain.resolve(final, SPEC, clean, sources, k=3)
    assert {e["snippet"] for e in doc["evidence"]} == {"A parent's death reduces earnings", "reduces earnings by 4 percent"}
    assert doc["evidence"][0]["source"].endswith("(r1, r2)")
    assert facts[0]["support"]["n"] == 2 and facts[0]["trace"][0]["source"]["is_run"] is False


def test_resolved_document_round_trips_and_an_unreferenced_entry_is_matched_by_identity() -> None:
    runs = _runs()
    sources = {r.label: r for r in runs}
    obj = {"claims": [{"cause": "tax rate", "effect": "evasion", "sign": "+", "statement": "x", "tags": [], "results": []}], "evidence": []}
    _, clean = chain.validate_refs(obj, SPEC, sources)
    doc, facts, _ = chain.resolve(obj, SPEC, clean, sources, k=3, identity=["cause", "effect"], runs=runs,
                                  fields_for_candidates=SPEC["entries"]["fields"])
    assert facts[0]["support"]["n"] == 1 and "by_identity" in facts[0]["flags"]
    assert doc["paper_metadata"]["title"] == "T"                       # from the runs
    text = json.dumps(doc)
    assert reconstruct_publishable(ingest(text, entries_key="claims")) == contract.strip_to_publishable(text, entries_key="claims")
