"""The PDF pane's search box: every page on which a text is printed, with the box of each hit in
image pixels (``pdf_utils.search_text_rects``), and the owner-only route that serves it."""
from __future__ import annotations

import json
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

from paperlens import pdf_utils  # noqa: E402


def _pdf(pages: list[str]) -> bytes:
    import fitz
    d = fitz.open()
    for text in pages:
        d.new_page().insert_text((72, 90), text)
    b = d.tobytes(); d.close()
    return b


def test_every_page_with_the_text_is_listed_with_its_boxes() -> None:
    pdf = _pdf(["Treatment effects on income", "Robustness: treatment and income again", "Nothing of note here"])
    hits, truncated = pdf_utils.search_text_rects(pdf, "treatment")           # case-insensitive
    assert [h["page"] for h in hits] == [1, 2] and not truncated
    assert [len(h["rects"]) for h in hits] == [1, 1]
    x, y, w, h = hits[0]["rects"][0]
    assert 140 <= x <= 148 and w > 40 and h > 8                                # 144 dpi: two image pixels per point, from x = 72 pt
    assert [h["page"] for h in pdf_utils.search_text_rects(pdf, "income")[0]] == [1, 2]
    assert pdf_utils.search_text_rects(pdf, "x") == ([], False)                 # one character: noise, nothing searched
    assert pdf_utils.search_text_rects(pdf, "  ") == ([], False)
    assert pdf_utils.search_text_rects(pdf, "absent") == ([], False)


def test_the_boxes_are_capped_across_pages() -> None:
    pdf = _pdf(["\n".join(["word"] * 40)] * 3)                                  # 120 hits over three pages
    hits, truncated = pdf_utils.search_text_rects(pdf, "word", limit=50)
    assert truncated and sum(len(h["rects"]) for h in hits) == 50 and [h["page"] for h in hits] == [1, 2]
    hits, truncated = pdf_utils.search_text_rects(pdf, "word", limit=120)
    assert not truncated and sum(len(h["rects"]) for h in hits) == 120


def test_the_route_answers_the_owner_only() -> None:
    from test_analysis_table import _db_ok
    if not _db_ok():
        pytest.skip("no Postgres")
    import fixtures
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    from paperlens import records
    conn = records.connect(); records.init_db(conn)
    sess = f"sess-search-{uuid.uuid4().hex[:6]}"
    c = TestClient(appmod.app); h = {"X-Session-Id": sess}
    pdf = _pdf(["Remote work productivity 0.42", "Productivity again, remotely"])
    result = json.loads(fixtures.FORESTPLOT_JSON)
    doc = c.post("/api/ingest-pdf", files={"pdf": ("goh.pdf", pdf, "application/pdf")},
                 data={"result": json.dumps(result)}, headers=h).json()["document_id"]
    r = c.get(f"/api/documents/{doc}/search", params={"q": "productivity"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["page"] for x in body["hits"]] == [1, 2] and body["n"] == 2 and not body["truncated"] and not body.get("no_pdf")
    assert all(len(rect) == 4 for x in body["hits"] for rect in x["rects"])
    assert c.get(f"/api/documents/{doc}/search", params={"q": "nowhere"}, headers=h).json()["hits"] == []
    assert c.get(f"/api/documents/{doc}/search", params={"q": "p"}, headers=h).json() == {"hits": [], "n": 0, "truncated": False}
    assert c.get(f"/api/documents/{doc}/search", params={"q": "productivity"}, headers={"X-Session-Id": "stranger"}).status_code == 404
    # a document imported without a PDF has nothing to search
    bare = c.post("/api/ingest", json={"result": result}, headers=h).json()["document_id"]
    assert c.get(f"/api/documents/{bare}/search", params={"q": "productivity"}, headers=h).json()["no_pdf"] is True
    for d in (doc, bare):
        records.delete_document(conn, d)
    conn.close()
