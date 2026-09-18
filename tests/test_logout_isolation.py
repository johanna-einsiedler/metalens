"""Logging out must hide an account's data even though the browser keeps its session id:
rows that belong to an account are reachable through that account only; a session match
counts for anonymous rows alone. Skips without Postgres."""
from __future__ import annotations

import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402
from paperlens import app as appmod, records  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def test_account_data_is_invisible_to_the_same_browser_after_logout() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = f"iso-{uuid.uuid4().hex[:8]}"; hdr = {"X-Session-Id": sess}
    c = TestClient(appmod.app)
    r = c.post("/api/auth/register", json={"email": f"iso-{uuid.uuid4().hex[:8]}@example.org", "password": "iso-test-pass-1"}, headers=hdr)
    assert r.status_code == 200, r.text
    # created while signed in, in this browser session
    doc = c.post("/api/ingest", json={"result": fixtures.FORESTPLOT_JSON if isinstance(fixtures.FORESTPLOT_JSON, dict) else __import__("json").loads(fixtures.FORESTPLOT_JSON)},
                 headers=hdr).json()["document_id"]
    ds = c.post("/api/datasets", json={"title": "Account set", "visibility": "private"}, headers=hdr).json()["id"]
    assert c.post(f"/api/datasets/{ds}/add", json={"document_id": doc}, headers=hdr).status_code == 200
    mine = {d["document_id"] for d in c.get("/api/documents", headers=hdr).json()["documents"]}
    assert doc in mine and c.get(f"/api/documents/{doc}/view", headers=hdr).status_code == 200

    assert c.post("/api/auth/logout", headers=hdr).status_code == 200
    c.cookies.clear()
    # same browser session id, no login: the account's data is gone from every surface
    after = {d["document_id"] for d in c.get("/api/documents", headers=hdr).json()["documents"]}
    assert doc not in after
    assert c.get(f"/api/documents/{doc}/view", headers=hdr).status_code == 404
    assert c.get(f"/api/datasets/{ds}/overview", headers=hdr).status_code == 404
    assert c.get(f"/api/datasets/{ds}/export", headers=hdr).status_code == 404
    assert ds not in {d["id"] for d in c.get("/api/datasets", headers=hdr).json()["datasets"]}
    assert c.delete(f"/api/documents/{doc}", headers=hdr).status_code in (403, 404)
    assert all(d["document_id"] != doc for d in c.post("/api/documents/check-duplicates", json={"hashes": ["0" * 64]}, headers=hdr).json()["duplicates"])
    # anonymous work in the same session is still the session's own
    anon = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None, source_job_id="iso", session_id=sess); conn.commit()
    assert c.get(f"/api/documents/{anon}/view", headers=hdr).status_code == 200
    for d in (doc, anon):
        records.delete_document(conn, d) if hasattr(records, "delete_document") else None
    records.delete_dataset(conn, ds); conn.commit(); conn.close()
