"""Duplicate detection is scoped to papers that are LIVE in a dataset: a paper is flagged
"already extracted" only while its real records belong to an existing dataset. After the
dataset is deleted (un-published) it must NOT be flagged, so a from-scratch rebuild is
clean. Skips without Postgres."""
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


def _make_pdf():
    import fitz
    d = fitz.open(); d.new_page().insert_text((72, 90), "dedup lifecycle paper"); b = d.tobytes(); d.close()
    return b


def _one(pdf, prompt, *, model="", api_key="", base_url=None, use_text=False):
    return extract.LLMResult(text=json.dumps({
        "paper_metadata": {"title": "Dedup Paper", "doi": "10.1/dedup"},
        "records": [{"AI_Type": "Deep", "Perf_Metric": "Accuracy", "Avg_Perf_HumanAI": 0.7}],
        "evidence": [{"snippet": "Deep", "page": 1, "source": "-", "field": "records[0].AI_Type"}],
    }), finish_reason="stop", usage={"total": 5}, resolved_model="fake")


def _hashes(doc_sha):
    return [doc_sha]


def test_duplicate_flagged_only_while_in_live_dataset() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    schema_id = "human-ai-collab@v1"
    with tempfile.TemporaryDirectory() as d:
        out = extract.run_extraction(conn, _make_pdf(), prompt="x", model="gpt-4o", api_key="",
                                     schema_id=schema_id, session_id="sess-dedup",
                                     complete=_one, store=storage.LocalObjectStore(root=d))
        conn.commit()
    doc_id = out["document_id"]
    sha = conn.execute("SELECT pdf_sha256 FROM extraction_document WHERE id=%s::uuid",
                       (doc_id,)).fetchone()[0]

    def check():
        return records.documents_by_hashes(conn, [sha], session_id="sess-dedup", schema_id=schema_id)

    # freshly extracted, not in any dataset yet → records are private (dataset_id NULL) → NOT flagged
    assert check() == {}

    # save it into a dataset → now it IS a live duplicate
    ds = records.create_dataset(conn, title="DS", schema_id=schema_id, session_id="sess-dedup")
    records.assign_document_to_dataset(conn, ds["id"], doc_id)
    conn.commit()
    hit = check()
    assert sha in hit and hit[sha][0]["document_id"] == doc_id and hit[sha][0]["n_records"] == 1

    # a DIFFERENT preset is never a duplicate of this one
    assert records.documents_by_hashes(conn, [sha], session_id="sess-dedup",
                                        schema_id="some-other@v1") == {}

    # delete the dataset → its records are discarded → NOT flagged → clean rebuild
    records.delete_dataset(conn, ds["id"])
    conn.commit()
    assert check() == {}
    assert conn.execute("SELECT count(*) FROM record WHERE document_id=%s::uuid",
                        (doc_id,)).fetchone()[0] == 0     # records gone
    # the document + its cached PDF survive (still re-extractable / listable)
    assert conn.execute("SELECT 1 FROM extraction_document WHERE id=%s::uuid",
                        (doc_id,)).fetchone() is not None


def test_screened_only_paper_not_flagged() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    schema_id = "human-ai-collab@v1"
    with tempfile.TemporaryDirectory() as d:
        # extraction that yields ZERO records
        def _empty(pdf, prompt, **kw):
            return extract.LLMResult(text=json.dumps({"paper_metadata": {"title": "Empty"}, "records": [],
                                     "evidence": []}), finish_reason="stop", usage={}, resolved_model="fake")
        out = extract.run_extraction(conn, _make_pdf(), prompt="x", model="gpt-4o", api_key="",
                                     schema_id=schema_id, session_id="sess-scr",
                                     complete=_empty, store=storage.LocalObjectStore(root=d))
        conn.commit()
    doc_id = out["document_id"]
    sha = conn.execute("SELECT pdf_sha256 FROM extraction_document WHERE id=%s::uuid",
                       (doc_id,)).fetchone()[0]
    ds = records.create_dataset(conn, title="DS2", schema_id=schema_id, session_id="sess-scr")
    records.assign_document_to_dataset(conn, ds["id"], doc_id)   # inserts a screened_empty sentinel
    conn.commit()
    # a paper with only a screened (0-record) sentinel in the dataset is NOT a real extraction
    assert records.documents_by_hashes(conn, [sha], session_id="sess-scr", schema_id=schema_id) == {}
