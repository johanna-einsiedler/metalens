"""Delete endpoints must not 500.

Two failure modes this pins down, both invisible with the local object store:

  * The blob cleanup in ``delete_document`` runs AFTER the row is already gone. On the
    S3/R2 backend a denied/unreachable bucket raised straight out of the request, so the
    paper WAS deleted but the reviewer saw a 500. LocalObjectStore can't fail
    (``unlink(missing_ok=True)`` / ``rmtree(ignore_errors=True)``), which is why it only
    ever bit the deployed beta.
  * Path ids go to Postgres as ``%s::uuid``; a malformed one (a stale/undefined id from
    the browser) raised InvalidTextRepresentation → 500 instead of 404/403.

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

import fixtures  # noqa: E402
from paperlens import app as appmod, records, storage  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _doc(conn, sess: str) -> str:
    return records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None,
                           source_job_id="del-paths", session_id=sess)


class _BrokenStore:
    """An R2 token without DeleteObject / ListBucket."""

    def delete(self, key: str) -> None:
        raise RuntimeError("AccessDenied: s3:DeleteObject")

    def delete_keys(self, keys: list) -> None:
        raise RuntimeError("AccessDenied: s3:DeleteObject")

    def delete_prefix(self, prefix: str) -> None:
        raise RuntimeError("AccessDenied: s3:ListBucket")


class _NoListStore:
    """Beta's real backend: per-key operations work, ListObjectsV2 does not."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, key: str) -> None:
        self.deleted.append(key)

    def delete_keys(self, keys: list) -> None:
        self.deleted += list(keys)

    def delete_prefix(self, prefix: str) -> None:
        raise AssertionError("delete_prefix must not be reached when n_pages is known")


def test_page_images_are_deleted_without_listing(monkeypatch) -> None:
    """The page images must come off as an explicit key list, not a prefix sweep."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-del-nolist"
    doc_id = _doc(conn, sess)
    # a cached parse gives delete_document the page count it derives the keys from
    sha = f"sha-nolist-{doc_id[:8]}"
    conn.execute("INSERT INTO parsed_document (pdf_sha256, n_pages) VALUES (%s, %s) "
                 "ON CONFLICT (pdf_sha256) DO UPDATE SET n_pages = EXCLUDED.n_pages", (sha, 3))
    conn.execute("UPDATE extraction_document SET pdf_sha256 = %s WHERE id = %s::uuid",
                 (sha, doc_id))
    store = _NoListStore()
    monkeypatch.setattr(storage, "_store", store)

    r = TestClient(appmod.app).delete(f"/api/documents/{doc_id}",
                                      headers={"X-Session-Id": sess})
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": 1, "blobs_orphaned": False}
    assert store.deleted == [f"pdf/{doc_id}.pdf"] + [
        f"pages/{doc_id}/{i}.jpg" for i in (1, 2, 3)]


def test_delete_document_survives_a_broken_object_store(monkeypatch) -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-del-brokenstore"
    doc_id = _doc(conn, sess)
    monkeypatch.setattr(storage, "_store", _BrokenStore())

    r = TestClient(appmod.app).delete(f"/api/documents/{doc_id}",
                                      headers={"X-Session-Id": sess})
    assert r.status_code == 200, r.text
    # the delete really happened — and it says the blobs leaked rather than hiding it
    assert r.json() == {"deleted": 1, "blobs_orphaned": True}
    assert conn.execute("SELECT count(*) FROM extraction_document WHERE id = %s::uuid",
                        (doc_id,)).fetchone()[0] == 0


def test_delete_document_reports_clean_blob_cleanup() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-del-cleanstore"
    doc_id = _doc(conn, sess)

    r = TestClient(appmod.app).delete(f"/api/documents/{doc_id}",
                                      headers={"X-Session-Id": sess})
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": 1, "blobs_orphaned": False}


def test_malformed_ids_are_not_500s() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    records.init_db(records.connect())
    c = TestClient(appmod.app)
    h = {"X-Session-Id": "sess-del-badid"}
    for bad in ("undefined", "null", "not-a-uuid"):
        assert c.delete(f"/api/records/{bad}", headers=h).status_code == 404
        assert c.delete(f"/api/documents/{bad}", headers=h).status_code == 403


def test_delete_record_ignores_a_malformed_id() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    assert records.delete_record(conn, "undefined") == {"deleted": 0}
    assert records.get_record(conn, "undefined") is None
