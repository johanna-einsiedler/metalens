"""The auth-ready identity seam (plan §"Phase 1 is auth-ready").

ALL authorization/scoping goes through ``current_principal`` — never hardcode
session_id-only logic. In Phase 1 it resolves the anonymous browser principal
from the ``X-Session-Id`` header. In Phase 2 the same function additionally
resolves a logged-in ``user_id`` from the session cookie, so no call site
changes when accounts land.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    session_id: str | None = None
    user_id: str | None = None      # always None in Phase 1; populated in Phase 2

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None


def current_principal(x_session_id: str | None = None,
                      session_cookie: str | None = None) -> Principal:
    """Resolve the acting principal.

    Phase 1: anonymous, scoped by the X-Session-Id header.
    Phase 2 (future): if ``session_cookie`` maps to a live session row, return a
    Principal with that ``user_id`` (the cookie path is wired but inert now).
    """
    # Phase 2 hook (inert): look up session_cookie -> users/sessions -> user_id.
    return Principal(session_id=x_session_id, user_id=None)
