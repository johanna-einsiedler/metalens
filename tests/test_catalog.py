"""Catalog query layer — search, facets, public datasets+badges, record detail,
paper search, and aggregate (incl. run_view parity). Skips without Postgres.
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
    records.init_db(conn)            # applies the field_values_tsv generated column
    failures = 0

    def check(label, cond, extra=""):
        nonlocal failures
        print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if not cond else ""))
        if not cond:
            failures += 1

    # seed: a public dataset of forestplot studies (paper year 2022, designs RCT/cohort)
    doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON),
                          schema_id="forestplot@v1", source_job_id="cat-test")
    ds = records.create_dataset(conn, title="catalog demo", schema_id="forestplot@v1",
                                session_id="cat-sess", visibility="public")
    records.assign_document_to_dataset(conn, ds["id"], doc)
    recs = records.dataset_records(conn, ds["id"])
    check("seeded 2 records", len(recs) == 2)

    # 1) FTS search hits the GIN index and finds the right record
    res = records.search_records(conn, {"q": "Smith", "dataset": [ds["id"]]})
    check("search q=Smith -> the RCT study",
          res["total"] >= 1 and any(r["field_values"].get("id") == "Smith 2018"
                                    for r in res["results"]), str(res["total"]))
    # The GIN index exists and is USABLE for the @@ predicate. (On a tiny table the
    # planner rightly prefers a seq scan, so disable it to prove the index is wired.)
    conn.execute("SET enable_seqscan = off")
    plan = conn.execute(
        "EXPLAIN SELECT 1 FROM record r WHERE r.field_values_tsv @@ "
        "websearch_to_tsquery('english', 'Smith')").fetchall()
    conn.execute("SET enable_seqscan = on")
    check("FTS GIN index (record_fts_col_idx) is usable",
          any("record_fts_col_idx" in row[0] for row in plan),
          "\n".join(row[0] for row in plan))

    # schema filter
    check("search schema filter -> 2",
          records.search_records(conn, {"schema": "forestplot@v1", "dataset": [ds["id"]]})["total"] == 2)

    # 2) facets
    f = records.facets(conn, {"dataset": [ds["id"]]})
    schema_counts = {x["value"]: x["count"] for x in f["schema"]}
    year_counts = {x["value"]: x["count"] for x in f["year"]}
    check("facet schema -> forestplot@v1:2", schema_counts.get("forestplot@v1") == 2, str(f["schema"]))
    check("facet year -> 2022:2 (paper year)", year_counts.get(2022) == 2, str(f["year"]))

    # verify one record, then status facet + status filter reflect it
    records.verify_record(conn, recs[0]["id"], status="verified")
    f2 = records.facets(conn, {"dataset": [ds["id"]]})
    st = {x["value"]: x["count"] for x in f2["verification_status"]}
    check("facet status reflects verify", st.get("verified") == 1 and st.get("unverified") == 1, str(st))
    check("status filter -> 1 verified",
          records.search_records(conn, {"status": "verified", "dataset": [ds["id"]]})["total"] == 1)

    # 3) public datasets + badge == per-dataset credibility (parity, no drift)
    pub = records.public_datasets_with_badges(conn)
    mine = next((d for d in pub if d["id"] == ds["id"]), None)
    cred = records.dataset_credibility(conn, ds["id"])
    check("public dataset present with badge", mine is not None)
    check("batch badge == dataset_credibility",
          mine and mine["credibility"]["tier"] == cred["tier"]
          and mine["credibility"]["audited"] == cred["audited"]
          and mine["credibility"]["agreement"] == cred["agreement"], str(mine))

    # record detail
    rd = records.record_detail(conn, recs[0]["id"])
    check("record_detail -> record+paper+evidence+events",
          rd and rd["record"]["id"] == recs[0]["id"] and rd["paper"]["doi"] == "10.1037/abc.0000123"
          and isinstance(rd["evidence"], list) and len(rd["events"]) == 1, str(bool(rd)))

    # 4) paper search (title)
    ps = records.papers_search(conn, q="meta-analysis")
    check("paper search by title", ps["total"] >= 1 and any("meta-analysis" in (p["title"] or "").lower()
                                                            for p in ps["papers"]), str(ps["total"]))

    # 5) aggregate over paper column + run_view parity
    agg = records.aggregate(conn, filters={"dataset": [ds["id"]]}, group_by="paper.year")
    check("aggregate by paper.year -> 2022:2",
          agg["total_records"] == 2 and agg["series"][0] == {"group": "2022", "value": 2, "n": 2}, str(agg))
    view = records.create_view(conn, title="design", dataset_ids=[ds["id"]],
                               viz_config={"group_by": "design", "measure": "count"})
    rv = records.run_view(conn, view["id"])
    groups = {s["group"]: s["value"] for s in rv["series"]}
    check("run_view still works after refactor (RCT:1, cohort:1)",
          rv["total_records"] == 2 and groups.get("RCT") == 1 and groups.get("cohort") == 1
          and set(rv.keys()) >= {"view_id", "title", "kind", "group_by", "measure",
                                 "value_field", "total_records", "series"}, str(rv))
    conn.close()

    # HTTP smoke
    from fastapi.testclient import TestClient
    from paperlens.app import app
    c = TestClient(app)
    check("GET /api/search", c.get("/api/search", params={"q": "Smith", "dataset": ds["id"]}).json()["total"] >= 1)
    check("GET /api/datasets/public", any(d["id"] == ds["id"]
          for d in c.get("/api/datasets/public").json()["datasets"]))
    check("GET /api/records/{id}", c.get(f"/api/records/{recs[0]['id']}").status_code == 200)
    check("GET /api/records/{id} 404", c.get("/api/records/00000000-0000-0000-0000-000000000000").status_code == 404)
    check("POST /api/aggregate", c.post("/api/aggregate", json={"filters": {"dataset": [ds["id"]]},
          "group_by": "paper.year"}).json()["total_records"] == 2)

    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


def test_catalog() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
