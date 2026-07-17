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

from fastapi import (Cookie, Depends, FastAPI, File, Form, Header, HTTPException,
                     Query, Response, UploadFile)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, credits, enrich, extract, figures_spec, presets, presets_loader, providers, records, storage, worker
from .ingest import ingest
from .principal import Principal

_SESSION_COOKIE = "pl_session"

app = FastAPI(title="PaperLens record spine", version="0.1.0")

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
            return Response(status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Metalens beta"'})
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


@app.get("/")
def landing() -> FileResponse:
    """Public entry (L2 sans/neutral)."""
    return _page("landing.html")


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
    user_id = auth.resolve_session(db, pl_session)
    return Principal(session_id=x_session_id, user_id=user_id)


# ── owner-gated artifact serving (replaces the old public StaticFiles mount) ──
_ARTIFACT_SECRET = os.environ.get("PAPERLENS_SECRET", "dev-artifact-secret").encode()


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


@app.post("/api/ingest")
def ingest_endpoint(body: IngestBody, db=Depends(get_db),
                    who: Principal = Depends(principal)) -> dict:
    try:
        res = ingest(body.result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    doc_id = records.persist(
        db, res, schema_id=body.schema_id, source_job_id=body.source_job_id,
        session_id=who.session_id,
    )
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
def locate_value(document_id: str, value: str, page: int, db=Depends(get_db),
                 who: Principal = Depends(principal)) -> dict:
    """Owner-only: find the exact numeric ``value`` on ``page`` of the source PDF so
    the workspace can pinpoint-highlight it. `found=false` = not there verbatim (may
    be transformed/rounded) — a soft signal, not an error."""
    if not records.is_document_owner(db, document_id, who):
        raise HTTPException(status_code=404, detail="Document not found.")
    from . import pdf_utils
    store = storage.get_store()
    key = storage.pdf_key(document_id)
    if not store.exists(key):
        return {"rects": [], "found": False, "no_pdf": True}
    found_page, rects = pdf_utils.locate_value_rects_any(store.get(key), value, prefer_page=page)
    return {"rects": rects, "found": bool(rects), "page": found_page}


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
    return records.create_dataset(
        db, title=body.title, description=body.description, schema_id=body.schema_id,
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
        for doc in ov.get("documents", []):
            doc["filename"] = None
    return ov


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


@app.delete("/api/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, db=Depends(get_db),
                   who: Principal = Depends(principal)) -> dict:
    """Owner-only: delete the dataset (its records revert to private, not deleted)."""
    if not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="Not authorized.")
    return records.delete_dataset(db, dataset_id)


class DatasetPatch(BaseModel):
    visibility: str                        # public | private


@app.patch("/api/datasets/{dataset_id}")
def patch_dataset(dataset_id: str, body: DatasetPatch, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    """Owner-only visibility change. Publishing (→ public) requires owning EVERY
    record in the dataset — never publish another user's work (the bright wall)."""
    if not records.is_dataset_owner(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="Not authorized.")
    if body.visibility not in ("public", "private"):
        raise HTTPException(status_code=422, detail="visibility must be public|private")
    if body.visibility == "public" and not records.dataset_records_all_owned(db, dataset_id, who):
        raise HTTPException(status_code=403, detail="You can only publish records you own.")
    res = records.set_dataset_visibility(db, dataset_id, body.visibility)
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
    uid = auth.authenticate(db, body.email, body.password)
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    _set_session_cookie(response, auth.create_session(db, uid))
    claimed = auth.claim_anonymous(db, session_id=who.session_id, user_id=uid)
    return {"user": auth.get_user(db, uid), "claimed": claimed}


@app.post("/api/auth/logout")
def logout(response: Response, pl_session: str | None = Cookie(default=None),
           db=Depends(get_db)) -> dict:
    auth.delete_session(db, pl_session)
    response.delete_cookie(_SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(who: Principal = Depends(principal), db=Depends(get_db)) -> dict:
    if not who.user_id:
        raise HTTPException(status_code=401, detail="Not logged in.")
    return auth.get_user(db, who.user_id)


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


@app.post("/api/extract")
def extract_endpoint(
    pdf: UploadFile = File(...),
    prompt: str = Form(""),
    preset_id: str | None = Form(None),
    model: str = Form(""),
    api_key: str = Form(""),
    base_url: str | None = Form(None),
    use_text: bool = Form(False),
    schema_id: str | None = Form(None),
    use_credits: bool = Form(False),
    db=Depends(get_db),
    who: Principal = Depends(principal),
) -> dict:
    """Upload a PDF -> extract into records. Browser supplies the model + api_key
    per request (never persisted). The prompt comes from `prompt` if given, else
    from the preset (`preset_id`, or the preset implied by `schema_id`). Enqueues
    when Redis is up (restart-safe), else runs synchronously."""
    data = pdf.file.read()
    fname = pdf.filename or None

    if not prompt.strip():
        pid = preset_id or (schema_id.split("@")[0] if schema_id else None)
        prompt = presets.prompt_for(pid, conn=db) or ""
        if not prompt:
            raise HTTPException(status_code=422, detail=(
                f"No `prompt` given and preset {pid!r} has no inline prompt. Pass a "
                "`prompt` with full canonical-JSON instructions (it must ask the model "
                "for an `evidence` array), or use a preset that ships one (e.g. "
                "forestplot). Template-based presets (masem / econ-headline / "
                "ai-findings) need their prompt templates ported first."))

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
            use_credits=True, credit_user_id=who.user_id, _expires=1800)
        if job_id:
            return {"queued": True, "job_id": job_id}
        # Redis down → run synchronously (no Redis payload to protect anyway)
        try:
            result = extract.run_extraction(
                db, data, prompt, model=cmodel, api_key=server_key, base_url=None,
                use_text=use_text, schema_id=schema_id, session_id=who.session_id,
                owner_user_id=who.user_id, filename=fname)
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
        filename=fname,
        # discard the job if no worker consumes it within 30 min (avoid running a
        # long-stale extraction the user has already abandoned — see delayed=… logs)
        _expires=1800)
    if job_id:
        return {"queued": True, "job_id": job_id}

    # Synchronous fallback (Redis down): translate failures into clean responses.
    try:
        result = extract.run_extraction(
            db, data, prompt, model=model, api_key=api_key, base_url=base_url,
            use_text=use_text, schema_id=schema_id, session_id=who.session_id,
            owner_user_id=who.user_id, filename=fname)
    except ValueError as exc:                 # bad/empty PDF, unparseable model output
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:                   # provider errors (bad key/model/quota)
        raise HTTPException(status_code=502,
                            detail=f"Extraction failed: {providers.extract_provider_message(exc)}")
    return {"queued": False, **result}


@app.get("/api/papers/provenance")
def papers_provenance(doi: str, db=Depends(get_db)) -> dict:
    """The §3.5 provenance footer: enriched paper + per-field source/method."""
    p = records.paper_with_provenance(db, doi)
    if p is None:
        raise HTTPException(status_code=404, detail="Paper not in catalog.")
    return p


@app.get("/api/presets")
def list_presets(db=Depends(get_db), who: Principal = Depends(principal)) -> dict:
    """The resolved view-grammar for every preset in the picker: global file presets
    (minus ``landing_hidden`` ones, e.g. the MASEMiner factor-loadings variant) PLUS
    the principal's own DB-backed personal presets AND everyone's PUBLIC ones. Each row
    is tagged ``personal``/``owned`` so the UI can label & manage them."""
    allp = presets.load_all()
    rows = []
    for pid in sorted(allp):
        if (allp[pid] or {}).get("landing_hidden"):
            continue
        row = presets.emit_schema_row(pid)
        if row:
            row.update(personal=False, owned=False)
            rows.append(row)
    for p in records.list_personal_presets(db, owner_user_id=who.user_id, session_id=who.session_id):
        row = presets.emit_schema_row(p["id"], conn=db)
        if row:
            row.update(personal=True, visibility=p["visibility"],
                       owned=records._owns(who, p["owner_user_id"], p["session_id"]))
            rows.append(row)
    return {"presets": rows}


class PresetBody(BaseModel):
    title: str
    prompt: str
    tagline: str | None = None
    description: str | None = None
    mode: str = "extraction"
    sub_views: list | None = None
    template_params: dict | None = None
    accent_color: str | None = None
    visibility: str = "private"


@app.post("/api/presets")
def create_preset(body: PresetBody, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    """Create a personal preset owned by the principal (user, or anon session claimable
    on login). Usable immediately in the extract picker."""
    if not body.title.strip() or not body.prompt.strip():
        raise HTTPException(status_code=422, detail="title and prompt are required.")
    if body.visibility not in ("public", "private"):
        raise HTTPException(status_code=422, detail="visibility must be public|private")
    return records.create_personal_preset(
        db, title=body.title, prompt=body.prompt, tagline=body.tagline,
        description=body.description, mode=body.mode, sub_views=body.sub_views,
        template_params=body.template_params, accent_color=body.accent_color,
        owner_user_id=who.user_id, session_id=who.session_id, visibility=body.visibility)


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


@app.patch("/api/presets/{preset_id}")
def update_preset(preset_id: str, body: PresetPatch, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    if not records.is_preset_owner(db, preset_id, who):
        raise HTTPException(status_code=403, detail="Not authorized.")
    if body.visibility is not None and body.visibility not in ("public", "private"):
        raise HTTPException(status_code=422, detail="visibility must be public|private")
    fields = {k: v for k, v in {
        "title": body.title, "prompt": body.prompt, "tagline": body.tagline,
        "description": body.description, "mode": body.mode, "sub_views": body.sub_views,
        "template_params": body.template_params, "accent_color": body.accent_color,
        "visibility": body.visibility}.items() if v is not None}
    return records.update_personal_preset(db, preset_id, **fields)


@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: str, db=Depends(get_db),
                  who: Principal = Depends(principal)) -> dict:
    if not records.is_preset_owner(db, preset_id, who):
        raise HTTPException(status_code=403, detail="Not authorized.")
    return records.delete_personal_preset(db, preset_id)


@app.get("/api/presets/{preset_id}/prompt")
def preset_prompt(preset_id: str, db=Depends(get_db)) -> dict:
    """The fully-rendered extraction prompt for a preset (for the Review-prompt step)."""
    p = presets.prompt_for(preset_id, conn=db)
    if p is None:
        raise HTTPException(status_code=404, detail="Preset not found.")
    return {"preset_id": preset_id, "prompt": p}


@app.get("/api/presets/{preset_id}/detail")
def preset_detail(preset_id: str, db=Depends(get_db)) -> dict:
    """Full preset meta incl. ``template_params`` (MASEMiner builder seeds its form
    from these) or the raw fields of a personal preset (the preset editor loads these)."""
    meta = presets.get(preset_id, conn=db)
    if meta is None:
        raise HTTPException(status_code=404, detail="Preset not found.")
    return meta


class BuildPresetBody(BaseModel):
    preset_id: str
    template_params: dict = {}


@app.post("/api/build-preset-prompt")
def build_preset_prompt(body: BuildPresetBody) -> dict:
    """Re-render a preset's template with user-supplied ``template_params`` (the
    MASEMiner builder posts here on every form change for the live preview). Form
    values merge over the preset defaults, so only changed fields need be sent."""
    meta = presets_loader.get(body.preset_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Preset not found.")
    template = presets_loader.read_template_for(body.preset_id)
    if template is None:
        raise HTTPException(status_code=400, detail="This preset does not use a parameterised template.")
    params = dict(meta.get("template_params") or {})
    params.update(body.template_params or {})
    prompt = presets_loader.render_template(template, params)
    sub_views = (presets.emit_schema_row(body.preset_id) or {}).get("sub_views", [])
    return {"prompt": prompt, "sub_views": sub_views}


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


_DESIGN_META = """You are designing a data-extraction prompt for another AI that will read an \
academic paper (PDF). Write a single, clear, self-contained prompt that instructs that AI to \
perform the task below.

TASK:
{task}

The prompt you write MUST instruct the model to return ONLY one JSON object (no prose, no markdown \
fences) containing:
- a top-level array named "records" — one element per extracted/labelled item, with sensible field names;
- a top-level "evidence" array where each element has EXACTLY: "snippet" (verbatim text from the PDF), \
"page" (1-indexed PDF page number), "source" (e.g. "Table 2" or null), "field" (the JSON path it supports, \
e.g. "records[0]"). Snippets must be quoted character-for-character; "page" must never be omitted.

Output ONLY the prompt text — nothing else."""


class DesignPromptBody(BaseModel):
    task: str
    model: str
    api_key: str
    base_url: str | None = None


@app.post("/api/design-prompt")
def design_prompt(body: DesignPromptBody) -> dict:
    """AI-write an extraction prompt from a free-text task description (the
    "Create prompt" path). Uses the browser-supplied key once; never stored."""
    try:
        prompt = providers.generate_text(
            body.model, body.api_key, _DESIGN_META.format(task=body.task), base_url=body.base_url)
        return {"ok": True, "prompt": prompt}
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
    return s
