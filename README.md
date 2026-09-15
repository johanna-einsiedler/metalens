# Metalens · MASEMiner

One engine, two products, for turning research papers into structured, verifiable data.

- **Metalens** ([beta.metalens.tech](https://beta.metalens.tech)) — the general platform:
  extract, label or summarise papers with any preset, review every value against the
  highlighted passage it came from, build datasets, publish them.
- **MASEMiner** — the focused product for meta-analytic structural equation modelling:
  correlations, correlation matrices, factor loadings and study metadata from primary
  studies, with the same review workflow. Hosted on its own hostname, and installable as a
  one-command local app (`maseminer`) that keeps PDFs and API keys on your machine.

Both are this repository. A *brand* is a landing page, page chrome and a preset filter,
chosen per request from the hostname (`paperlens/brands.py`); everything else — the
extraction pipeline, the review UI, the database schema, the presets — is shared, so a local
MASEMiner run and a hosted one of the same version are the same extraction.

## Run MASEMiner locally

Python 3.11 or newer. No database to install: PostgreSQL is embedded (`pgserver`).

```bash
pipx install maseminer        # or: uvx maseminer  ·  or, from a checkout: uv run maseminer
maseminer                     # data in ~/.maseminer, opens http://127.0.0.1:8765
maseminer --data-dir ./study  # keep everything next to a project
maseminer --brand metalens    # the general surface instead of MASEMiner
```

What runs locally: the web app, an embedded PostgreSQL in `<data>/pg`, PDFs and page
images in `<data>/artifacts`, extractions inline in the request. There are no accounts and
no credits; you bring an API key for OpenAI, Google, Anthropic, Mistral or DeepSeek, or point
a custom base URL at a local model (Ollama, vLLM, LM Studio). Nothing leaves the machine
except the model call. Back up by copying the data folder while the app is stopped. Presets
placed in `<data>/presets/` (a `<id>.json` plus `<id>.prompt.md`) are loaded next to the
built-in ones, so an extraction setup can be exchanged as files.

Docker alternative: `docker compose up` (see `docker-compose.yml`).

## Run the hosted stack (development)

```bash
uv sync --extra dev
createdb paperlens                     # local Postgres; PAPERLENS_DATABASE_URL to override
scripts/dev.sh                         # web (uvicorn --reload) + arq worker, loads .env
```

Then open http://127.0.0.1:8000. `PAPERLENS_BRAND=maseminer` pins the MASEMiner surface
locally; in deployment the Host header selects it (`PAPERLENS_BRAND_HOSTS` extends the
patterns). Credits (keyless extraction on the operator's key) appear only when
`PAPERLENS_CREDIT_MODEL` and a provider key are set. `.env.example` lists every variable.

Tests: `PYTHONPATH="$PWD" uv run pytest -q` (database-backed tests skip without Postgres).

## Cite and reproduce

Every export records what produced it: `metadata.engine` (version, commit),
`metadata.preset` (the preset's declarative spec and its content-addressed schema id such as
`masem-direct@eb5f0287`), and per paper the model, resolved model id, prompt hash,
parameters and extraction time. Cite the engine (see `CITATION.cff`) together with the
engine version, the schema id and the model id from your export. `docs/REPRODUCIBILITY.md`
explains how to re-run an extraction later, including on an open-weights model when a
hosted model has been retired.

## What's in the engine

| Module | Role |
|---|---|
| `paperlens/app.py` | FastAPI app: pages, API, auth, credits, brands, jobs. |
| `paperlens/brands.py` | Product surfaces (Metalens, MASEMiner) resolved from the hostname or `PAPERLENS_BRAND`. |
| `paperlens/local.py`, `paperlens/localmode.py` | The `maseminer` launcher and the single-user local mode switches. |
| `paperlens/preset_spec.py`, `paperlens/presets.py`, `paperlens/presets/` | The declarative preset format: prompt + typed parameters, fields, evidence and confidence policy, display; validation, content-addressed schema ids, generated prompt sections. |
| `paperlens/extract.py`, `paperlens/providers.py`, `paperlens/pdf_utils.py` | Render pages, call the model (vision or text), parse, locate every cited passage in the PDF, ingest. |
| `paperlens/contract.py`, `paperlens/ingest.py`, `paperlens/reconstruct.py`, `paperlens/records.py`, `paperlens/schema.sql` | The canonical record contract and its lossless round trip through Postgres. |
| `paperlens/exporter.py` | Dataset exports with preset and engine provenance. |
| `paperlens/retention.py` | Idle-timeout deletion for logged-out sessions; trial accounting. |
| `paperlens/worker.py` | arq queue for hosted runs (extraction, enrichment, publishing) and the retention cron; optional. |
| `paperlens/storage.py` | Object storage: local filesystem or S3/R2. |
| `paperlens/static/` | The no-build ES-module front end (review workspace, extract flow, import, datasets, chrome). |

Deployment of the hosted service is described in `docs/DEPLOYMENT.md`; the legal pages the
hosted service shows are `docs/TERMS.md`, `docs/PRIVACY.md`, `docs/DMCA.md`.

## License

MIT — see `LICENSE`.
