# Metalens — production deployment & config

The stack: FastAPI app + Arq worker (Redis) + Postgres + object storage (S3 / Cloudflare R2). The app is stateless — scale it horizontally behind a load balancer; state lives in Postgres, Redis, and the bucket.

## Processes

```
redis-server                                   # queue
uv run arq paperlens.worker.WorkerSettings     # background extraction worker (N replicas)
uv run uvicorn paperlens.app:app               # web (N replicas, behind TLS)
```

## Environment

| Var | Purpose | Prod value |
|---|---|---|
| `DATABASE_URL` / DSN | Postgres | managed Postgres URL |
| `PAPERLENS_REDIS_URL` | Arq queue | `redis://…` |
| `PAPERLENS_STORAGE` | storage backend | **`s3`** |
| `PAPERLENS_S3_BUCKET` | bucket name | your **private** bucket |
| `PAPERLENS_S3_ENDPOINT` | S3/R2 endpoint | R2: `https://<acct>.r2.cloudflarestorage.com` |
| `PAPERLENS_S3_PUBLIC_URL` | **DO NOT SET** | *(unset → app returns short-TTL presigned URLs)* |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | storage creds | scoped to the bucket |
| `PAPERLENS_SECRET` | signs the local artifact tokens & should back any future signing | a long random secret |

## The private-storage invariants (must hold in prod)

1. **Private bucket, no public URL.** Never set `PAPERLENS_S3_PUBLIC_URL`. With it unset, `storage.S3ObjectStore.url()` returns a **10-minute presigned** GET URL, minted only inside `document_view` after the owner check. Set a bucket policy that **blocks all public access**.
2. **Encryption at rest.** `put_object` sends `ServerSideEncryption=AES256`; also enable default bucket encryption (SSE-S3 or SSE-KMS).
3. **Set `PAPERLENS_SECRET`.** Without it the local artifact-token signer falls back to a dev constant.
4. **HTTPS + secure cookies.** Terminate TLS; set the session cookie `secure=True` (see `app._set_session_cookie`, currently `secure=False` for local http).
5. **Backups/retention.** Deletion (`DELETE /api/documents/{id}`, `DELETE /api/auth/me`) removes DB rows **and** storage blobs. Configure bucket lifecycle so deletes propagate and versioned backups of private PDFs are **not** retained indefinitely; document your retention window.
6. **API keys are never persisted.** Users' provider API keys live only in their browser (`static/keys.js` → localStorage) and transit per-request to the chosen provider. No `api_key` column exists anywhere. (A future cross-device option would store them **encrypted** server-side — see PRIVACY.md.)

## Access model (enforced in code)

- A record is **public** iff it's in a `visibility='public'` dataset; otherwise **owner-only** (incl. `dataset_id IS NULL`).
- Documents / PDFs / page images are **always owner-only** (the bright wall) — publishing a dataset shares **records only**, never the source PDF.
- Catalogue (`/api/search`, `/api/facets`, `/api/aggregate`) returns **public records only**. Verified by `tests/test_authz.py`.
