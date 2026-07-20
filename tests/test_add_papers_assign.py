"""Add-papers: POST /api/extract with a ``dataset_id`` attaches the finished document to
that dataset server-side (so queued AND sync extractions land in it), and rejects callers
who don't own the dataset. Skips without Postgres."""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import app as appmod, extract, records  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _pdf():
    import fitz
    d = fitz.open(); d.new_page().insert_text((72, 90), "add-papers assign"); b = d.tobytes(); d.close()
    return b


def _fake_complete(pdf, prompt, *, model="", api_key="", base_url=None, use_text=False):
    return extract.LLMResult(text=json.dumps({
        "paper_metadata": {"title": "Assigned Paper"},
        "records": [{"AI_Type": "Deep", "Perf_Metric": "Accuracy"}],
        "evidence": [],
    }), finish_reason="stop", usage={"total": 1}, resolved_model="fake")


def _force_sync_with_fake_llm(monkeypatch):
    # no Redis → sync path; inject a fake LLM so run_extraction needs no real API key
    monkeypatch.setattr(appmod.worker, "enqueue", lambda *a, **k: None)
    real = extract.run_extraction
    monkeypatch.setattr(appmod.extract, "run_extraction",
                        lambda c, pdf, prompt, **kw: real(c, pdf, prompt, complete=_fake_complete, **kw))


def test_extract_with_dataset_id_assigns_to_it(monkeypatch) -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-addpapers"
    ds = records.create_dataset(conn, title="AP DS", schema_id="human-ai-collab@v1", session_id=sess)
    conn.commit()
    _force_sync_with_fake_llm(monkeypatch)

    r = TestClient(appmod.app).post(
        "/api/extract", headers={"X-Session-Id": sess},
        files={"pdf": ("p.pdf", _pdf(), "application/pdf")},
        data={"prompt": "x", "schema_id": "human-ai-collab@v1", "dataset_id": ds["id"]})
    assert r.status_code == 200, r.text
    doc_id = r.json()["document_id"]

    conn.rollback()   # refresh snapshot to see the request's committed writes
    n = conn.execute("SELECT count(*) FROM record WHERE document_id=%s::uuid AND dataset_id=%s::uuid",
                     (doc_id, ds["id"])).fetchone()[0]
    assert n >= 1                                   # the new records are in the dataset
    docs = records.list_documents(conn, session_id=sess, dataset_id=ds["id"])
    assert any(d["document_id"] == doc_id for d in docs)   # shows under the dataset view


def test_dataset_never_holds_same_paper_twice(monkeypatch) -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-dupguard"
    ds = records.create_dataset(conn, title="Dup DS", schema_id="human-ai-collab@v1", session_id=sess)
    conn.commit()
    _force_sync_with_fake_llm(monkeypatch)
    client = TestClient(appmod.app)
    pdf = _pdf()

    def add():
        return client.post("/api/extract", headers={"X-Session-Id": sess},
                           files={"pdf": ("p.pdf", pdf, "application/pdf")},
                           data={"prompt": "x", "schema_id": "human-ai-collab@v1", "dataset_id": ds["id"]})

    assert add().status_code == 200
    assert add().status_code == 200          # same PDF again (e.g. re-upload while the 1st was in flight)

    conn.rollback()
    # both extractions persisted (they live in the library), but the dataset holds the paper ONCE
    n_docs = conn.execute("SELECT count(*) FROM extraction_document WHERE session_id=%s", (sess,)).fetchone()[0]
    assert n_docs == 2
    in_dataset = records.list_documents(conn, session_id=sess, dataset_id=ds["id"])
    assert len(in_dataset) == 1


def test_extract_rejects_non_owner_dataset(monkeypatch) -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    ds = records.create_dataset(conn, title="Owned DS", schema_id="human-ai-collab@v1",
                                session_id="owner-sess")
    conn.commit()
    _force_sync_with_fake_llm(monkeypatch)

    r = TestClient(appmod.app).post(
        "/api/extract", headers={"X-Session-Id": "intruder-sess"},
        files={"pdf": ("p.pdf", _pdf(), "application/pdf")},
        data={"prompt": "x", "schema_id": "human-ai-collab@v1", "dataset_id": ds["id"]})
    assert r.status_code == 403                     # not the dataset owner
