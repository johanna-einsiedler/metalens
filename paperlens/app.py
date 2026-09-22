"""FastAPI read path for the Phase 1 record spine.

Endpoints:
  POST /api/ingest               -- canonical JSON -> normalized records (returns ids)
  GET  /api/papers/lookup?doi=   -- paper coverage / passport (plan §3.5)
  GET  /api/papers/{id}/records  -- the normalized records for a paper

Browser-side API keys and the anonymous flow are preserved: requests are scoped
by current_principal(), which is anonymous (session-id) in Phase 1.
"""
from __future__ import annotations

from typing import Any, Generator

import base64
import hashlib
import hmac
import os
import time
import uuid

from fastapi import (Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Request,
                     Query, Response, UploadFile)
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, brands, credits, enrich, extract, localmode, presets, providers, records, storage, worker, retention
from .ingest import ingest
from .principal import Principal

_SESSION_COOKIE = "pl_session"

app = FastAPI(title="PaperLens record spine", version="0.1.0")


@app.on_event("startup")
def _migrate_on_startup() -> None:
    """Apply the idempotent schema (schema.sql via init_db) when the app boots, so a deploy
    is self-migrating even if the platform's release_command didn't run. Best-effort: a
    migration hiccup must never stop the app from starting."""
    try:
        conn = records.connect()
        try:
            records.init_db(conn)
        finally:
            conn.close()
    except Exception as exc:   # pragma: no cover - migration is best-effort
        import logging
        logging.getLogger("paperlens").warning("startup init_db skipped: %s", exc)

# ── beta gate ────────────────────────────────────────────────────────────────
# A single shared-password HTTP Basic Auth in front of the WHOLE site (beta
# testing). Reads PAPERLENS_BASIC_PASSWORD per request, so it's a complete no-op
# when unset — local dev and the test-suite are untouched. `/healthz` is exempt
# so Fly's health checks (which send no credentials) still pass.
_GATE_EXEMPT = frozenset({"/healthz"})


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.middleware("http")
async def _beta_password_gate(request, call_next):
    password = os.environ.get("PAPERLENS_BASIC_PASSWORD")
    if password and request.url.path not in _GATE_EXEMPT:
        expected_user = os.environ.get("PAPERLENS_BASIC_USER", "beta")
        ok = False
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, supplied = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                ok = (hmac.compare_digest(user, expected_user)
                      and hmac.compare_digest(supplied, password))
            except Exception:
                ok = False
        if not ok:
            realm = brands.resolve(request.headers.get("host")).title
            return Response(status_code=401,
                            headers={"WWW-Authenticate": f'Basic realm="{realm} beta"'})
    return await call_next(request)


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class _RevalidateStatic(StaticFiles):
    """Serve static assets with ``Cache-Control: no-cache`` so browsers always
    REVALIDATE before reusing a cached copy. This is a no-build ES-module front
    end: ``extract.js`` et al. are loaded by url with no content hash, so without
    this header browsers apply *heuristic* freshness and keep serving a stale
    module after a code change — you then see a fresh ``/api`` response (new
    preset title) alongside old JS behaviour (old routing). ``no-cache`` still
    lets the ETag/Last-Modified do their job: unchanged files come back as a
    cheap 304, changed files are re-fetched. (In prod behind a CDN you'd instead
    hash filenames + long-cache; here correctness during iteration wins.)"""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        resp = await super().get_response(path, scope)
        resp.headers.setdefault("Cache-Control", "no-cache")
        return resp


app.mount("/static", _RevalidateStatic(directory=_STATIC_DIR), name="static")

# Locally-stored artifacts (PDFs / page images) are served by the OWNER-GATED
# `/artifacts/{key}` route below — never a public mount — so dev mirrors prod's
# private access model. In prod the S3/R2 backend returns short-TTL presigned urls.


def _page(name: str) -> FileResponse:
    # Same revalidation contract for the HTML shells that import those modules.
    return FileResponse(os.path.join(_STATIC_DIR, name),
                        headers={"Cache-Control": "no-cache"})


def brand(request: Request) -> brands.Brand:
    """The product surface this request belongs to (Host header, or PAPERLENS_BRAND)."""
    return brands.resolve(request.headers.get("host"))


@app.get("/")
def landing(b: brands.Brand = Depends(brand)) -> FileResponse:
    """Public entry — the brand's own landing page."""
    return _page(b.landing)


@app.get("/maseminer")
def maseminer_landing() -> FileResponse:
    """The MASEMiner landing, reachable on every host (its own host serves it at /)."""
    return _page("maseminer.html")


@app.get("/api/version")
def version_info() -> dict:
    """What to cite: engine version + commit, and each built-in preset's content-addressed
    schema id (the data contract a run was made under)."""
    from . import __version__, preset_spec
    return {"version": __version__, "git_sha": _git_sha(),
            "presets": {pid: preset_spec.schema_id(spec) for pid, spec in presets.load_all().items()},
            "local_mode": localmode.enabled()}


def _git_sha() -> str | None:
    sha = os.environ.get("PAPERLENS_GIT_SHA")
    if sha:
        return sha
    head = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".git", "HEAD")
    try:
        ref = open(head).read().strip()
        if ref.startswith("ref: "):
            return open(os.path.join(os.path.dirname(head), ref[5:])).read().strip()[:12]
        return ref[:12]
    except OSError:
        return None


@app.get("/api/brand")
def brand_info(b: brands.Brand = Depends(brand)) -> dict:
    """Public, no DB: what the page chrome needs — title, logo, nav, links, default preset."""
    return b.as_json()


@app.get("/dashboards")
def dashboards_page() -> FileResponse:
    """Public: every published dashboard — built in Metalens or registered from elsewhere."""
    return _page("dashboards.html")


@app.get("/catalog")
def catalog() -> FileResponse:
    """The published datasets, one card each (records are read inside a dataset)."""
    return _page("catalog.html")


@app.get("/extract")
def extract_page() -> FileResponse:
    """The extraction workflow — pick a task, supply a browser-side key, upload a PDF."""
    return _page("extract.html")


@app.get("/import")
def import_page() -> FileResponse:
    """Import pre-computed extractions (JSON) + their source PDFs into the viewer."""
    return _page("import.html")


@app.get("/workspace")
def workspace() -> FileResponse:
    """PDF + highlight overlays alongside the extracted records (verify / edit-in-place)."""
    return _page("workspace.html")


@app.get("/observatory")
@app.get("/builder")
@app.get("/analysis")
def retired_pages() -> RedirectResponse:
    """The first dashboard pages (observatory, builder, analysis) were replaced by the
    dashboard builder (/compose, /dashboard); old links land on the start page."""
    return RedirectResponse("/", status_code=307)


@app.get("/projects")
def projects_page() -> FileResponse:
    """My Workspace: the signed-in hub listing the user's datasets + analyses."""
    return _page("projects.html")


@app.get("/dataset")
def dataset_page() -> FileResponse:
    """Dataset overview (?id=…): recipe + stats + papers (add / delete / review)."""
    return _page("dataset.html")


@app.get("/preset")
def preset_page() -> FileResponse:
    """Personal preset editor: create (no ?id) or edit (?id=…) an owned preset."""
    return _page("preset.html")


@app.get("/compose")
def compose_page(dataset: str | None = None, dashboard: str | None = None) -> RedirectResponse:
    """The composer became the edit mode of the dashboard page; old links keep working."""
    from urllib.parse import urlencode
    q = {"id": dashboard, "edit": 1} if dashboard else {"dataset": dataset or "", "edit": 1}
    return RedirectResponse(f"/dashboard?{urlencode(q)}", status_code=307)


@app.get("/dashboard")
def dashboard_page() -> FileResponse:
    """A dashboard over a dataset's analysis table: D3 blocks whose every mark traces back to
    its paper, extraction, verification status and evidence quote."""
    return _page("dashboard.html")


@app.get("/faq")
def faq_page() -> FileResponse:
    """What happens to API keys, PDFs and extracted data; legal access; publishing."""
    return _page("faq.html")


@app.get("/account")
def account_page() -> FileResponse:
    """Account settings: citation name, password, API keys (browser-only), delete."""
    return _page("account.html")


def get_db() -> Generator:
    conn = records.connect()
    try:
        yield conn
    finally:
        conn.close()


def principal(
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
    pl_session: str | None = Cookie(default=None),
    db=Depends(get_db),
) -> Principal:
    """Resolve the acting principal: anonymous via X-Session-Id, authenticated via
    the session cookie (Phase 2). Both coexist — logged-out flow is unchanged."""
    if localmode.enabled():                # one machine, one owner: every request is them
        return Principal(session_id=x_session_id, user_id=localmode.local_user_id())
    user_id = auth.resolve_session(db, pl_session)
    try:                                   # activity clock for logged-out retention; never fatal
        retention.touch(db, x_session_id, user_id)
    except Exception:  # noqa: BLE001
        pass
    return Principal(session_id=x_session_id, user_id=user_id)


def _no_accounts_locally() -> None:
    if localmode.enabled():
        raise HTTPException(status_code=404, detail="Accounts do not exist in local mode.")


@app.post("/api/session/forget")
def session_forget(who: Principal = Depends(principal), db=Depends(get_db)) -> dict:
    """'Delete my data now' for a logged-out session: everything the anonymous session
    owns goes immediately (a signed-in user's rows are never touched)."""
    if who.user_id or not who.session_id:
        return {"deleted": False, "reason": "signed in — delete papers from your workspace"}
    out = retention.forget_session(db, who.session_id)
    retention.remove_orphans(db, storage.get_store())
    return {"deleted": True, **out}


# ── owner-gated artifact serving (replaces the old public StaticFiles mount) ──
def _secret() -> bytes:
    """PAPERLENS_SECRET signs artifact URLs and salts the trial hashes. A dev checkout may run
    without one; a deployment that stores blobs in S3/R2 or sets secure cookies may not."""
    s = os.environ.get("PAPERLENS_SECRET", "")
    if not s:
        prod = os.environ.get("PAPERLENS_STORAGE", "local").lower() == "s3" or os.environ.get("PAPERLENS_SECURE_COOKIES") == "1"
        if prod:
            raise RuntimeError("PAPERLENS_SECRET is not set: `fly secrets set PAPERLENS_SECRET=$(openssl rand -hex 32)`.")
        s = "dev-artifact-secret"
    return s.encode()


_ARTIFACT_SECRET = _secret()


