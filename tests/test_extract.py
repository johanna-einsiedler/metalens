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
        # the model's verbatim response is kept next to the PDF (what a coder debugs against)
        raw = store.get(storage.raw_key(out["document_id"])).decode("utf-8")
        assert '"sample_id": "S1"' in raw and store.exists(storage.raw_key(out["document_id"]))

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


# ── the server decides prompt + schema id (declarative presets) ───────────────

_V2_FAKE_JSON = """{
  "paper_metadata": {"title": "Video games and activity", "doi": null, "year": 2023, "authors": ["Lee C"], "journal": null},
  "samples": [
    {"sample_id": "S1", "pubyear": 2023, "country": "KR", "continent": "Asia", "lang": "ko", "pubtype": 1,
     "female": 51.0, "age": 14.2, "clinical": 0, "notes": "",
     "records": [{"var1": "pa", "var2": "vg", "desc1": "steps", "desc2": "hours", "es": -0.21, "type": "r",
                  "n": 147, "rel1": 0.8, "rel2": null, "rel1_type": "alpha", "rel2_type": null,
                  "instr1": "pedometer", "instr2": "self-report"}],
     "confidence": {"effect_sizes": {"level": "high", "notes": "Table 2"},
                    "reliabilities": {"level": "medium", "notes": "rel2 not reported"},
                    "metadata": {"level": "low", "notes": "age inferred"}}}
  ],
  "evidence": [
    {"snippet": "N = 147 participants", "page": 1, "source": null, "field": "samples[0]"},
    {"snippet": "Table 2. Rotated factor matrix", "page": 3, "source": "Table 2", "field": "samples[0].records[0]"}
  ]
}"""


def _fake_v2(pdf_bytes, prompt, *, model="", api_key="", base_url=None, use_text=False):
    return extract.LLMResult(text=_V2_FAKE_JSON, finish_reason="stop", usage={}, resolved_model="fake")


def test_run_with_a_spec_records_confidence_and_issues() -> None:
    """A run under a format-2 preset: the declared core array drives ingest, per-sample
    confidence reaches the view payload, structural issues are stored for triage, and the
    exact prompt is remembered."""
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    from paperlens import presets, records
    conn = records.connect(); records.init_db(conn)
    run = presets.resolve_run(conn, preset_id="masem-direct")
    assert run.schema_id == presets.schema_id_for("masem-direct") and run.entries_key == "samples"
    assert run.prompt.startswith("# TASK") and not run.prompt_edited
    with conn.transaction():
        records.upsert_schema(conn, run.schema_id, run.field_defs)
    with tempfile.TemporaryDirectory() as d:
        out = extract.run_extraction(
            conn, _make_pdf(_PAGES), prompt=run.prompt, model="gpt-4o", api_key="",
            schema_id=run.schema_id, session_id="sess-v2", spec=run.spec, params=run.params,
            complete=_fake_v2, store=storage.LocalObjectStore(root=d))
    assert out["schema_id"] == run.schema_id and out["n_records"] == 1
    view = records.document_view(conn, out["document_id"])
    assert view["spec"]["entries"]["key"] == "samples" and view["field_defs"]["format"] == 2
    rec = view["records"][0]
    assert rec["confidence"]["metadata"] == {"level": "low", "notes": "age inferred"}
    assert "confidence" not in rec["field_values"]
    assert rec["extraction"]["prompt_sha256"] and rec["extraction"]["prompt_edited"] is False
    # MASEMiner writes its own evidence rules (frozen prompt): no generated coverage rule,
    # so an uncited value is not an issue here — see test_preset_spec for the generated case
    assert not any(i["code"] == "uncited_value" for i in view["issues"])
    assert not any(i["code"] == "missing_confidence" for i in view["issues"])
    conn.close()


