"""Accounts (Phase 2): email + password, opaque cookie sessions, claim-on-login.

Email+password is the primary method (bcrypt); GitHub OAuth can be added later
behind the same `sessions` table. Deliberately holds NO API keys — accounts own
records, never credentials. Timestamps come from Postgres (`now()`), tokens from
``secrets``. Every write commits in its own outermost transaction.
"""
from __future__ import annotations

import secrets
import uuid

import bcrypt
import psycopg

_BCRYPT_MAX = 72  # bcrypt only considers the first 72 bytes


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:_BCRYPT_MAX], bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_BCRYPT_MAX], password_hash.encode())
    except (ValueError, TypeError):
        return False


def create_user(conn: psycopg.Connection, email: str, password: str) -> dict:
    """Register a user. Raises ValueError if the email is already taken."""
    email = (email or "").strip().lower()
    if not email or not password:
        raise ValueError("Email and password are required.")
    uid = str(uuid.uuid4())
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
                (uid, email, hash_password(password)),
            )
    except psycopg.errors.UniqueViolation:
        raise ValueError("That email is already registered.")
    return {"id": uid, "email": email}


def authenticate(conn: psycopg.Connection, email: str, password: str) -> str | None:
    """Return the user id on valid credentials, else None."""
    row = conn.execute(
        "SELECT id, password_hash FROM users WHERE email = %s", ((email or "").strip().lower(),)
    ).fetchone()
    if not row or not verify_password(password, row[1]):
        return None
    return str(row[0])


def create_session(conn: psycopg.Connection, user_id: str, *, days: int = 30) -> str:
    token = secrets.token_urlsafe(32)
    with conn.transaction():
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) "
            "VALUES (%s, %s::uuid, now() + (%s || ' days')::interval)",
            (token, user_id, str(days)),
        )
    return token


def resolve_session(conn: psycopg.Connection, token: str | None) -> str | None:
    """Map a live (unexpired) session token to its user id."""
    if not token:
        return None
    row = conn.execute(
        "SELECT user_id FROM sessions WHERE token = %s AND expires_at > now()", (token,)
    ).fetchone()
    return str(row[0]) if row else None


def delete_session(conn: psycopg.Connection, token: str | None) -> None:
    if not token:
        return
    with conn.transaction():
        conn.execute("DELETE FROM sessions WHERE token = %s", (token,))


def get_user(conn: psycopg.Connection, user_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, email, created_at, citation_name, credits_granted, credits_used "
        "FROM users WHERE id = %s::uuid", (user_id,)
    ).fetchone()
    if not row:
        return None
    granted, used = int(row[4] or 0), int(row[5] or 0)
    return {"id": str(row[0]), "email": row[1],
            "created_at": row[2].isoformat() if row[2] else None,
            "citation_name": row[3],
            "credits": {"granted": granted, "used": used, "balance": granted - used}}


def update_profile(conn: psycopg.Connection, user_id: str, *, citation_name: str | None) -> None:
    with conn.transaction():
        conn.execute("UPDATE users SET citation_name = %s WHERE id = %s::uuid",
                     ((citation_name or "").strip() or None, user_id))


def change_password(conn: psycopg.Connection, user_id: str, old: str, new: str) -> bool:
    """Verify the current password, then set the new one. Returns False if the old
    password is wrong or the new one is empty (caller maps to an error)."""
    row = conn.execute("SELECT password_hash FROM users WHERE id = %s::uuid", (user_id,)).fetchone()
    if row is None or not verify_password(old, row[0]) or not (new or "").strip():
        return False
    with conn.transaction():
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s::uuid",
                     (hash_password(new), user_id))
    return True


def claim_anonymous(conn: psycopg.Connection, *, session_id: str | None, user_id: str) -> dict:
    """On login, hand the anonymous session's owner-less records + datasets to the
    user (the "log in to keep these" flow). Records stay attached to their session."""
    if not session_id:
        return {"records": 0, "datasets": 0, "documents": 0}
    with conn.transaction():
        r = conn.execute(
            "UPDATE record SET owner_user_id = %s::uuid "
            "WHERE session_id = %s AND owner_user_id IS NULL",
            (user_id, session_id),
        )
        d = conn.execute(
            "UPDATE dataset SET owner_user_id = %s::uuid "
            "WHERE session_id = %s AND owner_user_id IS NULL",
            (user_id, session_id),
        )
        doc = conn.execute(
            "UPDATE extraction_document SET owner_user_id = %s::uuid "
            "WHERE session_id = %s AND owner_user_id IS NULL",
            (user_id, session_id),
        )
        pre = conn.execute(
            "UPDATE personal_preset SET owner_user_id = %s::uuid "
            "WHERE session_id = %s AND owner_user_id IS NULL",
            (user_id, session_id),
        )
    return {"records": r.rowcount, "datasets": d.rowcount, "documents": doc.rowcount,
            "presets": pre.rowcount}
