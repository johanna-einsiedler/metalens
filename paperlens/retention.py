"""Retention for logged-out use: what an anonymous browser session uploads is deleted
ANON_RETENTION_MINUTES after that session was last seen.

"Last seen" is the newest API request carrying the X-Session-Id header (touched from the
request principal, throttled to one write per minute) — a plain idle timeout, the usual
sliding-expiry-plus-purge arrangement. The sweep — a worker cron every few minutes — removes
the session's documents (PDF, page images, raw model response, records, evidence), datasets,
saved views and presets once the session has been idle for the retention period. Signing in
claims the session's rows (``auth.claim_anonymous``) and marks the session as claimed, which
exempts it for good.

Two things deliberately survive the sweep, both in ``anon_session``: the free-trial counter
(otherwise deleting the trial paper would hand out a new trial) and the session's
``claimed_by``. Orphaned ``paper`` rows and the hash-keyed parsed text of PDFs no document
references any more are removed in the same pass.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import time

import psycopg

from . import localmode, records

ANON_RETENTION_MINUTES = int(os.environ.get("PAPERLENS_ANON_RETENTION_MINUTES", "120"))
ANON_TRIAL_PER_IP_DAY = int(os.environ.get("PAPERLENS_ANON_TRIAL_PER_IP_DAY", "5"))
_TOUCH_EVERY = 60.0                      # seconds between DB touches for one session
_last_touch: dict[str, float] = {}


def touch(conn: psycopg.Connection, session_id: str | None, user_id: str | None = None,
          *, force: bool = False) -> None:
    """Record activity for a browser session. Cheap enough to call from the request
    principal: one upsert per session per minute. A logged-in principal marks the
    session as claimed, which keeps the sweep away from anything it owns."""
    if not session_id or localmode.enabled():
        return
    now = time.monotonic()
    if not force and not user_id and now - _last_touch.get(session_id, -1e9) < _TOUCH_EVERY:
        return
    _last_touch[session_id] = now
    with conn.transaction():
        conn.execute(
            """INSERT INTO anon_session (session_id, last_seen, claimed_by)
               VALUES (%s, now(), %s::uuid)
               ON CONFLICT (session_id) DO UPDATE
               SET last_seen = now(),
                   claimed_by = COALESCE(anon_session.claimed_by, EXCLUDED.claimed_by)""",
            (session_id, user_id))


# ── free trial accounting (survives the sweep) ────────────────────────────────

def trial_used(conn: psycopg.Connection, session_id: str | None) -> int:
    """Free-trial papers this anonymous session has run. The durable counter wins over
    the document count (which the sweep resets); the document count still covers rows
    produced before the counter existed."""
    if not session_id:
        return 0
    row = conn.execute("SELECT trial_used FROM anon_session WHERE session_id = %s", (session_id,)).fetchone()
    return max(int(row[0]) if row else 0, records.count_documents_for_session(conn, session_id))


def bump_trial(conn: psycopg.Connection, session_id: str | None, ip_hash: str | None) -> None:
    if not session_id:
        return
    with conn.transaction():
        conn.execute(
            """INSERT INTO anon_session (session_id, trial_used) VALUES (%s, 1)
               ON CONFLICT (session_id) DO UPDATE
               SET trial_used = anon_session.trial_used + 1, last_seen = now()""", (session_id,))
        if ip_hash:
            conn.execute(
                """INSERT INTO anon_trial_ip (ip_hash, day, n) VALUES (%s, CURRENT_DATE, 1)
                   ON CONFLICT (ip_hash, day) DO UPDATE SET n = anon_trial_ip.n + 1""", (ip_hash,))


def ip_hash(ip: str | None) -> str | None:
    """A daily-salted hash of the client address: enough to cap repeat trials from one
    network today, useless for tracking anyone across days."""
    if not ip:
        return None
    salt = os.environ.get("PAPERLENS_SECRET", "dev-artifact-secret")
    return hashlib.sha256(f"{salt}|{dt.date.today().isoformat()}|{ip}".encode()).hexdigest()[:32]


def ip_trials_today(conn: psycopg.Connection, ip_hash_: str | None) -> int:
    if not ip_hash_:
        return 0
    row = conn.execute("SELECT n FROM anon_trial_ip WHERE ip_hash = %s AND day = CURRENT_DATE", (ip_hash_,)).fetchone()
    return int(row[0]) if row else 0


def client_ip(request) -> str | None:
    """The caller's address behind Fly's proxy (Fly-Client-IP), a generic proxy
    (X-Forwarded-For, first hop) or a direct connection."""
    h = request.headers
    ip = h.get("fly-client-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip()
    if not ip and request.client:
        ip = request.client.host
    return ip or None


# ── the sweep ─────────────────────────────────────────────────────────────────

def expired_sessions(conn: psycopg.Connection, *, max_age_minutes: int | None = None,
                     now: dt.datetime | None = None) -> list[str]:
    """Anonymous sessions (never claimed by a user) last seen longer ago than the
    retention period, that still own something."""
    age = max_age_minutes if max_age_minutes is not None else ANON_RETENTION_MINUTES
    cutoff = (now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(minutes=age)
    rows = conn.execute(
        """SELECT s.session_id FROM anon_session s
           WHERE s.claimed_by IS NULL AND s.last_seen < %s
             AND (EXISTS (SELECT 1 FROM extraction_document d WHERE d.session_id = s.session_id AND d.owner_user_id IS NULL)
               OR EXISTS (SELECT 1 FROM dataset ds WHERE ds.session_id = s.session_id AND ds.owner_user_id IS NULL)
               OR EXISTS (SELECT 1 FROM saved_view v WHERE v.session_id = s.session_id AND v.owner_user_id IS NULL)
               OR EXISTS (SELECT 1 FROM personal_preset p WHERE p.session_id = s.session_id AND p.owner_user_id IS NULL))""",
        (cutoff,)).fetchall()
    return [r[0] for r in rows]


def forget_session(conn: psycopg.Connection, session_id: str) -> dict:
    """Delete everything an anonymous session owns, now. Used by the sweep and by the
    'delete my data' button. Owned (claimed) rows are never touched."""
    out = {"documents": 0, "datasets": 0, "saved_views": 0, "presets": 0}
    docs = [r[0] for r in conn.execute(
        "SELECT id FROM extraction_document WHERE session_id = %s AND owner_user_id IS NULL", (session_id,)).fetchall()]
    for d in docs:
        records.delete_document(conn, str(d))
        out["documents"] += 1
    sets = [r[0] for r in conn.execute(
        "SELECT id FROM dataset WHERE session_id = %s AND owner_user_id IS NULL", (session_id,)).fetchall()]
    for s in sets:
        records.delete_dataset(conn, str(s))
        out["datasets"] += 1
    with conn.transaction():
        out["saved_views"] = conn.execute(
            "DELETE FROM saved_view WHERE session_id = %s AND owner_user_id IS NULL", (session_id,)).rowcount
        out["presets"] = conn.execute(
            "DELETE FROM personal_preset WHERE session_id = %s AND owner_user_id IS NULL", (session_id,)).rowcount
        conn.execute("UPDATE anon_session SET swept_at = now() WHERE session_id = %s", (session_id,))
    return out


def remove_orphans(conn: psycopg.Connection, store=None) -> dict:
    """Paper rows nothing references any more, and the hash-keyed parsed text (plus its
    blobs) of PDFs no document holds any more."""
    out = {"papers": 0, "parsed": 0}
    with conn.transaction():
        out["papers"] = conn.execute(
            """DELETE FROM paper p
               WHERE NOT EXISTS (SELECT 1 FROM extraction_document d WHERE d.paper_id = p.id)
                 AND NOT EXISTS (SELECT 1 FROM record r WHERE r.paper_id = p.id)""").rowcount
    rows = conn.execute(
        """SELECT pd.pdf_sha256, pd.md_key, pd.pages_key FROM parsed_document pd
           WHERE NOT EXISTS (SELECT 1 FROM extraction_document d WHERE d.pdf_sha256 = pd.pdf_sha256)""").fetchall()
    for sha, md_key, pages_key in rows:
        if store is not None:
            for k in (md_key, pages_key):
                if k:
                    try:
                        store.delete(k)
                    except Exception:  # noqa: BLE001 - the row goes regardless; blobs are best-effort
                        pass
        with conn.transaction():
            conn.execute("DELETE FROM parsed_document WHERE pdf_sha256 = %s", (sha,))
        out["parsed"] += 1
    return out


def sweep(conn: psycopg.Connection, store=None, *, max_age_minutes: int | None = None,
          now: dt.datetime | None = None) -> dict:
    """One pass: every expired anonymous session is forgotten, then orphans go."""
    totals = {"sessions": 0, "documents": 0, "datasets": 0, "saved_views": 0, "presets": 0}
    if localmode.enabled():
        return {**totals, "papers": 0, "parsed": 0}
    for sid in expired_sessions(conn, max_age_minutes=max_age_minutes, now=now):
        r = forget_session(conn, sid)
        totals["sessions"] += 1
        for k in ("documents", "datasets", "saved_views", "presets"):
            totals[k] += r[k]
    totals.update(remove_orphans(conn, store))
    return totals