def test_stale_and_unknown_schema_ids_resolve_sanely() -> None:
    """A client still posting ``<preset>@v1`` lands on the preset's real (hashed) row; an
    unknown id stays an 'auto' row; an EXISTING row is kept exactly."""
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    from paperlens import presets, records
    conn = records.connect(); records.init_db(conn)
    stale = presets.resolve_run(conn, schema_id="masem-direct@v1", prompt="my own text")
    assert stale.schema_id == presets.schema_id_for("masem-direct") and stale.prompt_edited
    assert stale.prompt == "my own text"
    auto = presets.resolve_run(conn, schema_id="extract@v1", prompt="custom")
    assert auto.schema_id == "extract@v1" and auto.field_defs is None and auto.spec is None
    with conn.transaction():
        records.upsert_schema(conn, "masem@v3", None)      # an old row with no grammar
    kept = presets.resolve_run(conn, schema_id="masem@v3")
    assert kept.schema_id == "masem@v3" and kept.prompt.startswith("# TASK")   # prompt via the alias
    # the aliased legacy id renders the CURRENT preset's prompt when a preset_id is given
    assert presets.resolve_run(conn, preset_id="masem").schema_id == presets.schema_id_for("masem-direct")
    conn.close()


def test_extract_endpoint_with_preset_id_returns_hashed_schema_id() -> None:
    from paperlens import worker as wk
    if not (_db_ok() and wk.redis_available()):
        import pytest
        pytest.skip("no Postgres/Redis available")
    import asyncio, warnings
    warnings.filterwarnings("ignore")
    from arq import create_pool
    from fastapi.testclient import TestClient
    from paperlens import presets, records
    from paperlens.app import app
    c = TestClient(app)
    r = c.post("/api/extract",
               data={"preset_id": "masem-direct", "model": "gpt-4o", "api_key": "sk-test",
                     "params": '{"effect_sizes": [{"code": "smd", "label": "SMD"}]}'},
               files={"pdf": ("p.pdf", _make_pdf(_PAGES), "application/pdf")},
               headers={"X-Session-Id": "s-v2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] is True and body["schema_id"] == presets.schema_id_for("masem-direct")
    conn = records.connect()
    row = records.get_schema(conn, body["schema_id"])
    assert row and row["field_defs"]["format"] == 2 and row["field_defs"]["schema_id"] == body["schema_id"]
    conn.close()
    # a malformed params blob is a 422, not a crash
    r2 = c.post("/api/extract", data={"preset_id": "masem-direct", "model": "gpt-4o", "api_key": "k",
                                      "params": "not json"},
                files={"pdf": ("p.pdf", _make_pdf(_PAGES), "application/pdf")},
                headers={"X-Session-Id": "s-v2"})
    assert r2.status_code == 422

    async def _flush():
        pool = await create_pool(wk.redis_settings())
        await pool.flushall(); await pool.aclose()
    asyncio.run(_flush())


def _table_pdf() -> bytes:
    """One page, a three-panel table: the SAME row label in every panel, different cells,
    and the en-dash of every interval encoded as the letter "e" the way some publishers'
    text layers do ("(0.88e0.93)a" where the page shows "(0.88–0.93)a")."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    y = 100
    for tool, cells in (("PRISMA", ["0.90 (0.88e0.93)a", "1878/1989 (94%, 93%e96%)a", "954/2943 (32%, 30%e34%)a"]),
                        ("AMSTAR", ["0.92 (0.89e0.94)a", "798/837 (95%, 94%e97%)a", "362/1199 (30%, 28%e33%)a"]),
                        ("PRECIS-2", ["0.73 (0.62e0.83)", "106/127 (83%, 77%e90%)", "377/504 (75%, 72%e78%)"])):
        page.insert_text((40, y), f"Table 3 {tool}", fontsize=9)
        y += 16
        page.insert_text((40, y), "(4) Human Rater 1 & Claude-3-Opus", fontsize=8)
        for k, cell in enumerate(cells):
            page.insert_text((230 + 120 * k, y), cell, fontsize=8)
        y += 60
    data = doc.tobytes()
    doc.close()
    return data


_ROW = "(4) Human Rater 1 & Claude-3-Opus 0.90 (0.88–0.93)a 1878/1989 (94%, 93%–96%)a 954/2943 (32%, 30%–34%)a"


def test_table_row_citation_lights_the_whole_row() -> None:
    """A row citation whose snippet cannot match as one string (cells are separate spans,
    the dashes differ) used to highlight only the row LABEL — three times, once per panel.
    Now the whole row lights up, and the snippet's own numbers pick the right panel."""
    scale = pdf_utils.DISPLAY_DPI / 72.0
    pdf = _table_pdf()
    row_cite = {"page": 1, "snippet": _ROW, "field": "experiments[0].conditions[0].measures[0]", "source": "Table 3"}
    _pages, hl, _ = pdf_utils.pdf_to_pages_with_rects(pdf, [row_cite], render_pages=False)
    assert _pages == [] and len(hl) == 1
    rects = hl[0]["rects"]
    assert len(rects) == 1, rects                          # the PRISMA row only: its numbers are there
    x, y, w, h = rects[0]
    assert x + w > 470 * scale and 100 * scale < y < 125 * scale   # label through the last cell, first panel

    # the same snippet cited for ONE value: no expansion (the label anchors, three panels)
    value_cite = dict(row_cite, field="experiments[0].conditions[0].AI_Type")
    _p, hl, _ = pdf_utils.pdf_to_pages_with_rects(pdf, [value_cite], render_pages=False)
    assert len(hl[0]["rects"]) == 3 and all(r[2] < 200 * scale for r in hl[0]["rects"])

    # numbers that match no panel: every candidate row lights up whole (the coder decides)
    vague = dict(row_cite, snippet="(4) Human Rater 1 & Claude-3-Opus 0.55 (0.50–0.60) 5/9 (55%)")
    _p, hl, _ = pdf_utils.pdf_to_pages_with_rects(pdf, [vague], render_pages=False)
    assert len(hl[0]["rects"]) == 3 and all(r[0] + r[2] > 470 * scale for r in hl[0]["rects"])


def test_evidence_fields_list_expands_and_bands_filter() -> None:
    items = pdf_utils.evidence_items_from_result(
        '{"evidence": [{"snippet": "deferred to a second rater", "page": 2, "source": null,'
        ' "field": ["experiments[0].conditions[0].Final_Decision", "experiments[0].conditions[1].Final_Decision"]},'
        ' {"snippet": "no path", "page": 2, "source": null, "field": []}]}')
    assert [i["field"] for i in items] == ["experiments[0].conditions[0].Final_Decision",
                                           "experiments[0].conditions[1].Final_Decision", None]
    assert pdf_utils.parse_bands("722:738,1002.4:1018") == [(722.0, 738.0), (1002.4, 1018.0)]
    rects = [[903, 722, 40, 16], [906, 770, 40, 16], [912, 793, 40, 16]]
    assert pdf_utils.rects_in_bands(rects, [(720, 740)]) == [[903, 722, 40, 16]]
    assert pdf_utils.rects_in_bands(rects, []) == rects


def test_number_locator_prefers_whole_numbers() -> None:
    """"94" must land on the "94%" cell, not inside "2943" or "1994" on the same line."""
    import fitz
    doc = fitz.open(); page = doc.new_page()
    page.insert_text((60, 100), "1878/1989 (94%, 93%e96%)a   954/2943 (32%)   since 1994", fontsize=10)
    pdf = doc.tobytes(); doc.close()
    scale = pdf_utils.DISPLAY_DPI / 72.0
    rects = pdf_utils.locate_value_rects(pdf, 1, "94")
    assert len(rects) == 1 and 60 * scale < rects[0][0] < 200 * scale, rects
    assert len(pdf_utils.locate_value_rects(pdf, 1, "2943")) == 1
    assert len(pdf_utils.locate_value_rects(pdf, 1, "1994")) == 1
