"""Deterministic aggregation of k runs: entries matched by identity fields, per-field
rules, candidates only where the runs disagree, flags, and the union of the runs' quotes
re-pathed to the merged entries and labelled by run."""
from __future__ import annotations

import copy
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import chain, preset_spec  # noqa: E402
from test_chain_spec import BASE  # noqa: E402

SPEC = preset_spec.normalize({**copy.deepcopy(BASE), "chain": {"k": 3, "identity": ["cause", "effect"],
                                                                   "aggregate": {"kind": "vote", "fields": {"statement": "first"}}}})
PLAN = preset_spec.chain_plan(SPEC)


def _run(label, idx, claims, quotes):
    spans = [{"entry_index": int(q[0].split("[")[1].split("]")[0]), "field_path": q[0], "snippet": q[1], "page": q[2], "source": q[3]} for q in quotes]
    return chain.Source(label=label, obj={"paper_metadata": {"title": "T", "doi": None, "year": 2020, "authors": ["A"], "journal": None},
                                          "claims": claims}, spec=SPEC, spans=spans, document_id=f"doc-{label}", pass_index=idx)


def _claims(sign_b="+", extra=None):
    c = [{"cause": "Parental death", "effect": "earnings", "sign": "-", "statement": "Losing a parent lowers earnings.",
          "tags": ["health"], "results": [{"estimate": -0.1}]},
         {"cause": "import competition", "effect": "employment", "sign": sign_b, "statement": "Imports move jobs.",
          "tags": ["trade"], "results": [{"estimate": -1.3}, {"estimate": -0.9}]}]
    return c + (extra or [])


def _runs():
    return [
        _run("r1", 0, _claims("+"), [("claims[0]", "A parent's death reduces earnings", 3, None), ("claims[0].sign", "reduces earnings by 4 percent", 3, None),
                                      ("claims[1]", "import competition lowered employment", 5, "Table 2"), ("claims[1].results[0].estimate", "−1.3", 5, "Table 2")]),
        _run("r2", 1, [dict(_claims("-")[0], cause="parental death", tags=["health", "family"]), _claims("-")[1]],
             [("claims[0]", "A parent's death reduces earnings", 3, None), ("claims[1]", "import competition lowered employment", 5, "Table 2")]),
        _run("r3", 2, _claims("-", extra=[{"cause": "tax rate", "effect": "evasion", "sign": "+", "statement": "x", "tags": [], "results": []}]),
             [("claims[0]", "A parent's death reduces earnings", 3, None), ("claims[2]", "higher rates, more evasion", 7, None)]),
    ]


def test_vote_rules_support_candidates_and_flags() -> None:
    doc, facts = chain.vote(_runs(), SPEC, PLAN)
    claims = doc["claims"]
    assert [c["cause"] for c in claims] == ["Parental death", "import competition", "tax rate"]   # identity ignores case
    assert claims[0]["tags"] == ["health", "family"]                      # list → union
    assert claims[0]["statement"] == "Losing a parent lowers earnings."   # text → first
    assert len(claims[1]["results"]) == 2                                 # child rows: union by their fields
    assert facts[0]["support"] == {"n": 3, "k": 3} and facts[0]["candidates"] == {}
    assert facts[1]["support"] == {"n": 3, "k": 3}
    assert claims[1]["sign"] == "-"                                       # 2 of 3 runs
    assert [c["value"] for c in facts[1]["candidates"]["sign"]] == ["+", "-"]
    assert facts[1]["candidates"]["sign"][0]["runs"] == ["r1"] and facts[1]["candidates"]["sign"][1]["runs"] == ["r2", "r3"]
    assert "contested:sign" in facts[1]["flags"] and "minority" not in facts[1]["flags"]
    assert facts[2]["support"] == {"n": 1, "k": 3} and "minority" in facts[2]["flags"]
    assert [r["label"] for r in facts[2]["runs"]] == ["r3"] and facts[2]["runs"][0]["entry_index"] == 2
    # the paper's own fields come from the first run
    assert doc["paper_metadata"]["title"] == "T"


def test_vote_evidence_follows_the_merged_entry_and_names_its_runs() -> None:
    doc, _ = chain.vote(_runs(), SPEC, PLAN)
    ev = {(e["field"], e["snippet"]): e for e in doc["evidence"]}
    assert ev[("claims[0]", "A parent's death reduces earnings")]["source"] == "quote · all (r1, r2, r3)"
    assert ev[("claims[0].sign", "reduces earnings by 4 percent")]["source"] == "quote · single (r1)"
    assert ev[("claims[1]", "import competition lowered employment")]["source"] == "Table 2 · majority (r1, r2)"
    assert ("claims[1].results[0].estimate", "−1.3") in ev                # child row re-pathed through the union
    assert ev[("claims[2]", "higher rates, more evasion")]["page"] == 7   # the lone run's entry sits at index 2 now
    assert all(e["page"] is not None for e in doc["evidence"])


def test_vote_ties_duplicates_and_permutation() -> None:
    runs = _runs()[:2]
    runs[0].obj["claims"][1]["sign"], runs[1].obj["claims"][1]["sign"] = "+", "-"
    doc, facts = chain.vote(runs, SPEC, {**PLAN, "k": 2})
    assert doc["claims"][1]["sign"] == "+" and "tie:sign" in facts[1]["flags"]      # the lowest run wins a tie
    dup = _runs()
    dup[0].obj["claims"].append(copy.deepcopy(dup[0].obj["claims"][0]))
    _, facts = chain.vote(dup, SPEC, PLAN)
    assert "duplicate_in_run:r1" in facts[0]["flags"]
    # a permutation of the runs changes labels, not groups or values
    a, _ = chain.vote(_runs(), SPEC, PLAN)
    b, _ = chain.vote(list(reversed(_runs())), SPEC, PLAN)
    assert sorted(c["cause"].lower() for c in a["claims"]) == sorted(c["cause"].lower() for c in b["claims"])
    assert {c["cause"].lower(): c["sign"] for c in a["claims"]} == {c["cause"].lower(): c["sign"] for c in b["claims"]}
