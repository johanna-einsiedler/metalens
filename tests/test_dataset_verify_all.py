"""POST /api/datasets/{id}/verify-all: every unverified record of an owned dataset gets a
verification event in the caller's name; flagged ones stay flagged; the badge becomes
human-verified. Skips without Postgres."""
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


def test_verify_all_marks_the_dataset_human_verified() -> None:
    try:
        conn = records.connect(); records.init_db(conn)
    except Exception:
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    sess = f"sess-verify-all-{uuid.uuid4().hex[:6]}"
    ds = records.create_dataset(conn, title="Verify all", schema_id=None, session_id=sess)
    for _ in range(2):
        doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None, source_job_id="va", session_id=sess)
        records.assign_document_to_dataset(conn, ds["id"], doc)
    rids = [str(r[0]) for r in conn.execute("SELECT id FROM record WHERE dataset_id = %s::uuid ORDER BY id", (ds["id"],)).fetchall()]
    records.verify_record(conn, rids[0], status="flagged", notes="looks wrong", verifier_kind="community")
    conn.commit()
    c = TestClient(appmod.app); h = {"X-Session-Id": sess}
    assert c.post(f"/api/datasets/{ds['id']}/verify-all", headers={"X-Session-Id": "stranger"}).status_code == 404
    r = c.post(f"/api/datasets/{ds['id']}/verify-all", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(rids) and body["verified"] == len(rids) - 1 and body["flagged"] == 1
    assert body["credibility"]["tier"] == "human_verified"          # every record audited
    statuses = {str(x[0]): x[1] for x in conn.execute("SELECT id, verification_status FROM record WHERE dataset_id = %s::uuid", (ds["id"],)).fetchall()}
    assert statuses[rids[0]] == "flagged" and all(v == "verified" for k, v in statuses.items() if k != rids[0])
    # idempotent
    assert c.post(f"/api/datasets/{ds['id']}/verify-all", headers=h).json()["verified"] == 0
    records.delete_dataset(conn, ds["id"]); conn.commit(); conn.close()
