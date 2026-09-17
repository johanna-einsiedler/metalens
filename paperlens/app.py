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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, brands, credits, enrich, extract, figures_spec, localmode, presets, providers, records, storage, worker, retention
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


@app.get("/catalog")
def catalog() -> FileResponse:
    """Tool-faithful dataset/record browser (search + facets)."""
    return _page("catalog.html")


@app.get("/catalog/record/{record_id}")
def catalog_record(record_id: str) -> FileResponse:
    """Same shell; the JS reads the path → record detail (deep-link/refresh-safe)."""
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
def observatory() -> FileResponse:
    """The flagship public view — a saved view over records, rendered as a chart."""
    return _page("observatory.html")


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


@app.get("/builder")
def builder_page() -> FileResponse:
    """Analysis/dashboard builder: goal → LLM proposes figures → edit → save."""
    return _page("builder.html")


@app.get("/analysis")
def analysis_page() -> FileResponse:
    """Render a saved dashboard analysis (?view=…) as D3 figures over live rows."""
    return _page("analysis.html")


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
    if not owner:                        # don't leak the uploader's local filenames publicly
        ov.pop("owner_citation_name", None)   # …nor the name behind an anonymous dataset
        for doc in ov.get("documents", []):
            doc["filename"] = None
    return ov


@app.get("/api/datasets/{dataset_id}/export")
def dataset_export(dataset_id: str, db=Depends(get_db),
                   who: Principal = Depends(principal)) -> dict:
    """The downloadable dataset file: metadata + papers, each with its own records.
    Same owner-or-public gate as the overview, PLUS an account: anyone may review
    extracted data in the browser, but taking it away as a file needs a login."""
    if not who.user_id:
        raise HTTPException(status_code=401, detail=(
            "Create a free account to download extracted data."))
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
    res = {**res, **records.set_dataset_visibility(db, dataset_id, body.visibility)}
    if body.visibility == "public":
        # publishing a dataset also publishes the personal preset it was built with,
        # so others can find and use it (only if the publisher owns that preset)
        promoted = records.promote_dataset_preset(db, dataset_id, who)
        if promoted:
            res["promoted_preset"] = promoted
    return res


@app.post("/api/datasets/{dataset_id}/publish")
def publish_dataset(dataset_id: str, db=Depends(get_db),
                    who: Principal = Depends(principal)) -> dict:
    """Publish a dataset to the metalens-datasets GitHub repo as a PR. Owner-only, and
    (the bright wall) only when the owner owns EVERY record. Enqueues when Redis is up,
    else runs synchronously. Returns {pr_url} or {queued, job_id}."""
    from . import github_publish
    if not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="Not authorized.")
    if not records.dataset_records_all_owned(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="You can only publish records you own.")
    if not github_publish.token():
        raise HTTPException(status_code=400,
                            detail="GitHub publishing isn’t configured on this server.")
    records.promote_dataset_preset(db, dataset_id, who)   # share the preset alongside the data
    job_id = worker.enqueue("publish_dataset_task", dataset_id)
    if job_id:
        return {"queued": True, "job_id": job_id}
    try:
        return {"queued": False, **github_publish.publish_dataset(db, dataset_id)}
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


@app.get("/api/analyses/{view_id}/rows")
def analysis_rows(view_id: str, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    """Tidy rows for a saved dashboard analysis — the data its D3 figures aggregate
    client-side. View access is gated by _require_view; individual rows are further
    scoped (public dataset rows + the principal's own) so a public dashboard over a
    private dataset never leaks another user's records."""
    v = _require_view(db, view_id, who)
    return {"rows": records.dataset_rows(
        db, v.get("dataset_ids") or [], principal=who, public_only=False)}


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
    """Record a verification/flag event (+ optional value correction) on a record."""
    if records.get_record(db, record_id) is None:
        raise HTTPException(status_code=404, detail="Record not found.")
    kind = body.verifier_kind or ("maintainer" if who.user_id else "community")
    try:
        return records.verify_record(
            db, record_id, status=body.status, diff=body.diff, notes=body.notes,
            verifier_user_id=who.user_id, verifier_kind=kind, field_values=body.field_values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/records/{record_id}/events")
def record_events(record_id: str, db=Depends(get_db)) -> dict:
    if records.get_record(db, record_id) is None:
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
        "can_download": bool(who.user_id),     # exports are an account feature
        "local_mode": localmode.enabled(),
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

    own_key = bool(api_key.strip())

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


class ProposeFiguresBody(BaseModel):
    goals: str = ""
    model: str
    api_key: str
    base_url: str | None = None
    entry: str = "dataset"                 # "dataset" | "papers"
    dataset_id: str | None = None
    page_images: list[str] | None = None   # base64 PNGs (Entry A / papers)


@app.post("/api/analyses/propose-figures")
def propose_figures(body: ProposeFiguresBody, db=Depends(get_db),
                    who: Principal = Depends(principal)) -> dict:
    """AI-propose dashboard figures (the builder's brain). Entry 'dataset' grounds the
    proposal in an existing dataset's variables + sample records; entry 'papers' grounds
    it in uploaded page images. Output is validated/repaired to the figure grammar; the
    browser key is used once and never stored."""
    if body.entry == "dataset":
        if not body.dataset_id:
            raise HTTPException(status_code=422, detail="dataset_id required for entry='dataset'.")
        d = records.get_dataset(db, body.dataset_id)
        if d is None or (d["visibility"] != "public" and not records.is_dataset_owner(db, body.dataset_id, who)):
            raise HTTPException(status_code=404, detail="Dataset not found.")
        rows = records.dataset_rows(db, [body.dataset_id], principal=who, public_only=False, limit=40)
        keys = sorted({k for r in rows for k in (r.get("field_values") or {}).keys()})
        prompt = figures_spec.dataset_prompt(body.goals, keys, [r["field_values"] for r in rows[:15]])
        call = lambda p: providers.generate_text(body.model, body.api_key, p, base_url=body.base_url)  # noqa: E731
    elif body.entry == "papers":
        if not body.page_images:
            raise HTTPException(status_code=422, detail="page_images required for entry='papers'.")
        prompt = figures_spec.papers_prompt(body.goals)
        blocks = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}", "detail": "high"}}
            for img in body.page_images[:12]]
        call = lambda p: providers.extract_with_images(  # noqa: E731
            model=body.model, api_key=body.api_key, content_blocks=blocks,
            extraction_images=body.page_images[:12], prompt=p,
            page_instruction="", n=len(body.page_images[:12]), base_url=body.base_url)[0]
    else:
        raise HTTPException(status_code=422, detail="entry must be 'dataset' or 'papers'.")

    try:
        text = call(prompt)
        figures, dropped = figures_spec.parse_and_validate(text)
        if not figures:                     # one repair retry with an explicit nudge
            text = call(prompt + "\n\nYour previous reply was not valid. Return ONLY the JSON "
                        '{"figures":[...]} exactly as specified.')
            figures, dropped = figures_spec.parse_and_validate(text)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": providers.extract_provider_message(exc)}
    return {"ok": True, "figures": figures, "dropped": dropped, "raw": text}


@app.get("/api/schemas/{schema_id}")
def get_schema(schema_id: str, db=Depends(get_db)) -> dict:
    s = records.get_schema(db, schema_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Schema not found.")
    s["spec"] = records.schema_spec(db, schema_id)
    return s
