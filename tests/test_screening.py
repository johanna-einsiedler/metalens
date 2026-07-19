"""0-record "screened" sentinel (feature 1) + duplicate detection by content hash
(feature 2). Skips without Postgres."""
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

from paperlens import extract, parsed, records, storage  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _make_pdf(text="human ai collaboration study"):
    import fitz
    d = fitz.open(); d.new_page().insert_text((72, 90), text); b = d.tobytes(); d.close()
    return b


def _empty(pdf, prompt, *, model="", api_key="", base_url=None, use_text=False):
    # a valid extraction that yields ZERO records (paper didn't fit the preset)
    return extract.LLMResult(text=json.dumps({
        "paper_metadata": {"title": "Screened Paper", "doi": "10.1/screen"},
        "records": [],
        "evidence": [{"snippet": "not a collab experiment", "page": 1, "source": "-", "field": "records"}],
    }), finish_reason="stop", usage={"total": 3}, resolved_model="fake")


def test_screened_sentinel_and_dedup() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres available")
    conn = records.connect(); records.init_db(conn)
    pdf = _make_pdf()
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        out = extract.run_extraction(conn, pdf, prompt="x", model="gpt-4o", api_key="",
                                     schema_id="human-ai-collab@v1", session_id="sess-scr",
                                     complete=_empty, store=store)
        conn.commit()
    doc_id = out["document_id"]

    # 0-record doc → no data rows to begin with
    v0 = records.document_view(conn, doc_id)
    assert [r for r in v0["records"] if not r["screened_empty"]] == []

    # saving it to a dataset mints ONE "screened, no records" sentinel (idempotent)
    ds = records.create_dataset(conn, title="Screen DS", schema_id="human-ai-collab@v1",
                                session_id="sess-scr", visibility="private", model="gpt-4o")
    assert records.assign_document_to_dataset(conn, ds["id"], doc_id) == 1
    records.assign_document_to_dataset(conn, ds["id"], doc_id)      # must NOT add a 2nd
    conn.commit()

    sentinels = [r for r in records.document_view(conn, doc_id)["records"] if r["screened_empty"]]
    assert len(sentinels) == 1

    ov = records.dataset_overview(conn, ds["id"])
    assert ov["stats"]["n_screened"] == 1 and ov["stats"]["n_records"] == 0
    assert any(doc["screened"] and doc["n_records"] == 0 for doc in ov["documents"])

    # export/load excludes the sentinel — no empty data row leaks into the round-trip
    assert records.load(conn, doc_id).records == []

    # duplicate detection: the exact same PDF hash is found for this principal
    sha = parsed.pdf_sha256(pdf)
    dup = records.documents_by_hashes(conn, [sha], session_id="sess-scr")
    assert any(m["document_id"] == doc_id for m in dup.get(sha, []))
    # a different hash → no match; empty input → empty result
    assert records.documents_by_hashes(conn, ["deadbeef"], session_id="sess-scr") == {}
    assert records.documents_by_hashes(conn, [], session_id="sess-scr") == {}
