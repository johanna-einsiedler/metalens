"""Dataset history: GET /api/datasets/{id}/activity.

Merges two sources into one newest-first feed — papers entering the dataset
(extraction_document.created_at) and human review actions (verification_event).
The distinction that matters: an event whose diff actually changes a value is an EDIT,
not a verification, and the feed must label it that way and show before → after.

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
from paperlens import app as appmod, records  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _dataset_with_a_paper(conn, sess: str, title: str):
    """A dataset holding one extracted paper; returns (dataset, record ids)."""
    ds = records.create_dataset(conn, title=title, schema_id=None, session_id=sess)
    doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None,
                          source_job_id="activity", session_id=sess)
    conn.execute("UPDATE record SET dataset_id = %s::uuid WHERE document_id = %s::uuid",
                 (ds["id"], doc))
    rids = [str(r[0]) for r in conn.execute(
        "SELECT id FROM record WHERE document_id = %s::uuid ORDER BY entry_index",
        (doc,)).fetchall()]
    return ds, rids


def test_activity_reports_paper_added_and_creation_time() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-act-a"
    ds, _rids = _dataset_with_a_paper(conn, sess, "Activity A")

    r = TestClient(appmod.app).get(f"/api/datasets/{ds['id']}/activity",
                                   headers={"X-Session-Id": sess})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created_at"]                      # when the dataset was created
    added = [e for e in body["events"] if e["kind"] == "paper_added"]
    assert len(added) == 1
    assert added[0]["paper"] == "A meta-analysis of remote-work productivity"
    assert added[0]["at"]


def test_a_value_edit_is_labelled_edited_and_shows_before_after() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-act-b"
    ds, rids = _dataset_with_a_paper(conn, sess, "Activity B")
    c = TestClient(appmod.app)
    h = {"X-Session-Id": sess}

    c.post(f"/api/records/{rids[0]}/verify", headers=h, json={
        "status": "corrected",
        "diff": [{"field_path": "yi", "original_value": -0.42, "final_value": -0.5}],
        "notes": "recomputed from the CI"})

    events = c.get(f"/api/datasets/{ds['id']}/activity", headers=h).json()["events"]
    edits = [e for e in events if e["kind"] == "edited"]
    assert len(edits) == 1
    e = edits[0]
    assert e["entry_index"] == 0 and e["notes"] == "recomputed from the CI"
    assert e["changes"] == [{"field_path": "yi", "from": -0.42, "to": -0.5}]


def test_a_plain_verification_is_not_an_edit() -> None:
    """Verifying affirms; it must not show up as a value change."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-act-c"
    ds, rids = _dataset_with_a_paper(conn, sess, "Activity C")
    c = TestClient(appmod.app)
    h = {"X-Session-Id": sess}

    c.post(f"/api/records/{rids[0]}/verify", headers=h, json={"status": "verified"})
    events = c.get(f"/api/datasets/{ds['id']}/activity", headers=h).json()["events"]
    kinds = [e["kind"] for e in events]
    assert "verified" in kinds and "edited" not in kinds
    assert next(e for e in events if e["kind"] == "verified")["changes"] == []


def test_events_are_newest_first() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-act-d"
    ds, rids = _dataset_with_a_paper(conn, sess, "Activity D")
    c = TestClient(appmod.app)
    h = {"X-Session-Id": sess}

    c.post(f"/api/records/{rids[0]}/verify", headers=h, json={"status": "flagged"})
    c.post(f"/api/records/{rids[1]}/verify", headers=h, json={"status": "verified"})

    events = c.get(f"/api/datasets/{ds['id']}/activity", headers=h).json()["events"]
    ats = [e["at"] for e in events]
    assert ats == sorted(ats, reverse=True)
    # the paper entering the dataset predates the review of it
    assert events[-1]["kind"] == "paper_added"


def test_activity_is_private_to_the_owner() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-act-e"
    ds, _rids = _dataset_with_a_paper(conn, sess, "Activity E")

    stranger = TestClient(appmod.app).get(f"/api/datasets/{ds['id']}/activity",
                                          headers={"X-Session-Id": "act-stranger"})
    assert stranger.status_code == 404       # same gate as the overview
