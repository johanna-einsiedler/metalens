"""DB-level round-trip: persist -> load -> reconstruct == strip_to_publishable.

Requires a reachable Postgres (PAPERLENS_DATABASE_URL or dbname=paperlens) and
psycopg installed. Skips cleanly otherwise so the stdlib core test always runs.
Also exercises the paper coverage/passport lookup.
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

try:
    from paperlens import records
    _HAVE_PSYCOPG = True
except Exception:  # pragma: no cover - import guard
    _HAVE_PSYCOPG = False


def _db_available() -> bool:
    if not _HAVE_PSYCOPG:
        return False
    try:
        conn = records.connect()
        conn.close()
        return True
    except Exception:
        return False


def run() -> int:
    if not _db_available():
        print("  SKIP  no Postgres / psycopg available (set PAPERLENS_DATABASE_URL)")
        return 0

    conn = records.connect()
    records.init_db(conn)
    failures = 0

    for name, raw in fixtures.ALL_FIXTURES.items():
        res = ingest(raw)
        doc_id = records.persist(conn, res, schema_id=None, source_job_id=f"smoke-{name}")
        loaded = records.load(conn, doc_id)
        rebuilt = reconstruct_publishable(loaded)
        expected = strip_to_publishable(raw)
        if rebuilt == expected:
            print(f"  PASS  db-roundtrip:{name}  (doc {doc_id[:8]}, {len(res.records)} records)")
        else:
            failures += 1
            print(f"  FAIL  db-roundtrip:{name}\n    expected: {expected}\n    rebuilt:  {rebuilt}")

    # coverage / passport lookup on a DOI-bearing fixture
    cov = records.paper_coverage(conn, "10.1037/abc.0000123")
    if cov and cov["paper"]["doi"] == "10.1037/abc.0000123" and cov["coverage"]:
        print(f"  PASS  coverage-lookup  ({cov['coverage'][0]['records']} records under "
              f"{cov['coverage'][0]['schema_id']})")
    else:
        failures += 1
        print(f"  FAIL  coverage-lookup  got: {cov}")

    conn.close()
    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


def test_confidence_rows_have_a_record() -> None:
    """Per-entry / per-sub-entry ratings persist WITH their record, and reach the view
    payload grouped by what they rate (the badges the review UI shows)."""
    if not _db_available():
        import pytest
        pytest.skip("no Postgres/psycopg available")
    conn = records.connect(); records.init_db(conn)
    doc_id = records.persist(conn, ingest(fixtures.MASEM_V2_JSON), schema_id=None,
                             source_job_id="conf-home", prompt_sha256="abc", params={"k": 1})
    rows = conn.execute(
        """SELECT placement, field_path, block, level, record_id IS NOT NULL
           FROM field_confidence WHERE document_id = %s ORDER BY ord""", (doc_id,)).fetchall()
    assert ("paper", "paper_metadata", "design", "high", False) in rows
    assert ("entry", "samples[0]", "effect_sizes", "high", True) in rows
    assert ("child", "samples[0].records[0]", "row_check", "high", True) in rows
    # provenance columns landed
    assert conn.execute("SELECT prompt_sha256, params FROM extraction_document WHERE id = %s::uuid",
                        (doc_id,)).fetchone() == ("abc", {"k": 1})
    view = records.document_view(conn, doc_id)
    assert view["paper_confidence"] == {"design": {"level": "high", "notes": "stated in methods"}}
    s1 = next(r for r in view["records"] if r["entry_index"] == 0)
    assert s1["confidence"]["metadata"] == {"level": "medium", "notes": "n inferred from df"}
    assert s1["child_confidence"] == {"records[0]": {"row_check": {"level": "high", "notes": "Table 2 row 3"}}}
    assert view["document_confidence"] == {}
    # the DB round-trip still holds with the new columns in play
    assert reconstruct_publishable(records.load(conn, doc_id)) == strip_to_publishable(fixtures.MASEM_V2_JSON)
    conn.close()


# pytest entry points
def test_db_roundtrip() -> None:
    if not _db_available():
        import pytest
        pytest.skip("no Postgres/psycopg available")
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())


def test_legacy_rows_get_a_spec_at_read_time() -> None:
    """A document under a pre-format schema row (or none) still comes back with a spec —
    inferred from the row's grammar and its own data — flagged as legacy."""
    if not _db_available():
        import pytest
        pytest.skip("no Postgres/psycopg available")
    conn = records.connect(); records.init_db(conn)
    with conn.transaction():
        records.upsert_schema(conn, "forestplot@v1", None)      # an adapter-only preset
    doc_id = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id="forestplot@v1",
                             source_job_id="legacy-spec")
    view = records.document_view(conn, doc_id)
    assert view["field_defs"]["legacy"] is True and view["field_defs"]["format"] == 2
    spec = view["spec"]
    assert spec["entries"]["key"] == "studies"
    names = {f["name"]: f["type"] for f in spec["entries"]["fields"]}
    assert names["yi"] == "number" and names["design"] == "string" and names["n"] == "integer"
    assert records.schema_spec(conn, "forestplot@v1")["entries"]["key"] == "studies"
    conn.close()
