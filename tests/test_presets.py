"""Preset -> schema-row emission (plan §3 schema entity).

Verifies the ported view-grammar resolution: declared sub_views win, data_sources
auto-derive tabs + a Descriptives tab, and emit_schema_row produces faithful
field_defs. Stdlib-only for the core; the persistence check skips without a DB.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import presets  # noqa: E402


def test_all_presets_discovered() -> None:
    # The globbed FILE presets. The human-AI presets live under presets/_library/
    # (seeded as personal DB presets), so they are intentionally NOT in this set.
    ids = set(presets.load_all())
    assert ids == {"ai-findings", "econ-headline", "forestplot",
                   "masem-ncs18", "masem", "summarize"}


def test_declared_sub_views_win() -> None:
    # masem declares sub_views explicitly -> used verbatim (not auto-derived).
    fd = presets.emit_schema_row("masem")
    labels = [sv["label"] for sv in fd["sub_views"]]
    assert "Effect sizes" in labels
    assert fd["mode"] == "extraction"


def test_data_sources_auto_derive_tabs_and_descriptives() -> None:
    # masem-ncs18 declares NO sub_views but has data_sources -> auto-derived.
    fd = presets.emit_schema_row("masem-ncs18")
    ids = [sv["id"] for sv in fd["sub_views"]]
    assert "loadings" in ids and "correlations" in ids
    assert ids[-1] == "descriptives"            # Descriptives always appended last
    # evidence/confidence unions are surfaced for the renderer/validators
    assert "factor_loadings" in fd["evidence_keys"]
    assert "factor_loadings" in fd["confidence_keys"]
    assert "factor_loadings" in fd["core_keys"] and "sample_id" not in fd["core_keys"]


def test_econ_headline_grammar() -> None:
    # econ-headline declares its OWN sub_views (one "Details" tab using exclude_keys),
    # so they are used verbatim rather than auto-derived from data_sources.
    fd = presets.emit_schema_row("econ-headline")
    assert [sv["id"] for sv in fd["sub_views"]] == ["regmeta"]
    # its declared evidence/confidence grammar is surfaced faithfully
    assert {"paper_metadata", "tables", "regressions"} <= set(fd["evidence_keys"])
    assert "paper_metadata" in fd["confidence_keys"]
    # the single declared sub_view uses exclude_keys -> no include-derived core_keys
    assert fd["core_keys"] == [] and "exclude_keys" in fd["sub_views"][0]


def test_forestplot_minimal_grammar() -> None:
    # forestplot has neither sub_views nor data_sources -> empty grammar, still valid.
    fd = presets.emit_schema_row("forestplot")
    assert fd is not None and fd["sub_views"] == [] and fd["mode"] == "extraction"


def test_unknown_preset_returns_none() -> None:
    assert presets.emit_schema_row("does-not-exist") is None


# ── persistence: upsert_schema fills field_defs from the preset (needs DB) ─────

def _db_ok() -> bool:
    try:
        from paperlens import records
        c = records.connect(); c.close()
        return True
    except Exception:
        return False


def test_upsert_schema_fills_field_defs() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    from paperlens import records
    conn = records.connect()
    records.init_db(conn)
    records.upsert_schema(conn, "masem@v3")
    conn.commit()
    s = records.get_schema(conn, "masem@v3")
    assert s["source"] == "preset"
    assert s["preset_id"] == "masem" and s["schema_version"] == "v3"
    assert s["field_defs"]["sub_views"]            # populated, not a NULL stub
    assert "Effect sizes" in [sv["label"] for sv in s["field_defs"]["sub_views"]]
    conn.close()


def _main() -> int:
    failures = 0
    tests = [
        ("presets:discovered", test_all_presets_discovered),
        ("presets:declared-win", test_declared_sub_views_win),
        ("presets:auto-derive", test_data_sources_auto_derive_tabs_and_descriptives),
        ("presets:econ-grammar", test_econ_headline_grammar),
        ("presets:forestplot-min", test_forestplot_minimal_grammar),
        ("presets:unknown-none", test_unknown_preset_returns_none),
        ("presets:upsert-fills", test_upsert_schema_fills_field_defs),
    ]
    for label, fn in tests:
        try:
            fn()
            print(f"  PASS  {label}")
        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ == "Skipped":
                print(f"  SKIP  {label}: {exc}")
                continue
            failures += 1
            print(f"  FAIL  {label}: {exc!r}")
    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
