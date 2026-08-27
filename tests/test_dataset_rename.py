"""Retitling a dataset (PATCH /api/datasets/{id} with a title).

The load-bearing detail is that the SLUG must not follow the title. It is minted once at
creation and is the dataset's published address — github_publish writes to
``datasets/<slug>/`` — so regenerating it on every rename would strand an already-published
copy under its old directory. Titles are labels; slugs are addresses.

Skips without Postgres.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import app as appmod, auth, records  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _owner(conn, email: str):
    """A logged-in owner + a client carrying their session cookie."""
    from fastapi.testclient import TestClient
    user = auth.create_user(conn, email, "pw-rename-123")
    tok = auth.create_session(conn, user["id"])
    c = TestClient(appmod.app)
    c.cookies.set("pl_session", tok if isinstance(tok, str) else tok["token"])
    return user, c


def test_rename_keeps_the_slug_and_updates_the_title() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    user, c = _owner(conn, "rename-a@example.org")
    ds = records.create_dataset(conn, title="Working title", schema_id=None,
                                owner_user_id=user["id"])

    r = c.patch(f"/api/datasets/{ds['id']}", json={"title": "  Wage elasticities, 2024  "})
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1, "title": "Wage elasticities, 2024"}   # trimmed

    row = conn.execute("SELECT title, slug FROM dataset WHERE id = %s::uuid",
                       (ds["id"],)).fetchone()
    assert row[0] == "Wage elasticities, 2024"
    assert row[1] == ds["slug"]                 # the published address did NOT move


def test_rename_refreshes_the_catalogue_search_index() -> None:
    """search_tsv is a generated column, so the new title must be findable immediately."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    user, c = _owner(conn, "rename-b@example.org")
    ds = records.create_dataset(conn, title="Untitled", schema_id=None,
                                owner_user_id=user["id"])

    c.patch(f"/api/datasets/{ds['id']}", json={"title": "Photosynthesis meta-analysis"})
    hit = conn.execute(
        "SELECT 1 FROM dataset WHERE id = %s::uuid "
        "AND search_tsv @@ websearch_to_tsquery('english', 'photosynthesis')",
        (ds["id"],)).fetchone()
    assert hit is not None


def test_rename_rejects_empty_and_overlong_titles() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    user, c = _owner(conn, "rename-c@example.org")
    ds = records.create_dataset(conn, title="Keep me", schema_id=None,
                                owner_user_id=user["id"])

    for bad in ("", "   ", "x" * 201):
        assert c.patch(f"/api/datasets/{ds['id']}", json={"title": bad}).status_code == 422
    assert c.patch(f"/api/datasets/{ds['id']}", json={}).status_code == 422
    assert conn.execute("SELECT title FROM dataset WHERE id = %s::uuid",
                        (ds["id"],)).fetchone()[0] == "Keep me"


def test_rename_is_owner_only() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    user, _c = _owner(conn, "rename-owner@example.org")
    ds = records.create_dataset(conn, title="Mine", schema_id=None, owner_user_id=user["id"])

    stranger = TestClient(appmod.app)
    r = stranger.patch(f"/api/datasets/{ds['id']}", json={"title": "Hijacked"},
                       headers={"X-Session-Id": "rename-stranger"})
    assert r.status_code == 403
    assert conn.execute("SELECT title FROM dataset WHERE id = %s::uuid",
                        (ds["id"],)).fetchone()[0] == "Mine"


def test_visibility_patch_still_works_alone() -> None:
    """The pre-existing publish/unpublish path must be unaffected by the new field."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    user, c = _owner(conn, "rename-d@example.org")
    ds = records.create_dataset(conn, title="Publishable", schema_id=None,
                                owner_user_id=user["id"])

    r = c.patch(f"/api/datasets/{ds['id']}", json={"visibility": "public"})
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "public"
    assert c.patch(f"/api/datasets/{ds['id']}", json={"visibility": "sideways"}).status_code == 422


def test_rename_and_visibility_together() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    user, c = _owner(conn, "rename-e@example.org")
    ds = records.create_dataset(conn, title="Before", schema_id=None, owner_user_id=user["id"])

    r = c.patch(f"/api/datasets/{ds['id']}", json={"title": "After", "visibility": "public"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "After" and r.json()["visibility"] == "public"
