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


def _main() -> int:
    failures = 0
    tests = [
        ("roundtrip:forestplot", test_roundtrip_forestplot),
        ("roundtrip:masem", test_roundtrip_masem),
        ("roundtrip:masem_rich", test_roundtrip_masem_rich),
        ("decomposition:shapes", test_decomposition_shapes),
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
