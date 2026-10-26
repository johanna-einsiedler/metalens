"""Dataset download: GET /api/datasets/{id}/export.

The old flat shape mapped every record to its ``field_values`` alone, so a row carried
neither its own id nor the paper it came from — fine for one paper, useless across forty.
This groups by paper and gives each row a record_id.

Screened papers (attempted, zero records) must appear too: stats.n_papers counts them, so
dropping them would leave the file contradicting its own stats block.

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

import uuid  # noqa: E402

import fixtures  # noqa: E402
from paperlens import app as appmod, records  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _client(sess: str):
    """A TestClient logged in as a fresh user carrying ``sess`` — downloading extracted
    data is an account feature, so every export caller needs one. Registering with the
    session header also claims that session's datasets for the new user."""
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    r = c.post("/api/auth/register",
               json={"email": f"exp-{uuid.uuid4().hex[:8]}", "password": "pw123456"},
               headers={"X-Session-Id": sess})
    assert r.status_code == 200, r.text
    return c


def _dataset(conn, sess: str, title: str):
    ds = records.create_dataset(conn, title=title, schema_id=None, session_id=sess)
    doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None,
                          source_job_id="export", session_id=sess)
    conn.execute("UPDATE record SET dataset_id = %s::uuid WHERE document_id = %s::uuid",
                 (ds["id"], doc))
    return ds, doc


def test_records_are_grouped_under_their_paper_with_ids() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-exp-a"
    ds, doc = _dataset(conn, sess, "Export A")

    out = _client(sess).get(f"/api/datasets/{ds['id']}/export",
                            headers={"X-Session-Id": sess}).json()
    assert [p["document_id"] for p in out["papers"]] == [doc]
    paper = out["papers"][0]
    assert paper["paper"]["title"] == "A meta-analysis of remote-work productivity"
    assert paper["paper"]["doi"] == "10.1037/abc.0000123"
    assert paper["paper"]["year"] == 2022
    assert len(paper["records"]) == 2

    r = paper["records"][0]
    assert r["record_id"] and r["entry_index"] == 0            # the id that was missing
    assert r["verification_status"] == "unverified"
    assert r["values"]["id"] == "Smith 2018"                   # the schema's own field
    # the whole point: every row is traceable back to a paper
    for pr in out["papers"]:
        for rec in pr["records"]:
            assert rec["record_id"]


def test_verification_status_travels_with_the_row() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-exp-b"
    ds, doc = _dataset(conn, sess, "Export B")
    rid = str(conn.execute("SELECT id FROM record WHERE document_id = %s::uuid "
                           "ORDER BY entry_index", (doc,)).fetchone()[0])
    c = _client(sess); h = {"X-Session-Id": sess}
    c.post(f"/api/records/{rid}/verify", headers=h, json={"status": "verified"})

    out = c.get(f"/api/datasets/{ds['id']}/export", headers=h).json()
    by_id = {r["record_id"]: r for p in out["papers"] for r in p["records"]}
    assert by_id[rid]["verification_status"] == "verified"
    assert sum(r["verification_status"] == "unverified" for r in by_id.values()) == 1


