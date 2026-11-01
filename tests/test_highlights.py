"""The coder's marks end to end: the endpoints add, list and delete a mark under the paper
owner's authorization, the document view and the dataset export carry it. Skips without
Postgres."""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import app as appmod, extract, records, storage  # noqa: E402
from paperlens.pdf_utils import DISPLAY_DPI  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _pdf() -> bytes:
    import fitz
    d = fitz.open(); pg = d.new_page()
    pg.insert_text((72, 90), "First sentence here. Second one follows! Third ends.", fontsize=10)
    b = d.tobytes(); d.close()
    return b


def _one_record(pdf, prompt, *, model="", api_key="", base_url=None, use_text=False):
    return extract.LLMResult(text=json.dumps({
        "paper_metadata": {"title": "Marked Paper", "doi": "10.1/marks"},
        "records": [{"task": "write"}],
        "evidence": [{"snippet": "First sentence here.", "page": 1, "source": "-", "field": "records[0].task"}],
    }), finish_reason="stop", usage={"total": 3}, resolved_model="fake")


def test_marks_round_trip(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres available")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    pdf = _pdf()
    tmp = tempfile.TemporaryDirectory()
    store = storage.LocalObjectStore(root=tmp.name)
    monkeypatch.setattr(storage, "get_store", lambda: store)
    out = extract.run_extraction(conn, pdf, prompt="x", model="gpt-4o", api_key="", schema_id="human-ai-collab@v1",
                                 session_id="sess-marks", complete=_one_record, store=store)
    conn.commit()
    doc_id = out["document_id"]
    c = TestClient(appmod.app); H = {"X-Session-Id": "sess-marks"}

    # where the word "Second" sits, in the viewer's pixels
    import fitz
    d = fitz.open(stream=pdf, filetype="pdf"); w = next(w for w in d[0].get_text("words") if w[4] == "Second"); d.close()
    scale = DISPLAY_DPI / 72.0
    body = {"page": 1, "x": (w[0] + w[2]) / 2 * scale, "y": (w[1] + w[3]) / 2 * scale}

    assert c.get(f"/api/documents/{doc_id}/highlights", headers=H).json() == {"highlights": []}
    r = c.post(f"/api/documents/{doc_id}/highlights", headers=H, json=body)
    assert r.status_code == 200, r.text
    h = r.json()["highlight"]
    assert h["page"] == 1 and h["text"] == "Second one follows!" and len(h["rects"]) == 1 and h["created_by"] == "sess-marks" and h["note"] is None
    assert c.post(f"/api/documents/{doc_id}/highlights", headers=H, json={"page": 1, "x": 5, "y": 5}).status_code == 404     # no text there
    assert c.post(f"/api/documents/{doc_id}/highlights", headers={"X-Session-Id": "someone-else"}, json=body).status_code == 404
    listed = c.get(f"/api/documents/{doc_id}/highlights", headers=H).json()["highlights"]
    assert [x["id"] for x in listed] == [h["id"]]
    view = c.get(f"/api/documents/{doc_id}/view", headers=H).json()
    assert [x["text"] for x in view["highlights"]] == ["Second one follows!"]

    # the dataset export carries the marks per paper
    ds = records.create_dataset(conn, title="Marks DS", schema_id="human-ai-collab@v1", session_id="sess-marks")
    records.assign_document_to_dataset(conn, ds["id"], doc_id); conn.commit()
    exp = records.dataset_export(conn, ds["id"])
    paper = next(p for p in exp["papers"] if p["document_id"] == doc_id)
    assert paper["highlights"] == [{"page": 1, "text": "Second one follows!", "rects": h["rects"], "created_at": h["created_at"], "created_by": "sess-marks", "note": None}]

    assert c.delete(f"/api/highlights/{h['id']}", headers={"X-Session-Id": "someone-else"}).status_code == 403
    assert c.delete(f"/api/highlights/{h['id']}", headers=H).json() == {"deleted": 1}
    assert c.delete(f"/api/highlights/{h['id']}", headers=H).status_code == 404
    assert c.get(f"/api/documents/{doc_id}/highlights", headers=H).json() == {"highlights": []}
    tmp.cleanup()


def _words_pdf() -> bytes:
    import fitz
    d = fitz.open()
    p1 = d.new_page(); p1.insert_text((72, 90), "Anxiety and sleep correlated at r = .32 in Study 1.", fontsize=11)
    p1.insert_text((72, 110), "The anxiety scale had alpha = .88.", fontsize=11)
    p2 = d.new_page(); p2.insert_text((72, 90), "Study 2: anxiety and sleep, r = -.21.", fontsize=11)
    b = d.tobytes(); d.close()
    return b


def _seed_pdf_doc(conn, store, pdf, sess):
    out = extract.run_extraction(conn, pdf, prompt="x", model="gpt-4o", api_key="", schema_id="human-ai-collab@v1",
                                 session_id=sess, complete=_one_record, store=store)
    conn.commit()
    return out["document_id"]


def test_find_in_pdf_lists_every_hit_and_a_hit_becomes_a_mark(monkeypatch) -> None:
    """"Find in PDF": the search answers with every occurrence as viewer rectangles, page by
    page, case-insensitively; a one-letter query finds nothing (it would light up the whole
    paper); a stranger gets 404. A hit kept as a mark — a drag across its rectangle — is the
    matched word, resolved by the same text layer as any other mark."""
    if not _db_ok():
        pytest.skip("no Postgres available")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    tmp = tempfile.TemporaryDirectory()
    store = storage.LocalObjectStore(root=tmp.name)
    monkeypatch.setattr(storage, "get_store", lambda: store)
    doc_id = _seed_pdf_doc(conn, store, _words_pdf(), "sess-find")
    c = TestClient(appmod.app); H = {"X-Session-Id": "sess-find"}

    r = c.get(f"/api/documents/{doc_id}/search", params={"q": "anxiety"}, headers=H)
    assert r.status_code == 200, r.text
    hits = r.json()["hits"]
    assert [h["page"] for h in hits] == [1, 1, 2] and r.json()["total"] == 3 and r.json()["capped"] is False
    assert all(len(h["rect"]) == 4 and h["rect"][2] > 0 and h["rect"][3] > 0 for h in hits)
    assert c.get(f"/api/documents/{doc_id}/search", params={"q": "a"}, headers=H).json()["hits"] == []
    assert c.get(f"/api/documents/{doc_id}/search", params={"q": "nowhere"}, headers=H).json()["total"] == 0
    assert c.get(f"/api/documents/{doc_id}/search", params={"q": "anxiety"}, headers={"X-Session-Id": "someone-else"}).status_code == 404

    x, y, w, h = hits[1]["rect"]                               # the second hit: "anxiety" on the second line
    kept = c.post(f"/api/documents/{doc_id}/highlights", headers=H,
                  json={"page": 1, "x": x + 1, "y": y + h / 2, "x2": x + w - 1, "y2": y + h / 2})
    assert kept.status_code == 200, kept.text
    assert kept.json()["highlight"]["text"] == "anxiety" and kept.json()["highlight"]["page"] == 1
    tmp.cleanup()


def test_a_verdict_points_at_the_coders_marks(monkeypatch) -> None:
    """A flag carries the marks it points at (highlight_ids on the event): the document view
    shows them on the entry, the history on the event; the last list given wins, so a later
    verdict with fewer ids detaches; a mark on another paper or a stranger is refused; a mark
    that is removed drops out of the verdicts that pointed at it."""
    if not _db_ok():
        pytest.skip("no Postgres available")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    tmp = tempfile.TemporaryDirectory()
    store = storage.LocalObjectStore(root=tmp.name)
    monkeypatch.setattr(storage, "get_store", lambda: store)
    doc_id = _seed_pdf_doc(conn, store, _words_pdf(), "sess-point")
    other_doc = _seed_pdf_doc(conn, store, _words_pdf(), "sess-point")
    c = TestClient(appmod.app); H = {"X-Session-Id": "sess-point"}
    rid = c.get(f"/api/documents/{doc_id}/view", headers=H).json()["records"][0]["id"]
    hit = c.get(f"/api/documents/{doc_id}/search", params={"q": "alpha"}, headers=H).json()["hits"][0]
    x, y, w, h = hit["rect"]
    m1 = c.post(f"/api/documents/{doc_id}/highlights", headers=H, json={"page": 1, "x": x + 1, "y": y + h / 2}).json()["highlight"]
    p2 = c.get(f"/api/documents/{doc_id}/search", params={"q": "Study 2"}, headers=H).json()["hits"][0]
    m2 = c.post(f"/api/documents/{doc_id}/highlights", headers=H, json={"page": 2, "x": p2["rect"][0] + 1, "y": p2["rect"][1] + p2["rect"][3] / 2}).json()["highlight"]
    elsewhere = c.post(f"/api/documents/{other_doc}/highlights", headers=H, json={"page": 1, "x": x + 1, "y": y + h / 2}).json()["highlight"]

    r = c.post(f"/api/records/{rid}/verify", headers=H, json={"status": "flagged", "highlight_ids": [m1["id"], m2["id"]]})
    assert r.status_code == 200, r.text
    rec = c.get(f"/api/documents/{doc_id}/view", headers=H).json()["records"][0]
    assert rec["verification_status"] == "flagged"
    assert [(m["id"], m["page"]) for m in rec["marks"]] == [(m1["id"], 1), (m2["id"], 2)] and rec["marks"][0]["text"] == m1["text"]
    ev = c.get(f"/api/records/{rid}/events", headers=H).json()["events"][0]
    assert ev["status"] == "flagged" and [m["id"] for m in ev["highlights"]] == [m1["id"], m2["id"]]

    # refused: a mark on another paper; a stranger (the entry is private, so 404); not an id
    assert c.post(f"/api/records/{rid}/verify", headers=H, json={"status": "flagged", "highlight_ids": [elsewhere["id"]]}).status_code == 422
    assert c.post(f"/api/records/{rid}/verify", headers=H, json={"status": "flagged", "highlight_ids": ["nope"]}).status_code == 422
    assert c.post(f"/api/records/{rid}/verify", headers={"X-Session-Id": "someone-else"}, json={"status": "flagged", "highlight_ids": [m1["id"]]}).status_code == 404

    # detaching: a note-like event with the shorter list, the verdict stays flagged
    assert c.post(f"/api/records/{rid}/verify", headers=H, json={"status": "noted", "highlight_ids": [m2["id"]]}).status_code == 200
    rec = c.get(f"/api/documents/{doc_id}/view", headers=H).json()["records"][0]
    assert rec["verification_status"] == "flagged" and [m["id"] for m in rec["marks"]] == [m2["id"]]
    # a plain verdict (no list) leaves the pointers as they are
    assert c.post(f"/api/records/{rid}/verify", headers=H, json={"status": "verified"}).status_code == 200
    assert [m["id"] for m in c.get(f"/api/documents/{doc_id}/view", headers=H).json()["records"][0]["marks"]] == [m2["id"]]
    # a removed mark drops out
    assert c.delete(f"/api/highlights/{m2['id']}", headers=H).json() == {"deleted": 1}
    assert c.get(f"/api/documents/{doc_id}/view", headers=H).json()["records"][0]["marks"] == []
    assert c.get(f"/api/records/{rid}/events", headers=H).json()["events"][-1]["highlights"] == [{"id": m1["id"], "page": 1, "text": m1["text"]}]
    tmp.cleanup()
