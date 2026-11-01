"""The schema run survives a busy database: a lock held by another session makes an attempt
time out, the run backs off and tries again, and succeeds once the lock is gone; with the
lock never released it gives up after the configured attempts with the lock error. Skips
without Postgres."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

import psycopg
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import records  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


class _Holder:
    """A second session holding an ACCESS SHARE lock on ``dataset`` inside an open transaction,
    which is enough to block the ALTER TABLE statements of schema.sql."""
    def __init__(self):
        self.conn = psycopg.connect(records.dsn(), autocommit=False)
        self.conn.execute("SELECT 1 FROM dataset LIMIT 1")

    def release(self):
        self.conn.rollback(); self.conn.close()


def test_init_db_retries_past_a_lock_and_finishes(monkeypatch, caplog) -> None:
    if not _db_ok():
        pytest.skip("no Postgres available")
    records.init_db(records.connect())                       # the tables exist
    real_sleep = time.sleep
    monkeypatch.setattr(records.time, "sleep", lambda s: real_sleep(min(s, 0.05)))   # quick backoff
    holder = _Holder()
    threading.Timer(0.6, holder.release).start()
    conn = records.connect()
    with caplog.at_level(logging.WARNING, logger="paperlens"):
        t0 = time.monotonic()
        attempts = records.init_db(conn, attempts=20, lock_timeout="100ms")
    assert attempts > 1 and time.monotonic() - t0 >= 0.5
    assert any("LockNotAvailable" in r.getMessage() and "retrying" in r.getMessage() for r in caplog.records)
    assert conn.execute("SELECT 1").fetchone() == (1,)      # the connection is clean afterwards
    conn.close()


def test_init_db_gives_up_after_the_last_attempt(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres available")
    records.init_db(records.connect())
    monkeypatch.setattr(records.time, "sleep", lambda s: None)
    holder = _Holder()
    try:
        conn = records.connect()
        with pytest.raises(psycopg.errors.LockNotAvailable):
            records.init_db(conn, attempts=2, lock_timeout="50ms")
        assert conn.execute("SELECT 1").fetchone() == (1,)  # rolled back, still usable
        conn.close()
    finally:
        holder.release()
    assert records.init_db(records.connect()) == 1          # first try, once the lock is gone
