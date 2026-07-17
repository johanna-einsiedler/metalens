"""Per-user extraction credits (server-key runs).

Logged-in users can be granted a small integer allowance of extractions that run on
Metalens's own provider key + a fixed model, so they don't need their own API key.
Each server-key run decrements the allowance by 1 (per-extraction quota). Balance =
credits_granted - credits_used; every change is mirrored to `credit_ledger`.

The server key is read from the environment here and NEVER returned to the browser or
placed in a Redis job payload — credit runs execute synchronously in the web process.
"""
from __future__ import annotations

import os
import uuid

# provider (from providers.get_provider) → env var holding Metalens's key for it
_PROVIDER_ENV = {
    "openai": "PAPERLENS_OPENAI_KEY",
    "google": "PAPERLENS_GOOGLE_KEY",
    "anthropic": "PAPERLENS_ANTHROPIC_KEY",
    "deepseek": "PAPERLENS_DEEPSEEK_KEY",
    "mistral": "PAPERLENS_MISTRAL_KEY",
}


# ── balance / ledger ────────────────────────────────────────────────────────────
def balance(conn, user_id: str) -> int:
    row = conn.execute(
        "SELECT credits_granted - credits_used FROM users WHERE id = %s::uuid", (user_id,)
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def summary(conn, user_id: str) -> dict:
    row = conn.execute(
        "SELECT credits_granted, credits_used FROM users WHERE id = %s::uuid", (user_id,)
    ).fetchone()
    granted, used = (int(row[0]), int(row[1])) if row else (0, 0)
    return {"granted": granted, "used": used, "balance": granted - used}


def ledger(conn, user_id: str, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT delta, reason, model, created_at FROM credit_ledger "
        "WHERE user_id = %s::uuid ORDER BY created_at DESC LIMIT %s", (user_id, limit)
    ).fetchall()
    return [{"delta": d, "reason": r, "model": m,
             "created_at": ts.isoformat() if ts else None} for (d, r, m, ts) in rows]


# ── mutations (atomic; no oversell) ─────────────────────────────────────────────
def try_consume(conn, user_id: str, *, model: str | None = None,
                document_id: str | None = None) -> bool:
    """Spend one credit iff the user has a positive balance. Returns True on success.
    The guarded UPDATE makes concurrent runs safe — no balance can go negative."""
    with conn.transaction():
        cur = conn.execute(
            "UPDATE users SET credits_used = credits_used + 1 "
            "WHERE id = %s::uuid AND credits_granted - credits_used > 0", (user_id,))
        if cur.rowcount == 0:
            return False
        conn.execute(
            "INSERT INTO credit_ledger (id, user_id, delta, reason, document_id, model) "
            "VALUES (%s, %s::uuid, -1, 'extraction', %s, %s)",
            (str(uuid.uuid4()), user_id, document_id, model))
    return True


def refund(conn, user_id: str, *, document_id: str | None = None, model: str | None = None) -> None:
    """Give back one credit (only after a consumed run fails). Never goes below 0."""
    with conn.transaction():
        conn.execute(
            "UPDATE users SET credits_used = GREATEST(credits_used - 1, 0) WHERE id = %s::uuid",
            (user_id,))
        conn.execute(
            "INSERT INTO credit_ledger (id, user_id, delta, reason, document_id, model) "
            "VALUES (%s, %s::uuid, 1, 'refund', %s, %s)",
            (str(uuid.uuid4()), user_id, document_id, model))


def grant(conn, user_id: str, n: int, *, reason: str = "grant") -> None:
    if n <= 0:
        return
    with conn.transaction():
        conn.execute("UPDATE users SET credits_granted = credits_granted + %s WHERE id = %s::uuid",
                     (n, user_id))
        conn.execute(
            "INSERT INTO credit_ledger (id, user_id, delta, reason, document_id, model) "
            "VALUES (%s, %s::uuid, %s, %s, NULL, NULL)",
            (str(uuid.uuid4()), user_id, n, reason))


# ── server-side model + key configuration (env only; never sent to the browser) ──
def credit_models() -> list[str]:
    return [m.strip() for m in os.environ.get("PAPERLENS_CREDIT_MODELS", "").split(",") if m.strip()]


def credit_model() -> str | None:
    """The model a keyless credit run uses (PAPERLENS_CREDIT_MODEL, else the first
    entry of PAPERLENS_CREDIT_MODELS)."""
    m = os.environ.get("PAPERLENS_CREDIT_MODEL", "").strip()
    if m:
        return m
    models = credit_models()
    return models[0] if models else None


def is_allowed_model(model: str) -> bool:
    """Whether ``model`` may be run on Metalens credits."""
    models = credit_models()
    if models:
        return model in models
    dm = credit_model()
    return bool(dm) and model == dm


def server_key_for(provider: str) -> str | None:
    env = _PROVIDER_ENV.get(provider)
    return os.environ.get(env) if env else None


def offered() -> bool:
    """True iff the server is configured to offer credit-based extraction (a default
    model AND a server key for that model's provider are present)."""
    model = credit_model()
    if not model:
        return False
    from . import providers
    return bool(server_key_for(providers.get_provider(model, None)))
