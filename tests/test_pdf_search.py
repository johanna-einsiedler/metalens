"""The PDF pane's search box: every page on which a text is printed, with the box of each hit in
image pixels (``pdf_utils.search_text_rects``), and the owner-only route that serves it; the text layer
(``pdf_utils.text_words``, ``/text``) the box searches in the browser (``static/pdfview.js`` searchWords,
run through node), which finds what the server's search finds."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
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


def _lines_pdf(pages: list[list[str]]) -> bytes:
    """Each page's lines printed as one paragraph (one text block)."""
    import fitz
    d = fitz.open()
    for lines in pages:
        d.new_page().insert_text((72, 90), "\n".join(lines))
    b = d.tobytes(); d.close()
    return b


PAGES = [["Treatment effects on Income", "of the treated, and the treat-", "ment group earned more income."],
         ["Robustness: treatment and income again", "Nothing else"],
         ["no match here"]]


def test_the_text_layer_holds_every_word_with_its_box_and_line() -> None:
    pages = pdf_utils.text_words(_lines_pdf(PAGES))
    assert [p["page"] for p in pages] == [1, 2, 3]
    w = pages[0]["w"]
    assert [x[4] for x in w[:4]] == ["Treatment", "effects", "on", "Income"]
    x, y, ww, h, text, line = w[0]
    assert 140 <= x <= 148 and ww > 40 and h > 8                          # 144 dpi: two image pixels per point
    assert len({x[5] for x in w}) == 3                                     # three printed lines
    assert pdf_utils.text_words(_pdf([""])) == []                          # a page without text has no entry


def _node_search(pages: list[dict], queries: list[tuple[str, int]]) -> list[dict]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    mod = "file://" + os.path.join(_ROOT, "paperlens", "static", "pdfview.js")
    js = ("const m = await import(%s); const c = JSON.parse(process.argv[process.argv.length - 1]);"
          "process.stdout.write(JSON.stringify(c.q.map(([q, lim]) => m.searchWords(c.pages, q, lim))));") % json.dumps(mod)
    r = subprocess.run([node, "--input-type=module", "-e", js, json.dumps({"pages": pages, "q": queries})],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_browser_search_finds_what_the_server_finds() -> None:
    pdf = _lines_pdf(PAGES)
    pages = pdf_utils.text_words(pdf)
    queries = ["treatment", "INCOME", "treated, and", "income again", "group earned", "eat", "x", "  ", "absent", "Treatment effects on Income",
               "on income of the", "the treat- ment", "earned   more"]
    got = _node_search(pages, [(q, 300) for q in queries])
    for q, mine in zip(queries, got):
        hits, truncated = pdf_utils.search_text_rects(pdf, q)
        assert [h["page"] for h in mine["hits"]] == [h["page"] for h in hits], q
        assert [len(h["rects"]) for h in mine["hits"]] == [len(h["rects"]) for h in hits], q
        assert mine["truncated"] == truncated
        for a, b in zip(mine["hits"], hits):                               # the same boxes, give or take a letter's width
            for ra, rb in zip(a["rects"], b["rects"]):
                assert abs(ra[0] - rb[0]) < 14 and abs(ra[1] - rb[1]) < 6 and abs(ra[2] - rb[2]) < 20, (q, ra, rb)
    # a text across a line break: one box on each line
    across = got[queries.index("on income of the")]["hits"][0]["rects"]
    assert len(across) == 2 and across[0][1] < across[1][1]


def test_the_browser_search_caps_the_boxes_across_pages() -> None:
    pages = pdf_utils.text_words(_pdf(["\n".join(["word"] * 40)] * 3))
    capped, full = _node_search(pages, [("word", 50), ("word", 120)])
    assert capped["truncated"] and sum(len(h["rects"]) for h in capped["hits"]) == 50 and [h["page"] for h in capped["hits"]] == [1, 2]
    assert not full["truncated"] and sum(len(h["rects"]) for h in full["hits"]) == 120


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
    # the text layer the box searches in the browser
    t = c.get(f"/api/documents/{doc}/text", headers=h)
    assert t.status_code == 200, t.text
    assert [p["page"] for p in t.json()["pages"]] == [1, 2] and t.json()["pages"][0]["w"][0][4] == "Remote"
    assert c.get(f"/api/documents/{doc}/text", headers={"X-Session-Id": "stranger"}).status_code == 404
    # a document imported without a PDF has nothing to search
    bare = c.post("/api/ingest", json={"result": result}, headers=h).json()["document_id"]
    assert c.get(f"/api/documents/{bare}/search", params={"q": "productivity"}, headers=h).json()["no_pdf"] is True
    assert c.get(f"/api/documents/{bare}/text", headers=h).json() == {"pages": [], "no_pdf": True}
    for d in (doc, bare):
        records.delete_document(conn, d)
    conn.close()
