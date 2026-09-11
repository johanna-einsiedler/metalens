"""The §5 round-trip proof: reconstruct(ingest(x)) == strip_to_publishable(x).

Pure-stdlib so it runs with no install: ``python3 tests/test_roundtrip.py``
(also discoverable by pytest). If this passes across both evidence-placement
conventions, the contract -> normalized decomposition is loss-free and the
Postgres schema is safe to build on.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402
from paperlens.contract import strip_to_publishable  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402
from paperlens.reconstruct import reconstruct_publishable  # noqa: E402


def _check_roundtrip(name: str, raw: str) -> None:
    res = ingest(raw)
    rebuilt = reconstruct_publishable(res)
    expected = strip_to_publishable(raw)
    assert rebuilt == expected, (
        f"[{name}] round-trip mismatch\n"
        f"  expected keys: {sorted(expected)}\n"
        f"  rebuilt keys:  {sorted(rebuilt)}\n"
        f"  expected: {expected}\n"
        f"  rebuilt:  {rebuilt}"
    )


def test_roundtrip_forestplot() -> None:
    _check_roundtrip("forestplot", fixtures.FORESTPLOT_JSON)


def test_roundtrip_masem() -> None:
    _check_roundtrip("masem", fixtures.MASEM_JSON)


def test_roundtrip_masem_rich() -> None:
    _check_roundtrip("masem_rich", fixtures.MASEM_RICH_JSON)


def test_decomposition_shapes() -> None:
    # Forestplot: 2 study records, 3 top-level evidence spans, typed DOI present.
    fp = ingest(fixtures.FORESTPLOT_JSON)
    assert fp.core_key == "studies" and fp.core_shape == "table"
    assert len(fp.records) == 2
    assert fp.had_top_evidence is True
    assert all(s.placement == "top" for s in fp.evidence)
    assert len(fp.evidence) == 3
    # entry routing parsed from field paths
    routed = {s.field_path: s.entry_index for s in fp.evidence}
    assert routed["studies._table[0]"] == 0
    assert routed["studies._table[1]"] == 1
    assert routed["studies"] is None  # caption-level, no entry
    assert fp.doi == "10.1037/abc.0000123"

    # Masem: nested evidence, no top-level evidence, no paper_metadata.
    ms = ingest(fixtures.MASEM_JSON)
    assert ms.core_key == "samples" and ms.core_shape == "list"
    assert ms.had_top_evidence is False
    assert all(s.placement == "entry" and s.entry_index == 0 for s in ms.evidence)
    assert len(ms.evidence) == 2
    assert ms.paper_metadata_raw is None
    # evidence was stripped out of the stored field_values
    assert "evidence" not in ms.records[0].field_values

    # Masem rich: confidence decomposed, DOI normalized from a URL.
    mr = ingest(fixtures.MASEM_RICH_JSON)
    assert len(mr.records) == 2
    assert {c.block for c in mr.confidence} == {"factor_loadings", "metadata"}
    assert mr.doi == "10.1016/j.paid.2021.99999"  # normalized + lowercased
    assert mr.top_extras.get("schema_version") == "masem-v3"


def test_roundtrip_masem_v2() -> None:
    _check_roundtrip("masem_v2", fixtures.MASEM_V2_JSON)


def test_roundtrip_legacy_entry_confidence() -> None:
    """Per-sample bare-string ``extraction_confidence`` (the shipping MASEM prompts) must
    round-trip: it is stripped on BOTH sides now, instead of only by ingest."""
    _check_roundtrip("masem_legacy_entry_conf", fixtures.MASEM_LEGACY_ENTRY_CONF_JSON)
    res = ingest(fixtures.MASEM_LEGACY_ENTRY_CONF_JSON)
    legacy = [c for c in res.confidence if c.placement == "entry_legacy"]
    assert {c.block: c.level for c in legacy} == {
        "factor_loadings": "high", "factor_correlations": "low", "metadata": "medium"}
    assert all(c.entry_index == 0 and c.field_path == "samples[0]" for c in legacy)
    assert "extraction_confidence" not in res.records[0].field_values
    # never re-nested: legacy blocks are not part of the publishable document
    assert "extraction_confidence" not in reconstruct_publishable(res)["samples"][0]
    assert "confidence" not in reconstruct_publishable(res)["samples"][0]


def test_v2_confidence_has_a_home() -> None:
    """Declared confidence blocks are routed to the instance they rate — paper, entry,
    sub-entry — leave field_values, and come back in canonical form on reconstruct."""
    res = ingest(fixtures.MASEM_V2_JSON)
    by = {(c.placement, c.field_path, c.block): c for c in res.confidence}
    assert by[("paper", "paper_metadata", "design")].level == "high"
    assert by[("entry", "samples[0]", "effect_sizes")].entry_index == 0
    assert by[("entry", "samples[1]", "metadata")].entry_index == 1
    child = by[("child", "samples[0].records[0]", "row_check")]
    assert child.entry_index == 0 and child.notes == "Table 2 row 3"
    # stripped from what the spine stores…
    assert "confidence" not in res.records[0].field_values
    assert "confidence" not in res.records[0].field_values["records"][0]
    assert "confidence" not in res.paper_metadata_raw
    assert res.paper_metadata_raw["preregistered"] is True      # declared paper field kept
    # …and re-nested verbatim (canonical form) on the way out
    out = reconstruct_publishable(res)
    assert out["paper_metadata"]["confidence"] == {"design": {"level": "high", "notes": "stated in methods"}}
    assert out["samples"][0]["records"][0]["confidence"] == {"row_check": {"level": "high", "notes": "Table 2 row 3"}}
    assert "confidence" not in out["samples"][0]["records"][1]
    assert list(out["samples"][0]["confidence"]) == ["effect_sizes", "metadata"]   # group order kept
    # evidence addressing a sub-entry routes to its entry
    routed = {s.field_path: s.entry_index for s in res.evidence}
    assert routed["samples[1].records[0].es"] == 1


def test_confidence_note_alias_is_read() -> None:
    """Some pipelines write ``note`` instead of ``notes``; the rating keeps its text."""
    from paperlens.contract import normalize_confidence
    assert normalize_confidence({"design": {"level": "high", "note": "stated"}}) == {"design": {"level": "high", "notes": "stated"}}
    assert normalize_confidence({"design": {"level": "high", "notes": "n", "note": "ignored"}}) == {"design": {"level": "high", "notes": "n"}}


def test_bare_string_confidence_is_canonicalised() -> None:
    """``{"g": "high"}`` and ``{"g": {"level": "high"}}`` are the same rating; an empty
    block is the same as no block."""
    import json
    raw = json.dumps({"samples": [{"sample_id": "a", "confidence": {"g": "high"}},
                                  {"sample_id": "b", "confidence": {}}]})
    res = ingest(raw)
    assert [(c.block, c.level, c.notes) for c in res.confidence] == [("g", "high", None)]
    out = reconstruct_publishable(res)
    assert out["samples"][0]["confidence"] == {"g": {"level": "high", "notes": None}}
    assert "confidence" not in out["samples"][1]
    assert out == strip_to_publishable(raw)


def test_data_field_named_confidence_survives() -> None:
    """A field that merely shares the name (a number, a list) is data, not a rating."""
    import json
    raw = json.dumps({"records": [{"x": 1, "confidence": 0.95}, {"x": 2, "confidence": [1, 2]}]})
    res = ingest(raw)
    assert res.confidence == []
    assert res.records[0].field_values["confidence"] == 0.95
    assert reconstruct_publishable(res) == strip_to_publishable(raw)


def test_root_confidence_blocks_are_top_and_unpublished() -> None:
    res = ingest(fixtures.MASEM_RICH_JSON)          # root-level extraction_confidence
    assert {c.placement for c in res.confidence} == {"top"}
    assert all(c.entry_index is None and c.field_path is None for c in res.confidence)
    assert "extraction_confidence" not in reconstruct_publishable(res)


def test_declared_entries_key_wins_over_detection() -> None:
    import json
    raw = json.dumps({"records": [{"a": 1}], "samples": [{"b": 2}, {"b": 3}]})
    assert ingest(raw).core_key == "samples"                        # candidate order
    assert ingest(raw, entries_key="records").core_key == "records" # the declared key
    assert len(ingest(raw, entries_key="records").records) == 1
    # a declared key the model ignored falls back to detection
    assert ingest(raw, entries_key="tables").core_key == "samples"


def test_any_core_key_is_publishable() -> None:
    """A document whose spine is called ``tables`` used to fall out of the publishable
    set entirely (the invariant was untestable for it). The core array is part of the
    contract whatever its name."""
    import json
    raw = json.dumps({"tables": [{"table_id": "T1", "regressions": [{"column": "1"}]}],
                      "paper_metadata": {"title": "t"}})
    res = ingest(raw)
    assert res.core_key == "tables"
    assert reconstruct_publishable(res) == strip_to_publishable(raw)
    assert "tables" in strip_to_publishable(raw)


def test_inline_evidence_harvested_but_excluded_from_roundtrip() -> None:
    """A snippet cited IN PLACE (an object with evidence_snippet/evidence_page nested in an
    entry, not in a top-level evidence[]) is harvested into a highlight-only 'inline' span so
    it can be located — but it must NOT re-nest into the entry on reconstruct (round-trip stays
    loss-free)."""
    import json
    raw = json.dumps({
        "tables": [{
            "table_id": "T1",
            "regressions": [{
                "column": "1",
                "headline_classification": {
                    "is_headline": False,
                    "mentioned_in_narrative": {
                        "value": True,
                        "evidence_snippet": "We find that WTP is significantly lower on weekends.",
                        "evidence_page": 3,
                    },
                },
            }],
        }],
    })
    import copy
    res = ingest(raw)
    inline = [s for s in res.evidence if s.placement == "inline"]
    assert len(inline) == 1
    s = inline[0]
    assert s.field_path == "tables[0].regressions[0].headline_classification.mentioned_in_narrative.evidence_snippet"
    assert s.page == 3 and s.entry_index == 0
    assert s.snippet.startswith("We find that WTP")
    # the inline span must NOT re-nest into the entry on reconstruct, and must not change the
    # reconstruct output at all vs. a run without it → export / round-trip stays loss-free.
    rebuilt = reconstruct_publishable(res)
    assert "evidence" not in rebuilt["tables"][0]["regressions"][0]
    res_wo = copy.copy(res)
    res_wo.evidence = [x for x in res.evidence if x.placement != "inline"]
    assert reconstruct_publishable(res_wo) == rebuilt


def _main() -> int:
    failures = 0
    tests = [
        ("roundtrip:forestplot", test_roundtrip_forestplot),
        ("roundtrip:masem", test_roundtrip_masem),
        ("roundtrip:masem_rich", test_roundtrip_masem_rich),
        ("decomposition:shapes", test_decomposition_shapes),
        ("roundtrip:masem_v2", test_roundtrip_masem_v2),
        ("roundtrip:legacy-entry-confidence", test_roundtrip_legacy_entry_confidence),
        ("confidence:v2-home", test_v2_confidence_has_a_home),
        ("confidence:canonical", test_bare_string_confidence_is_canonicalised),
        ("confidence:data-field", test_data_field_named_confidence_survives),
        ("confidence:root-top", test_root_confidence_blocks_are_top_and_unpublished),
        ("core:declared-key", test_declared_entries_key_wins_over_detection),
        ("core:any-key-publishable", test_any_core_key_is_publishable),
    ]
    for label, fn in tests:
        try:
            fn()
            print(f"  PASS  {label}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {label}\n{exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())


def test_linked_evidence_round_trips_as_one_item_per_path() -> None:
    """``field`` may be a LIST (one quote reused for several values). Ingest stores one span
    per path; the publishable form is the same expansion, so the invariant holds."""
    raw = fixtures.HAC_LINKED_EVIDENCE_JSON
    _check_roundtrip("hac_linked", raw)
    res = ingest(raw)
    assert [s.field_path for s in res.evidence] == [
        "experiments[0].conditions[0].Final_Decision",
        "experiments[0].conditions[1].Final_Decision",
        "experiments[0].conditions[0].measures[0]"]
    assert [s.ord for s in res.evidence] == [0, 1, 2]
    pub = strip_to_publishable(raw)["evidence"]
    assert len(pub) == 3 and pub[0]["snippet"] == pub[1]["snippet"] and all(isinstance(e["field"], str) for e in pub)
