"""WS6 — reusable parsed-document cache (markdown + per-page text, deduped by hash).

Verifies: stable content hash; get_or_parse returns text+pages and persists exactly
one row on first sight (cache hit on the second call); run_extraction stamps the
document's pdf_sha256 so for_document/page_text resolve; and the /text endpoint is
owner-gated. Skips without Postgres.
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

from paperlens import extract, parsed, storage  # noqa: E402

_PAGES = [
    "Study 1 included N = 147 participants across two sites.",
    "Methods. A two-factor solution was retained after rotation.",
    "Table 2. Results.\nrow1   0.83   0.12",
]

_FAKE_JSON = (
    '{"paper_metadata": {"title": "Parsed Cache Test"}, '
    '"records": [{"Paper_Name": "T_2026", "Avg_Perf_HumanAI": 0.6}], '
    '"evidence": [{"snippet": "N = 147", "page": 1, "source": "p1", '
    '"field": "records[0].Avg_Perf_HumanAI"}]}'
)


def _make_pdf(pages):
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
                             usage={"total": 10}, resolved_model="fake")


def _db_ok() -> bool:
    try:
        from paperlens import records
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def test_pdf_sha256_stable() -> None:
    pdf = _make_pdf(_PAGES)
    assert parsed.pdf_sha256(pdf) == parsed.pdf_sha256(pdf)
    assert parsed.pdf_sha256(pdf) != parsed.pdf_sha256(_make_pdf(_PAGES[:2]))
    assert parsed.text_md_key("abc") == "text/abc.md"
    assert parsed.text_pages_key("abc") == "text/abc.pages.json"


def test_get_or_parse_caches() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    from paperlens import records
    conn = records.connect(); records.init_db(conn)
    pdf = _make_pdf(_PAGES)
    sha = parsed.pdf_sha256(pdf)
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        first = parsed.get_or_parse(conn, store, pdf)
        assert first["cached"] is False
        assert first["markdown"].strip() and len(first["pages"]) >= 1
        assert "147" in first["markdown"]
        second = parsed.get_or_parse(conn, store, pdf)
        assert second["cached"] is True and second["sha"] == sha
        n = conn.execute("SELECT count(*) FROM parsed_document WHERE pdf_sha256=%s", (sha,)).fetchone()[0]
        assert n == 1  # exactly one row despite two calls
        # objects landed in the store under the text/ prefix
        assert store.exists(parsed.text_md_key(sha)) and store.exists(parsed.text_pages_key(sha))
    conn.close()


def test_run_extraction_stamps_sha_and_reuses() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    from paperlens import records
    conn = records.connect(); records.init_db(conn)
    pdf = _make_pdf(_PAGES)
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        out = extract.run_extraction(conn, pdf, prompt="x", model="gpt-4o", api_key="",
                                     schema_id="human-ai-collab@v1", session_id="sess-parsed",
                                     complete=_fake_complete, store=store)
        doc_id = out["document_id"]
        sha = conn.execute("SELECT pdf_sha256 FROM extraction_document WHERE id=%s", (doc_id,)).fetchone()[0]
        assert sha == parsed.pdf_sha256(pdf)                     # stamped
        data = parsed.for_document(conn, store, doc_id)
        assert data and data["markdown"].strip()                # cache populated as a side-effect
        pg1 = parsed.page_text(conn, store, doc_id, 1)
        assert pg1 and "147" in pg1                              # per-page text resolves
        assert parsed.page_text(conn, store, doc_id, 99) is None  # out-of-range → None
    conn.close()


def test_text_endpoint_owner_gated() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    from fastapi.testclient import TestClient
    from paperlens import records
    from paperlens.app import app
    records.init_db(records.connect())
    c = TestClient(app)
    # unknown / non-owned document → 404 (never leaks existence)
    r = c.get("/api/documents/00000000-0000-0000-0000-000000000000/text",
              headers={"X-Session-Id": "stranger"})
    assert r.status_code == 404


def _main() -> int:
    failures = 0
    for label, fn in [
        ("parsed:sha-stable", test_pdf_sha256_stable),
        ("parsed:get-or-parse-caches", test_get_or_parse_caches),
        ("parsed:run-extraction-stamps", test_run_extraction_stamps_sha_and_reuses),
        ("parsed:endpoint-owner-gated", test_text_endpoint_owner_gated),
    ]:
        try:
            fn(); print(f"  PASS  {label}")
        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ == "Skipped":
                print(f"  SKIP  {label}: {exc}"); continue
            failures += 1; print(f"  FAIL  {label}: {exc!r}")
    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
