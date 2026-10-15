"""The request pool: requests share a few long-lived connections instead of opening one
each, a connection the server dropped is replaced, a busy pool falls back to a direct
connection, and PAPERLENS_DB_POOL_MAX=0 goes back to one connection per request.

Skips without Postgres.
"""
from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens import records  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


@pytest.fixture
def pool_size(monkeypatch):
    """Size a fresh pool for one test; the default pool comes back afterwards."""
    if not _db_ok():
        pytest.skip("no Postgres")

    def size(lo: int, hi: int) -> None:
        monkeypatch.setenv("PAPERLENS_DB_POOL_MIN", str(lo))
        monkeypatch.setenv("PAPERLENS_DB_POOL_MAX", str(hi))
        records.close_pool()

    yield size
    records.close_pool()


def test_requests_share_pooled_connections(pool_size) -> None:
    pool_size(1, 2)
    pids = set()
    for _ in range(6):
        with records.pooled() as conn:
            pids.add(conn.info.backend_pid)
            assert conn.execute("SELECT 1").fetchone()[0] == 1
    assert len(pids) <= 2                      # six requests, at most the pool's two connections
    assert not conn.closed                     # handed back open, for the next request


def test_pooled_connections_autocommit_and_never_prepare(pool_size) -> None:
    pool_size(1, 1)
    with records.pooled() as conn:
        assert conn.autocommit
        assert conn.prepare_threshold is None  # safe behind a transaction-mode pooler


def test_a_connection_the_server_dropped_is_replaced(pool_size) -> None:
    pool_size(1, 1)
    with records.pooled() as conn:
        dropped = conn.info.backend_pid
    admin = records.connect()
    try:
        admin.execute("SELECT pg_terminate_backend(%s)", (dropped,))
    finally:
        admin.close()
    with records.pooled() as conn:             # the ping on lending finds it dead → a new one
        assert conn.info.backend_pid != dropped
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_a_busy_pool_falls_back_to_a_direct_connection(pool_size, monkeypatch) -> None:
    pool_size(1, 1)
    monkeypatch.setattr(records, "_POOL_WAIT_S", 0.2)
    with records.pooled() as held:
        with records.pooled() as extra:        # the pool's only connection is taken
            assert extra.info.backend_pid != held.info.backend_pid
            assert extra.execute("SELECT 1").fetchone()[0] == 1
        assert extra.closed                    # a direct connection is closed, not pooled
    assert not held.closed


def test_pool_off_connects_per_request(pool_size) -> None:
    pool_size(0, 0)
    assert records.open_pool() is None
    with records.pooled() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    assert conn.closed
