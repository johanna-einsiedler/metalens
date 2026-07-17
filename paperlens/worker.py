"""Arq task queue — restart-safe background jobs (brief §5: daemon-thread jobs
-> a task queue, so a Fly restart doesn't kill in-flight work).

Run the worker:    uv run arq paperlens.worker.WorkerSettings
Enqueue from API:  worker.enqueue("enrich_paper_task", doi)   (sync helper)

Tasks open their own DB connection so they're independent of the web process.
``ingest_task`` chains enrichment by enqueuing ``enrich_paper_task`` for the DOI —
the "ingest -> records change -> enrich" pipeline, now durable.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from . import enrich, records


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(
        os.environ.get("PAPERLENS_REDIS_URL", "redis://localhost:6379"))


def _fast_settings() -> RedisSettings:
    """Fail-fast variant for the API-side helpers: when Redis is down we want a
    single quick failure (then the endpoint falls back to running synchronously),
    not the worker's resilient multi-retry connect."""
    return dataclasses.replace(redis_settings(), conn_retries=1, conn_timeout=2,
                               conn_retry_delay=0.2)


# ── task bodies (ctx-first, per arq) ──────────────────────────────────────────

async def enrich_paper_task(ctx: dict, doi: str, abstract: str | None = None) -> dict:
    conn = records.connect()
    try:
        return enrich.enrich_paper(conn, doi, abstract=abstract)
    finally:
        conn.close()


async def ingest_task(ctx: dict, result: Any, *, schema_id: str | None = None,
                      source_job_id: str | None = None,
                      session_id: str | None = None) -> dict:
    from .ingest import ingest
    conn = records.connect()
    try:
        res = ingest(result)
        doc_id = records.persist(conn, res, schema_id=schema_id,
                                 source_job_id=source_job_id, session_id=session_id)
        paper_id = conn.execute(
            "SELECT paper_id FROM extraction_document WHERE id = %s", (doc_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    # chain enrichment for the canonical paper (durable, best-effort)
    if res.doi and ctx.get("redis"):
        await ctx["redis"].enqueue_job("enrich_paper_task", res.doi)
    return {"document_id": doc_id, "paper_id": str(paper_id),
            "n_records": len(res.records), "doi": res.doi}


_EXTRACT_MAX_TRIES = 3   # retry extraction with arq's backoff (spreads out rate-limited calls)


async def extract_job(ctx: dict, pdf_b64: str, prompt: str, *, model: str = "",
                      api_key: str = "", base_url: str | None = None,
                      use_text: bool = False, schema_id: str | None = None,
                      session_id: str | None = None, owner_user_id: str | None = None,
                      filename: str | None = None, use_credits: bool = False,
                      credit_user_id: str | None = None) -> dict:
    """Full PDF extraction as a durable job (render -> LLM -> highlight -> records).

    For ``use_credits`` runs the api_key is EMPTY in the payload — resolved here from the
    worker's own env — so the server key never rests in Redis. A consumed credit is
    refunded only on the FINAL give-up (arq retries earlier attempts with backoff).

    NOTE: for own-key runs the browser api_key still transits the job payload; productionizing
    should tokenize/encrypt it (see plan §privacy).
    """
    import base64
    from . import extract
    pdf_bytes = base64.b64decode(pdf_b64)
    if use_credits and not api_key:
        from . import credits, providers
        provider = providers.get_provider(model, None)
        api_key = credits.server_key_for(provider) or ""
        if not api_key:
            # Misconfiguration (the worker's env has no server key for this model's
            # provider), not a transient error. Without this guard we'd send the
            # providers.py "dummy-key" placeholder and get a misleading 401 against
            # the wrong endpoint, three times over. Fail fast with a clear message;
            # refund the consumed credit on the final attempt (mirrors the except
            # block below so retries don't over-refund).
            if credit_user_id and ctx.get("job_try", 1) >= _EXTRACT_MAX_TRIES:
                rconn = records.connect()
                try:
                    credits.refund(rconn, credit_user_id, model=model)
                finally:
                    rconn.close()
            raise RuntimeError(
                f"Metalens credit run: no server API key configured for provider "
                f"{provider!r} in the worker env (credit refunded on final attempt).")
    conn = records.connect()
    try:
        return extract.run_extraction(
            conn, pdf_bytes, prompt, model=model, api_key=api_key, base_url=base_url,
            use_text=use_text, schema_id=schema_id, session_id=session_id,
            owner_user_id=owner_user_id, source_job_id=ctx.get("job_id"),
            filename=filename)
    except Exception:
        if use_credits and credit_user_id and ctx.get("job_try", 1) >= _EXTRACT_MAX_TRIES:
            from . import credits
            try:
                credits.refund(conn, credit_user_id, model=model)
            except Exception:
                pass
        raise
    finally:
        conn.close()


async def publish_dataset_task(ctx: dict, dataset_id: str) -> dict:
    """Publish a dataset to the metalens-datasets repo as a PR. Only the dataset_id
    transits Redis — the GitHub token lives in the worker env, never in the payload."""
    from . import github_publish
    conn = records.connect()
    try:
        return github_publish.publish_dataset(conn, dataset_id)
    finally:
        conn.close()


class WorkerSettings:
    functions = [enrich_paper_task, ingest_task, extract_job, publish_dataset_task]
    redis_settings = redis_settings()
    max_jobs = 10
    max_tries = _EXTRACT_MAX_TRIES   # retry with backoff → spreads rate-limited LLM calls
    job_timeout = 300  # extraction LLM calls can be slow


# ── enqueue / status helpers for the (sync) API ───────────────────────────────

async def _enqueue(task: str, *args, **kwargs) -> str | None:
    pool = await create_pool(_fast_settings())
    try:
        job = await pool.enqueue_job(task, *args, **kwargs)
        return job.job_id if job else None
    finally:
        await pool.aclose()


def enqueue(task: str, *args, **kwargs) -> str | None:
    """Enqueue a job from sync code. Returns job id, or None if Redis is down
    (callers fall back to running synchronously)."""
    try:
        return asyncio.run(_enqueue(task, *args, **kwargs))
    except Exception:
        return None


async def _job_status(job_id: str) -> dict:
    from arq.jobs import Job
    pool = await create_pool(_fast_settings())
    try:
        job = Job(job_id, pool)
        status = await job.status()
        info = await job.result_info()
        out: dict[str, Any] = {"job_id": job_id, "status": str(status)}
        if info is not None:
            out["success"] = info.success
            out["result"] = info.result if info.success else None
            out["error"] = None if info.success else str(info.result)
        return out
    finally:
        await pool.aclose()


def job_status(job_id: str) -> dict | None:
    try:
        return asyncio.run(_job_status(job_id))
    except Exception:
        return None


def redis_available() -> bool:
    async def go() -> bool:
        pool = await create_pool(_fast_settings())
        try:
            await pool.ping()
            return True
        finally:
            await pool.aclose()
    try:
        return asyncio.run(go())
    except Exception:
        return False
