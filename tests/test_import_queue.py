"""A PDF import can be queued. ``POST /api/ingest-pdf`` with ``queue=1`` validates the JSON and
mints the schema row at once, then hands the rendering to the worker and answers with a job id;
the worker's ``ingest_pdf_job`` builds the same document the synchronous path does and attaches
it to the dataset; with the queue down the request imports synchronously, as it always has; and
a malformed import is refused before anything is queued."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402
from paperlens import app as appmod, records, worker  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _pdf() -> bytes:
    import fitz
    pdf = fitz.open(); pdf.new_page().insert_text((72, 72), "Remote work productivity 0.42")
    return pdf.tobytes()


def _client():
    from fastapi.testclient import TestClient
    sess = f"sess-import-q-{uuid.uuid4().hex[:6]}"
    return TestClient(appmod.app), {"X-Session-Id": sess}, sess


def test_queued_import_answers_with_a_job_and_the_worker_builds_the_document(monkeypatch) -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    c, h, sess = _client()
    conn = records.connect(); records.init_db(conn)
    ds = records.create_dataset(conn, title="Queued import", schema_id=None, session_id=sess)
    captured = {}

    def fake_enqueue(name, *args, **kwargs):
        captured.update(name=name, args=args, kwargs=kwargs)
        return "job-import-test"
    monkeypatch.setattr(appmod.worker, "enqueue", fake_enqueue)

    r = c.post("/api/ingest-pdf", files={"pdf": ("goh.pdf", _pdf(), "application/pdf")},
               data={"result": fixtures.FORESTPLOT_JSON, "dataset_id": ds["id"], "queue": "1",
                     "produced_by": "an agent pipeline"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"queued": True, "job_id": "job-import-test", "schema_id": body["schema_id"]}
    assert captured["name"] == "ingest_pdf_job"
    kw = captured["kwargs"]
    assert kw["dataset_id"] == ds["id"] and kw["session_id"] == sess and kw["filename"] == "goh.pdf"
    assert kw["made_by"] == "an agent pipeline"
    # the web process rendered nothing: no document yet
    assert conn.execute("SELECT count(*) FROM record WHERE dataset_id = %s::uuid", (ds["id"],)).fetchone()[0] == 0

    # the worker's job, run here: the same document the synchronous path would have built
    job_kwargs = {k: v for k, v in kw.items() if not k.startswith("_")}
    res = asyncio.run(worker.ingest_pdf_job({"job_id": "job-import-test", "job_try": 1},
                                            *captured["args"], **job_kwargs))
    assert res["n_pages"] == 1 and res["n_records"] >= 1 and res["document_id"]
    assert conn.execute("SELECT count(*) FROM record WHERE document_id = %s::uuid AND dataset_id = %s::uuid",
                        (res["document_id"], ds["id"])).fetchone()[0] == res["n_records"]
    assert conn.execute("SELECT filename FROM extraction_document WHERE id = %s::uuid",
                        (res["document_id"],)).fetchone()[0] == "goh.pdf"
    records.delete_document(conn, res["document_id"])
    conn.close()


def test_queue_down_imports_synchronously(monkeypatch) -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    c, h, _ = _client()
    monkeypatch.setattr(appmod.worker, "enqueue", lambda *a, **k: None)
    r = c.post("/api/ingest-pdf", files={"pdf": ("goh.pdf", _pdf(), "application/pdf")},
               data={"result": fixtures.FORESTPLOT_JSON, "queue": "1"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] is False and body["n_pages"] == 1 and body["n_records"] >= 1
    conn = records.connect(); records.delete_document(conn, body["document_id"]); conn.close()


def test_malformed_json_is_refused_before_anything_is_queued(monkeypatch) -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    c, h, _ = _client()

    def never(*a, **k):
        raise AssertionError("a malformed import must not reach the queue")
    monkeypatch.setattr(appmod.worker, "enqueue", never)
    for bad in ("not json at all", json.dumps([1, 2, 3])):
        r = c.post("/api/ingest-pdf", files={"pdf": ("goh.pdf", _pdf(), "application/pdf")},
                   data={"result": bad, "queue": "1"}, headers=h)
        assert r.status_code == 422, (bad, r.text)
