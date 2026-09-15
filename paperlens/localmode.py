"""Single-user local mode — the open-source MASEMiner that runs on one machine.

``PAPERLENS_LOCAL_MODE=1`` makes every request act as one fixed local owner: no accounts,
no login UI, exports allowed, no free-trial caps, no retention purge. ``PAPERLENS_INLINE_JOBS=1``
runs extractions inside the request (no Redis, no worker). Both are read at call time so
tests can flip them; ``paperlens.local`` sets them before the app is imported.

The engine is otherwise unchanged: same Postgres schema (embedded through ``pgserver`` by the
launcher), same presets, same review UI — so a local run and a hosted run of the same
version are the same extraction.
"""
from __future__ import annotations

import os
import uuid

import psycopg

LOCAL_EMAIL = "local"
# a stable id so the local owner's rows survive re-runs; overridable for tests
_DEFAULT_LOCAL_USER_ID = "00000000-0000-4000-8000-00000000000a"


def enabled() -> bool:
    return os.environ.get("PAPERLENS_LOCAL_MODE", "").strip() == "1"


def inline_jobs() -> bool:
    return enabled() or os.environ.get("PAPERLENS_INLINE_JOBS", "").strip() == "1"


def local_user_id() -> str:
    return os.environ.get("PAPERLENS_LOCAL_USER_ID", _DEFAULT_LOCAL_USER_ID)


def ensure_local_user(conn: psycopg.Connection) -> str:
    """The one owner row. Its password hash is random and never shown, so the account can
    only be used by being the local process."""
    uid = local_user_id()
    row = conn.execute("SELECT id FROM users WHERE id = %s::uuid", (uid,)).fetchone()
    if row is None:
        from . import auth
        with conn.transaction():
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s::uuid, %s, %s) ON CONFLICT DO NOTHING",
                (uid, LOCAL_EMAIL, auth.hash_password(uuid.uuid4().hex)))
    return uid
