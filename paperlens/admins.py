"""Who may moderate this deployment.

One environment variable, no role in the database — the same shape as the Zenodo allow-list
(``PAPERLENS_ZENODO_USERS``), and for the same reason: a deployment has one or two people who
moderate, naming them in a secret is enough, and a schema for roles can wait until it is not.

    PAPERLENS_ADMINS="you@example.org,colleague@example.org"

Unset means *nobody* is an admin, which is the safe default: an open-source or local run has no
moderator, so anything that waits for approval simply waits. ``localmode`` is the exception — a
single-user machine has no one else to ask, so its fixed owner moderates its own instance.
"""
from __future__ import annotations

import os

from . import localmode


def emails() -> set[str]:
    raw = os.environ.get("PAPERLENS_ADMINS") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(user: dict | None) -> bool:
    if not user:
        return False
    if localmode.enabled():
        return True
    return (user.get("email") or "").strip().lower() in emails()
