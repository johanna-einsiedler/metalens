"""A record of a public dataset, checked against the paper by a reader with their OWN copy of
the PDF (paperlens/review.py): what a reader sees of the document (public records, quotes, roles
— no pages, no geometry, no file name), the owner's view, dropping a copy (hash match, pages,
rects, gated page images, pinpoint search), reader checks that are counted but never adopted
as the maintainer's status, and the copy's removal by hand, with the session, and by the sweep.

Skips without Postgres.
"""
from __future__ import annotations

import hashlib
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402
from paperlens import app as appmod, records, retention, review, storage  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _skip():
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")


def _pdf(lines: list[str]) -> bytes:
    import fitz
    d = fitz.open(); page = d.new_page()
    for k, line in enumerate(lines):
        page.insert_text((72, 100 + 16 * k), line, fontsize=9)
    out = d.tobytes(); d.close()
    return out


# the quotes the forestplot fixture cites, printed verbatim so the locator finds them
PAPER = _pdf(["A meta-analysis of remote-work productivity",
              "Jones (2020): SMD 0.13, 95% CI 0.04-0.22",
              "Figure 2. Forest plot of included studies.",
              "Smith et al. 2018 reported SMD -0.42 (95% CI -0.61 to -0.23)"])
OTHER = _pdf(["A different edition of the paper", "Jones (2020): SMD 0.13, 95% CI 0.04-0.22"])


def _setup(conn, owner: str):
    """A public dataset of the owner with one document (PDF stored, hash recorded) and a second
    document of theirs that is NOT public."""
    records.init_db(conn)
    store = storage.get_store()
    doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None, source_job_id="review",
                          session_id=owner, pdf_sha256=hashlib.sha256(PAPER).hexdigest())
    store.put(storage.pdf_key(doc), PAPER, "application/pdf")
    store.put(storage.page_image_key(doc, 1), b"owner-page-image", "image/jpeg")
    ds = records.create_dataset(conn, title="Public remote-work", schema_id=None, session_id=owner, visibility="public")
    records.assign_document_to_dataset(conn, ds["id"], doc)
    private = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None, source_job_id="review", session_id=owner)
    conn.commit()
    rids = [str(r[0]) for r in conn.execute("SELECT id FROM record WHERE document_id = %s::uuid ORDER BY entry_index", (doc,)).fetchall()]
    prid = str(conn.execute("SELECT id FROM record WHERE document_id = %s::uuid ORDER BY entry_index LIMIT 1", (private,)).fetchone()[0])
    return doc, ds, rids, prid


def _client():
    from fastapi.testclient import TestClient
    return TestClient(appmod.app)


def test_reader_and_owner_views() -> None:
    _skip()
    conn = records.connect()
    owner, reader = f"rv-owner-{uuid.uuid4().hex[:6]}", f"rv-reader-{uuid.uuid4().hex[:6]}"
    doc, ds, rids, prid = _setup(conn, owner)
    records.verify_record(conn, rids[0], status="flagged", notes="check the CI", verifier_kind="maintainer"); conn.commit()
    c = _client(); R = {"X-Session-Id": reader}; O = {"X-Session-Id": owner}
    # a reader: the public records with their quotes, nothing of the owner's file
    r = c.get(f"/api/records/{rids[0]}/review", headers=R); assert r.status_code == 200, r.text
    v = r.json()
    assert v["focus"] == rids[0] and v["viewer"] == {"owner": False, "anonymous": True}
    assert {x["id"] for x in v["records"]} == set(rids) and v["pages"] == [] and v["review"] is None
    assert v["filename"] is None and v["params"] is None
    assert v["evidence"] and all(e["rect"] is None for e in v["evidence"]) and all(e["snippet"] for e in v["evidence"])
    assert v["dataset"]["id"] == ds["id"] and v["dataset"]["visibility"] == "public"
    assert v["events"][0]["by"] == "maintainer" and v["events"][0]["verifier_email"] is None
    assert v["readers"] == {"verified": 0, "flagged": 0, "same_pdf": 0, "with_pdf": 0}
    # a record outside any public dataset is not a page
    assert c.get(f"/api/records/{prid}/review", headers=R).status_code == 404
    assert c.get(f"/api/records/{uuid.uuid4()}/review", headers=R).status_code == 404
    # the owner: their own page images, signed
    v = c.get(f"/api/records/{rids[0]}/review", headers=O).json()
    assert v["viewer"]["owner"] is True and v["pages"] and "?t=" in v["pages"][0]["url"]
    assert c.get(v["pages"][0]["url"]).status_code == 200
    # the owner cannot drop a copy: they have the real thing
    assert c.post(f"/api/records/{rids[0]}/review/pdf", headers=O,
                  files={"pdf": ("p.pdf", PAPER, "application/pdf")}).status_code == 409
    # and the page itself is served
    assert c.get("/record?id=x").status_code == 200
    conn.close()


