"""Authorization / private-isolation tests for the private-storage hardening.

Two anonymous principals (alice/bob) via the X-Session-Id header. Verifies:
documents/records are owner-only, the catalogue returns only public records,
cross-user dataset injection + un-owned publishing are blocked, deletion removes
DB rows + storage blobs, and the gated /artifacts route. Skips without Postgres.
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
    from paperlens import records, storage
    from paperlens.ingest import ingest
    from fastapi.testclient import TestClient
    from paperlens.app import app

    conn = records.connect(); records.init_db(conn)
    cl = TestClient(app)
    ALICE = {"X-Session-Id": "authz-alice"}
    BOB = {"X-Session-Id": "authz-bob"}
    failures = 0

    def check(label, cond, extra=""):
        nonlocal failures
        print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if not cond else ""))
        if not cond:
            failures += 1

    # Alice extracts a document (persist directly, owned by her session) + a blob.
    doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id="forestplot@v1",
                          session_id="authz-alice", source_job_id="authz")
    store = storage.get_store()
    store.put(storage.pdf_key(doc), b"%PDF-fake", "application/pdf")
    store.put(storage.page_image_key(doc, 1), b"fake-jpeg-bytes", "image/jpeg")
    rec_ids = [str(r[0]) for r in conn.execute(
        "SELECT id FROM record WHERE document_id = %s::uuid", (doc,)).fetchall()]

    # 1. document_view is owner-only (the bright wall).
    check("doc view: owner 200", cl.get(f"/api/documents/{doc}/view", headers=ALICE).status_code == 200)
    check("doc view: stranger 404", cl.get(f"/api/documents/{doc}/view", headers=BOB).status_code == 404)
    check("doc view: anon 404", cl.get(f"/api/documents/{doc}/view").status_code == 404)

    # 2. record_detail: private record → owner only.
    rid = rec_ids[0]
    check("record (private): owner 200", cl.get(f"/api/records/{rid}", headers=ALICE).status_code == 200)
    check("record (private): stranger 404", cl.get(f"/api/records/{rid}", headers=BOB).status_code == 404)

    # 3. Catalogue returns ONLY public records. A private dataset never leaks.
    pub_ds = records.create_dataset(conn, title="authz public", session_id="authz-alice", visibility="public")
    records.assign_document_to_dataset(conn, pub_ds["id"], doc)            # doc's records now public
    doc2 = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id="forestplot@v1",
                           session_id="authz-alice", source_job_id="authz2")
    priv_ds = records.create_dataset(conn, title="authz private", session_id="authz-alice", visibility="private")
    records.assign_document_to_dataset(conn, priv_ds["id"], doc2)          # private dataset

    sp = cl.get("/api/search", params={"q": "cohort", "dataset": pub_ds["id"]}).json()
    check("search public dataset returns records", sp["total"] >= 1, str(sp["total"]))
    spv = cl.get("/api/search", params={"q": "cohort", "dataset": priv_ds["id"]}).json()
    check("search private dataset returns NOTHING", spv["total"] == 0, str(spv["total"]))
    ag = cl.post("/api/aggregate", json={"filters": {"dataset": [priv_ds["id"]]},
                                         "group_by": "design", "measure": "count"}).json()
    check("aggregate private dataset → 0 records", ag["total_records"] == 0, str(ag["total_records"]))
    # a now-public record IS visible in record_detail to anyone
    pub_rid = rec_ids[0]
    check("record (public): stranger 200", cl.get(f"/api/records/{pub_rid}", headers=BOB).status_code == 200)

    # 4. Cross-user dataset injection is blocked.
    bob_ds = records.create_dataset(conn, title="bob ds", session_id="authz-bob", visibility="private")
    inj = cl.post(f"/api/datasets/{bob_ds['id']}/add", json={"document_id": doc2}, headers=BOB)
    check("cross-user inject (alice's doc → bob's ds) blocked 403", inj.status_code == 403, str(inj.status_code))
    own = cl.post(f"/api/datasets/{bob_ds['id']}/add", json={"document_id": doc2}, headers=ALICE)
    check("non-owner of dataset blocked 403", own.status_code == 403, str(own.status_code))

    # 5. Publishing: own records OK; a dataset holding un-owned records → 403.
    okp = cl.patch(f"/api/datasets/{priv_ds['id']}", json={"visibility": "public"}, headers=ALICE)
    check("publish own dataset 200", okp.status_code == 200, str(okp.status_code))
    records.assign_document_to_dataset(conn, bob_ds["id"], doc2)           # alice's records into bob's ds (direct)
    badp = cl.patch(f"/api/datasets/{bob_ds['id']}", json={"visibility": "public"}, headers=BOB)
    check("publish un-owned records blocked 403", badp.status_code == 403, str(badp.status_code))

    # 6. Gated /artifacts: owner via signed url 200, stranger on bare url 403.
    dv = cl.get(f"/api/documents/{doc}/view", headers=ALICE).json()
    if dv.get("pages"):
        purl = dv["pages"][0]["url"]
        check("owner fetches page via signed url", cl.get(purl).status_code == 200, purl)
        check("stranger blocked on bare artifact url",
              cl.get(purl.split("?")[0], headers=BOB).status_code == 403)

    # 7. Public examples + catalogue still work.
    pubs = cl.get("/api/datasets/public").json()["datasets"]
    check("public datasets feed non-empty", len(pubs) >= 1, str(len(pubs)))

    # 8. Deletion (do last — destroys `doc`): owner removes rows + blobs; stranger 403.
    check("delete: stranger 403", cl.delete(f"/api/documents/{doc}", headers=BOB).status_code == 403)
    delr = cl.delete(f"/api/documents/{doc}", headers=ALICE)
    check("delete: owner 200", delr.status_code == 200, str(delr.status_code))
    gone = conn.execute("SELECT count(*) FROM record WHERE document_id = %s::uuid", (doc,)).fetchone()[0]
    check("delete cascades records", gone == 0, str(gone))
    check("delete removes PDF blob", not store.exists(storage.pdf_key(doc)))
    check("delete removes page blob", not store.exists(storage.page_image_key(doc, 1)))

    # 9. Account deletion removes the user's documents + blobs + the account row.
    import uuid
    email = f"authz-del-{uuid.uuid4().hex[:8]}@test.dev"
    reg = cl.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    check("register 200", reg.status_code == 200, str(reg.status_code))
    uid = reg.json()["user"]["id"]
    udoc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id="forestplot@v1",
                           owner_user_id=uid, source_job_id="authz-del")
    store.put(storage.pdf_key(udoc), b"%PDF", "application/pdf")
    check("owner (cookie) views own doc", cl.get(f"/api/documents/{udoc}/view").status_code == 200)
    dr = cl.delete("/api/auth/me")                      # TestClient carries the session cookie
    check("delete account 200", dr.status_code == 200, str(dr.status_code))
    ug = conn.execute("SELECT count(*) FROM extraction_document WHERE id = %s::uuid", (udoc,)).fetchone()[0]
    check("account deletion removed the document", ug == 0, str(ug))
    check("account deletion removed the blob", not store.exists(storage.pdf_key(udoc)))
    urow = conn.execute("SELECT count(*) FROM users WHERE id = %s::uuid", (uid,)).fetchone()[0]
    check("account deletion removed the user row", urow == 0, str(urow))
    cl.post("/api/auth/logout")                         # clear the (now-invalid) cookie for cleanliness

    conn.close()
    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


def test_authz() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
