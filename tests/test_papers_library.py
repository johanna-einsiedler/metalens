"""The "All my papers" library: records.list_papers dedups by content hash, counts
extractions, and lists the live datasets a paper is in; records.delete_paper removes every
extraction of a PDF. Skips without Postgres."""
from __future__ import annotations

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import extract, records, storage  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _pdf(text: str):
    import fitz
    d = fitz.open(); d.new_page().insert_text((72, 90), text); b = d.tobytes(); d.close()
    return b


def _complete(pdf, prompt, *, model="", api_key="", base_url=None, use_text=False):
    return extract.LLMResult(text=json.dumps({
        "paper_metadata": {"title": "Lib Paper"},
        "records": [{"AI_Type": "Deep", "Perf_Metric": "Accuracy", "Avg_Perf_HumanAI": 0.6}],
        "evidence": [],
    }), finish_reason="stop", usage={"total": 5}, resolved_model="fake")


def _extract(conn, pdf_bytes, session_id, store):
    return extract.run_extraction(conn, pdf_bytes, prompt="x", model="gpt-4o", api_key="",
                                  schema_id="human-ai-collab@v1", session_id=session_id,
                                  complete=_complete, store=store)


def _sha(conn, doc_id):
    return conn.execute("SELECT pdf_sha256 FROM extraction_document WHERE id=%s::uuid",
                        (doc_id,)).fetchone()[0]


def test_library_dedup_and_datasets() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    sess = "sess-lib"
    pdf_a, pdf_b = _pdf("library paper A"), _pdf("library paper B")
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        out_a = _extract(conn, pdf_a, sess, store)
        _extract(conn, pdf_b, sess, store)
        conn.commit()
        sha_a = _sha(conn, out_a["document_id"])

        # two distinct PDFs → two library entries; no dataset membership yet
        papers = records.list_papers(conn, session_id=sess)
        assert len(papers) == 2
        assert all(p["n_extractions"] == 1 and p["datasets"] == [] for p in papers)

        # re-extract A (same bytes → same sha) → still ONE entry for A, now 2 extractions
        _extract(conn, pdf_a, sess, store)
        conn.commit()
        lib = {p["pdf_sha256"]: p for p in records.list_papers(conn, session_id=sess)}
        assert len(lib) == 2 and lib[sha_a]["n_extractions"] == 2

        # save an extraction of A into a dataset → the paper lists that live dataset
        ds = records.create_dataset(conn, title="LibDS", schema_id="human-ai-collab@v1", session_id=sess)
        records.assign_document_to_dataset(conn, ds["id"], out_a["document_id"])
        conn.commit()
        lib = {p["pdf_sha256"]: p for p in records.list_papers(conn, session_id=sess)}
        names = [x["title"] for x in lib[sha_a]["datasets"]]
        assert names == ["LibDS"]

        # deleting the dataset detaches it — paper stays in the library, now in no dataset
        records.delete_dataset(conn, ds["id"])
        conn.commit()
        lib = {p["pdf_sha256"]: p for p in records.list_papers(conn, session_id=sess)}
        assert sha_a in lib and lib[sha_a]["datasets"] == []


def test_delete_paper_removes_every_extraction() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    sess = "sess-del"
    pdf = _pdf("delete-me paper")
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        a = _extract(conn, pdf, sess, store)
        _extract(conn, pdf, sess, store)          # a second extraction of the same PDF
        conn.commit()
        sha = _sha(conn, a["document_id"])

    assert len(records.list_papers(conn, session_id=sess)) == 1
    n = records.delete_paper(conn, sha, session_id=sess)
    conn.commit()
    assert n == 2                                  # both extractions removed
    assert records.list_papers(conn, session_id=sess) == []
    assert conn.execute("SELECT count(*) FROM extraction_document WHERE pdf_sha256=%s",
                        (sha,)).fetchone()[0] == 0

    # scoping: another principal's identical PDF is untouched by this caller's delete
    with tempfile.TemporaryDirectory() as d:
        other = _extract(conn, _pdf("delete-me paper"), "sess-other", storage.LocalObjectStore(root=d))
        conn.commit()
    osha = _sha(conn, other["document_id"])
    assert records.delete_paper(conn, osha, session_id=sess) == 0   # not mine → nothing deleted
    conn.commit()
    assert conn.execute("SELECT count(*) FROM extraction_document WHERE id=%s::uuid",
                        (other["document_id"],)).fetchone()[0] == 1
