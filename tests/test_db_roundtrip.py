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


# pytest entry points
def test_db_roundtrip() -> None:
    if not _db_available():
        import pytest
        pytest.skip("no Postgres/psycopg available")
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
