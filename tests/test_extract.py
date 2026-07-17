"""PDF extraction core — full chain, offline.

A real generated PDF + a FAKE LLM (no API key, no network) drives:
  render pages -> parse canonical JSON -> REAL evidence-rect highlighting
  (PyMuPDF text search) -> ingest into records -> store page images -> attach rects.

Test A (geometry) needs only PyMuPDF. Test B (full chain) needs Postgres; skips otherwise.
"""
from __future__ import annotations

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import extract, pdf_utils, storage  # noqa: E402

# A 3-page PDF whose text contains the exact snippets the fake LLM will cite.
_PAGES = [
    "Study 1 included N = 147 participants from two universities.",
    "Methods. A two-factor solution was retained after rotation.",
    "Table 2. Rotated factor matrix.\nitem1   0.83   0.12\nitem2   0.45   0.71",
]

# Masem-shaped canonical JSON; nested evidence cites page 1 and page 3 verbatim.
_FAKE_JSON = """{
  "samples": [
    {
      "sample_id": "S1",
      "n": 147,
      "factor_loadings": {"item1": {"F1": 0.83, "F2": 0.12}, "item2": {"F1": 0.45, "F2": 0.71}},
      "evidence": [
        {"snippet": "N = 147 participants", "page": 1, "source": null, "field": "samples[0]"},
        {"snippet": "Table 2. Rotated factor matrix", "page": 3, "source": "Table 2", "field": "samples[0].factor_loadings"}
      ]
    }
  ]
}"""


def _make_pdf(pages: list[str]) -> bytes:
    import fitz
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        for i, line in enumerate(text.split("\n")):
            page.insert_text((72, 90 + 16 * i), line, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def _fake_complete(pdf_bytes, prompt, *, model="", api_key="", base_url=None, use_text=False):
    return extract.LLMResult(text=_FAKE_JSON, finish_reason="stop",
                             usage={"total": 1234}, resolved_model="fake-model-2026")


def test_highlight_geometry_offline() -> None:
    """Pure PyMuPDF: evidence snippet -> rect on the correct page."""
    pdf = _make_pdf(_PAGES)
    items = [
        {"page": 1, "snippet": "N = 147 participants", "field": None, "source": None},
        {"page": 3, "snippet": "Table 2. Rotated factor matrix", "field": None, "source": None},
    ]
    pages, highlights, scanned = pdf_utils.pdf_to_pages_with_rects(pdf, items)
    assert len(pages) == 3                         # three JPEG page images rendered
    assert len(highlights) >= 1                    # snippets located on the page
    hpages = {h["page"] for h in highlights}
    assert 1 in hpages or 3 in hpages
    for h in highlights:
        assert h["rects"] and len(h["rects"][0]) == 4   # [x, y, w, h]


def _db_ok() -> bool:
    try:
        from paperlens import records
        c = records.connect(); c.close()
        return True
    except Exception:
        return False


def test_full_extraction_chain() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    from paperlens import records
    conn = records.connect()
    records.init_db(conn)

    pdf = _make_pdf(_PAGES)
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        out = extract.run_extraction(
            conn, pdf, prompt="extract", model="gpt-4o", api_key="",
            schema_id="masem@v3", session_id="sess-1",
            complete=_fake_complete, store=store)

        assert out["n_records"] == 1
        assert out["n_pages"] == 3
        assert out["n_highlights"] >= 1
        # page images landed in object storage and are retrievable
        for key in out["page_image_keys"]:
            assert store.exists(key) and store.get(key)[:2] == b"\xff\xd8"  # JPEG magic
        assert len(out["page_image_keys"]) == 3

    conn.close()

    # Re-open a FRESH connection so we assert only what was actually COMMITTED
    # (catches the savepoint/rollback bug where rects never persisted).
    fresh = records.connect()
    try:
        recs = records.records_for_paper(fresh, out["paper_id"])
        assert len(recs) == 1 and recs[0]["field_values"]["n"] == 147

        # the highlight rects were durably attached (powers click-to-source)
        n_rects = fresh.execute(
            "SELECT count(*) FROM evidence_span WHERE document_id = %s AND rect IS NOT NULL",
            (out["document_id"],)).fetchone()[0]
        assert n_rects >= 1, "expected >=1 committed evidence span with an attached rect"

        # extraction metadata captured (model / resolved_model)
        ex = fresh.execute(
            "SELECT extraction FROM record WHERE document_id = %s LIMIT 1",
            (out["document_id"],)).fetchone()[0]
        assert ex["resolved_model"] == "fake-model-2026" and ex["n_pages"] == 3
    finally:
        fresh.close()


def test_extract_endpoint_enqueues() -> None:
    """POST /api/extract accepts a multipart PDF and enqueues a job (wiring only;
    the worker isn't running, so no LLM call fires)."""
    from paperlens import worker as wk
    if not (_db_ok() and wk.redis_available()):
        import pytest
        pytest.skip("no Postgres/Redis available")
    import asyncio
    import warnings
    warnings.filterwarnings("ignore")
    from arq import create_pool
    from fastapi.testclient import TestClient
    from paperlens.app import app

    pdf = _make_pdf(_PAGES)
    c = TestClient(app)
    r = c.post("/api/extract",
               data={"prompt": "extract", "model": "gpt-4o", "schema_id": "masem@v3"},
               files={"pdf": ("p.pdf", pdf, "application/pdf")},
               headers={"X-Session-Id": "s"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] is True and body["job_id"]

    async def _flush():  # don't leave the un-run job in the dev queue
        pool = await create_pool(wk.redis_settings())
        await pool.flushall(); await pool.aclose()
    asyncio.run(_flush())


def _main() -> int:
    failures = 0
    for label, fn in [
        ("extract:highlight-geometry", test_highlight_geometry_offline),
        ("extract:full-chain", test_full_extraction_chain),
        ("extract:endpoint-enqueues", test_extract_endpoint_enqueues),
    ]:
        try:
            fn()
            print(f"  PASS  {label}")
        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ == "Skipped":
                print(f"  SKIP  {label}: {exc}")
                continue
            failures += 1
            print(f"  FAIL  {label}: {exc!r}")
    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
