"""Test isolation: route the whole suite at a THROWAWAY database.

Historically the tests called ``records.connect()`` against the same dev DB
(``dbname=paperlens``) with no teardown, so every ``pytest`` run appended rows
(fresh uuids + fixed session ids) — datasets, documents, users, and even a few
PUBLIC datasets that then leaked into the Data Catalogue / observatory.

This conftest forces ``PAPERLENS_DATABASE_URL`` to a dedicated test database
(``paperlens_test`` by default, override with ``PAPERLENS_TEST_DATABASE_URL``)
BEFORE any test imports ``paperlens``, auto-creates it if missing, and wipes it
at the start of each session so runs are deterministic. The dev DB is never
touched. If no server / no create privilege, tests self-skip as before.
"""
from __future__ import annotations

import os

import pytest

# Must run at import time (before test modules call records.connect()). We never
# inherit an ambient PAPERLENS_DATABASE_URL (which might point at the dev DB) —
# the only override honored is the explicit test-DB one.
_TEST_DB_URL = os.environ.get("PAPERLENS_TEST_DATABASE_URL", "dbname=paperlens_test")
os.environ["PAPERLENS_DATABASE_URL"] = _TEST_DB_URL


def _test_db_name() -> str | None:
    # parse "dbname=..." out of the (keyword) DSN; None if we can't tell
    for tok in _TEST_DB_URL.split():
        if tok.startswith("dbname="):
            return tok.split("=", 1)[1]
    return None


def _ensure_test_db() -> None:
    """Create the test DB if it doesn't exist (best-effort; needs a reachable
    maintenance DB + CREATE privilege). Silent on failure — tests then skip."""
    name = _test_db_name()
    if not name:
        return
    try:
        import psycopg
    except Exception:
        return
    for maint in ("dbname=postgres", "dbname=template1"):
        try:
            admin = psycopg.connect(maint, autocommit=True)
        except Exception:
            continue
        try:
            exists = admin.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
            if not exists:
                admin.execute(f'CREATE DATABASE "{name}"')
        except Exception:
            pass
        finally:
            admin.close()
        return


_ensure_test_db()


@pytest.fixture(scope="session", autouse=True)
def _fresh_test_db():
    """Wipe the throwaway DB once per session so accumulated rows never linger."""
    try:
        from paperlens import records
        conn = records.connect()
    except Exception:
        yield
        return
    try:
        records.init_db(conn)  # ensure the schema exists before truncating
        names = [r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()]
        if names:
            conn.execute("TRUNCATE " + ", ".join(f'"{n}"' for n in names)
                         + " RESTART IDENTITY CASCADE")
    except Exception:
        pass
    finally:
        conn.close()
    yield
