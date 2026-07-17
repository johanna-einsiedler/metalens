# PaperLens — record spine (Phase 1)

Clean rebuild of the PaperLens record layer per
[the build strategy](../../../../.claude/plans/1-look-at-the-wiggly-rabbit.md).
This is **Phase 1: the record spine** — the canonical extraction contract,
normalized into Postgres, with a faithful round-trip back to the publishable
format and a paper coverage/passport read path.

The archived reference pipeline lives in [`_archive/`](_archive/) and is treated
as a **frozen spec**, not code to evolve.

## What's here (the §5 vertical slice)

| Module | Role |
|---|---|
| `paperlens/contract.py` | The frozen canonical-record contract: JSON parse/repair + the publishable-key subset (`strip_to_publishable`). |
| `paperlens/ingest.py` | Decomposes one canonical document → `paper` + N `record`s + `evidence_span`s + `field_confidence`. Handles both evidence conventions (forestplot's flat top-level array, masem's per-entry nested) and routes evidence to entries by field-path. |
| `paperlens/reconstruct.py` | Rebuilds the publishable form from the decomposition. Proves no information loss. |
| `paperlens/records.py` | Postgres persistence (psycopg): `persist`/`load`, DOI-dedupe `upsert_paper`, schema upsert, and the `paper_coverage` passport query. |
| `paperlens/schema.sql` | Full §3 spine schema, **auth-ready** (nullable `owner_user_id`/`session_id` on every ownable row). |
| `paperlens/principal.py` | The identity seam (`Principal{session_id, user_id}`) — resolved per request: anonymous via `X-Session-Id`, authenticated via the session cookie. |
| `paperlens/auth.py` | Phase 2 accounts: bcrypt email+password, opaque cookie sessions, and claim-on-login (hand an anon session's records + datasets to the user). Holds no API keys. |
| `paperlens/enrich.py` | DOI enrichment (Crossref→Unpaywall→OpenAlex + JEL prediction + DOI-prefix link typing), each field tagged with `paper_field_provenance`. HTTP injected for offline tests. |
| `paperlens/presets.py` | Facade over `presets_loader`: `prompt_for()` (rendered extraction prompt) + `emit_schema_row()` (resolved `sub_views` → `schema.field_defs`; presets stay source of truth). |
| `paperlens/presets_loader.py` | **Vendored** from the archive — preset discovery + the full prompt-template rendering (`<id>.template.md` + `template_params`) for all presets. |
| `paperlens/worker.py` | Arq task queue: `extract_job` / `enrich_paper_task` / `ingest_task` (restart-safe), `RedisSettings`, and sync `enqueue`/`job_status` helpers for the API. |
| `paperlens/storage.py` | Object storage for PDFs / page images — `LocalObjectStore` (default) + `S3ObjectStore` (R2/S3, boto3 lazy-imported), chosen by env via `get_store()`. |
| `paperlens/extract.py` | PDF extraction orchestrator: render → (injectable) LLM → parse → evidence-rect highlight → ingest into records → store page images + attach rects. |
| `paperlens/pdf_utils.py` | **Vendored** from the archive (PyMuPDF render + the hard text→rect highlighting + fuzzy orphan recovery). |
| `paperlens/providers.py` | **Vendored** LLM abstraction (OpenAI/Gemini/Mistral/DeepSeek/vLLM) — browser-side keys, never persisted. |
| `paperlens/app.py` | FastAPI. **Pages:** `/` landing, `/catalog` (+ `/catalog/record/{id}`), `/workspace`, `/observatory`. **Catalog API:** `/api/search`, `/api/facets`, `/api/papers/search`, `/api/datasets/public`, `/api/records/{id}`, `POST /api/aggregate`. Plus extraction, papers (lookup/enrich/provenance), datasets, verification, views, accounts. |
| `paperlens/static/` | Themed vanilla front-end (no build): `theme.css` (token palette — the only place with raw color), `base.css` (chrome), `grammar.css`+`grammar.js` (the `rv-*`/`ev-*` record grammar + yellow highlight, edit-in-place), `api.js`, `store.js` (URL-as-state), `pdfview.js`, and the page scripts `landing.js` / `catalog.js` / `workspace.js` / `observatory.js`. |
| `paperlens/static/observatory.*` | The flagship public view (vanilla): a saved `view` rendered as a live SVG chart, recomputed from records on each load. |

The core invariant: **`reconstruct(ingest(x)) == strip_to_publishable(x)`** across
both evidence conventions — verified at the pure-Python level *and* through Postgres.

## Run it

```bash
uv sync --extra dev                 # install
createdb paperlens                  # local Postgres (PAPERLENS_DATABASE_URL to override)

python3 tests/test_roundtrip.py     # stdlib-only contract round-trip (no DB, no install)
uv run pytest -q                    # full suite (DB / Redis tests skip if unavailable)
uv run uvicorn paperlens.app:app --reload   # serve the API + viewer

# then open the read-only viewer in a browser:
#   http://127.0.0.1:8000/        (pick a document -> PDF pages with highlight
#                                  overlays, extracted records, click-to-source)

# background queue (restart-safe extraction/enrichment jobs):
redis-server --daemonize yes        # or: brew services start redis
uv run arq paperlens.worker.WorkerSettings   # run the worker in another shell
```

Env: `PAPERLENS_REDIS_URL` (default `redis://localhost:6379`), `PAPERLENS_CONTACT_EMAIL`
(polite-pool / Unpaywall identity), `PAPERLENS_STORAGE=local|s3` (+ `PAPERLENS_S3_*` for R2).
The enrich endpoint enqueues when Redis is up and falls back to running synchronously otherwise.

## Deliberately preserved / deferred

- **Browser-side API keys** stay client-side — there is no `api_key` column anywhere.
- **Anonymous flow** preserved via `X-Session-Id`; accounts (email+password, GitHub OAuth) land in **Phase 2** and only populate the already-present `owner_user_id`.
All of Phase 1 is implemented and verified offline: the record spine + round-trip,
universal `paper` + DOI enrichment + provenance, preset→schema emission, the Arq
queue, object storage, and the **PDF extraction core** (render → LLM → highlight →
records). The extraction LLM step is injectable, so the full chain is proven with a
generated PDF + a fake provider — real runs supply a browser-side key per request.

All extraction presets render end-to-end: `POST /api/extract -F schema_id=<preset>@<ver>`
pulls the rendered prompt (masem / econ-headline / ai-findings / masem-ncs18 / forestplot
/ summarize) — no hand-written prompt needed. Supply `model` + `api_key` (browser-side).

**Phase 2 (accounts + persistent owned datasets) is done:** owned, principal-scoped
`dataset`s (`dataset_id` no longer floats `NULL`); email+password accounts with cookie
sessions; the `owner_user_id` seam is populated, and an anon session's work is claimed
on login. The anonymous flow + browser-side keys are untouched.

**All four planned phases are done.** Phase 3 (credibility): record-level
`verification_event`s, `record.verification_status` projection, **computed** dataset badges
(AI-only → sample-verified `X% audited · Y% agree` w/ Wilson CI → human-verified), viewer
verify/flag controls. Phase 4 (views as data): a `saved_view` = `{dataset_ids, query,
viz_config}` that **recomputes from records on every read** (aggregate count/mean, dotted
field paths, verified-only filter), surfaced as the `/observatory` chart — the OWID-style
"query across datasets". `theme` tokens are stored for later org branding.

**Public catalog + themed UI are done.** A **catalog query layer** (cross-dataset full-text
search wiring the previously-dead FTS index, faceted filtering, fuzzy paper search, a
public-datasets-with-badges index in one query, record detail, and a generalized
`aggregate` that `run_view` now delegates to). A **cohesive themed front-end** on a single
swappable token layer (`theme.css`; default = teal-navy+mint, the proven metalens look):
`/` landing (sans/neutral), `/catalog` (tool-faithful browser with search + facet rail +
dataset badges + record detail), `/workspace` (PDF + yellow highlights + the ported
`rv-*`/`ev-*` grammar + verify/edit-in-place), `/observatory`. URL-as-state (no framework);
no raw color outside `theme.css` (grep-gated).

Known follow-ups (not blockers): the browser key still transits the Redis job payload for
`extract_job` — productionize with a short-TTL token or encrypted job args; session cookie
`secure=True` behind HTTPS; add GitHub OAuth alongside email+password. Deferred per the
plan: a heavier **report/dashboard builder** (interactive editing), the `metalens-datasets`
publication layer (ingest-on-PR-merge), versioned snapshots/Zenodo, org-themed embeds with
attribution backlinks, richer observatory chart types, a generic factor-loadings variant.