def _sign_artifact(doc_id: str, ttl: int = 3600) -> str:
    """A short-lived capability token so an anonymous owner's <img> tags (which
    can't send X-Session-Id) can fetch their own local page images."""
    exp = int(time.time()) + ttl
    mac = hmac.new(_ARTIFACT_SECRET, f"{doc_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{mac}"


def _verify_artifact(doc_id: str, token: str | None) -> bool:
    try:
        exp_s, mac = (token or "").split(".", 1)
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < time.time():
        return False
    good = hmac.new(_ARTIFACT_SECRET, f"{doc_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(mac, good)


def _doc_id_from_key(key: str) -> str | None:
    parts = key.split("/")
    if len(parts) == 2 and parts[0] == "pdf" and parts[1].endswith(".pdf"):
        return parts[1][:-4]
    if len(parts) == 3 and parts[0] == "pages":
        return parts[1]
    return None


@app.get("/artifacts/{key:path}")
def artifact(key: str, t: str | None = None, db=Depends(get_db),
             who: Principal = Depends(principal)) -> Response:
    """Owner-gated serving of locally-stored PDFs / page images. Authorized by a
    signed token (minted in document_view) OR by the principal owning the doc."""
    doc_id = _doc_id_from_key(key)
    if doc_id is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if not (_verify_artifact(doc_id, t) or records.is_document_owner(db, doc_id, who)):
        raise HTTPException(status_code=403, detail="Not authorized.")
    store = storage.get_store()
    if not store.exists(key):
        raise HTTPException(status_code=404, detail="Not found.")
    media = "application/pdf" if key.endswith(".pdf") else "image/jpeg"
    return Response(content=store.get(key), media_type=media)


class IngestBody(BaseModel):
    result: Any                       # canonical JSON (object or stringified)
    schema_id: str | None = None
    source_job_id: str | None = None
    dataset_id: str | None = None     # add the document to this (owned) dataset
    # Re-importing a corrected JSON for a paper already uploaded: take the PDF from that
    # (owned) document so page images and highlights carry over — a replace must never
    # lose the PDF just because the JSON came alone.
    pdf_from_document_id: str | None = None


@app.post("/api/ingest")
def ingest_endpoint(body: IngestBody, db=Depends(get_db),
                    who: Principal = Depends(principal)) -> dict:
    if body.dataset_id and not records.is_dataset_owner(db, body.dataset_id, who):
        raise HTTPException(status_code=403, detail="You don't own that dataset.")
    if body.pdf_from_document_id:
        if not records.is_document_owner(db, body.pdf_from_document_id, who):
            raise HTTPException(status_code=403, detail="Not your document.")
        key = storage.pdf_key(body.pdf_from_document_id)
        store = storage.get_store()
        if store.exists(key):
            fn = db.execute("SELECT filename FROM extraction_document WHERE id = %s::uuid",
                            (body.pdf_from_document_id,)).fetchone()
            return _ingest_with_pdf(db, who, store.get(key), body.result, body.schema_id,
                                    body.dataset_id, filename=fn[0] if fn else None)
        # no stored PDF on the old copy either → plain JSON import below
    run = presets.resolve_run(db, schema_id=body.schema_id)
    try:
        res = ingest(body.result, entries_key=run.entries_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if run.schema_id:
        with db.transaction():
            records.upsert_schema(db, run.schema_id, run.field_defs)
    doc_id = records.persist(
        db, res, schema_id=run.schema_id, source_job_id=body.source_job_id,
        session_id=who.session_id, owner_user_id=who.user_id,
    )
    try:      # keep what was imported verbatim — the counterpart of a live run's model response
        import json as _json
        raw = body.result if isinstance(body.result, str) else _json.dumps(body.result, ensure_ascii=False, indent=2)
        storage.get_store().put(storage.raw_key(doc_id), raw.encode("utf-8"), "text/plain; charset=utf-8")
    except Exception:  # noqa: BLE001 - never fail an import over the debugging copy
        pass
    if body.dataset_id:
        records.assign_document_to_dataset(db, body.dataset_id, doc_id)
    paper_id = db.execute(
        "SELECT paper_id FROM extraction_document WHERE id = %s", (doc_id,)
    ).fetchone()[0]
    return {
        "document_id": doc_id,
        "paper_id": str(paper_id),
        "n_records": len(res.records),
        "n_evidence": len(res.evidence),
        "doi": res.doi,
    }


@app.get("/api/papers/coverage")
def papers_coverage(q: str, db=Depends(get_db)) -> dict:
    """Public: is this paper (DOI or title words) part of a published dataset? Each hit lists
    the catalogue datasets that contain it."""
    return {"q": q, "papers": records.paper_coverage_search(db, q)}


@app.get("/api/papers/lookup")
def papers_lookup(doi: str, db=Depends(get_db)) -> dict:
    cov = records.paper_coverage(db, doi)
    if cov is None:
        raise HTTPException(status_code=404, detail="Paper not in catalog.")
    return cov


@app.get("/api/papers/{paper_id}/records")
def papers_records(paper_id: str, db=Depends(get_db)) -> dict:
    return {"paper_id": paper_id, "records": records.records_for_paper(db, paper_id)}


@app.get("/api/documents")
def list_documents(limit: int = 50, dataset: str | None = None, db=Depends(get_db),
                   who: Principal = Depends(principal)) -> dict:
    """The principal's extraction documents (their workspace index); optionally
    restricted to a single project via ``dataset``."""
    return {"documents": records.list_documents(
        db, limit=limit, owner_user_id=who.user_id, session_id=who.session_id,
        dataset_id=dataset)}


@app.get("/api/documents/{document_id}/view")
def document_view(document_id: str, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    """Full viewer payload: paper + schema grammar + pages + records + evidence rects.
    Owner-only — documents and their page images are NEVER public (the bright wall);
    this is also the mint-time gate for the page-image URLs in the payload."""
    if not records.is_document_owner(db, document_id, who):
        raise HTTPException(status_code=404, detail="Document not found.")
    v = records.document_view(db, document_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    # Local backend: sign page urls so the owner's <img> tags fetch through the gate.
    if isinstance(storage.get_store(), storage.LocalObjectStore):
        tok = _sign_artifact(document_id)
        for pg in v.get("pages", []):
            pg["url"] = f"{pg['url']}?t={tok}"
    return v


@app.get("/api/documents/{document_id}/locate")
def locate_value(document_id: str, value: str, page: int, bands: str | None = None,
                 anchor: list[str] | None = Query(None), page_only: bool = False,
                 db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Owner-only: find ``value`` in the source PDF so the workspace can pinpoint-
    highlight it — tries an exact numeric match first, then a literal text search so a
    non-numeric field the model didn't cite still highlights. `found=false` = not there
    verbatim (may be transformed/rounded/paraphrased) — a soft signal, not an error.

    ``bands`` (``y0:y1,…`` in image pixels of ``page``) restricts the search to the cited
    table row(s): a value covered only by a row citation is pinpointed INSIDE that row,
    never anywhere else on the page."""
    if not records.is_document_owner(db, document_id, who):
        raise HTTPException(status_code=404, detail="Document not found.")
    from . import pdf_utils
    store = storage.get_store()
    key = storage.pdf_key(document_id)
    if not store.exists(key):
        return {"rects": [], "found": False, "no_pdf": True}
    pdf_bytes = store.get(key)
    if anchor:                          # a table number: on the printed row of its anchor text (item wording, variable label)
        rows = [b for a in anchor[:4] for b in pdf_utils.anchor_bands(pdf_bytes, page, a[:400])]
        if rows:
            hits = pdf_utils.locate_value_rects(pdf_bytes, page, value)
            per_row = [(b, pdf_utils.rects_in_bands(hits, [b])) for b in rows]
            per_row = [(b, r) for b, r in per_row if r]
            if len(per_row) > 1:        # two variable rows both show the value: a triangular matrix
                counts = [pdf_utils.numbers_in_band(pdf_bytes, page, b) for b, _ in per_row]   # prints the pair on the fuller row
                if sorted(counts)[-1] > sorted(counts)[-2]:
                    per_row = [per_row[counts.index(max(counts))]]
            rects = [r for _, rs in per_row for r in rs]
            if rects:
                return {"rects": rects, "found": True, "page": page, "anchored": True}
    if page_only:                       # a table cited as a whole: every whole-number match on ITS page
        rects = pdf_utils.locate_value_rects(pdf_bytes, page, value)
        return {"rects": rects, "found": bool(rects), "page": page, "anchored": False}
    if bands:
        rows = pdf_utils.parse_bands(bands)
        rects = pdf_utils.rects_in_bands(pdf_utils.locate_value_rects(pdf_bytes, page, value), rows)
        return {"rects": rects, "found": bool(rects), "page": page}
    found_page, rects = pdf_utils.locate_value_rects_any(pdf_bytes, value, prefer_page=page)
    if not rects:                       # not a number (or not found) → try literal text
        found_page, rects = pdf_utils.locate_text_rects_any(pdf_bytes, value, prefer_page=page)
    return {"rects": rects, "found": bool(rects), "page": found_page}


@app.get("/api/documents/{document_id}/raw")
def document_raw_response(document_id: str, db=Depends(get_db),
                          who: Principal = Depends(principal)) -> Response:
    """Owner-only: the model's verbatim response for this document (or the JSON that was
    imported), exactly as stored at extraction time. 404 for documents from before it was kept."""
    if not records.is_document_owner(db, document_id, who):
        raise HTTPException(status_code=404, detail="Document not found.")
    store = storage.get_store()
    key = storage.raw_key(document_id)
    if not store.exists(key):
        raise HTTPException(status_code=404, detail="No stored model response for this document.")
    return Response(content=store.get(key), media_type="text/plain; charset=utf-8")


class DuplicateCheck(BaseModel):
    hashes: list[str] = []
    schema_id: str | None = None       # only flag docs extracted with THIS exact schema row
    preset_id: str | None = None       # …or with ANY version of this preset


@app.post("/api/documents/check-duplicates")
def check_duplicates(body: DuplicateCheck, db=Depends(get_db),
                     who: Principal = Depends(principal)) -> dict:
    """For each pdf_sha256 the client sends, return the caller's existing documents with
    that exact content AND the same preset (schema_id) — so the extract page flags a paper
    only when re-running the identical PDF through the identical preset. Principal-scoped."""
    matches = records.documents_by_hashes(
        db, body.hashes or [], owner_user_id=who.user_id, session_id=who.session_id,
        schema_id=body.schema_id, preset_id=body.preset_id)
    return {"duplicates": matches}


@app.get("/api/documents/{document_id}/text")
def document_text(document_id: str, page: int | None = None, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    """Owner-only: the reusable parsed text layer of the source PDF (a 'parsed text'
    preview, and the substrate for re-extraction). Returns the full markdown, or one
    1-indexed page when ``page`` is given. ``cached=false`` = not parsed yet."""
    if not records.is_document_owner(db, document_id, who):
        raise HTTPException(status_code=404, detail="Document not found.")
    from . import parsed
    store = storage.get_store()
    if page is not None:
        text = parsed.page_text(db, store, document_id, page)
        return {"page": page, "text": text, "cached": text is not None}
    data = parsed.for_document(db, store, document_id)
    if not data:
        return {"text": None, "pages": [], "n_pages": 0, "cached": False}
    return {"text": data["markdown"], "pages": data["pages"],
            "n_pages": data["n_pages"], "cached": True}


@app.get("/api/papers/mine")
def my_papers(db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """The principal's cached-PDF library ("All my papers") — one entry per distinct PDF,
    with its extraction count and any live datasets that currently contain it. A paper
    stays here after its dataset is deleted, so it can be re-extracted for another one."""
    return {"papers": records.list_papers(
        db, owner_user_id=who.user_id, session_id=who.session_id)}


@app.delete("/api/papers/mine/{sha}")
def delete_my_paper(sha: str, db=Depends(get_db),
                    who: Principal = Depends(principal)) -> dict:
    """Remove a cached PDF from the library entirely — deletes every extraction the caller
    owns for that content hash, plus its stored PDF + page images. Cannot be undone."""
    n = records.delete_paper(db, sha, owner_user_id=who.user_id, session_id=who.session_id)
    return {"deleted": n}


# ── datasets (Phase 2) ────────────────────────────────────────────────────────

class DatasetCreate(BaseModel):
    title: str
    description: str | None = None
    schema_id: str | None = None
    visibility: str = "private"
    prompt: str | None = None   # extraction recipe (browser-supplied, saved only here)
    model: str | None = None


class DatasetAdd(BaseModel):
    document_id: str | None = None
    record_ids: list[str] | None = None


@app.post("/api/datasets")
def create_dataset(body: DatasetCreate, db=Depends(get_db),
                   who: Principal = Depends(principal)) -> dict:
    """Create an owned dataset (owner = logged-in user, else the anon session)."""
    schema_id = presets.resolve_run(db, schema_id=body.schema_id).schema_id if body.schema_id else None
    return records.create_dataset(
        db, title=body.title, description=body.description, schema_id=schema_id,
        visibility=body.visibility, owner_user_id=who.user_id, session_id=who.session_id,
        prompt=body.prompt, model=body.model)


@app.get("/api/datasets")
def list_datasets(db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    return {"datasets": records.list_datasets(
        db, owner_user_id=who.user_id, session_id=who.session_id)}


@app.get("/api/datasets/public")
def datasets_public(limit: int = 50, offset: int = 0, q: str | None = None,
                    db=Depends(get_db)) -> dict:
    """Public datasets each with a computed credibility badge (one query). Declared
    before /{dataset_id} so 'public' isn't captured as an id. ``q`` full-text searches
    the dataset title/description/prompt/preset."""
    return {"datasets": records.public_datasets_with_badges(
        db, limit=min(limit, 200), offset=offset, q=q)}


@app.get("/api/datasets/rows")
def dataset_rows(dataset: list[str] | None = Query(None), db=Depends(get_db),
                 who: Principal = Depends(principal)) -> dict:
    """Tidy rows (each record's field_values) across one or more datasets — the data
    a D3 dashboard aggregates client-side. Authz: public rows + the principal's own
    private rows. Declared before /{dataset_id} so 'rows' isn't captured as an id."""
    return {"rows": records.dataset_rows(db, dataset or [], principal=who, public_only=False)}


@app.get("/api/datasets/{dataset_id}")
def get_dataset(dataset_id: str, db=Depends(get_db),
                who: Principal = Depends(principal)) -> dict:
    d = records.get_dataset(db, dataset_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if d["visibility"] != "public" and not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=404, detail="Dataset not found.")
    d["records"] = records.dataset_records(db, dataset_id)
    d["credibility"] = records.dataset_credibility(db, dataset_id)  # computed badge
    if not records.is_dataset_owner(db, dataset_id, who):
        d.pop("owner_citation_name", None)
    return d


@app.get("/api/datasets/{dataset_id}/overview")
def dataset_overview(dataset_id: str, db=Depends(get_db),
                     who: Principal = Depends(principal)) -> dict:
    """Recipe + computed stats + papers list for the dataset overview page. Same
    owner-or-public gate as get_dataset (404 for non-owners on private datasets)."""
    d = records.get_dataset(db, dataset_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    owner = records.is_dataset_owner(db, dataset_id, who)
    if d["visibility"] != "public" and not owner:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    ov = records.dataset_overview(db, dataset_id)
    ov["viewer_is_owner"] = bool(owner)
    ov["viewer_is_anonymous"] = not who.user_id
    if not owner:                        # don't leak the uploader's local filenames publicly
        ov.pop("owner_citation_name", None)   # …nor the name behind an anonymous dataset
        for doc in ov.get("documents", []):
            doc["filename"] = None
    return ov


def _dataset_gate(db, dataset_id: str, who: Principal) -> bool:
    """404 unless the dataset is public or the caller's; returns whether the caller owns it."""
    d = records.get_dataset(db, dataset_id)
    owner = d is not None and records.is_dataset_owner(db, dataset_id, who)
    if d is None or not (owner or d.get("visibility") == "public"):
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return owner


@app.get("/api/datasets/{dataset_id}/analysis")
def dataset_analysis_table(dataset_id: str, unit: str | None = None, release: int | None = None, db=Depends(get_db),
                           who: Principal = Depends(principal)) -> dict:
    """The flat, typed analysis table of a dataset for one row unit (default: the preset's):
    columns with roles and statistics, papers, records with their provenance, rows. Document
    ids and filenames only for the owner."""
    from . import analysis_table
    owner = _dataset_gate(db, dataset_id, who)
    return analysis_table.build(db, dataset_id, unit, owner=owner, release=_release_or_404(db, dataset_id, release))


def _release_or_404(db, dataset_id: str, number: int | None) -> dict | None:
    """The release with this number (its snapshot loaded), None for the live dataset."""
    if number is None:
        return None
    from . import releases
    rel = releases.get_by_number(db, dataset_id, number, with_snapshot=True)
    if rel is None:
        raise HTTPException(status_code=404, detail="This dataset has no such release.")
    return rel


class CellEvidenceBody(BaseModel):
    unit: str | None = None
    cells: list[dict] = []
    release: int | None = None           # read the evidence of a release instead of the live dataset


@app.post("/api/datasets/{dataset_id}/analysis/evidence")
def dataset_analysis_evidence(dataset_id: str, body: CellEvidenceBody, db=Depends(get_db),
                              who: Principal = Depends(principal)) -> dict:
    """The evidence behind up to 300 cells of the analysis table: quote, page, source label and
    the kind of support (exact value / table row / whole table / entry-level). Never geometry,
    page images or editors: those stay behind the owner-only document view."""
    from . import analysis_table
    _dataset_gate(db, dataset_id, who)
    return {"cells": analysis_table.cell_evidence(db, dataset_id, body.unit, body.cells,
                                                   release=_release_or_404(db, dataset_id, body.release))}


# ── dataset releases: frozen copies that dashboards pin ─────────────────────────
@app.get("/api/datasets/{dataset_id}/releases")
def dataset_releases(dataset_id: str, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """The releases of a dataset, newest first. The owner also learns whether the live data has
    moved on since the latest one."""
    from . import releases
    owner = _dataset_gate(db, dataset_id, who)
    rows = releases.list_for_dataset(db, dataset_id)
    out = {"releases": [releases.public_row(r) for r in rows]}
    if owner:
        from . import zenodo
        out["head"] = {"changed": releases.changed_since(db, dataset_id, rows[0] if rows else None)}
        out["zenodo"] = zenodo.status()
    return out


@app.post("/api/datasets/{dataset_id}/releases/{number}/doi")
def dataset_release_doi(dataset_id: str, number: int, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Owner only: mint a DOI for this release on Zenodo (a new version of the dataset's Zenodo
    record). Permanent, so never implied by anything else. 400 when Zenodo is not configured,
    409 when the release already has one."""
    from . import releases, zenodo
    if not _dataset_gate(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="Only the owner can mint a DOI.")
    if not zenodo.configured():
        raise HTTPException(status_code=400, detail="Zenodo isn’t configured on this server.")
    rel = releases.get_by_number(db, dataset_id, number)
    if rel is None:
        raise HTTPException(status_code=404, detail="This dataset has no such release.")
    if rel.get("doi"):
        raise HTTPException(status_code=409, detail=f"Release v{number} already has a DOI: {rel['doi']}")
    try:
        return releases.public_row(zenodo.deposit(db, rel))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/datasets/{dataset_id}/releases/pending")
def dataset_release_pending(dataset_id: str, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Owner only: what the next release would contain (changes, what is not reviewed yet, the badge)."""
    from . import releases
    if not _dataset_gate(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="Only the owner can create releases.")
    return releases.pending(db, dataset_id)


@app.get("/api/datasets/{dataset_id}/releases/{number}/export")
def dataset_release_export(dataset_id: str, number: int, file: str | None = None, db=Depends(get_db),
                           who: Principal = Depends(principal)):
    """A release as static files (release.json, tables/<unit>.json, evidence.json, README.md): a
    zip, or ONE of the files with ``?file=``. What a dashboard written outside Metalens reads.
    Same gate as the dataset; never file names, document ids or geometry."""
    from . import release_export
    _dataset_gate(db, dataset_id, who)
    files = release_export.build(db, _release_or_404(db, dataset_id, number))
    if file:
        if file not in files:
            raise HTTPException(status_code=404, detail="This release has no such file.")
        kind = "text/markdown; charset=utf-8" if file.endswith(".md") else "application/json"
        return Response(content=files[file], media_type=kind)
    slug = (records.get_dataset(db, dataset_id) or {}).get("slug") or "dataset"
    name = f"{slug}-v{number}"
    return Response(content=release_export.as_zip(files, name), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})


# ── dashboards built outside Metalens, registered on the dataset ────────────────────────────────
class ExternalDashboardBody(BaseModel):
    title: str
    url: str
    repo_url: str | None = None
    manifest_url: str | None = None


@app.get("/api/datasets/{dataset_id}/external-dashboards")
def external_dashboards_list(dataset_id: str, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    from . import external_dashboards, releases
    _dataset_gate(db, dataset_id, who)
    latest = releases.latest(db, dataset_id)
    return {"dashboards": external_dashboards.list_for_dataset(db, dataset_id), "latest_release": latest["number"] if latest else None}


@app.post("/api/datasets/{dataset_id}/external-dashboards")
def external_dashboards_create(dataset_id: str, body: ExternalDashboardBody, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Owner only, signed in: register a dashboard hosted elsewhere; it is checked right away."""
    from . import external_dashboards
    if not _dataset_gate(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="Only the owner can register dashboards.")
    if not who.user_id and not localmode.enabled():
        raise HTTPException(status_code=401, detail="Sign in to register a dashboard.")
    for u in (body.url, body.repo_url, body.manifest_url):
        if u and not external_dashboards.valid_url(u):
            raise HTTPException(status_code=422, detail=f"Not an http(s) URL: {u}")
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="A title is needed.")
    ext = external_dashboards.create(db, dataset_id=dataset_id, owner_user_id=who.user_id, title=body.title, url=body.url,
                                     repo_url=body.repo_url, manifest_url=body.manifest_url)
    return external_dashboards.check(db, ext)


@app.post("/api/external-dashboards/{ext_id}/check")
def external_dashboards_check(ext_id: str, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    from . import external_dashboards
    ext = external_dashboards.get(db, ext_id)
    if ext is None:
        raise HTTPException(status_code=404, detail="Not found.")
    _dataset_gate(db, ext["dataset_id"], who)
    return external_dashboards.check(db, ext)


@app.delete("/api/external-dashboards/{ext_id}")
def external_dashboards_delete(ext_id: str, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    from . import external_dashboards
    ext = external_dashboards.get(db, ext_id)
    if ext is None or not _dataset_gate(db, ext["dataset_id"], who):
        raise HTTPException(status_code=404, detail="Not found.")
    return {"deleted": external_dashboards.delete(db, ext_id)}


class ReleaseBody(BaseModel):
    notes: str = ""


@app.post("/api/datasets/{dataset_id}/releases")
def dataset_release_create(dataset_id: str, body: ReleaseBody, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Owner only: freeze the dataset as it is now into the next release. 409 when nothing changed."""
    from . import releases
    if not _dataset_gate(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="Only the owner can create releases.")
    if not records.dataset_records_all_owned(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="This dataset holds records you don't own.")
    last = releases.latest(db, dataset_id)
    if last and not releases.changed_since(db, dataset_id, last):
        raise HTTPException(status_code=409, detail=f"Nothing changed since release v{last['number']}.")
    rel = releases.create(db, dataset_id, reason="manual", notes=body.notes, created_by=who.user_id)
    return releases.public_row(rel)


# ── dashboards: questions → blocks → D3, every mark traceable ───────────────────
@app.get("/api/analysis/templates")
def analysis_templates() -> dict:
    """The building blocks a dashboard is made of: figure, table and key-number templates with
    their slots. Icons, slot editors and the validator all read this one declaration."""
    from . import dashboard_spec
    return dashboard_spec.registry()


class DashboardValidateBody(BaseModel):
    dataset_id: str
    spec: dict | None = None            # None → the default dashboard for this dataset


@app.post("/api/dashboards/validate")
def dashboards_validate(body: DashboardValidateBody, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Validate (and repair) a dashboard spec against the dataset's columns; without a spec,
    return the deterministic default dashboard. Sufficiency is computed here from the data."""
    from . import dashboard_spec, dashboards
    owner = _dataset_gate(db, body.dataset_id, who)
    if body.spec is None:
        spec, report = dashboards.default_spec(db, body.dataset_id, owner=owner)
        return {"spec": spec, "report": report, "default": True}
    tables, default = dashboards.tables_for(db, body.dataset_id, owner=owner)
    spec, report = dashboard_spec.validate({**body.spec, "dataset_id": body.dataset_id}, tables, default_unit=default)
    return {"spec": spec, "report": report, "default": False}


class DashboardProposeBody(BaseModel):
    dataset_id: str
    questions: list[str] = []
    model: str = ""
    api_key: str = ""
    base_url: str | None = None
    use_credits: bool = False
    dashboard_id: str | None = None      # a re-proposal on a saved dashboard (free, capped)
    feedback: str = ""
    previous: dict | None = None
    keep: list[str] = []
    context: str = ""                    # free-text background and requests for the planner


FREE_REPROPOSALS = 5
_RAW_CAP = 64_000          # characters of the model's answer kept


@app.post("/api/dashboards/propose")
def dashboards_propose(body: DashboardProposeBody, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """A model proposes the dashboard's STRUCTURE for the user's questions, out of our building
    blocks; the validator repairs and checks it against the data, and a person reviews it
    before anything is built. Own key or self-hosted model: never stored, no ledger. Credits:
    ONE credit per dashboard; re-proposals with feedback are free (up to FREE_REPROPOSALS),
    and a failed proposal is refunded. Runs in the web process, so the server key is never queued."""
    if not who.user_id and not localmode.enabled():
        raise HTTPException(status_code=401, detail="Sign in to build dashboards.")
    import hashlib
    from . import contract, dashboard_spec, dashboards
    owner = _dataset_gate(db, body.dataset_id, who)
    own = bool(body.api_key.strip()) or bool((body.base_url or "").strip())
    revision = bool(body.previous and (body.previous.get("blocks") or []))
    charged = False
    if own:
        model, key, base_url = body.model, body.api_key, body.base_url
    elif body.use_credits:
        if localmode.enabled():
            raise HTTPException(status_code=422, detail="Local mode runs on your own API key or a local model.")
        if not who.user_id:
            raise HTTPException(status_code=401, detail="Sign in to use credits, or add your own API key.")
        if not credits.offered():
            raise HTTPException(status_code=422, detail="Credits are not available on this server; add your own API key.")
        model = credits.credit_model()
        key, base_url = credits.server_key_for(providers.get_provider(model, None)), None
        prior = dashboards.get(db, body.dashboard_id) if body.dashboard_id else None
        n_prior = int(((prior or {}).get("proposal") or {}).get("attempts_total") or 0) if prior and dashboards.owns(who, prior) else 0
        free = revision and (n_prior < FREE_REPROPOSALS if prior else True)
        if not free:
            if not credits.try_consume(db, who.user_id, model=model, reason="dashboard"):
                raise HTTPException(status_code=402, detail="No credits left. Add your own API key, or start from the default dashboard.")
            charged = True
    else:
        raise HTTPException(status_code=422, detail="Choose credits, your own API key or a local model; or start from the default dashboard.")
    if not model:
        raise HTTPException(status_code=422, detail="Choose a model.")

    tables, default = dashboards.tables_for(db, body.dataset_id, owner=owner)
    questions = [{"id": f"q{k + 1}", "text": q.strip()[:400]} for k, q in enumerate(body.questions) if isinstance(q, str) and q.strip()][:12]
    prompt = dashboard_spec.build_prompt(questions, tables, default, previous=body.previous if revision else None,
                                         feedback=body.feedback, keep=body.keep, context=body.context)
    attempts, raw, spec, report = 0, "", None, None
    try:
        for nudge in ("", "\n\nReturn ONLY the JSON object described above, with at least one block."):
            attempts += 1
            raw = providers.generate_text(model, key, prompt + nudge, base_url=base_url, max_tokens=16384, json_mode=True)
            parsed = contract.parse_result_json(raw)
            parsed = parsed if isinstance(parsed, dict) else {"blocks": parsed} if isinstance(parsed, list) else {}
            if revision and body.keep:                       # locked blocks survive verbatim, whatever came back
                locked = [b for b in body.previous.get("blocks") or [] if b.get("id") in body.keep]
                parsed["blocks"] = locked + [b for b in parsed.get("blocks") or [] if isinstance(b, dict) and b.get("id") not in body.keep]
            for b in parsed.get("blocks") or []:
                if isinstance(b, dict) and b.get("origin") not in ("user", "default"):
                    b["origin"] = "llm"
            parsed["context"] = body.context                 # the user's, never the model's
            if revision:                                     # a revision keeps the look and the labels the user settled on
                prev = body.previous or {}
                parsed["theme"] = prev.get("theme")
                parsed["column_labels"] = prev.get("column_labels")
                new_labels = parsed.get("value_labels") if isinstance(parsed.get("value_labels"), dict) else {}
                parsed["value_labels"] = {**new_labels, **{c: {**(new_labels.get(c) or {}), **m} for c, m in (prev.get("value_labels") or {}).items() if isinstance(m, dict)}}
            spec, report = dashboard_spec.validate({**parsed, "questions": questions, "dataset_id": body.dataset_id}, tables, default_unit=default)
            if spec["blocks"]:
                break
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - provider errors (bad key, quota, unreachable server)
        if charged:
            credits.refund(db, who.user_id, model=model, reason="dashboard refund")
        return {"ok": False, "error": providers.extract_provider_message(exc)}
    if not spec or not spec["blocks"]:
        if charged:
            credits.refund(db, who.user_id, model=model, reason="dashboard refund")
        text = (raw or "").strip()
        why = ("The model returned nothing." if not text
               else "The model's answer was cut off before the JSON ended (it ran out of output tokens)." if not text.rstrip("`\n ").endswith(("}", "]"))
               else "The model answered, but no block in its answer could be used.")
        return {"ok": False, "error": f"{why} Try again, rephrase the questions, or start from the default dashboard.",
                "raw": text[:_RAW_CAP], "prompt": prompt, "report": report or {"dropped": [], "repairs": []}, "model": model, "attempts": attempts}
    out = {"ok": True, "spec": spec, "report": report,
           "proposal": {"model": model, "provider": providers.get_provider(model, base_url), "attempts": attempts, "charged": charged,
                        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                        "registry_version": dashboard_spec.REGISTRY_VERSION, "feedback": body.feedback[:400] or None,
                        # the model's verbatim answer: shown in the composer (to debug, to keep) and stored
                        # with the dashboard for its owner. The prompt is returned once and never stored.
                        "raw": (raw or "")[:_RAW_CAP]},
           "prompt": prompt}
    if who.user_id and not own:
        out["credits"] = credits.summary(db, who.user_id)
    if body.dashboard_id:                                    # count the attempt on the saved dashboard
        prior = dashboards.get(db, body.dashboard_id)
        if prior and dashboards.owns(who, prior):
            dashboards.count_proposal(db, body.dashboard_id)
    return out


class DashboardCreateBody(BaseModel):
    dataset_id: str
    title: str = ""
    spec: dict
    proposal: dict | None = None


def _dashboard_or_404(db, dashboard_id: str, who: Principal, *, write: bool = False) -> dict:
    from . import dashboards
    d = dashboards.get(db, dashboard_id)
    if d is None or not (dashboards.owns(who, d) if write else dashboards.visible(db, d, who)):
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    return d


@app.post("/api/dashboards")
def dashboards_create(body: DashboardCreateBody, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Save a dashboard over a dataset the caller may see (their own, or a public one). The spec
    is validated again here: what is stored is always a clean spec."""
    from . import dashboard_spec, dashboards
    if not who.user_id and not localmode.enabled():
        raise HTTPException(status_code=401, detail="Sign in to build dashboards.")   # dashboards are kept, updated and published: account work
    owner = _dataset_gate(db, body.dataset_id, who)
    tables, default = dashboards.tables_for(db, body.dataset_id, owner=owner)
    spec, _report = dashboard_spec.validate({**body.spec, "dataset_id": body.dataset_id}, tables, default_unit=default)
    if not spec["blocks"]:
        raise HTTPException(status_code=422, detail="A dashboard needs at least one valid block.")
    ds = records.get_dataset(db, body.dataset_id) or {}
    prop = {k: v for k, v in (body.proposal or {}).items() if k in ("model", "provider", "prompt_sha256", "registry_version", "attempts", "feedback", "created_at")}
    if isinstance((body.proposal or {}).get("raw"), str):
        prop["raw"] = body.proposal["raw"][:_RAW_CAP]
    return dashboards.create(db, dataset_id=body.dataset_id, title=(body.title.strip() or spec.get("title") or f"{ds.get('title') or 'Dataset'}: dashboard")[:200],
                             spec=spec, proposal=prop or None, owner_user_id=who.user_id, session_id=who.session_id)


@app.get("/api/dashboards")
def dashboards_list(dataset: str | None = None, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Dashboards of one dataset the caller may see, or (without ``dataset``) the caller's own."""
    from . import dashboards
    if dataset:
        _dataset_gate(db, dataset, who)
        return {"dashboards": dashboards.list_for_dataset(db, dataset, who)}
    return {"dashboards": dashboards.list_mine(db, who)}


def _dashboard_view(db, d: dict, who: Principal, view: str | None) -> tuple[str, dict | None]:
    """Which face of a dashboard the caller gets: ('draft', None) = the owner's live working copy;
    ('published', release) = the frozen page over its pinned release; ('live', None) = a public
    dashboard from before releases existed. Only the owner may ask for the draft."""
    from . import dashboards, releases
    mine = dashboards.owns(who, d)
    if view not in (None, "draft", "published"):
        raise HTTPException(status_code=422, detail="view is 'draft' or 'published'.")
    if view == "draft" and not mine:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    if (view == "published" or not mine) and dashboards.is_published(d):
        return "published", releases.get(db, d["published_release_id"], with_snapshot=True)
    if view == "published":
        raise HTTPException(status_code=404, detail="This dashboard is not published.")
    return ("draft" if mine else "live"), None


@app.get("/api/dashboards/public")
def dashboards_public(db=Depends(get_db)) -> dict:
    from . import dashboards, external_dashboards
    return {"metalens": dashboards.list_public(db), "external": external_dashboards.list_public(db)}


@app.get("/api/dashboards/{dashboard_id}")
def dashboards_get(dashboard_id: str, view: str | None = None, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """A dashboard. Its owner gets the draft (``?view=published`` shows them the public page);
    everyone else gets the published page: the frozen spec over the pinned release."""
    from . import dashboards, releases
    d = _dashboard_or_404(db, dashboard_id, who)
    face, rel = _dashboard_view(db, d, who, view)
    mine = dashboards.owns(who, d)
    out = {"id": d["id"], "dataset_id": d["dataset_id"], "grammar": d["grammar"], "visibility": d["visibility"], "view": face,
           "title": d.get("published_title") or d["title"] if face == "published" else d["title"],
           "spec": d["published_spec"] if face == "published" else d["spec"],
           "created_at": d["created_at"], "updated_at": d["published_at"] if face == "published" else d["updated_at"],
           "release": {k: rel[k] for k in ("number", "created_at", "content_sha")} if rel else None,
           "published": dashboards.is_published(d), "can_edit": mine,
           "author": dashboards.author_name(db, d, records.get_dataset(db, d["dataset_id"]))}
    if mine:                                                # the owner's bookkeeping; never for readers
        pinned = rel or (releases.get(db, d["published_release_id"]) if d.get("published_release_id") else None)
        latest = releases.latest(db, d["dataset_id"])
        out.update({"rev": d["rev"], "proposal": d.get("proposal"), "published_at": d.get("published_at"),
                    "draft_differs": dashboards.is_published(d) and (d["spec"] != d["published_spec"] or d["title"] != d.get("published_title")),
                    "update": {"pinned": pinned["number"] if pinned else None, "latest": latest["number"] if latest else None,
                               "available": bool(pinned and latest and latest["number"] > pinned["number"]),
                               "head_changed": releases.changed_since(db, d["dataset_id"], latest)} if dashboards.is_published(d) else None})
    return out


def _dashboard_data_source(db, dashboard_id: str, who: Principal, view: str | None) -> tuple[dict, bool, dict | None]:
    """(dashboard, read as owner?, release) for the data endpoints of a dashboard. Readers of a
    published dashboard reach its data HERE, never through the dataset's own endpoints, so a
    published dashboard works over a private dataset without opening the dataset."""
    from . import dashboards
    d = _dashboard_or_404(db, dashboard_id, who)
    face, rel = _dashboard_view(db, d, who, view)
    if face == "live" and (records.get_dataset(db, d["dataset_id"]) or {}).get("visibility") != "public":
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    # the published page is ALWAYS read without owner rights: no filenames, no document ids
    return d, (face == "draft" and records.is_dataset_owner(db, d["dataset_id"], who)), rel


@app.get("/api/dashboards/{dashboard_id}/table")
def dashboards_table(dashboard_id: str, unit: str | None = None, view: str | None = None, db=Depends(get_db),
                     who: Principal = Depends(principal)) -> dict:
    """The analysis table behind a dashboard, for one row unit: the pinned release for the
    published page, the live dataset for the owner's draft."""
    from . import analysis_table
    d, as_owner, rel = _dashboard_data_source(db, dashboard_id, who, view)
    return analysis_table.build(db, d["dataset_id"], unit, owner=as_owner, release=rel)


class DashboardEvidenceBody(CellEvidenceBody):
    view: str | None = None


@app.post("/api/dashboards/{dashboard_id}/evidence")
def dashboards_evidence(dashboard_id: str, body: DashboardEvidenceBody, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    from . import analysis_table
    d, _as_owner, rel = _dashboard_data_source(db, dashboard_id, who, body.view)
    return {"cells": analysis_table.cell_evidence(db, d["dataset_id"], body.unit, body.cells, release=rel)}


class DashboardPublishBody(BaseModel):
    rev: int
    release: int | str = "latest"        # a release number, "latest", or "new" (cut one from the live data first)
    source: str = "draft"                # "draft" = publish my current edits; "published" = keep the public spec, move the data


@app.post("/api/dashboards/{dashboard_id}/publish")
def dashboards_publish(dashboard_id: str, body: DashboardPublishBody, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Owner only, signed in. Freezes a spec over a dataset release as the public page: the first
    publication, publishing draft edits, or moving the page to a newer release (the update)."""
    from . import dashboard_spec, dashboards, releases
    d = _dashboard_or_404(db, dashboard_id, who, write=True)
    if not who.user_id:
        raise HTTPException(status_code=401, detail="Sign in to publish a dashboard.")
    ds_owner = records.is_dataset_owner(db, d["dataset_id"], who)
    if body.release == "new" or (body.release == "latest" and releases.latest(db, d["dataset_id"]) is None):
        if not ds_owner or not records.dataset_records_all_owned(db, d["dataset_id"], who):
            raise HTTPException(status_code=409, detail="This dataset has no release yet; only its owner can create one.")
        rel = releases.ensure_current(db, d["dataset_id"], reason="dashboard_publish", created_by=who.user_id)
    elif body.release == "latest":
        rel = releases.latest(db, d["dataset_id"])
    else:
        rel = releases.get_by_number(db, d["dataset_id"], int(body.release)) if str(body.release).isdigit() else None
    if rel is None:
        raise HTTPException(status_code=404, detail="This dataset has no such release.")
    rel = releases.get(db, rel["id"], with_snapshot=True)
    use_published = body.source == "published" and dashboards.is_published(d)
    source_spec, title = (d["published_spec"], d.get("published_title") or d["title"]) if use_published else (d["spec"], d["title"])
    tables, default = dashboards.tables_for(db, d["dataset_id"], owner=False, release=rel)
    spec, report = dashboard_spec.validate({**source_spec, "dataset_id": d["dataset_id"]}, tables, default_unit=default)
    hidden = [b["title"] for b in spec["blocks"] if b["sufficiency"]["status"] == "insufficient"]
    spec["blocks"] = [b for b in spec["blocks"] if b["sufficiency"]["status"] != "insufficient"]   # a public page has no empty cards
    if not spec["blocks"]:
        raise HTTPException(status_code=422, detail="No block of this dashboard can be drawn from that release.")
    out = dashboards.publish(db, dashboard_id, rev=body.rev, release_id=rel["id"], spec=spec, title=title)
    if out is None:
        raise HTTPException(status_code=409, detail="This dashboard was changed elsewhere; reload and try again.")
    return {"id": out["id"], "rev": out["rev"], "release": rel["number"], "published_at": out["published_at"],
            "hidden_blocks": hidden, "report": report}


@app.get("/api/dashboards/{dashboard_id}/update-preview")
def dashboards_update_preview(dashboard_id: str, release: str = "latest", source: str = "published",
                              db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Owner only: what moving the public page to another release would do, block by block
    (rows and studies before → after; broken / needs attention / changed / unchanged).
    ``release`` = a number, "latest", or "head" (the live dataset, not yet released)."""
    from . import dashboard_diff, dashboards, releases
    d = _dashboard_or_404(db, dashboard_id, who, write=True)
    if not dashboards.is_published(d):
        raise HTTPException(status_code=409, detail="This dashboard is not published yet.")
    pinned = releases.get(db, d["published_release_id"], with_snapshot=True)
    if release == "head":
        target, snap_b = None, releases.snapshot(db, d["dataset_id"])
    else:
        target = releases.latest(db, d["dataset_id"]) if release == "latest" else (
            releases.get_by_number(db, d["dataset_id"], int(release)) if release.isdigit() else None)
        if target is None:
            raise HTTPException(status_code=404, detail="This dataset has no such release.")
        target = releases.get(db, target["id"], with_snapshot=True)
        snap_b = target["snapshot"]
    spec = d["spec"] if source == "draft" else d["published_spec"]
    tables_a, default_a = dashboards.tables_for(db, d["dataset_id"], owner=False, release=pinned)
    tables_b, default_b = dashboards.tables_for(db, d["dataset_id"], owner=False, release=target)
    out = dashboard_diff.preview({**spec, "dataset_id": d["dataset_id"]}, tables_a, default_a, tables_b, default_b,
                                 changes=releases.diff_snapshots(pinned["snapshot"], snap_b))
    out["from"] = pinned["number"]
    out["to"] = target["number"] if target else None       # None = the live dataset (a release would be created)
    return out


@app.post("/api/dashboards/{dashboard_id}/unpublish")
def dashboards_unpublish(dashboard_id: str, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    from . import dashboards
    _dashboard_or_404(db, dashboard_id, who, write=True)
    out = dashboards.unpublish(db, dashboard_id)
    return {"id": out["id"], "rev": out["rev"], "visibility": out["visibility"]}


class DashboardPatchBody(BaseModel):
    rev: int
    title: str | None = None
    spec: dict | None = None
    visibility: str | None = None


@app.patch("/api/dashboards/{dashboard_id}")
def dashboards_patch(dashboard_id: str, body: DashboardPatchBody, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Owner only. ``rev`` must be the revision the edit started from (409 otherwise)."""
    from . import dashboard_spec, dashboards
    d = _dashboard_or_404(db, dashboard_id, who, write=True)
    spec = None
    if body.spec is not None:
        tables, default = dashboards.tables_for(db, d["dataset_id"], owner=True)
        spec, _ = dashboard_spec.validate({**body.spec, "dataset_id": d["dataset_id"]}, tables, default_unit=default)
        if not spec["blocks"]:
            raise HTTPException(status_code=422, detail="A dashboard needs at least one valid block.")
    if body.visibility == "public":                          # going public freezes a spec over a release: a separate act
        raise HTTPException(status_code=409, detail="Use publish to make a dashboard public.")
    if body.visibility == "private" and dashboards.is_published(d):
        dashboards.unpublish(db, dashboard_id); body.rev += 1
    out = dashboards.update(db, dashboard_id, rev=body.rev, title=(body.title.strip()[:200] if body.title else None),
                            spec=spec, visibility=body.visibility)
    if out is None:
        raise HTTPException(status_code=409, detail="This dashboard was changed elsewhere; reload and try again.")
    return {k: v for k, v in out.items() if k not in ("owner_user_id", "session_id", "published_spec")}


@app.delete("/api/dashboards/{dashboard_id}")
def dashboards_delete(dashboard_id: str, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    from . import dashboards
    _dashboard_or_404(db, dashboard_id, who, write=True)
    return {"deleted": dashboards.delete(db, dashboard_id)}


@app.get("/api/datasets/{dataset_id}/audit")
def dataset_audit_report(dataset_id: str, db=Depends(get_db),
                         who: Principal = Depends(principal)) -> dict:
    """Audit report: per extraction target, how many values the model extracted and what the
    human review changed, with sensitivity / precision / Jaccard against the reviewed state.
    Same owner-or-public gate as the overview."""
    from . import audit
    d = records.get_dataset(db, dataset_id)
    if d is None or not (records.is_dataset_owner(db, dataset_id, who) or d.get("visibility") == "public"):
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return audit.dataset_audit(db, dataset_id)


@app.get("/api/datasets/{dataset_id}/export")
def dataset_export(dataset_id: str, db=Depends(get_db),
                   who: Principal = Depends(principal)) -> dict:
    """The downloadable dataset file: metadata + papers, each with its own records.
    Same owner-or-public gate as the overview; no account needed (a logged-out visitor's
    data is deleted after the idle timeout, so taking it away as a file is how they keep it)."""
    d = records.get_dataset(db, dataset_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    owner = records.is_dataset_owner(db, dataset_id, who)
    if d["visibility"] != "public" and not owner:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    out = records.dataset_export(db, dataset_id)
    if not owner:                        # don't leak the uploader's local filenames
        for paper in out.get("papers", []):
            paper["filename"] = None
    return out


@app.get("/api/datasets/{dataset_id}/activity")
def dataset_activity(dataset_id: str, limit: int = 100, db=Depends(get_db),
                     who: Principal = Depends(principal)) -> dict:
    """Newest-first history of the dataset: papers added, and records verified / flagged /
    edited. Same owner-or-public gate as the overview."""
    d = records.get_dataset(db, dataset_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if d["visibility"] != "public" and not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return {"dataset_id": dataset_id, "created_at": d["created_at"],
            "updated_at": d["updated_at"],
            "events": records.dataset_activity(db, dataset_id, limit=max(1, min(limit, 500)))}


@app.get("/api/datasets/{dataset_id}/credibility")
def dataset_credibility(dataset_id: str, db=Depends(get_db)) -> dict:
    if records.get_dataset(db, dataset_id) is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return records.dataset_credibility(db, dataset_id)


@app.post("/api/datasets/{dataset_id}/add")
def add_to_dataset(dataset_id: str, body: DatasetAdd, db=Depends(get_db),
                   who: Principal = Depends(principal)) -> dict:
    """Assign records to a dataset — by `document_id` (all its records) or `record_ids`.
    Requires owning the dataset AND the source rows (blocks cross-user injection)."""
    if records.get_dataset(db, dataset_id) is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="Not your dataset.")
    if body.document_id:
        if not records.is_document_owner(db, body.document_id, who):
            raise HTTPException(status_code=403, detail="Not your document.")
        n = records.assign_document_to_dataset(db, dataset_id, body.document_id)
    else:
        if not records.records_all_owned(db, body.record_ids or [], who):
            raise HTTPException(status_code=403, detail="Not your records.")
        n = records.assign_records_to_dataset(db, dataset_id, body.record_ids or [])
    return {"dataset_id": dataset_id, "assigned": n}


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str, db=Depends(get_db),
                    who: Principal = Depends(principal)) -> dict:
    """Owner-only: delete the document, its records/evidence, and its stored PDF + pages."""
    if not records.is_document_owner(db, document_id, who):
        raise HTTPException(status_code=403, detail="Not authorized.")
    return records.delete_document(db, document_id)


class SetField(BaseModel):
    key: str
    value: Any = None                  # any JSON value (string / number / array / null)


@app.post("/api/documents/{document_id}/set-field")
def set_document_field(document_id: str, body: SetField, db=Depends(get_db),
                       who: Principal = Depends(principal)) -> dict:
    """Owner-only: set one field on EVERY record of the document — used to edit a study-
    constant field once and have it apply to all entries. Logs a correction per record."""
    if not records.is_document_owner(db, document_id, who):
        raise HTTPException(status_code=404, detail="Document not found.")
    n = records.set_field_across_records(
        db, document_id, body.key, body.value, verifier_user_id=who.user_id,
        verifier_kind=("maintainer" if who.user_id else "community"))
    return {"updated": n}


class PaperEdit(BaseModel):
    title: str | None = None
    year: int | None = None
    journal: str | None = None
    authors: list[str] | None = None
    fields: dict | None = None         # declared paper-level fields → paper_metadata.<name>


@app.patch("/api/documents/{document_id}/paper")
def edit_document_paper(document_id: str, body: PaperEdit, db=Depends(get_db),
                        who: Principal = Depends(principal)) -> dict:
    """Owner-only: correct the paper identity (title / year / venue / authors) shown in the
    Study-information panel. The paper row is DOI-deduped, so this applies to every document
    that shares the paper."""
    if not records.is_document_owner(db, document_id, who):
        raise HTTPException(status_code=404, detail="Document not found.")
    row = db.execute("SELECT paper_id, schema_id FROM extraction_document WHERE id = %s::uuid",
                     (document_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found.")
    out: dict = {}
    fields = {}
    for k in ("title", "year", "journal", "authors"):
        v = getattr(body, k)
        if v is not None:
            fields[k] = v
    if fields:
        if not row[0]:
            raise HTTPException(status_code=400, detail="Document has no paper record.")
        out["paper"] = records.update_paper_fields(db, str(row[0]), fields)
    if body.fields:
        # only fields the document's preset DECLARED at paper level are editable here —
        # anything else would silently invent a column in the export
        spec = records.schema_spec(db, row[1]) if row[1] else None
        declared = {f["name"] for f in ((spec or {}).get("paper") or {}).get("fields") or []}
        unknown = sorted(set(body.fields) - declared)
        if unknown:
            raise HTTPException(status_code=422, detail=(
                f"Not declared paper-level fields of this preset: {', '.join(unknown)}"))
        out.update(records.set_paper_metadata_fields(
            db, document_id, body.fields, verifier_user_id=who.user_id,
            verifier_kind="maintainer" if who.user_id else "community"))
    return out


@app.post("/api/datasets/{dataset_id}/verify-all")
def dataset_verify_all(dataset_id: str, db=Depends(get_db),
                       who: Principal = Depends(principal)) -> dict:
    """Owner-only: mark every unverified record of the dataset verified (one event per
    record, attributed to the caller). Flagged records stay flagged."""
    if not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if not records.dataset_records_all_owned(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="You can only verify records you own.")
    out = records.verify_all_records(db, dataset_id, verifier_user_id=who.user_id)
    return {**out, "credibility": records.dataset_credibility(db, dataset_id)}


@app.get("/api/datasets/{dataset_id}/duplicates")
def dataset_duplicates_endpoint(dataset_id: str, db=Depends(get_db),
                                who: Principal = Depends(principal)) -> dict:
    """Owner-only: papers that appear more than once in the dataset (same DOI, title or
    filename), each group newest first — what a re-import of a corrected file leaves behind."""
    if not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return {"groups": records.dataset_duplicates(db, dataset_id)}


@app.post("/api/datasets/{dataset_id}/dedupe")
def dataset_dedupe_endpoint(dataset_id: str, db=Depends(get_db),
                            who: Principal = Depends(principal)) -> dict:
    """Owner-only: delete the older copies of every duplicated paper, keeping the newest."""
    if not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return records.dedupe_dataset(db, dataset_id)


@app.delete("/api/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, db=Depends(get_db),
                   who: Principal = Depends(principal)) -> dict:
    """Owner-only: delete the dataset and discard its extraction records; each paper's
    cached PDF is kept in "All my papers" so it can be re-extracted into another dataset."""
    if not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="Not authorized.")
    return records.delete_dataset(db, dataset_id)


class DatasetPatch(BaseModel):
    visibility: str | None = None          # public | private
    title: str | None = None               # rename; the slug deliberately stays put
    # publishing details (all optional; only the keys sent are changed)
    description: str | None = None
    readme: str | None = None
    keywords: list[str] | None = None
    attribution: str | None = None         # named | anonymous
    citation: str | None = None            # custom citation text; "" resets to the suggested one


@app.patch("/api/datasets/{dataset_id}")
def patch_dataset(dataset_id: str, body: DatasetPatch, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    """Owner-only: rename and/or change visibility. Publishing (→ public) requires owning
    EVERY record in the dataset — never publish another user's work (the bright wall)."""
    if not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="Not authorized.")
    meta = {k: v for k, v in body.model_dump(exclude_unset=True).items()
            if k in ("description", "readme", "keywords", "attribution", "citation")}
    if body.visibility is None and body.title is None and not meta:
        raise HTTPException(status_code=422, detail="Nothing to change.")
    if "attribution" in meta and meta["attribution"] not in ("named", "anonymous"):
        raise HTTPException(status_code=422, detail="attribution must be named|anonymous")
    if "keywords" in meta and meta["keywords"] is not None and len(meta["keywords"]) > 20:
        raise HTTPException(status_code=422, detail="At most 20 keywords.")
    if "readme" in meta and meta["readme"] and len(meta["readme"]) > 20000:
        raise HTTPException(status_code=422, detail="README must be 20,000 characters or fewer.")

    res: dict = {}
    if meta:
        res = {"meta": records.update_dataset_meta(db, dataset_id, **meta)}
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="Title must not be empty.")
        if len(title) > 200:
            raise HTTPException(status_code=422, detail="Title must be 200 characters or fewer.")
        res = records.rename_dataset(db, dataset_id, title)
    if body.visibility is None:
        return res

    if body.visibility not in ("public", "private"):
        raise HTTPException(status_code=422, detail="visibility must be public|private")
    if body.visibility == "public" and not records.dataset_records_all_owned(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="You can only publish records you own.")
    before = records.get_dataset(db, dataset_id) or {}          # status BEFORE the visibility flip
    res = {**res, **records.set_dataset_visibility(db, dataset_id, body.visibility)}
    if body.visibility == "public":
        # publishing a dataset also publishes the personal preset it was built with,
        # so others can find and use it (only if the publisher owns that preset)
        promoted = records.promote_dataset_preset(db, dataset_id, who)
        if promoted:
            res["promoted_preset"] = promoted
        # …and creates the canonical copy in the datasets repository, which is what the
        # citation and the catalogue link to. Not having a token, or GitHub failing, must not
        # stop the dataset from going public here.
        # The datasets repository is the source of truth: publishing opens a pull request
        # there and the dataset is 'pending' until it is merged (the sync flips it to
        # 'published'). Without a token (local mode, a dev checkout) it is listed here only.
        from . import github_publish
        if github_publish.token() and before.get("publish_status") != "published":
            try:
                res["github"] = github_publish.publish_dataset(db, dataset_id)
                records.set_publish_status(db, dataset_id, "pending")
                res["publish_status"] = "pending"
            except Exception as exc:  # noqa: BLE001 - reported, not fatal
                res["github_error"] = str(exc)[:300]
    return res


@app.post("/api/github/sync")
def github_sync_now(db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Reconcile the catalogue with the datasets repository now (merged pull requests become
    published; GitHub-only datasets are imported). Runs hourly on the worker as well."""
    if not who.user_id:
        raise HTTPException(status_code=401, detail="Sign in to sync.")
    from . import github_sync
    try:
        return github_sync.sync(db)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Sync failed: {exc}")


class PublishBody(BaseModel):
    target: str = "github+metalens"     # github+metalens | github | metalens
    release: int | None = None          # publish this release (default: the current data, releasing it first)


@app.post("/api/datasets/{dataset_id}/publish")
def publish_dataset(dataset_id: str, body: PublishBody | None = None, db=Depends(get_db),
                    who: Principal = Depends(principal)) -> dict:
    """Publish a dataset. ``github+metalens`` (default): pull request in the datasets repo,
    viewable by link now, listed in the catalogue once merged. ``github``: the pull request
    only — never listed here. ``metalens``: listed here only (no GitHub configured, local
    mode). Owner-only, and only when the owner owns EVERY record. Returns {pr_url} or
    {queued, job_id}."""
    from . import github_publish
    target = (body.target if body else "github+metalens")
    if target not in ("github+metalens", "github", "metalens"):
        raise HTTPException(status_code=422, detail="target must be github+metalens | github | metalens")
    if not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="Not authorized.")
    if not records.dataset_records_all_owned(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="You can only publish records you own.")
    if target != "metalens" and not github_publish.token():          # validate before any write
        raise HTTPException(status_code=400,
                            detail="GitHub publishing isn’t configured on this server.")
    records.set_publish_target(db, dataset_id, catalogue=(target != "github"))
    if target == "metalens":
        records.set_dataset_visibility(db, dataset_id, "public")
        records.promote_dataset_preset(db, dataset_id, who)
        return {"queued": False, "publish_status": "published", "target": target}
    from . import releases
    already = (records.get_dataset(db, dataset_id) or {}).get("publish_status") == "published"
    # what goes to GitHub is a RELEASE: the current one when nothing changed since, else a new one
    # (a re-publication of changed data is the next version). Dashboards elsewhere pin its files.
    if body and body.release is not None:
        rel = releases.get_by_number(db, dataset_id, body.release)
        if rel is None:
            raise HTTPException(status_code=404, detail="This dataset has no such release.")
        if (releases.latest(db, dataset_id) or {}).get("number") != rel["number"]:
            raise HTTPException(status_code=409, detail="Only the latest release can be published (GitHub holds one current copy).")
    else:
        rel = releases.ensure_current(db, dataset_id, reason="github_publish", created_by=who.user_id)
    new_version = rel["number"] if rel else None
    if target == "github+metalens" and not already:
        records.set_dataset_visibility(db, dataset_id, "public")    # viewable by link while the PR is open
    records.promote_dataset_preset(db, dataset_id, who)   # share the preset alongside the data
    job_id = worker.enqueue("publish_dataset_task", dataset_id)
    if job_id:
        if not already:
            records.set_publish_status(db, dataset_id, "pending")
        return {"queued": True, "job_id": job_id, "update": already, **({"version": new_version} if already else {})}
    try:
        out = github_publish.publish_dataset(db, dataset_id)
        if not already:                     # an update keeps the current copy listed until the merge lands
            records.set_publish_status(db, dataset_id, "pending")
        return {"queued": False, **out, "update": already, **({"version": new_version} if already else {})}
    except Exception as exc:                    # network / GitHub API / auth errors
        raise HTTPException(status_code=502, detail=f"Publish failed: {exc}")


# ── views: the observatory as data (Phase 4) ──────────────────────────────────

class ViewCreate(BaseModel):
    title: str
    view_type: str = "aggregate"
    dataset_ids: list[str] | None = None
    query: dict | None = None        # {schema_id?, verification_status?}
    viz_config: dict | None = None   # {kind, group_by, measure, value_field}
    visibility: str = "public"


@app.post("/api/views")
def create_view(body: ViewCreate, db=Depends(get_db),
                who: Principal = Depends(principal)) -> dict:
    return records.create_view(
        db, title=body.title, view_type=body.view_type, dataset_ids=body.dataset_ids,
        query=body.query, viz_config=body.viz_config, visibility=body.visibility,
        owner_user_id=who.user_id, session_id=who.session_id)


@app.get("/api/views")
def list_views(db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    return {"views": records.list_views(db, owner_user_id=who.user_id,
                                        session_id=who.session_id)}


def _require_view(db, view_id: str, who: Principal) -> dict:
    v = records.get_view(db, view_id)
    if v is None or (v["visibility"] != "public" and not records.is_view_owner(db, view_id, who)):
        raise HTTPException(status_code=404, detail="View not found.")
    return v


@app.get("/api/views/{view_id}")
def get_view(view_id: str, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    return _require_view(db, view_id, who)


@app.get("/api/views/{view_id}/data")
def view_data(view_id: str, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Run the view against the CURRENT records — recomputes on every request."""
    _require_view(db, view_id, who)
    result = records.run_view(db, view_id)
    if result is None:
        raise HTTPException(status_code=404, detail="View not found.")
    return result


# ── catalog query layer (cross-dataset search / facets / paper search) ─────────

def _search_filters(q, schema, jel, topic, status, year, dataset) -> dict:
    return {"q": q, "schema": schema, "jel": jel, "topic": topic,
            "status": status, "year": year, "dataset": dataset}


@app.get("/api/search")
def search(q: str | None = None, schema: str | None = None, jel: str | None = None,
           topic: str | None = None, status: str | None = None, year: int | None = None,
           dataset: list[str] | None = Query(None), limit: int = 50, offset: int = 0,
           db=Depends(get_db)) -> dict:
    """Cross-dataset record search + filters (the catalog browser's engine)."""
    return records.search_records(
        db, _search_filters(q, schema, jel, topic, status, year, dataset),
        limit=min(limit, 200), offset=offset)


@app.get("/api/facets")
def facets(q: str | None = None, schema: str | None = None, jel: str | None = None,
           topic: str | None = None, status: str | None = None, year: int | None = None,
           dataset: list[str] | None = Query(None), db=Depends(get_db)) -> dict:
    """Facet value+counts for the current query (drill-down rail)."""
    return records.facets(db, _search_filters(q, schema, jel, topic, status, year, dataset))


@app.get("/api/papers/search")
def papers_search(q: str | None = None, jel: str | None = None, topic: str | None = None,
                  year: int | None = None, limit: int = 25, offset: int = 0,
                  db=Depends(get_db)) -> dict:
    """Fuzzy paper search (title/abstract/keywords + jel/topic/year)."""
    return records.papers_search(db, q=q, jel=jel, topic=topic, year=year,
                                 limit=min(limit, 100), offset=offset)


@app.get("/api/records/{record_id}")
def record_detail(record_id: str, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    """Full record detail: record + paper(+provenance) + evidence + events + dataset.
    Visible iff the record is in a public dataset OR owned by the principal."""
    if not records.record_is_visible(db, record_id, who):
        raise HTTPException(status_code=404, detail="Record not found.")
    d = records.record_detail(db, record_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Record not found.")
    return d


class ProvenanceBody(BaseModel):
    ids: list[str] = []


@app.post("/api/records/provenance")
def records_provenance(body: ProvenanceBody, db=Depends(get_db),
                       who: Principal = Depends(principal)) -> dict:
    """Compact provenance (document_id, paper, top evidence page+snippet) for a set of
    record ids — powers a figure's data table + chart tooltips. Records the caller
    can't see are omitted (same gate as the tidy rows)."""
    return {"records": records.records_provenance(
        db, body.ids or [], principal=who, public_only=False)}


@app.delete("/api/records/{record_id}")
def delete_record(record_id: str, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    """Delete a single finding/record — owner only."""
    if records.get_record(db, record_id) is None:
        raise HTTPException(status_code=404, detail="Record not found.")
    if not records.records_all_owned(db, [record_id], who):
        raise HTTPException(status_code=403, detail="Not your record.")
    return records.delete_record(db, record_id)


class AddRecordBody(BaseModel):
    field_values: dict | None = None


@app.post("/api/documents/{document_id}/records")
def add_record(document_id: str, body: AddRecordBody, db=Depends(get_db),
               who: Principal = Depends(principal)) -> dict:
    """Add a manual finding/record to a document — owner only."""
    if not records.is_document_owner(db, document_id, who):
        raise HTTPException(status_code=403, detail="Not your document.")
    try:
        return records.add_record(db, document_id, field_values=body.field_values,
                                  session_id=who.session_id, owner_user_id=who.user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Document not found.")


class AggregateBody(BaseModel):
    filters: dict | None = None
    group_by: str | None = None
    measure: str = "count"
    value_field: str | None = None


@app.post("/api/aggregate")
def aggregate(body: AggregateBody, db=Depends(get_db)) -> dict:
    """Ad-hoc cross-dataset aggregation (the observatory generalized)."""
    return records.aggregate(db, filters=body.filters or {}, group_by=body.group_by,
                             measure=body.measure, value_field=body.value_field)


# ── verification / credibility (Phase 3) ──────────────────────────────────────

class VerifyBody(BaseModel):
    status: str = "verified"               # verified | flagged | unverified
    diff: list[dict] | None = None         # [{field_path, original_value, final_value}]
    notes: str | None = None
    verifier_kind: str | None = None       # maintainer | community | paperlens
    field_values: dict | None = None       # optional correction (full entry replacement)


@app.post("/api/records/{record_id}/verify")
def verify_record(record_id: str, body: VerifyBody, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    """Record a verification/flag event (+ optional value correction) on a record.

    Who may: the record's owner, anything; anyone else only on a record they can READ (it sits
    in a public dataset) and only an opinion (verified / flagged, with notes) — never a change of
    the values. A record id alone opens nothing: ids travel with published dashboards."""
    if not records.record_is_visible(db, record_id, who):
        raise HTTPException(status_code=404, detail="Record not found.")
    mine = records.records_all_owned(db, [record_id], who)
    if not mine and (body.field_values is not None or body.diff):
        raise HTTPException(status_code=403, detail="Only the owner can change the values of an entry.")
    kind = ("maintainer" if who.user_id else "community") if not mine else (body.verifier_kind or ("maintainer" if who.user_id else "community"))
    try:
        return records.verify_record(
            db, record_id, status=body.status, diff=body.diff, notes=body.notes,
            verifier_user_id=who.user_id, verifier_kind=kind, field_values=body.field_values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/records/{record_id}/events")
def record_events(record_id: str, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    if not records.record_is_visible(db, record_id, who):
        raise HTTPException(status_code=404, detail="Record not found.")
    return {"record_id": record_id, "events": records.record_events(db, record_id)}


# ── accounts (Phase 2b) ───────────────────────────────────────────────────────

class Credentials(BaseModel):
    email: str
    password: str


def _set_session_cookie(response: Response, token: str) -> None:
    # secure=False for local http dev; set PAPERLENS_SECURE_COOKIES=1 behind HTTPS (prod/Fly).
    secure = os.environ.get("PAPERLENS_SECURE_COOKIES") == "1"
    response.set_cookie(_SESSION_COOKIE, token, httponly=True, samesite="lax",
                        secure=secure, max_age=30 * 24 * 3600, path="/")


@app.post("/api/auth/register")
def register(body: Credentials, response: Response, db=Depends(get_db),
             who: Principal = Depends(principal)) -> dict:
    """Create an account, start a session, and claim the anon session's work."""
    _no_accounts_locally()
    try:
        user = auth.create_user(db, body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    _set_session_cookie(response, auth.create_session(db, user["id"]))
    claimed = auth.claim_anonymous(db, session_id=who.session_id, user_id=user["id"])
    return {"user": user, "claimed": claimed}


@app.post("/api/auth/login")
def login(body: Credentials, response: Response, db=Depends(get_db),
          who: Principal = Depends(principal)) -> dict:
    _no_accounts_locally()
    uid = auth.authenticate(db, body.email, body.password)
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    _set_session_cookie(response, auth.create_session(db, uid))
    claimed = auth.claim_anonymous(db, session_id=who.session_id, user_id=uid)
    return {"user": auth.get_user(db, uid), "claimed": claimed}


@app.post("/api/auth/logout")
def logout(response: Response, pl_session: str | None = Cookie(default=None),
           db=Depends(get_db)) -> dict:
    _no_accounts_locally()
    auth.delete_session(db, pl_session)
    response.delete_cookie(_SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(who: Principal = Depends(principal), db=Depends(get_db)) -> dict:
    if not who.user_id:
        raise HTTPException(status_code=401, detail="Not logged in.")
    if localmode.enabled():                # the fixed local owner; the UI hides the account widget
        return {"id": who.user_id, "email": localmode.LOCAL_EMAIL, "local_mode": True}
    return auth.get_user(db, who.user_id)


# How many papers a logged-OUT visitor may extract on the server's own key before they
# have to make an account. Bringing your own API key lifts the cap entirely.
ANON_FREE_EXTRACTIONS = 1


@app.get("/api/extraction-config")
def extraction_config(who: Principal = Depends(principal), db=Depends(get_db)) -> dict:
    """Public: what the extract page needs before it knows who you are — the model
    extraction runs on by default, and the logged-out limits. Adds the credit balance
    when there IS a user, so the page can render its whole model line from one call."""
    model = credits.credit_model()
    out = {
        "model": model,
        "offered": credits.offered(),          # server actually holds a key for it
        "logged_in": bool(who.user_id),
        "anon_free_extractions": ANON_FREE_EXTRACTIONS,
        "anon_extractions_used": 0,
        "anon_retention_minutes": retention.ANON_RETENTION_MINUTES,
        "can_download": True,                  # JSON / CSV export needs no account
        "local_mode": localmode.enabled(),
        "github_publishing": bool(os.environ.get("PAPERLENS_GITHUB_TOKEN")),   # the publish dialog's options
    }
    if localmode.enabled():                    # own key or a local model; no credits, no trial
        out["offered"] = False
        return out
    if who.user_id:
        out["credits"] = credits.summary(db, who.user_id)
    else:
        out["anon_extractions_used"] = retention.trial_used(db, who.session_id)
    return out


@app.get("/api/credits")
def get_credits(who: Principal = Depends(principal), db=Depends(get_db)) -> dict:
    """The logged-in user's credit balance + recent ledger, and whether keyless
    credit extraction is currently offered by the server (so the UI can show the toggle)."""
    if not who.user_id:
        raise HTTPException(status_code=401, detail="Not logged in.")
    return {**credits.summary(db, who.user_id),
            "ledger": credits.ledger(db, who.user_id),
            "offered": credits.offered(), "model": credits.credit_model()}


class ProfileBody(BaseModel):
    citation_name: str | None = None


@app.patch("/api/auth/me")
def update_me(body: ProfileBody, who: Principal = Depends(principal), db=Depends(get_db)) -> dict:
    """Update the citation name used to attribute the user's public datasets."""
    if not who.user_id:
        raise HTTPException(status_code=401, detail="Not logged in.")
    auth.update_profile(db, who.user_id, citation_name=body.citation_name)
    return auth.get_user(db, who.user_id)


class PasswordBody(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/auth/password")
def change_password(body: PasswordBody, who: Principal = Depends(principal), db=Depends(get_db)) -> dict:
    _no_accounts_locally()
    if not who.user_id:
        raise HTTPException(status_code=401, detail="Not logged in.")
    if not auth.change_password(db, who.user_id, body.old_password, body.new_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect, or the new one is empty.")
    return {"ok": True}


@app.delete("/api/auth/me")
def delete_account(response: Response, who: Principal = Depends(principal),
                   pl_session: str | None = Cookie(default=None), db=Depends(get_db)) -> dict:
    """Account deletion (GDPR): removes the user's documents (+ stored PDFs/pages),
    datasets, and the account itself (cascading sessions)."""
    if not who.user_id:
        raise HTTPException(status_code=401, detail="Not logged in.")
    result = records.delete_user_data(db, who.user_id)
    response.delete_cookie(_SESSION_COOKIE, path="/")
    return {"ok": True, **result}


@app.post("/api/papers/enrich")
def enrich_endpoint(doi: str, abstract: str | None = None, db=Depends(get_db)) -> dict:
    """Trigger DOI enrichment (Crossref->Unpaywall->OpenAlex + JEL).

    Enqueues an Arq job when Redis is up (restart-safe); falls back to running
    synchronously otherwise. Never called from a read path.
    """
    job_id = worker.enqueue("enrich_paper_task", doi, abstract)
    if job_id:
        return {"queued": True, "job_id": job_id}
    result = enrich.enrich_paper(db, doi, abstract=abstract)
    return {"queued": False, **result}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    """Poll an Arq job (status + result/error). Mirrors the archive's extract->poll."""
    st = worker.job_status(job_id)
    if st is None:
        raise HTTPException(status_code=503, detail="Queue unavailable.")
    return st


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    """Stop a queued or in-progress extraction (mirrors the id-gated job-status endpoint)."""
    return {"cancelled": worker.cancel_job(job_id)}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """Re-run a failed extraction from its original stored args (PDF bytes + recipe) — no
    re-upload. Owner-gated; re-consumes a credit for credit runs like the first attempt."""
    ja = worker.job_args(job_id)
    if not ja or ja["function"] != "extract_job":
        raise HTTPException(status_code=410,
                            detail="This job's data has expired — re-upload the paper to retry.")
    kw = ja["kwargs"]
    owner, sess = kw.get("owner_user_id"), kw.get("session_id")
    if not ((owner and who.user_id and str(owner) == str(who.user_id))
            or (sess and sess == who.session_id)):
        raise HTTPException(status_code=403, detail="Not authorized to retry this job.")
    if kw.get("use_credits"):                     # mirror the extract endpoint's consume-at-enqueue
        cuid = kw.get("credit_user_id") or who.user_id
        cmodel = kw.get("model") or credits.credit_model()
        if not (cuid and cmodel and credits.try_consume(db, cuid, model=cmodel)):
            raise HTTPException(status_code=402,
                                detail="You have no Metalens credits left. Use your own API key instead.")
    new_id = worker.enqueue(ja["function"], *ja["args"], **kw)
    if not new_id:
        raise HTTPException(status_code=503, detail="Queue unavailable — try again shortly.")
    return {"job_id": new_id}


@app.post("/api/extract")
def extract_endpoint(
    request: Request,
    pdf: UploadFile = File(...),
    prompt: str = Form(""),
    preset_id: str | None = Form(None),
    model: str = Form(""),
    api_key: str = Form(""),
    base_url: str | None = Form(None),
    use_text: bool = Form(False),
    schema_id: str | None = Form(None),
    use_credits: bool = Form(False),
    dataset_id: str | None = Form(None),
    params: str | None = Form(None),
    db=Depends(get_db),
    who: Principal = Depends(principal),
) -> dict:
    """Upload a PDF -> extract into records. Browser supplies the model + api_key
    per request (never persisted). The SERVER decides the prompt and the schema row:
    ``preset_id`` (+ JSON ``params``) renders the preset's prompt and mints its
    content-addressed schema id; an existing ``schema_id`` is kept as is; a posted
    ``prompt`` always wins (and is flagged as edited). Enqueues when Redis is up
    (restart-safe), else runs synchronously. When ``dataset_id`` is given (add-papers),
    the finished document is attached to that dataset server-side — so queued papers
    land in it even though the browser has no document_id yet."""
    data = pdf.file.read()
    fname = pdf.filename or None
    if dataset_id and not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="You don't own that dataset.")

    try:
        run = presets.resolve_run(db, preset_id=preset_id, schema_id=schema_id,
                                  params=params, prompt=prompt)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    prompt = run.prompt
    schema_id = run.schema_id
    if not prompt.strip():
        pid = preset_id or (schema_id.split("@")[0] if schema_id else None)
        raise HTTPException(status_code=422, detail=(
            f"No `prompt` given and preset {pid!r} is unknown. Pass a `prompt` with full "
            "canonical-JSON instructions (it must ask the model for an `evidence` array), "
            "or use one of the built-in presets (masem-direct / masem-indirect / summarize)."))
    if schema_id:
        # mint the row NOW, before the worker needs it, so a queued job never races it
        with db.transaction():
            records.upsert_schema(db, schema_id, run.field_defs)

    own_key = bool(api_key.strip()) or bool((base_url or "").strip())   # a self-hosted model needs no key

    # Is this a keyless request this deployment can actually serve? ``credits.offered()``
    # gates the whole story: with no server key configured there is nothing to run on, so
    # such a request falls through to the legacy path (enqueue what the caller supplied)
    # rather than being refused something this deployment never promised.
    if localmode.enabled() and (use_credits or not own_key):
        raise HTTPException(status_code=422, detail="Local mode runs on your own API key (or a local model): add a key first.")
    keyless_available = not own_key and not use_credits and credits.offered()

    # ── logged-out free trial: the server's own key, capped per anonymous session ──
    # The model picker is gone from the UI, so a visitor with no account and no key still
    # has to be able to run something. They get ANON_FREE_EXTRACTIONS papers on the default
    # model; after that it's an account or their own key. NOTE: the cap is measured from
    # documents already persisted, so papers submitted concurrently can slip past it —
    # it's a free-trial nudge, not a hard quota.
    if keyless_available and not who.user_id:
        server_model = credits.credit_model()
        server_key = credits.server_key_for(providers.get_provider(server_model, None))
        if retention.trial_used(db, who.session_id) >= ANON_FREE_EXTRACTIONS:
            raise HTTPException(status_code=402, detail=(
                f"You’ve used your free trial ({ANON_FREE_EXTRACTIONS} paper). Create a free "
                "account to keep extracting, or add your own API key."))
        # a fresh browser profile is not a fresh trial: cap trials per network per day
        iph = retention.ip_hash(retention.client_ip(request)) if request is not None else None
        if retention.ip_trials_today(db, iph) >= retention.ANON_TRIAL_PER_IP_DAY:
            raise HTTPException(status_code=402, detail=(
                "The free trial has been used from this network today. Create a free account "
                "to keep extracting, or add your own API key."))
        retention.bump_trial(db, who.session_id, iph)
        # use_credits=True tells the WORKER to resolve the server key from its own env, so
        # the key never enters the Redis payload; credit_user_id=None means no ledger entry
        # and nothing to refund — an anonymous trial run spends no credits.
        job_id = worker.enqueue(
            "extract_job", base64.b64encode(data).decode(), prompt,
            model=server_model, api_key="", base_url=None, use_text=use_text,
            schema_id=schema_id, session_id=who.session_id, owner_user_id=None,
            filename=fname, params=run.params, prompt_edited=run.prompt_edited,
            use_credits=True, credit_user_id=None,
            dataset_id=dataset_id, _expires=1800)
        if job_id:
            return {"queued": True, "job_id": job_id, "schema_id": schema_id}
        try:                                   # Redis down → run it inline
            result = extract.run_extraction(
                db, data, prompt, model=server_model, api_key=server_key, base_url=None,
                use_text=use_text, schema_id=schema_id, session_id=who.session_id,
                owner_user_id=None, filename=fname, spec=run.spec, params=run.params,
                prompt_edited=run.prompt_edited)
            if dataset_id:
                records.assign_document_to_dataset(db, dataset_id, result["document_id"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502,
                                detail=f"Extraction failed: {providers.extract_provider_message(exc)}")
        return {"queued": False, **result}

    # A logged-in user with neither credits selected nor a key would otherwise reach the
    # provider with an empty key and get an opaque 401 — say what's actually wrong.
    if keyless_available and who.user_id:
        raise HTTPException(status_code=402, detail=(
            "You have no Metalens credits left. Add your own API key on the Review "
            "prompt step to keep extracting."))

    # ── Metalens credits: keyless run on the server's key + fixed model ───────────
    # ENQUEUED like any extraction so it survives navigation (review the first result
    # while the rest finish) and retries with backoff (beats provider rate limits). The
    # server key is NOT in the job payload — the worker resolves it from its env when it
    # sees ``use_credits``. Consume-at-enqueue; the worker refunds on final failure.
    if use_credits:
        if not who.user_id:
            raise HTTPException(status_code=401, detail="Log in to use Metalens credits.")
        cmodel = model if (model and credits.is_allowed_model(model)) else credits.credit_model()
        server_key = credits.server_key_for(providers.get_provider(cmodel, None)) if cmodel else None
        if not cmodel or not server_key:
            raise HTTPException(status_code=400,
                                detail="Metalens credits aren’t available right now — use your own API key.")
        if not credits.try_consume(db, who.user_id, model=cmodel):
            raise HTTPException(status_code=402,
                                detail="You have no Metalens credits left. Use your own API key instead.")
        job_id = worker.enqueue(
            "extract_job", base64.b64encode(data).decode(), prompt,
            model=cmodel, api_key="", base_url=None, use_text=use_text, schema_id=schema_id,
            session_id=who.session_id, owner_user_id=who.user_id, filename=fname,
            params=run.params, prompt_edited=run.prompt_edited,
            use_credits=True, credit_user_id=who.user_id, dataset_id=dataset_id, _expires=1800)
        if job_id:
            return {"queued": True, "job_id": job_id, "schema_id": schema_id}
        # Redis down → run synchronously (no Redis payload to protect anyway)
        try:
            result = extract.run_extraction(
                db, data, prompt, model=cmodel, api_key=server_key, base_url=None,
                use_text=use_text, schema_id=schema_id, session_id=who.session_id,
                owner_user_id=who.user_id, filename=fname, spec=run.spec, params=run.params,
                prompt_edited=run.prompt_edited)
            if dataset_id:
                records.assign_document_to_dataset(db, dataset_id, result["document_id"])
        except ValueError as exc:
            credits.refund(db, who.user_id, model=cmodel)
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            credits.refund(db, who.user_id, model=cmodel)
            raise HTTPException(status_code=502,
                                detail=f"Extraction failed: {providers.extract_provider_message(exc)}")
        return {"queued": False, "credits": credits.summary(db, who.user_id), **result}

    job_id = worker.enqueue(
        "extract_job", base64.b64encode(data).decode(), prompt,
        model=model, api_key=api_key, base_url=base_url, use_text=use_text,
        schema_id=schema_id, session_id=who.session_id, owner_user_id=who.user_id,
        filename=fname, dataset_id=dataset_id,
        params=run.params, prompt_edited=run.prompt_edited,
        # discard the job if no worker consumes it within 30 min (avoid running a
        # long-stale extraction the user has already abandoned — see delayed=… logs)
        _expires=1800)
    if job_id:
        return {"queued": True, "job_id": job_id, "schema_id": schema_id}

    # Synchronous fallback (Redis down): translate failures into clean responses.
    try:
        result = extract.run_extraction(
            db, data, prompt, model=model, api_key=api_key, base_url=base_url,
            use_text=use_text, schema_id=schema_id, session_id=who.session_id,
            owner_user_id=who.user_id, filename=fname, spec=run.spec, params=run.params,
                prompt_edited=run.prompt_edited)
        if dataset_id:
            records.assign_document_to_dataset(db, dataset_id, result["document_id"])
    except ValueError as exc:                 # bad/empty PDF, unparseable model output
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:                   # provider errors (bad key/model/quota)
        raise HTTPException(status_code=502,
                            detail=f"Extraction failed: {providers.extract_provider_message(exc)}")
    return {"queued": False, **result}


def _ingest_with_pdf(db, who: Principal, data: bytes, result, schema_id: str | None,
                     dataset_id: str | None, *, filename: str | None) -> dict:
    """The import pipeline with a PDF: render pages, locate the JSON's own evidence,
    persist a full viewable document — /api/extract minus the model call."""
    import json as _json
    if dataset_id and not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="You don't own that dataset.")
    obj = result
    if isinstance(obj, str):
        try:
            obj = _json.loads(obj)
        except Exception:
            raise HTTPException(status_code=422, detail="`result` is not valid JSON.")
    canonical = (obj.get("extraction") if isinstance(obj, dict)
                 and isinstance(obj.get("extraction"), dict) else obj)
    text = _json.dumps(canonical)
    usage = (obj.get("usage") if isinstance(obj, dict) else None) or {}
    run = presets.resolve_run(db, schema_id=schema_id)
    schema_id = run.schema_id
    if schema_id:
        with db.transaction():
            records.upsert_schema(db, schema_id, run.field_defs)

    def _supplied(pdf_bytes, prompt, **kw):
        return extract.LLMResult(text=text, finish_reason="stop", usage=usage, resolved_model="imported")

    try:
        res = extract.run_extraction(
            db, data, prompt="", schema_id=schema_id, session_id=who.session_id,
            owner_user_id=who.user_id, filename=filename, complete=_supplied,
            spec=run.spec)
    except ValueError as exc:                 # malformed canonical JSON (no records/evidence)
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Import failed: {exc}")
    if dataset_id:
        records.assign_document_to_dataset(db, dataset_id, res["document_id"])
    return {"queued": False, **res}


@app.post("/api/ingest-pdf")
def ingest_pdf_endpoint(
    pdf: UploadFile = File(...),
    result: str = Form(...),
    schema_id: str | None = Form(None),
    dataset_id: str | None = Form(None),
    db=Depends(get_db),
    who: Principal = Depends(principal),
) -> dict:
    """Import a PRE-COMPUTED extraction (canonical JSON) together with its source PDF: render
    the pages and compute highlight rects from the JSON's own evidence, then persist a full
    viewable document — the SAME pipeline as /api/extract, but with the result SUPPLIED
    instead of calling a model (no model, key, or credits).  Accepts either the canonical
    object or a ``{…, "extraction": {…}}`` wrapper.  With ``dataset_id`` the imported document
    is added to that dataset (owner-gated)."""
    return _ingest_with_pdf(db, who, pdf.file.read(), result, schema_id, dataset_id,
                            filename=pdf.filename)


@app.get("/api/papers/provenance")
def papers_provenance(doi: str, db=Depends(get_db)) -> dict:
    """The §3.5 provenance footer: enriched paper + per-field source/method."""
    p = records.paper_with_provenance(db, doi)
    if p is None:
        raise HTTPException(status_code=404, detail="Paper not in catalog.")
    return p


@app.get("/api/presets")
def list_presets(db=Depends(get_db), who: Principal = Depends(principal),
                 b: brands.Brand = Depends(brand)) -> dict:
    """The resolved view-grammar for every preset in the picker: global file presets
    (minus ``landing_hidden`` ones, e.g. the MASEMiner factor-loadings variant, and minus
    those tagged for another brand) PLUS the principal's own DB-backed personal presets AND
    the PUBLIC ones created on this brand. Each row is tagged ``personal``/``owned`` so the
    UI can label & manage them."""
    allp = presets.load_all()
    rows = []
    for pid in sorted(allp):
        meta = allp[pid].get("meta") or {}
        if meta.get("hidden") or not b.shows_preset(meta):
            continue
        row = presets.emit_schema_row(pid)
        if row:
            row.update(personal=False, owned=False)
            rows.append(row)
    for p in records.list_personal_presets(db, owner_user_id=who.user_id, session_id=who.session_id, brand=b.id):
        if p["id"] in allp:            # a built-in shadows a same-id personal row (seeded copies of old presets)
            continue
        base = allp.get(presets.resolve_id(p["base_preset_id"])) if p.get("base_preset_id") else None
        if base is not None and not b.shows_preset(base.get("meta")):
            continue                   # a setup of a preset this brand does not offer
        row = presets.emit_schema_row(p["id"], conn=db)
        if row:
            row.update(personal=True, visibility=p["visibility"],
                       owned=records._owns(who, p["owner_user_id"], p["session_id"]))
            if p.get("base_preset_id"):        # a saved setup: shown under its base, run as the base
                row.update(preset_id=p["id"], title=p["title"], tagline=p.get("tagline"),
                           setup=True, base_preset_id=p["base_preset_id"], params=p.get("params") or {})
            rows.append(row)
    return {"presets": rows}


class PresetBody(BaseModel):
    title: str | None = None
    prompt: str | None = None
    tagline: str | None = None
    description: str | None = None
    mode: str = "extraction"
    sub_views: list | None = None
    template_params: dict | None = None
    accent_color: str | None = None
    visibility: str = "private"
    spec: dict | None = None            # the declarative preset document (preferred)
    base_preset_id: str | None = None   # a saved setup: values for a built-in preset's parameters
    params: dict | None = None


def _validated_spec(doc: dict, preset_id: str) -> dict:
    """Normalise + validate a posted spec under the row id it will live at; 422 with the
    full error list otherwise (the editor shows them all at once)."""
    from . import preset_spec
    doc = dict(doc or {})
    doc["id"] = preset_id
    doc.setdefault("format", preset_spec.FORMAT)
    try:
        return preset_spec.normalize(doc)
    except preset_spec.SpecError as exc:
        raise HTTPException(status_code=422, detail={"message": "Invalid preset spec",
                                                     "errors": exc.errors})


@app.post("/api/presets")
def create_preset(body: PresetBody, db=Depends(get_db),
                  who: Principal = Depends(principal), b: brands.Brand = Depends(brand)) -> dict:
    """Create a personal preset owned by the principal (user, or anon session claimable
    on login). Usable immediately in the extract picker. Post a ``spec`` (the declarative
    document; the server assigns its id and renders its prompt) or, legacy, a bare
    ``title`` + ``prompt``."""
    if body.visibility not in ("public", "private"):
        raise HTTPException(status_code=422, detail="visibility must be public|private")
    if body.base_preset_id:
        return _create_setup(db, who, body, brand_id=b.id)
    if body.spec is not None:
        from . import preset_spec
        title = ((body.spec.get("meta") or {}).get("title") or body.title or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="spec.meta.title is required.")
        pid = f"{records._slugify(title)}-{uuid.uuid4().hex[:8]}"
        spec = _validated_spec(body.spec, pid)
        records.create_personal_preset(
            db, preset_id=pid, title=spec["meta"]["title"], prompt=preset_spec.render_prompt(spec),
            tagline=spec["meta"].get("tagline"), description=spec["meta"].get("description"),
            mode=spec["meta"]["mode"], spec=spec, owner_user_id=who.user_id,
            session_id=who.session_id, visibility=body.visibility, brand=b.id)
        return presets.get(pid, conn=db)
    if not (body.title or "").strip() or not (body.prompt or "").strip():
        raise HTTPException(status_code=422, detail="title and prompt are required.")
    return records.create_personal_preset(
        db, title=body.title, prompt=body.prompt, tagline=body.tagline,
        description=body.description, mode=body.mode, sub_views=body.sub_views,
        template_params=body.template_params, accent_color=body.accent_color,
        owner_user_id=who.user_id, session_id=who.session_id, visibility=body.visibility, brand=b.id)


def _setup_params(base_id: str, params: dict | None) -> tuple[dict, dict]:
    """(base spec, the declared subset of params); 422 on an unknown base or parameter."""
    base = presets.load_all().get(presets.resolve_id(base_id))
    if base is None:
        raise HTTPException(status_code=422, detail="base_preset_id must name a built-in preset.")
    decl = (base.get("prompt") or {}).get("params") or {}
    if not decl:
        raise HTTPException(status_code=422, detail=f"{base['id']} has no parameters to save.")
    unknown = sorted(set(params or {}) - set(decl))
    if unknown:
        raise HTTPException(status_code=422,
                            detail=f"unknown parameters for {base['id']}: {', '.join(unknown)}")
    return base, {k: v for k, v in (params or {}).items() if k in decl}


def _create_setup(db, who: Principal, body: PresetBody, *, brand_id: str | None = None) -> dict:
    """A saved setup (sub-preset): a name + parameter values for a built-in preset. Private
    to its creator for now; runs resolve the live base, so the setup only pins the values."""
    base, params = _setup_params(body.base_preset_id, body.params)
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required.")
    pid = f"{records._slugify(title)}-{uuid.uuid4().hex[:8]}"
    rendered = presets.render(base["id"], params)["prompt"]
    records.create_personal_preset(
        db, preset_id=pid, title=title, prompt=rendered,
        tagline=body.tagline or f"Saved setup of {base['meta'].get('title') or base['id']}",
        mode=base["meta"].get("mode", "extraction"), base_preset_id=base["id"], params=params,
        owner_user_id=who.user_id, session_id=who.session_id, visibility="private", brand=brand_id)
    return presets.get(pid, conn=db)


class SpecBody(BaseModel):
    spec: dict


@app.post("/api/presets/validate")
def validate_preset(body: SpecBody) -> dict:
    """Check a spec without saving it: every error and warning at once, and the schema
    id it would run under (the editor's live feedback)."""
    from . import preset_spec
    doc = dict(body.spec or {}); doc.setdefault("format", preset_spec.FORMAT)
    doc.setdefault("id", "draft")
    try:
        spec = preset_spec.normalize(doc)
    except preset_spec.SpecError as exc:
        return {"ok": False, "errors": exc.errors, "warnings": [], "schema_id": None}
    _errs, warns = preset_spec.validate(spec)
    return {"ok": True, "errors": [], "warnings": warns, "schema_id": preset_spec.schema_id(spec),
            "prompt": preset_spec.render_prompt(spec)}


@app.get("/api/presets/mine")
def my_presets(db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """The principal's OWN personal presets (My Workspace list)."""
    return {"presets": records.list_personal_presets(
        db, owner_user_id=who.user_id, session_id=who.session_id, owned_only=True)}


class PresetPatch(BaseModel):
    title: str | None = None
    prompt: str | None = None
    tagline: str | None = None
    description: str | None = None
    mode: str | None = None
    sub_views: list | None = None
    template_params: dict | None = None
    accent_color: str | None = None
    visibility: str | None = None
    spec: dict | None = None            # replaces the whole declaration (re-renders the prompt)
    params: dict | None = None          # a saved setup's values (re-renders its prompt)


@app.patch("/api/presets/{preset_id}")
def update_preset(preset_id: str, body: PresetPatch, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    if not records.is_preset_owner(db, preset_id, who):
        raise HTTPException(status_code=403, detail="Not authorized.")
    if body.visibility is not None and body.visibility not in ("public", "private"):
        raise HTTPException(status_code=422, detail="visibility must be public|private")
    row = records.get_personal_preset(db, preset_id) or {}
    if row.get("base_preset_id") and body.visibility == "public":
        raise HTTPException(status_code=422, detail="Saved setups are private for now.")
    if body.params is not None and not row.get("base_preset_id"):
        raise HTTPException(status_code=422, detail="params can only be set on a saved setup.")
    fields = {k: v for k, v in {
        "title": body.title, "prompt": body.prompt, "tagline": body.tagline,
        "description": body.description, "mode": body.mode, "sub_views": body.sub_views,
        "template_params": body.template_params, "accent_color": body.accent_color,
        "visibility": body.visibility}.items() if v is not None}
    if body.spec is not None:
        from . import preset_spec
        spec = _validated_spec(body.spec, preset_id)
        fields.update(spec=spec, prompt=preset_spec.render_prompt(spec), title=spec["meta"]["title"],
                      tagline=spec["meta"].get("tagline"), description=spec["meta"].get("description"),
                      mode=spec["meta"]["mode"])
    if body.params is not None:
        base, params = _setup_params(row["base_preset_id"], body.params)
        fields.update(params=params, prompt=presets.render(base["id"], params)["prompt"])
    records.update_personal_preset(db, preset_id, **fields)
    return presets.get(preset_id, conn=db)


@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: str, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    if not records.is_preset_owner(db, preset_id, who):
        raise HTTPException(status_code=403, detail="Not authorized.")
    return records.delete_personal_preset(db, preset_id)


class RenderBody(BaseModel):
    params: dict = {}


@app.post("/api/presets/{preset_id}/render")
def render_preset(preset_id: str, body: RenderBody, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    """The prompt (+ schema id) this preset produces for ``params`` — what a run will use."""
    if not presets.is_visible(db, preset_id, who):
        raise HTTPException(status_code=404, detail="Preset not found.")
    out = presets.render(preset_id, body.params or {}, conn=db)
    if out is None:
        raise HTTPException(status_code=404, detail="Preset not found.")
    return {"prompt": out["prompt"], "schema_id": out["schema_id"], "params": out["params"],
            "sub_views": out["sub_views"]}


@app.get("/api/presets/{preset_id}/prompt")
def preset_prompt(preset_id: str, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    """The fully-rendered extraction prompt for a preset (for the Review-prompt step).
    Owner-or-public: a private personal preset must not be readable by id."""
    if not presets.is_visible(db, preset_id, who):
        raise HTTPException(status_code=404, detail="Preset not found.")
    p = presets.prompt_for(preset_id, conn=db)
    if p is None:
        raise HTTPException(status_code=404, detail="Preset not found.")
    return {"preset_id": preset_id, "prompt": p}


@app.get("/api/presets/{preset_id}/detail")
def preset_detail(preset_id: str, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    """Full preset meta incl. ``template_params`` (MASEMiner builder seeds its form
    from these) or the raw fields of a personal preset (the preset editor loads these).
    Owner-or-public, like the picker."""
    if not presets.is_visible(db, preset_id, who):
        raise HTTPException(status_code=404, detail="Preset not found.")
    meta = presets.get(preset_id, conn=db)
    if meta is None:
        raise HTTPException(status_code=404, detail="Preset not found.")
    return meta


class BuildPresetBody(BaseModel):
    preset_id: str
    template_params: dict = {}     # alias kept for the MASEMiner builder
    params: dict | None = None


@app.post("/api/build-preset-prompt")
def build_preset_prompt(body: BuildPresetBody, db=Depends(get_db),
                        who: Principal = Depends(principal)) -> dict:
    """Render a preset's prompt with user-supplied parameter values (the MASEMiner
    builder posts here on every form change for the live preview). Values merge over the
    preset's declared defaults, so only changed fields need be sent. Works for built-in
    and (visible) personal presets alike."""
    if not presets.is_visible(db, body.preset_id, who):
        raise HTTPException(status_code=404, detail="Preset not found.")
    out = presets.render(body.preset_id, body.params or body.template_params or {}, conn=db)
    if out is None:
        raise HTTPException(status_code=404, detail="Preset not found.")
    return {"prompt": out["prompt"], "sub_views": out["sub_views"],
            "schema_id": out["schema_id"], "params": out["params"]}


class TestKeyBody(BaseModel):
    model: str
    api_key: str
    base_url: str | None = None


@app.post("/api/providers/test")
def providers_test(body: TestKeyBody) -> dict:
    """Test-connection: a tiny live call to the provider with the browser-supplied
    key. The key is used once and never stored."""
    try:
        providers.generate_text(body.model, body.api_key, "ping", base_url=body.base_url)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": providers.extract_provider_message(exc)}


@app.get("/api/schemas/{schema_id}")
def get_schema(schema_id: str, db=Depends(get_db)) -> dict:
    s = records.get_schema(db, schema_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Schema not found.")
    s["spec"] = records.schema_spec(db, schema_id)
    return s
