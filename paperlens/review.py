"""Checking a published record against the paper, with the reader's own copy of the PDF.

A record in a public dataset is readable by anyone (``GET /api/records/{id}``) and anyone may
record an opinion on it (``verify``: verified / flagged, with a note). What a reader could not
do was SEE the evidence: page images and highlight geometry belong to the owner's document,
and the owner's PDF is never served — dashboards link into a workspace that opens for the
owner alone. This module closes that gap without serving the PDF.

The reader drops their own copy of the paper. It is hashed and compared with the hash of the
owner's PDF (``extraction_document.pdf_sha256``), rendered to page images, and the document's
evidence quotes are located in it with the locator the extraction used, so the public record
page (``/record?id=…``) shows the quotes highlighted in the reader's copy and a clicked number
is pinpointed in it. The copy is the reader's: bound to their browser session or account, kept
under ``review/<token>/`` in object storage, deleted once unused for the retention period (the
worker's sweep), on request, or with the session's other data — and it never reaches the
dataset owner. A verification made with a copy attached carries ``context.review`` =
``{own_pdf, match}``, so a check against the very same file can be told from one against a
different edition, or none.

Reader checks never change the maintainer's verification status of a record
(``records.verify_record(project=False)``): they are counted, shown on the record page, and
the owner meets them in the record's history and as the latest note on the card.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import secrets

import psycopg

from . import pdf_utils, records, storage

# how long a copy survives without being looked at; the logged-out retention period by default
COPY_RETENTION_MINUTES = int(os.environ.get(
    "PAPERLENS_REVIEW_COPY_MINUTES", os.environ.get("PAPERLENS_ANON_RETENTION_MINUTES", "120")))
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_COPIES_PER_READER = int(os.environ.get("PAPERLENS_REVIEW_COPIES_PER_READER", "20"))   # live copies one reader may hold

_COLS = "token, document_id, session_id, owner_user_id, pdf_sha256, match, n_pages, created_at, last_used"
_ROLE = {"community": "a reader", "paperlens": "Metalens", "maintainer": "maintainer"}


def prefix(token: str) -> str:
    return f"review/{token}/"


def pdf_key(token: str) -> str:
    return f"review/{token}/source.pdf"


def page_key(token: str, page: int) -> str:
    return f"review/{token}/pages/{page}.jpg"


def rects_key(token: str) -> str:
    return f"review/{token}/rects.json"


def token_from_key(key: str) -> str | None:
    """The copy a storage key belongs to, for the artifact gate — page images only; the copy's
    PDF is never served back, not even to its reader."""
    parts = key.split("/")
    if len(parts) == 4 and parts[0] == "review" and parts[2] == "pages" and parts[1].isalnum():
        return parts[1]
    return None


def _row(r) -> dict:
    return {"token": r[0], "document_id": str(r[1]), "session_id": r[2],
            "owner_user_id": str(r[3]) if r[3] else None, "pdf_sha256": r[4], "match": r[5],
            "n_pages": int(r[6] or 0), "created_at": r[7], "last_used": r[8]}


def _expired(copy: dict, *, now: dt.datetime | None = None, max_age_minutes: int | None = None) -> bool:
    age = COPY_RETENTION_MINUTES if max_age_minutes is None else max_age_minutes
    now = now or dt.datetime.now(dt.timezone.utc)
    last = copy["last_used"]
    if last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    return last + dt.timedelta(minutes=age) < now


def public_copy(copy: dict) -> dict:
    """What the reader is told about their copy."""
    last = copy["last_used"]
    return {"token": copy["token"], "match": copy["match"], "n_pages": copy["n_pages"],
            "created_at": copy["created_at"].isoformat() if copy.get("created_at") else None,
            "expires_at": (last + dt.timedelta(minutes=COPY_RETENTION_MINUTES)).isoformat() if last else None,
            "retention_minutes": COPY_RETENTION_MINUTES}


# ── who may see what ────────────────────────────────────────────────────────────────────────

def visible_record_ids(conn: psycopg.Connection, document_id: str, principal) -> list[str]:
    """The document's records this principal may read: every one for the owner, else those
    that sit in a public dataset."""
    if records.is_document_owner(conn, document_id, principal):
        rows = conn.execute("SELECT id FROM record WHERE document_id = %s::uuid ORDER BY entry_index",
                            (document_id,)).fetchall()
    else:
        rows = conn.execute(
            """SELECT r.id FROM record r JOIN dataset d ON d.id = r.dataset_id
               WHERE r.document_id = %s::uuid AND d.visibility = 'public' ORDER BY r.entry_index""",
            (document_id,)).fetchall()
    return [str(r[0]) for r in rows]


def _role(by) -> str | None:
    """An editor as a reader may see them: a role, never an email."""
    if by is None:
        return None
    s = str(by)
    return "maintainer" if "@" in s else _ROLE.get(s, s)


def mask_events(events: list[dict], *, owner: bool) -> list[dict]:
    """A record's history as the viewer may see it: the owner sees who (email); a reader sees
    roles. ``by`` is the display name either way."""
    out = []
    for e in events:
        e = dict(e)
        kind = e.get("verifier_kind") or "maintainer"
        if owner and e.get("verifier_email"):
            e["by"] = e["verifier_email"]
        else:
            e["verifier_email"] = None
            e["by"] = _ROLE.get(kind, kind)
        out.append(e)
    return out


def reader_tally(events: list[dict]) -> dict:
    """What readers have said about a record: confirmations, disputes, and how many of them
    checked the very same PDF file."""
    t = {"verified": 0, "flagged": 0, "same_pdf": 0, "with_pdf": 0}
    for e in events:
        if (e.get("verifier_kind") or "") != "community" or e.get("status") not in ("verified", "flagged"):
            continue
        t[e["status"]] += 1
        rv = ((e.get("context") or {}).get("review") or {}) if isinstance(e.get("context"), dict) else {}
        if rv.get("own_pdf"):
            t["with_pdf"] += 1
            if rv.get("match") == "same":
                t["same_pdf"] += 1
    return t


# ── the reader's copy ───────────────────────────────────────────────────────────────────────

def get_copy(conn: psycopg.Connection, token: str) -> dict | None:
    r = conn.execute(f"SELECT {_COLS} FROM review_copy WHERE token = %s", (token,)).fetchone()
    return _row(r) if r else None


def copies_for(conn: psycopg.Connection, document_id: str, principal) -> list[dict]:
    """Every copy of this document the principal holds (normally one)."""
    if principal is None or not (principal.user_id or principal.session_id):
        return []
    rows = conn.execute(
        f"""SELECT {_COLS} FROM review_copy WHERE document_id = %s::uuid
            AND ((owner_user_id IS NOT NULL AND owner_user_id = %s::uuid)
                 OR (owner_user_id IS NULL AND session_id = %s))
            ORDER BY created_at DESC""",
        (document_id, principal.user_id, principal.session_id)).fetchall()
    return [_row(r) for r in rows]


def live_count(conn: psycopg.Connection, principal) -> int:
    """How many copies (of any paper) the principal holds right now — the cap on uploads."""
    if principal is None or not (principal.user_id or principal.session_id):
        return 0
    return conn.execute(
        """SELECT count(*) FROM review_copy
           WHERE ((owner_user_id IS NOT NULL AND owner_user_id = %s::uuid)
                  OR (owner_user_id IS NULL AND session_id = %s))
             AND last_used + (%s * interval '1 minute') >= now()""",
        (principal.user_id, principal.session_id, COPY_RETENTION_MINUTES)).fetchone()[0]


def _touch(conn: psycopg.Connection, token: str) -> None:
    with conn.transaction():
        conn.execute("UPDATE review_copy SET last_used = now() WHERE token = %s", (token,))


def current_copy(conn: psycopg.Connection, document_id: str, principal, *, store=None) -> dict | None:
    """The principal's live copy of this document, its clock refreshed; an expired one is
    deleted on the way."""
    for copy in copies_for(conn, document_id, principal):
        if _expired(copy):
            delete_copy(conn, copy["token"], store=store)
            continue
        _touch(conn, copy["token"])
        return copy
    return None


def copy_by_token(conn: psycopg.Connection, token: str, principal, *, store=None) -> dict | None:
    """A copy by its token, for its reader only (the token alone opens nothing through the API;
    page images use a signed url instead)."""
    copy = get_copy(conn, token)
    if copy is None or not records._owns(principal, copy["owner_user_id"], copy["session_id"]):
        return None
    if _expired(copy):
        delete_copy(conn, token, store=store)
        return None
    _touch(conn, token)
    return copy


def create_copy(conn: psycopg.Connection, document_id: str, principal, pdf_bytes: bytes, *,
                store=None) -> dict:
    """Store the reader's copy of the paper: hash it against the owner's PDF, render the pages,
    locate the document's quotes in it. Replaces the reader's previous copy of this document."""
    store = store or storage.get_store()
    if not pdf_bytes or not pdf_bytes.lstrip()[:5].startswith(b"%PDF"):
        raise ValueError("That file is not a PDF.")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ValueError("That PDF is larger than 50 MB.")
    row = conn.execute("SELECT pdf_sha256 FROM extraction_document WHERE id = %s::uuid", (document_id,)).fetchone()
    if row is None:
        raise LookupError("Document not found.")
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    match = "unknown" if not row[0] else ("same" if row[0] == sha else "different")
    items, seen = [], set()
    for fp, sn, pg, src in conn.execute(
            """SELECT field_path, snippet, page, source FROM evidence_span
               WHERE document_id = %s::uuid AND coalesce(snippet, '') <> '' ORDER BY ord""",
            (document_id,)).fetchall():
        if (fp, sn) in seen:
            continue
        seen.add((fp, sn))
        items.append({"page": pg, "snippet": sn, "field": fp, "source": src})
    pages, highlights, scanned = pdf_utils.pdf_to_pages_with_rects(pdf_bytes, items)
    token = secrets.token_hex(16)
    store.put(pdf_key(token), pdf_bytes, "application/pdf")
    for n, b64 in enumerate(pages, start=1):
        store.put(page_key(token, n), base64.b64decode(b64), "image/jpeg")
    store.put(rects_key(token), json.dumps({"highlights": highlights, "scanned": scanned}).encode("utf-8"),
              "application/json")
    for old in copies_for(conn, document_id, principal):     # one copy per reader and paper
        delete_copy(conn, old["token"], store=store)
    with conn.transaction():
        conn.execute(
            """INSERT INTO review_copy (token, document_id, session_id, owner_user_id, pdf_sha256, match, n_pages)
               VALUES (%s, %s::uuid, %s, %s::uuid, %s, %s, %s)""",
            (token, document_id, principal.session_id, principal.user_id, sha, match, len(pages)))
    return get_copy(conn, token)


def delete_copy(conn: psycopg.Connection, token: str, *, store=None) -> None:
    store = store or storage.get_store()
    try:
        store.delete_prefix(prefix(token))
    except Exception:  # noqa: BLE001 - the row goes regardless; blobs are best-effort
        pass
    with conn.transaction():
        conn.execute("DELETE FROM review_copy WHERE token = %s", (token,))


def forget_session(conn: psycopg.Connection, session_id: str, *, store=None) -> int:
    """Every copy an anonymous session holds, now ('delete my data' and the sweep)."""
    tokens = [r[0] for r in conn.execute(
        "SELECT token FROM review_copy WHERE session_id = %s AND owner_user_id IS NULL", (session_id,)).fetchall()]
    for t in tokens:
        delete_copy(conn, t, store=store)
    return len(tokens)


def sweep(conn: psycopg.Connection, store=None, *, max_age_minutes: int | None = None,
          now: dt.datetime | None = None) -> int:
    """Copies nobody has looked at for the retention period — readers' and accounts' alike: a
    copy is a convenience for one sitting, not a holding."""
    gone = 0
    for r in conn.execute(f"SELECT {_COLS} FROM review_copy ORDER BY last_used").fetchall():
        copy = _row(r)
        if _expired(copy, now=now, max_age_minutes=max_age_minutes):
            delete_copy(conn, copy["token"], store=store)
            gone += 1
    return gone


def apply_copy(view: dict, copy: dict, *, store=None) -> None:
    """Swap the reader's copy into a review payload: its page images, and the quotes' rects and
    resolved pages as located in THAT file (matched by quote, as ``records.attach_rects`` does —
    the same words sit in the same place whichever field cites them)."""
    store = store or storage.get_store()
    try:
        data = json.loads(store.get(rects_key(copy["token"])).decode("utf-8"))
    except Exception:  # noqa: BLE001 - a lost rects file leaves the pages without highlights
        data = {}
    by_snippet: dict[str, tuple] = {}
    for h in data.get("highlights") or []:
        by_snippet.setdefault(h.get("snippet"), (h.get("page"), h.get("rects")))
    for e in view.get("evidence", []):
        hit = by_snippet.get(e.get("snippet"))
        if hit:
            e["page"], e["rect"] = hit
        else:
            e["rect"] = None
    view["pages"] = [{"page": n, "url": store.url(page_key(copy["token"], n))} for n in range(1, copy["n_pages"] + 1)]
    view["scanned_pages"] = data.get("scanned") or []
    view["review"] = public_copy(copy)


def locate(copy: dict, value: str, page: int, *, bands: str | None = None,
           anchor: list[str] | None = None, page_only: bool = False, store=None) -> dict:
    """Pinpoint a clicked value in the reader's copy (the workspace's search, on their file)."""
    store = store or storage.get_store()
    key = pdf_key(copy["token"])
    if not store.exists(key):
        return {"rects": [], "found": False, "no_pdf": True}
    return pdf_utils.locate_in_pdf(store.get(key), page, value, bands=bands, anchor=anchor, page_only=page_only)


# ── the record page's payload ───────────────────────────────────────────────────────────────

def public_view(conn: psycopg.Connection, record_id: str, principal, *, store=None) -> dict | None:
    """The workspace's document payload, cut to what this viewer may see of it, focused on one
    record: the owner gets everything (their own page images included); a reader gets the
    document's public records, their quotes and page numbers, roles instead of emails, no
    file name, no geometry of a PDF they cannot see — plus their own copy's pages and rects
    when they have dropped one. None when the record is not visible to the principal."""
    rec = records.get_record(conn, record_id)
    if rec is None or not records.record_is_visible(conn, record_id, principal):
        return None
    store = store or storage.get_store()
    doc_id = rec["document_id"]
    owner = records.is_document_owner(conn, doc_id, principal)
    view = records.document_view(conn, doc_id, store=store)
    if view is None:
        return None
    if not owner:
        vis = set(visible_record_ids(conn, doc_id, principal))
        view["records"] = [r for r in view["records"] if r["id"] in vis]
        view["evidence"] = [e for e in view["evidence"]
                            if e.get("record_id") in vis or (e.get("record_id") is None and e.get("entry_index") is None)]
        for r in view["records"]:
            for c in r.get("corrections") or []:
                c["editor"] = _role(c.get("editor"))
            if r.get("note"):
                r["note"]["by"] = _role(r["note"].get("by"))
        view["filename"] = None
        view["params"] = None
        for e in view["evidence"]:
            e["rect"] = None
        view["pages"] = []
    view["review"] = None
    view["scanned_pages"] = []
    if not owner:
        copy = current_copy(conn, doc_id, principal, store=store)
        if copy:
            apply_copy(view, copy, store=store)
    ds = records.get_dataset(conn, rec["dataset_id"]) if rec.get("dataset_id") else None
    view["dataset"] = ({"id": ds["id"], "slug": ds.get("slug"), "title": ds.get("title"),
                        "visibility": ds.get("visibility")} if ds else None)
    events = records.record_events(conn, record_id)
    view["events"] = mask_events(events, owner=owner)
    view["readers"] = reader_tally(events)
    view["focus"] = record_id
    view["viewer"] = {"owner": owner, "anonymous": principal.user_id is None}
    return view
