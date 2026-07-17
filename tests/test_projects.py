"""Datasets-as-projects restructure: recipe on the dataset, computed overview,
add-papers-to-existing-dataset, filename surfacing, and owner-gating.

Two anonymous principals (alice/bob) via X-Session-Id + one via a real account
cookie. Skips cleanly without Postgres.
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
    from fastapi.testclient import TestClient
    from paperlens.app import app

    conn = records.connect(); records.init_db(conn)
    cl = TestClient(app)
    ALICE = {"X-Session-Id": "proj-alice"}
    BOB = {"X-Session-Id": "proj-bob"}
    failures = 0

    def check(label, cond, extra=""):
        nonlocal failures
        print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if not cond else ""))
        if not cond:
            failures += 1

    # ── 1. Recipe round-trips onto the dataset (API create → get) ───────────────
    created = cl.post("/api/datasets", headers=ALICE, json={
        "title": "Remote-work productivity", "visibility": "private",
        "prompt": "Extract the effect sizes.", "model": "openai:gpt-x",
        "schema_id": "forestplot@v1"}).json()
    ds_id = created["id"]
    got = cl.get(f"/api/datasets/{ds_id}", headers=ALICE).json()
    check("recipe round-trips (prompt)", got.get("prompt") == "Extract the effect sizes.", str(got.get("prompt")))
    check("recipe round-trips (model)", got.get("model") == "openai:gpt-x", str(got.get("model")))
    check("recipe round-trips (schema)", got.get("schema_id") == "forestplot@v1", str(got.get("schema_id")))

    # ── 2. Overview stats over two docs (2 records each) with filenames ─────────
    doc_a = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id="forestplot@v1",
                            session_id="proj-alice", source_job_id="proj-a", filename="alpha.pdf")
    # a distinct paper (different DOI) so n_papers counts 2
    json_b = fixtures.FORESTPLOT_JSON.replace("10.1037/abc.0000123", "10.1037/abc.0000999")
    doc_b = records.persist(conn, ingest(json_b), schema_id="forestplot@v1",
                            session_id="proj-alice", source_job_id="proj-b", filename="beta.pdf")
    records.assign_document_to_dataset(conn, ds_id, doc_a)
    records.assign_document_to_dataset(conn, ds_id, doc_b)

    ov = cl.get(f"/api/datasets/{ds_id}/overview", headers=ALICE).json()
    s = ov["stats"]
    check("overview n_records == 4", s["n_records"] == 4, str(s["n_records"]))
    check("overview n_papers == 2", s["n_papers"] == 2, str(s["n_papers"]))
    check("overview lists both documents", len(ov["documents"]) == 2, str(len(ov["documents"])))
    check("overview credibility badge present", bool(ov["credibility"].get("label")))
    check("overview first/last extracted set", bool(s["first_extracted"]) and bool(s["last_extracted"]))
    check("overview recipe echoes model", ov["recipe"]["model"] == "openai:gpt-x")

    # ── 3. Filename surfaces in documents listing + document view ───────────────
    dl = cl.get("/api/documents", headers=ALICE, params={"dataset": ds_id}).json()["documents"]
    names = {d.get("filename") for d in dl}
    check("filename surfaces in documents?dataset", {"alpha.pdf", "beta.pdf"} <= names, str(names))
    dvv = cl.get(f"/api/documents/{doc_a}/view", headers=ALICE).json()
    check("filename surfaces in document view", dvv.get("filename") == "alpha.pdf", str(dvv.get("filename")))

    # ── 4. Verifying a record bumps n_verified / verified_pct + last_change ──────
    rid = str(conn.execute("SELECT id FROM record WHERE document_id = %s::uuid LIMIT 1",
                           (doc_a,)).fetchone()[0])
    vr = cl.post(f"/api/records/{rid}/verify", headers=ALICE, json={"status": "verified"})
    check("verify 200", vr.status_code == 200, str(vr.status_code))
    ov2 = cl.get(f"/api/datasets/{ds_id}/overview", headers=ALICE).json()
    check("overview n_verified bumped", ov2["stats"]["n_verified"] == 1, str(ov2["stats"]["n_verified"]))
    check("overview verified_pct bumped", ov2["stats"]["verified_pct"] == 25.0, str(ov2["stats"]["verified_pct"]))
    check("overview last_change set", bool(ov2["stats"]["last_change"]))

    # ── 5. Add-papers to the EXISTING dataset — no new dataset created ───────────
    before = len(cl.get("/api/datasets", headers=ALICE).json()["datasets"])
    doc_c = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON.replace("0000123", "0000777")),
                            schema_id="forestplot@v1", session_id="proj-alice",
                            source_job_id="proj-c", filename="gamma.pdf")
    add = cl.post(f"/api/datasets/{ds_id}/add", headers=ALICE, json={"document_id": doc_c})
    check("add-papers 200", add.status_code == 200, str(add.status_code))
    after = len(cl.get("/api/datasets", headers=ALICE).json()["datasets"])
    check("add-papers creates NO new dataset", after == before, f"{before}->{after}")
    ov3 = cl.get(f"/api/datasets/{ds_id}/overview", headers=ALICE).json()
    check("add-papers grows n_records to 6", ov3["stats"]["n_records"] == 6, str(ov3["stats"]["n_records"]))

    # ── 6. Owner-gating on the overview (private → stranger 404) ─────────────────
    check("overview: owner 200", cl.get(f"/api/datasets/{ds_id}/overview", headers=ALICE).status_code == 200)
    check("overview: stranger 404 on private",
          cl.get(f"/api/datasets/{ds_id}/overview", headers=BOB).status_code == 404)
    cl.patch(f"/api/datasets/{ds_id}", headers=ALICE, json={"visibility": "public"})
    bov = cl.get(f"/api/datasets/{ds_id}/overview", headers=BOB)
    check("overview: stranger 200 after publish", bov.status_code == 200, str(bov.status_code))
    check("overview: stranger gets NO filenames (no local-name leak)",
          all(d.get("filename") is None for d in bov.json()["documents"]))

    # ── 7. Back-compat: persist without filename still works (filename NULL) ─────
    doc_nofn = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON.replace("0000123", "0000555")),
                               schema_id="forestplot@v1", session_id="proj-alice", source_job_id="proj-nofn")
    ld = records.list_documents(conn, dataset_id=None, owner_user_id=None, session_id="proj-alice")
    row = next((d for d in ld if d["document_id"] == doc_nofn), None)
    check("back-compat persist without filename", row is not None and row.get("filename") is None,
          str(row.get("filename") if row else None))

    # ── 8. Add / delete findings (records) — owner only ─────────────────────────
    n0 = cl.get(f"/api/datasets/{ds_id}/overview", headers=ALICE).json()["stats"]["n_records"]
    added = cl.post(f"/api/documents/{doc_a}/records", headers=ALICE, json={"field_values": {"design": "RCT"}})
    check("add finding → 200", added.status_code == 200, str(added.status_code))
    new_rid = added.json()["id"]
    n1 = cl.get(f"/api/datasets/{ds_id}/overview", headers=ALICE).json()["stats"]["n_records"]
    check("add finding grows n_records (inherits dataset)", n1 == n0 + 1, f"{n0}->{n1}")
    check("stranger cannot add finding → 403",
          cl.post(f"/api/documents/{doc_a}/records", headers=BOB, json={}).status_code == 403)
    check("stranger cannot delete finding → 403",
          cl.delete(f"/api/records/{new_rid}", headers=BOB).status_code == 403)
    check("owner deletes finding → 200", cl.delete(f"/api/records/{new_rid}", headers=ALICE).status_code == 200)
    n2 = cl.get(f"/api/datasets/{ds_id}/overview", headers=ALICE).json()["stats"]["n_records"]
    check("delete finding shrinks n_records", n2 == n0, f"{n0}->{n2}")

    conn.close()
    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


def test_projects() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
