"""Phase 3 — credibility: verification events, status projection, computed badges.

Drives a dataset through AI-only -> sample-verified -> human-verified, checks the
agreement rate (flag or value-changing diff = disagreement) + Wilson CI, and
confirms the status projection commits. Skips without Postgres.
"""
from __future__ import annotations

import os
import sys
import warnings

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

warnings.filterwarnings("ignore")
import fixtures  # noqa: E402


def _db_ok() -> bool:
    try:
        from paperlens import records
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def run() -> int:
    if not _db_ok():
        print("  SKIP  no Postgres available")
        return 0
    from paperlens import records
    from paperlens.ingest import ingest

    conn = records.connect()
    records.init_db(conn)
    failures = 0

    def check(label, cond, extra=""):
        nonlocal failures
        print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if not cond else ""))
        if not cond:
            failures += 1

    # dataset with 2 records (forestplot studies)
    doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON),
                          schema_id="forestplot@v1", source_job_id="cred-test")
    ds = records.create_dataset(conn, title="credibility demo", session_id="cred-sess")
    records.assign_document_to_dataset(conn, ds["id"], doc)
    recs = records.dataset_records(conn, ds["id"])
    check("dataset has 2 records", len(recs) == 2)
    r0, r1 = recs[0]["id"], recs[1]["id"]

    c = records.dataset_credibility(conn, ds["id"])
    check("AI-only before any verification",
          c["tier"] == "ai_only" and c["audited"] == 0 and c["agreement"] is None, str(c))

    # verify r0 (agreement, no value change)
    records.verify_record(conn, r0, status="verified")
    c = records.dataset_credibility(conn, ds["id"])
    check("sample-verified after 1/2",
          c["tier"] == "sample_verified" and c["audited"] == 1 and c["audited_pct"] == 50.0
          and c["agreement"] == 1.0 and c["agreement_ci"] is not None, str(c))

    # status projection committed (fresh connection)
    fresh = records.connect()
    st = fresh.execute("SELECT verification_status FROM record WHERE id = %s::uuid",
                       (r0,)).fetchone()[0]
    fresh.close()
    check("r0 status projected to 'verified' (committed)", st == "verified", st)

    # flag r1 (disagreement) -> all audited
    records.verify_record(conn, r1, status="flagged")
    c = records.dataset_credibility(conn, ds["id"])
    check("human-verified after 2/2, 50% agree",
          c["tier"] == "human_verified" and c["audited"] == 2 and c["audited_pct"] == 100.0
          and c["agreement"] == 0.5, str(c))
    check("badge label reads sensibly", "agree" in c["label"], c["label"])

    # value-changing diff counts as disagreement (separate dataset, 1 record)
    doc2 = records.persist(conn, ingest(fixtures.MASEM_JSON), schema_id="masem@v3",
                           source_job_id="cred-test2")
    ds2 = records.create_dataset(conn, title="diff demo", session_id="cred-sess")
    records.assign_document_to_dataset(conn, ds2["id"], doc2)
    rid = records.dataset_records(conn, ds2["id"])[0]["id"]
    records.verify_record(conn, rid, status="verified",
                          diff=[{"field_path": "n", "original_value": 147, "final_value": 150}])
    c = records.dataset_credibility(conn, ds2["id"])
    check("value-changing diff => disagreement (agreement 0.0)",
          c["audited"] == 1 and c["agreement"] == 0.0, str(c))
    conn.close()

    # one HTTP round-trip: verify via API, badge reflects it
    from fastapi.testclient import TestClient
    from paperlens.app import app
    cl = TestClient(app)
    doc3 = None
    conn3 = records.connect()
    doc3 = records.persist(conn3, ingest(fixtures.FORESTPLOT_JSON), schema_id="forestplot@v1",
                           source_job_id="cred-http")
    ds3 = records.create_dataset(conn3, title="http demo", session_id="cred-sess")
    records.assign_document_to_dataset(conn3, ds3["id"], doc3)
    rid3 = records.dataset_records(conn3, ds3["id"])[0]["id"]
    conn3.close()
    rv = cl.post(f"/api/records/{rid3}/verify", json={"status": "verified"})
    check("POST /verify -> 200", rv.status_code == 200 and rv.json()["status"] == "verified")
    badge = cl.get(f"/api/datasets/{ds3['id']}/credibility").json()
    check("GET /credibility reflects the verify", badge["audited"] == 1, str(badge))
    check("404 on verifying unknown record",
          cl.post("/api/records/00000000-0000-0000-0000-000000000000/verify",
                  json={"status": "verified"}).status_code == 404)

    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


def test_credibility() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