def test_readers_copy_same_and_different_file() -> None:
    _skip()
    conn = records.connect()
    owner, reader, other = (f"rv-{k}-{uuid.uuid4().hex[:6]}" for k in ("owner", "reader", "other"))
    doc, ds, rids, _ = _setup(conn, owner)
    c = _client(); R = {"X-Session-Id": reader}
    store = storage.get_store()
    # not a PDF
    r = c.post(f"/api/records/{rids[0]}/review/pdf", headers=R, files={"pdf": ("p.pdf", b"hello", "application/pdf")})
    assert r.status_code == 422
    # the same file the extractor read
    r = c.post(f"/api/records/{rids[1]}/review/pdf", headers=R, files={"pdf": ("p.pdf", PAPER, "application/pdf")})
    assert r.status_code == 200, r.text
    v = r.json(); rv = v["review"]
    assert rv["match"] == "same" and rv["n_pages"] == 1 and v["pages"] and v["focus"] == rids[1]
    tok = rv["token"]
    assert v["pages"][0]["url"].startswith(f"/artifacts/review/{tok}/pages/1.jpg?t=")
    located = [e for e in v["evidence"] if e["rect"]]
    assert located and any("Jones (2020)" in e["snippet"] for e in located)
    assert review.get_copy(conn, tok)["match"] == "same"
    assert store.exists(review.pdf_key(tok)) and store.exists(review.page_key(tok, 1)) and store.exists(review.rects_key(tok))
    # page images: by signed url for anyone holding it, by session for the reader, otherwise not
    assert c.get(v["pages"][0]["url"]).status_code == 200
    assert c.get(v["pages"][0]["url"]).headers["content-type"].startswith("image/jpeg")
    bare = v["pages"][0]["url"].split("?")[0]
    assert c.get(bare, headers=R).status_code == 200
    assert c.get(bare, headers={"X-Session-Id": other}).status_code == 403
    assert c.get(f"/artifacts/review/{tok}/source.pdf", headers=R).status_code == 404    # the copy's PDF is never served back
    # the copy follows the reader to the paper's other records, and survives a reload
    v2 = c.get(f"/api/records/{rids[0]}/review", headers=R).json()
    assert v2["review"]["token"] == tok and v2["focus"] == rids[0] and v2["pages"]
    # another reader has no copy
    assert c.get(f"/api/records/{rids[0]}/review", headers={"X-Session-Id": other}).json()["review"] is None
    # pinpoint search in the copy — the reader's alone
    r = c.get(f"/api/review/{tok}/locate", params={"value": "0.13", "page": 1}, headers=R)
    assert r.status_code == 200 and r.json()["found"] and r.json()["rects"]
    assert c.get(f"/api/review/{tok}/locate", params={"value": "0.13", "page": 1}, headers={"X-Session-Id": other}).status_code == 404
    # a different edition replaces the copy; its highlights are the quotes it does contain
    r = c.post(f"/api/records/{rids[0]}/review/pdf", headers=R, files={"pdf": ("o.pdf", OTHER, "application/pdf")})
    assert r.status_code == 200
    v3 = r.json(); tok2 = v3["review"]["token"]
    assert v3["review"]["match"] == "different" and tok2 != tok
    assert review.get_copy(conn, tok) is None and not store.exists(review.page_key(tok, 1))
    assert any(e["rect"] for e in v3["evidence"] if "Jones (2020)" in e["snippet"])
    assert all(not e["rect"] for e in v3["evidence"] if "Smith et al" in e["snippet"])
    # a document without a recorded hash: the match is unknown
    conn.execute("UPDATE extraction_document SET pdf_sha256 = NULL WHERE id = %s::uuid", (doc,)); conn.commit()
    r = c.post(f"/api/records/{rids[0]}/review/pdf", headers=R, files={"pdf": ("p.pdf", PAPER, "application/pdf")})
    assert r.json()["review"]["match"] == "unknown"
    conn.close()


