"""Phase 4 — views as data: aggregate a saved view over records, recompute on read.

Verifies grouped counts, the "add records -> view recomputes" property, the
verified-only filter, and a mean aggregation. Skips without Postgres.
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

    # forestplot studies carry a `design` field (RCT / cohort) — group on it
    doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON),
                          schema_id="forestplot@v1", source_job_id="view-test")
    ds = records.create_dataset(conn, title="forest view ds", session_id="view-sess")
    records.assign_document_to_dataset(conn, ds["id"], doc)

    view = records.create_view(
        conn, title="Studies by design", dataset_ids=[ds["id"]],
        viz_config={"kind": "bar", "group_by": "design", "measure": "count"})

    r = records.run_view(conn, view["id"])
    groups = {s["group"]: s["value"] for s in r["series"]}
    check("aggregate count by design",
          r["total_records"] == 2 and groups.get("RCT") == 1 and groups.get("cohort") == 1, str(r))

    # "add paper -> view recomputes": assign a second document, re-run (no code change)
    doc2 = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON),
                           schema_id="forestplot@v1", source_job_id="view-test-2")
    records.assign_document_to_dataset(conn, ds["id"], doc2)
    r2 = records.run_view(conn, view["id"])
    check("recomputes after adding records (2 -> 4)", r2["total_records"] == 4, str(r2["total_records"]))

    # verified-only filter: verify the RCT records, view filtered to verified
    for rec in records.dataset_records(conn, ds["id"]):
        if rec["field_values"].get("design") == "RCT":
            records.verify_record(conn, rec["id"], status="verified")
    vview = records.create_view(
        conn, title="Verified studies by design", dataset_ids=[ds["id"]],
        query={"verification_status": "verified"},
        viz_config={"kind": "bar", "group_by": "design", "measure": "count"})
    rv = records.run_view(conn, vview["id"])
    vgroups = {s["group"]: s["value"] for s in rv["series"]}
    check("verified-only filter (2 RCT verified)",
          rv["total_records"] == 2 and vgroups.get("RCT") == 2 and "cohort" not in vgroups, str(rv))

    # mean aggregation: average yi per design
    mview = records.create_view(
        conn, title="Mean yi by design", dataset_ids=[ds["id"]],
        viz_config={"kind": "bar", "group_by": "design", "measure": "mean", "value_field": "yi"})
    rm = records.run_view(conn, mview["id"])
    means = {s["group"]: s["value"] for s in rm["series"]}
    # forestplot fixture: RCT yi=-0.42 (x2 docs), cohort yi=0.13 (x2)
    check("mean yi by design", means.get("RCT") == -0.42 and means.get("cohort") == 0.13, str(rm))
    conn.close()

    # HTTP round-trip: create + data via the API
    from fastapi.testclient import TestClient
    from paperlens.app import app
    cl = TestClient(app)
    cv = cl.post("/api/views", json={"title": "api view", "dataset_ids": [ds["id"]],
                                     "viz_config": {"group_by": "design", "measure": "count"}})
    check("POST /api/views -> 200", cv.status_code == 200)
    data = cl.get(f"/api/views/{cv.json()['id']}/data").json()
    check("GET /api/views/{id}/data -> series", data["total_records"] == 4 and len(data["series"]) == 2,
          str(data))
    check("/observatory served", cl.get("/observatory").status_code == 200)
    check("observatory.js served", cl.get("/static/observatory.js").status_code == 200)

    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


def test_views() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
