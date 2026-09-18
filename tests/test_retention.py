"""Logged-out retention: an anonymous session's uploads are deleted once the session has
not been seen for the retention period; claimed (logged-in) sessions and fresh sessions
are left alone; the free-trial counter survives the deletion; the 'forget' endpoint deletes
immediately; orphaned paper rows and parsed text go with the documents.

Skips without Postgres.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402
from paperlens import app as appmod, records, retention  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _skip():
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")


def _anon_doc(conn, sess: str, *, with_dataset: bool = True):
    doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None,
                          source_job_id="retention", session_id=sess)
    ds = None
    if with_dataset:
        ds = records.create_dataset(conn, title=f"ds {sess}", schema_id=None, session_id=sess)
        records.assign_document_to_dataset(conn, ds["id"], doc)
    conn.commit()
    return doc, ds


def _seen(conn, sess: str, minutes_ago: int, claimed_by: str | None = None):
    retention.touch(conn, sess, claimed_by, force=True)
    conn.execute("UPDATE anon_session SET last_seen = now() - (%s || ' minutes')::interval WHERE session_id = %s",
                 (str(minutes_ago), sess))
    conn.commit()


def _exists(conn, table, id_):
    return conn.execute(f"SELECT 1 FROM {table} WHERE id = %s::uuid", (id_,)).fetchone() is not None


def test_sweep_deletes_stale_anonymous_sessions_only() -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    stale, fresh, claimed = (f"ret-{k}-{uuid.uuid4().hex[:6]}" for k in ("stale", "fresh", "claimed"))
    d_stale, ds_stale = _anon_doc(conn, stale)
    d_fresh, ds_fresh = _anon_doc(conn, fresh)
    d_claimed, ds_claimed = _anon_doc(conn, claimed)
    retention.bump_trial(conn, stale, None)          # the trial run happened while the session was live
    _seen(conn, stale, minutes_ago=45)
    _seen(conn, fresh, minutes_ago=5)
    _seen(conn, claimed, minutes_ago=600, claimed_by=str(uuid.uuid4()))   # a login used this session id

    assert retention.expired_sessions(conn, max_age_minutes=30) == [stale]
    out = retention.sweep(conn, max_age_minutes=30)
    assert out["sessions"] == 1 and out["documents"] == 1 and out["datasets"] == 1

    assert not _exists(conn, "extraction_document", d_stale) and not _exists(conn, "dataset", ds_stale["id"])
    assert _exists(conn, "extraction_document", d_fresh) and _exists(conn, "dataset", ds_fresh["id"])
    assert _exists(conn, "extraction_document", d_claimed) and _exists(conn, "dataset", ds_claimed["id"])
    # the trial counter outlives the deletion: no second free trial by waiting it out
    assert retention.trial_used(conn, stale) == 1
    assert records.count_documents_for_session(conn, stale) == 0
    # nothing left to sweep for that session
    assert retention.expired_sessions(conn, max_age_minutes=30) == []
    conn.close()


def test_sweep_removes_orphaned_paper_and_parsed_text() -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    sess = f"ret-orphan-{uuid.uuid4().hex[:6]}"
    doc, _ = _anon_doc(conn, sess, with_dataset=False)
    paper_id = conn.execute("SELECT paper_id FROM extraction_document WHERE id = %s::uuid", (doc,)).fetchone()[0]
    sha = "deadbeef" + uuid.uuid4().hex
    conn.execute("UPDATE extraction_document SET pdf_sha256 = %s WHERE id = %s::uuid", (sha, doc))
    conn.execute("INSERT INTO parsed_document (pdf_sha256, n_pages, md_key, pages_key, char_len) VALUES (%s, 1, %s, %s, 10)",
                 (sha, f"text/{sha}.md", f"text/{sha}.pages.json"))
    conn.commit()
    _seen(conn, sess, minutes_ago=90)

    class Store:                                   # records what it was asked to delete
        deleted: list[str] = []
        def delete(self, key): self.deleted.append(key)

    store = Store()
    out = retention.sweep(conn, store, max_age_minutes=30)
    assert out["documents"] == 1 and out["parsed"] == 1
    others = conn.execute("SELECT count(*) FROM extraction_document WHERE paper_id = %s::uuid", (paper_id,)).fetchone()[0]
    if others == 0:                                # the fixture's DOI may be shared with other tests' documents
        assert not _exists(conn, "paper", str(paper_id))
    assert conn.execute("SELECT 1 FROM parsed_document WHERE pdf_sha256 = %s", (sha,)).fetchone() is None
    assert set(store.deleted) == {f"text/{sha}.md", f"text/{sha}.pages.json"}
    conn.close()


def test_forget_endpoint_deletes_now_and_any_request_keeps_alive() -> None:
    _skip()
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = f"ret-forget-{uuid.uuid4().hex[:6]}"
    doc, ds = _anon_doc(conn, sess)
    c = TestClient(appmod.app)
    h = {"X-Session-Id": sess}

    # any request through the principal refreshes the idle clock (no heartbeat needed)
    r = c.get("/api/extraction-config", headers=h)
    assert r.status_code == 200 and r.json()["anon_retention_minutes"] == retention.ANON_RETENTION_MINUTES
    seen = conn.execute("SELECT last_seen FROM anon_session WHERE session_id = %s", (sess,)).fetchone()[0]
    assert (dt.datetime.now(dt.timezone.utc) - seen).total_seconds() < 60
    assert sess not in retention.expired_sessions(conn, max_age_minutes=30)

    r = c.post("/api/session/forget", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True and r.json()["documents"] == 1 and r.json()["datasets"] == 1
    assert not _exists(conn, "extraction_document", doc) and not _exists(conn, "dataset", ds["id"])
    conn.close()


def test_config_counts_the_durable_trial() -> None:
    _skip()
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = f"ret-trial-{uuid.uuid4().hex[:6]}"
    retention.bump_trial(conn, sess, retention.ip_hash("203.0.113.7"))
    b = TestClient(appmod.app).get("/api/extraction-config", headers={"X-Session-Id": sess}).json()
    assert b["anon_extractions_used"] == 1 and b["anon_retention_minutes"] == retention.ANON_RETENTION_MINUTES
    assert retention.ip_trials_today(conn, retention.ip_hash("203.0.113.7")) >= 1
    assert retention.ip_hash("203.0.113.7") != retention.ip_hash("203.0.113.8")
    conn.close()


def test_sessions_without_an_activity_entry_still_expire() -> None:
    """Anonymous data from before the activity table existed has no ``anon_session`` row; the
    sweep registers such sessions with the date of their newest row and expires them normally."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    old, new = (f"ret-{k}-{uuid.uuid4().hex[:6]}" for k in ("untracked-old", "untracked-new"))
    d_old, ds_old = _anon_doc(conn, old); d_new, _ = _anon_doc(conn, new)
    conn.execute("DELETE FROM anon_session WHERE session_id = ANY(%s)", ([old, new],))
    conn.execute("UPDATE extraction_document SET created_at = now() - interval '3 days' WHERE id = %s::uuid", (d_old,))
    conn.execute("UPDATE dataset SET created_at = now() - interval '3 days' WHERE id = %s::uuid", (ds_old["id"],)); conn.commit()
    retention.sweep(conn)
    assert not _exists(conn, "extraction_document", d_old) and not _exists(conn, "dataset", ds_old["id"])
    assert _exists(conn, "extraction_document", d_new)                  # registered as just seen, kept
    retention.forget_session(conn, new); conn.commit(); conn.close()