def test_reader_checks_are_counted_not_adopted() -> None:
    _skip()
    conn = records.connect()
    owner, reader = f"rv-owner-{uuid.uuid4().hex[:6]}", f"rv-reader-{uuid.uuid4().hex[:6]}"
    doc, ds, rids, _ = _setup(conn, owner)
    c = _client(); R = {"X-Session-Id": reader}; O = {"X-Session-Id": owner}
    tok = c.post(f"/api/records/{rids[0]}/review/pdf", headers=R, files={"pdf": ("p.pdf", PAPER, "application/pdf")}).json()["review"]["token"]
    status = lambda: conn.execute("SELECT verification_status FROM record WHERE id = %s::uuid", (rids[0],)).fetchone()[0]  # noqa: E731
    # a confirmation with the copy attached: recorded as a reader's, the record's status untouched
    r = c.post(f"/api/records/{rids[0]}/verify", headers=R, json={"status": "verified", "review_token": tok})
    assert r.status_code == 200, r.text
    assert r.json()["verifier_kind"] == "community" and status() == "unverified"
    ev = c.get(f"/api/records/{rids[0]}/events", headers=R).json()["events"]
    assert ev[0]["context"] == {"review": {"own_pdf": True, "match": "same"}} and ev[0]["by"] == "a reader" and ev[0]["verifier_email"] is None
    # a flag with a proposed correction: the diff is kept, the values are not changed
    fv = records.get_record(conn, rids[0])["field_values"]
    r = c.post(f"/api/records/{rids[0]}/verify", headers=R,
               json={"status": "flagged", "notes": "the CI reads 0.04 to 0.22", "review_token": tok,
                     "diff": [{"field_path": "n", "original_value": fv.get("n"), "final_value": 489}]})
    assert r.status_code == 200, r.text
    assert records.get_record(conn, rids[0])["field_values"] == fv and status() == "unverified"
    # a reader may not change values, nor attach a diff to anything but a flag, nor use another's copy
    assert c.post(f"/api/records/{rids[0]}/verify", headers=R, json={"status": "corrected", "field_values": {**fv, "n": 1}}).status_code == 403
    assert c.post(f"/api/records/{rids[0]}/verify", headers=R, json={"status": "verified", "diff": [{"field_path": "n", "original_value": 1, "final_value": 2}]}).status_code == 403
    assert c.post(f"/api/records/{rids[0]}/verify", headers={"X-Session-Id": "someone"}, json={"status": "verified", "review_token": tok}).status_code == 404
    # a check without a copy is recorded as such
    assert c.post(f"/api/records/{rids[1]}/verify", headers=R, json={"status": "verified"}).status_code == 200
    v = c.get(f"/api/records/{rids[0]}/review", headers=R).json()
    assert v["readers"] == {"verified": 1, "flagged": 1, "same_pdf": 2, "with_pdf": 2}
    assert c.get(f"/api/records/{rids[1]}/review", headers=R).json()["readers"]["with_pdf"] == 0
    # the owner's verdict still projects, and the owner sees the reader's proposal in the history
    assert c.post(f"/api/records/{rids[0]}/verify", headers=O, json={"status": "verified"}).status_code == 200
    assert status() == "verified"
    ev = c.get(f"/api/records/{rids[0]}/events", headers=O).json()["events"]
    assert any(e["verifier_kind"] == "community" and e["diff"] for e in ev)
    conn.close()