def test_screened_papers_are_present_and_marked() -> None:
    """stats.n_papers counts them, so the papers list has to as well."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-exp-c"
    ds, _doc = _dataset(conn, sess, "Export C")
    empty = records.persist(conn, ingest('{"studies": {"_table": []}}'), schema_id=None,
                            source_job_id="export-screened", session_id=sess)
    records.assign_document_to_dataset(conn, ds["id"], empty)

    out = _client(sess).get(f"/api/datasets/{ds['id']}/export",
                            headers={"X-Session-Id": sess}).json()
    screened = [p for p in out["papers"] if p["screened"]]
    assert len(screened) == 1 and screened[0]["records"] == []
    assert len(out["papers"]) == out["stats"]["n_papers"]      # file agrees with its stats


def test_export_carries_the_dataset_metadata() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-exp-d"
    ds, _doc = _dataset(conn, sess, "Export D")

    out = _client(sess).get(f"/api/datasets/{ds['id']}/export",
                            headers={"X-Session-Id": sess}).json()
    assert out["title"] == "Export D" and out["slug"] == ds["slug"]
    assert out["created_at"] and out["recipe"] and out["stats"] and out["credibility"]


def test_export_is_private_to_the_owner() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-exp-e"
    ds, _doc = _dataset(conn, sess, "Export E")

    # anonymous: no account needed to download your OWN data, and a stranger's stays hidden
    anon = TestClient(appmod.app).get(f"/api/datasets/{ds['id']}/export",
                                      headers={"X-Session-Id": "exp-stranger"})
    assert anon.status_code == 404
    mine = TestClient(appmod.app).get(f"/api/datasets/{ds['id']}/export", headers={"X-Session-Id": sess})
    assert mine.status_code == 200 and mine.json()["papers"]
    # logged in, but not the owner: the dataset must not even admit it exists
    r = _client("exp-stranger").get(f"/api/datasets/{ds['id']}/export",
                                    headers={"X-Session-Id": "exp-stranger"})
    assert r.status_code == 404


def test_export_carries_the_prompt_and_each_papers_provenance() -> None:
    """A dataset saved from the workspace records no prompt; its documents do. The export's recipe
    takes the prompt the documents share, every paper names its model, prompt hash and parameters,
    and when papers ran with different prompts each paper spells its own out. Imports carry none."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    import hashlib, json, tempfile
    import fitz
    from paperlens import extract, storage
    conn = records.connect(); records.init_db(conn)
    sess = f"sess-exp-{uuid.uuid4().hex[:6]}"
    ds = records.create_dataset(conn, title="Export P", schema_id=None, session_id=sess)   # no prompt on the row

    def fake(pdf, prompt, *, model="", api_key="", base_url=None, use_text=False):
        body = fixtures.FORESTPLOT_JSON if isinstance(fixtures.FORESTPLOT_JSON, str) else json.dumps(fixtures.FORESTPLOT_JSON)
        return extract.LLMResult(text=body, finish_reason="stop", usage={"prompt": 1, "completion": 1}, resolved_model="fake-1")

    def pdf(text):
        d = fitz.open(); d.new_page().insert_text((72, 90), text); b = d.tobytes(); d.close(); return b

    with tempfile.TemporaryDirectory() as tmp:
        store = storage.LocalObjectStore(root=tmp)
        run = lambda text, prompt: extract.run_extraction(conn, pdf(text), prompt=prompt, model="fake", api_key="",
                                                            session_id=sess, params={"k": 1}, complete=fake, store=store)
        a, b = run("paper a", "ask A"), run("paper b", "ask A")
        for doc in (a, b):
            records.assign_document_to_dataset(conn, ds["id"], doc["document_id"])
        conn.commit()
        sha = hashlib.sha256(b"ask A").hexdigest()
        assert records.document_view(conn, a["document_id"])["prompt"] == "ask A"
        out = records.dataset_export(conn, ds["id"])
        assert out["recipe"]["prompt"] == "ask A" and out["recipe"]["prompt_sha256"] == sha
        assert [p["provenance"] for p in out["papers"]] == [{"model": "fake", "prompt_sha256": sha, "params": {"k": 1}}] * 2

        c = run("paper c", "ask C")
        records.assign_document_to_dataset(conn, ds["id"], c["document_id"])
        imp = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None, source_job_id="imp", session_id=sess)
        conn.execute("UPDATE record SET dataset_id = %s::uuid WHERE document_id = %s::uuid", (ds["id"], imp)); conn.commit()
        out = records.dataset_export(conn, ds["id"])
        assert out["recipe"]["prompt"] is None and out["recipe"]["prompt_sha256"] is None      # no single prompt
        by = {p["document_id"]: p["provenance"] for p in out["papers"]}
        assert by[a["document_id"]]["prompt"] == "ask A" and by[c["document_id"]]["prompt"] == "ask C"
        assert by[imp] == {"model": None, "prompt_sha256": None, "params": None}
        # an import through the extraction path (a PDF with results made elsewhere) has no prompt
        # and therefore no hash — not the hash of the empty string
        pdfimp = extract.run_extraction(conn, pdf("paper d"), prompt="", model="", api_key="", session_id=sess, complete=fake, store=store)
        records.assign_document_to_dataset(conn, ds["id"], pdfimp["document_id"]); conn.commit()
        v = records.document_view(conn, pdfimp["document_id"])
        assert v["prompt"] is None and v["prompt_sha256"] is None and v["records"][0]["extraction"]["prompt_sha256"] is None
        assert {p["document_id"]: p["provenance"]["prompt_sha256"] for p in records.dataset_export(conn, ds["id"])["papers"]}[pdfimp["document_id"]] is None
        r = _client(sess).get(f"/api/datasets/{ds['id']}/export", headers={"X-Session-Id": sess}).json()
        assert r["recipe"]["prompt_sha256"] is None and len(r["papers"]) == 5