def test_copy_removal_by_hand_with_the_session_and_by_the_sweep() -> None:
    _skip()
    conn = records.connect()
    owner, reader = f"rv-owner-{uuid.uuid4().hex[:6]}", f"rv-reader-{uuid.uuid4().hex[:6]}"
    doc, ds, rids, _ = _setup(conn, owner)
    c = _client(); R = {"X-Session-Id": reader}
    store = storage.get_store()
    drop = lambda: c.post(f"/api/records/{rids[0]}/review/pdf", headers=R, files={"pdf": ("p.pdf", PAPER, "application/pdf")}).json()["review"]["token"]  # noqa: E731
    # by hand
    tok = drop()
    assert c.delete(f"/api/review/{tok}", headers={"X-Session-Id": "other"}).status_code == 404
    assert c.delete(f"/api/review/{tok}", headers=R).status_code == 200
    assert review.get_copy(conn, tok) is None and not store.exists(review.page_key(tok, 1)) and not store.exists(review.pdf_key(tok))
    assert c.get(f"/api/records/{rids[0]}/review", headers=R).json()["review"] is None
    # with the session ('delete my data now')
    tok = drop()
    assert c.post("/api/session/forget", headers=R).status_code == 200
    assert review.get_copy(conn, tok) is None and not store.exists(review.pdf_key(tok))
    # by the sweep, once unused for the retention period — a fresh copy stays
    tok = drop()
    conn.execute("UPDATE review_copy SET last_used = now() - interval '3 hours' WHERE token = %s", (tok,)); conn.commit()
    fresh = c.post(f"/api/records/{rids[1]}/review/pdf", headers={"X-Session-Id": "rv-fresh"},
                   files={"pdf": ("p.pdf", PAPER, "application/pdf")}).json()["review"]["token"]
    out = retention.sweep(conn, store)
    assert out["review_copies"] >= 1
    assert review.get_copy(conn, tok) is None and not store.exists(review.pdf_key(tok))
    assert review.get_copy(conn, fresh) is not None and store.exists(review.pdf_key(fresh))
    # looking at an expired copy removes it too
    conn.execute("UPDATE review_copy SET last_used = now() - interval '3 hours' WHERE token = %s", (fresh,)); conn.commit()
    assert c.get(f"/api/records/{rids[1]}/review", headers={"X-Session-Id": "rv-fresh"}).json()["review"] is None
    assert review.get_copy(conn, fresh) is None
    conn.close()


def test_a_reader_holds_a_bounded_number_of_copies(monkeypatch) -> None:
    _skip()
    conn = records.connect()
    owner, reader = f"rv-owner-{uuid.uuid4().hex[:6]}", f"rv-reader-{uuid.uuid4().hex[:6]}"
    _, _, rids, _ = _setup(conn, owner)
    _, _, rids2, _ = _setup(conn, owner)
    c = _client(); R = {"X-Session-Id": reader}
    monkeypatch.setattr(review, "MAX_COPIES_PER_READER", 1)
    assert c.post(f"/api/records/{rids[0]}/review/pdf", headers=R, files={"pdf": ("p.pdf", PAPER, "application/pdf")}).status_code == 200
    # the same paper again replaces the copy; another paper is one too many
    assert c.post(f"/api/records/{rids[1]}/review/pdf", headers=R, files={"pdf": ("p.pdf", OTHER, "application/pdf")}).status_code == 200
    assert c.post(f"/api/records/{rids2[0]}/review/pdf", headers=R, files={"pdf": ("p.pdf", PAPER, "application/pdf")}).status_code == 429
    tok = c.get(f"/api/records/{rids[0]}/review", headers=R).json()["review"]["token"]
    assert c.delete(f"/api/review/{tok}", headers=R).status_code == 200
    assert c.post(f"/api/records/{rids2[0]}/review/pdf", headers=R, files={"pdf": ("p.pdf", PAPER, "application/pdf")}).status_code == 200
    conn.close()
