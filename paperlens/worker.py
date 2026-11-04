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
import functools
import json
import os
from typing import Any

from arq import Retry, create_pool, cron
from arq.connections import RedisSettings
from arq.worker import func

from . import enrich, records


def redis_settings() -> RedisSettings:
    base = RedisSettings.from_dsn(
        os.environ.get("PAPERLENS_REDIS_URL", "redis://localhost:6379"))
    # The worker holds this connection across long, synchronous extractions that block the
    # event loop; the default 1s connect timeout then makes a post-job reconnect fail on a
    # busy box (or a managed-Redis blip) and crashes the whole worker. Give it real slack.
    return dataclasses.replace(base, conn_timeout=15, conn_retries=10, conn_retry_delay=1)


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


_EXTRACT_MAX_TRIES = 3   # transient provider failures are re-queued with backoff (Retry); others end the job


def _progress_key(job_id: str) -> str:
    return f"arq:progress:{job_id}"


async def _set_progress(ctx: dict, stage: str) -> None:
    redis, job_id = ctx.get("redis"), ctx.get("job_id")
    if redis is None or not job_id:
        return
    try:
        await redis.set(_progress_key(job_id), json.dumps({"stage": stage}), ex=3600)
    except Exception:  # noqa: BLE001, S110 - progress is advisory
        pass


async def _extract_body(ctx: dict, pdf_b64: str, prompt: str, *, model: str = "",
                        api_key: str = "", base_url: str | None = None,
                        use_text: bool = False, schema_id: str | None = None,
                        session_id: str | None = None, owner_user_id: str | None = None,
                        filename: str | None = None, use_credits: bool = False,
                        credit_user_id: str | None = None,
                        dataset_id: str | None = None, params: dict | None = None,
                        prompt_edited: bool = False, credit_cost: int = 1,
                        run_id: str | None = None, step_id: str | None = None,
                        run_extraction=None) -> dict:
    """Full PDF extraction as a durable job (render -> model call -> highlight -> records).

    For ``use_credits`` runs the api_key is EMPTY in the payload — resolved here from the
    worker's own env — so the server key never rests in Redis. ``credit_cost`` credits were
    consumed at enqueue (one per model call); a transient provider failure re-queues the job
    (``Retry``, with backoff), any other failure refunds them all, and so does an abort.

    The model call is synchronous, so it runs in a worker thread: the event loop stays free
    for arq's heartbeat, the per-function timeout and ``Job.abort``.

    NOTE: for own-key runs the browser api_key still transits the job payload; productionizing
    should tokenize/encrypt it (see plan §privacy).
    """
    import base64
    from . import extract, providers
    run_extraction = run_extraction or extract.run_extraction
    pdf_bytes = base64.b64decode(pdf_b64)
    if use_credits and not api_key:
        from . import credits
        provider = providers.get_provider(model, None)
        api_key = credits.server_key_for(provider) or ""
        if not api_key:
            # Misconfiguration (the worker's env has no server key for this model's
            # provider), not a transient error: fail fast with a clear message and give the
            # credits back — a retry cannot fix an env.
            if credit_user_id:
                rconn = records.connect()
                try:
                    credits.refund(rconn, credit_user_id, model=model, n=credit_cost)
                finally:
                    rconn.close()
            raise RuntimeError(
                f"Metalens credit run: no server API key configured for provider "
                f"{provider!r} in the worker env (credits refunded).")
    conn = records.connect()
    loop = asyncio.get_running_loop()

    def _refund(n: int) -> None:
        if use_credits and credit_user_id and n > 0:
            from . import credits
            try:
                credits.refund(conn, credit_user_id, model=model, n=n)
            except Exception:  # noqa: BLE001
                pass

    try:
        await _set_progress(ctx, "extracting")
        result = await loop.run_in_executor(None, functools.partial(
            run_extraction, conn, pdf_bytes, prompt, model=model, api_key=api_key, base_url=base_url,
            use_text=use_text, schema_id=schema_id, session_id=session_id,
            owner_user_id=owner_user_id, source_job_id=ctx.get("job_id"),
            filename=filename, params=params, prompt_edited=prompt_edited,
            **({"run_id": run_id, "step_id": step_id} if run_id else {})))
    except asyncio.CancelledError:               # aborted from the UI: nothing was delivered
        _refund(credit_cost)
        raise
    except Exception as exc:
        if providers.is_transient(exc) and ctx.get("job_try", 1) < _EXTRACT_MAX_TRIES:
            raise Retry(defer=30 * int(ctx.get("job_try", 1)))
        _refund(credit_cost)
        if run_id:                               # the run learns that this paper is lost
            from . import workflows
            try:
                workflows.on_job_failed(conn, run_id, job_id=ctx.get("job_id"), error=providers.extract_provider_message(exc), final=True)
            except Exception:  # noqa: BLE001 - the run's cron reconciles what this misses
                pass
        raise
    else:
        # a run that made fewer calls than planned gives the unused credits back; a plain
        # extraction made exactly the one it was charged for
        made = result.get("n_calls") if isinstance(result, dict) else None
        if isinstance(made, int) and made < credit_cost:
            _refund(credit_cost - made)
        # Add-papers: attach the finished document to its target dataset here, so it lands
        # in the dataset whether the run was queued or synchronous (the browser can't do it
        # for queued jobs — it has no document_id until the worker finishes). Best-effort:
        # the extraction already succeeded, so never fail/retry the job over the link.
        if dataset_id and isinstance(result, dict) and result.get("document_id"):
            try:
                records.assign_document_to_dataset(conn, dataset_id, result["document_id"])
            except Exception:
                pass
        if run_id and isinstance(result, dict) and result.get("document_id"):
            # the workflow run takes it from here (attaches, advances); the cron reconciles a miss
            from . import workflows
            try:
                await loop.run_in_executor(None, functools.partial(
                    workflows.on_document, conn, run_id, step_id, _sha(pdf_bytes), result["document_id"], job_id=ctx.get("job_id")))
            except Exception as exc:  # noqa: BLE001
                print(f"[workflow] run {run_id}: could not record the document: {exc}", flush=True)
        return result
    finally:
        conn.close()


def _sha(pdf_bytes: bytes) -> str:
    import hashlib
    return hashlib.sha256(pdf_bytes).hexdigest()


async def extract_job(ctx: dict, pdf_b64: str, prompt: str, **kw) -> dict:
    """A single-call extraction (the 5-minute job)."""
    return await _extract_body(ctx, pdf_b64, prompt, **kw)


async def workflow_step_job(ctx: dict, run_id: str, step_id: str) -> dict:
    """A workflow's derive step (dataset-level model calls) as a durable job: the server key is
    resolved on the worker from the run's model, never carried in the payload. The step runs in
    a thread so the loop stays free; it advances the run when it is done."""
    from . import workflows
    loop = asyncio.get_running_loop()

    def body():
        conn = records.connect()
        try:
            run = workflows.run_step_job(conn, run_id, step_id)
            return {"run_id": run_id, "step_id": step_id, "status": (run or {}).get("status")}
        finally:
            conn.close()

    await _set_progress(ctx, f"{step_id}")
    return await loop.run_in_executor(None, body)


async def publish_dataset_task(ctx: dict, dataset_id: str) -> dict:
    """Publish a dataset to the metalens-datasets repo as a PR. Only the dataset_id
    transits Redis — the GitHub token lives in the worker env, never in the payload."""
    from . import github_publish
    conn = records.connect()
    try:
        return github_publish.publish_dataset(conn, dataset_id)
    finally:
        conn.close()


async def sweep_anonymous_task(ctx: dict) -> dict:
    """Every few minutes: delete what logged-out sessions uploaded once their window has
    been closed for the retention period (paperlens/retention.py)."""
    from . import retention, storage
    conn = records.connect()
    try:
        out = retention.sweep(conn, storage.get_store())
        if out.get("sessions"):
            print(f"[retention] swept {out}", flush=True)
        return out
    finally:
        conn.close()


async def workflow_tick_task(ctx: dict) -> dict:
    """Every few minutes: a workflow run that has not moved for a while asks the queue about
    the jobs it still waits for — landed, failed, or forgotten (results expire after an hour)."""
    from . import workflows
    conn = records.connect()
    out = {"checked": 0, "landed": 0, "failed": 0, "lost": 0, "recovered": 0, "waiting": 0}
    try:
        for run, job in workflows.reconcile_candidates(conn):
            out["checked"] += 1
            try:
                status = await _job_status(job["job_id"])
            except Exception:  # noqa: BLE001
                status = None
            try:
                what = workflows.apply_job_status(conn, run, job, status)
                out[what] = out.get(what, 0) + 1
            except Exception as exc:  # noqa: BLE001
                print(f"[workflow] reconcile {run['id']}/{job['pdf_sha256'][:8]}: {exc}", flush=True)
        for run, sr in workflows.step_job_candidates(conn):
            out["checked"] += 1
            try:
                status = await _job_status((sr.get("outputs") or {}).get("job_id"))
            except Exception:  # noqa: BLE001
                status = None
            try:
                what = workflows.apply_step_job_status(conn, run, sr, status)
                out[what] = out.get(what, 0) + 1
            except Exception as exc:  # noqa: BLE001
                print(f"[workflow] reconcile step {run['id']}/{sr['step_id']}: {exc}", flush=True)
        if out["checked"]:
            print(f"[workflow] reconciled {out}", flush=True)
        return out
    finally:
        conn.close()


async def github_sync_task(ctx: dict) -> dict:
    """Hourly: merged pull requests become published datasets; GitHub-only datasets are
    imported (paperlens/github_sync.py)."""
    from . import github_sync
    conn = records.connect()
    try:
        out = github_sync.sync(conn)
        if out.get("published") or out.get("imported") or out.get("reimported"):
            print(f"[github-sync] {out}", flush=True)
        return out
    except Exception as exc:  # noqa: BLE001 - GitHub down is not a worker failure
        print(f"[github-sync] skipped: {exc}", flush=True)
        return {"error": str(exc)[:200]}
    finally:
        conn.close()


class WorkerSettings:
    functions = [enrich_paper_task, ingest_task, extract_job, publish_dataset_task, func(workflow_step_job, timeout=1800)]
    cron_jobs = [cron(sweep_anonymous_task, minute=set(range(0, 60, 5)), run_at_startup=True),
                 cron(workflow_tick_task, minute=set(range(1, 60, 2)), run_at_startup=False),
                 cron(github_sync_task, minute={17}, run_at_startup=True)]
    redis_settings = redis_settings()
    allow_abort_jobs = True   # let the UI stop an in-progress extraction (Job.abort)
    max_jobs = 10
    max_tries = _EXTRACT_MAX_TRIES   # retry with backoff → spreads rate-limited LLM calls
    job_timeout = 300  # extraction LLM calls can be slow
    # The worker polls Redis for new jobs every poll_delay seconds — CONTINUOUSLY, whether
    # or not anything is running. arq's default (0.5s) means ~hundreds of thousands of
    # commands/day just idling, which dominates managed-Redis (Upstash) usage. 3s cuts that
    # ~6× and only delays a job's pickup by up to 3s (negligible vs 20-40s extractions).
    poll_delay = 3.0


# ── enqueue / status helpers for the (sync) API ───────────────────────────────

async def _enqueue(task: str, *args, **kwargs) -> str | None:
    pool = await create_pool(_fast_settings())
    try:
        job = await pool.enqueue_job(task, *args, **kwargs)
        return job.job_id if job else None
    finally:
        await pool.aclose()


def enqueue(task: str, *args, **kwargs) -> str | None:
    """Enqueue a job from sync code. Returns job id, or None if Redis is down or the
    process runs jobs inline (PAPERLENS_INLINE_JOBS — the single-user local mode);
    callers fall back to running synchronously."""
    from . import localmode
    if localmode.inline_jobs():
        return None
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
        try:                                      # the stage a running job reported (advisory)
            raw = await pool.get(_progress_key(job_id))
            if raw:
                out["progress"] = json.loads(raw)
        except Exception:  # noqa: BLE001, S110 - the stage is advisory
            pass
        if info is not None:
            out["success"] = info.success
            out["result"] = info.result if info.success else None
            out["error"] = None
            if not info.success:
                # Surface a clean, human-readable failure (dig the provider message out of a
                # noisy "Error code: 401 - {...}" blob; plain message for other errors).
                from . import providers
                out["error"] = providers.extract_provider_message(info.result)
        return out
    finally:
        await pool.aclose()


def job_status(job_id: str) -> dict | None:
    try:
        return asyncio.run(_job_status(job_id))
    except Exception:
        return None


async def _job_args(job_id: str) -> dict | None:
    from arq.jobs import Job
    pool = await create_pool(_fast_settings())
    try:
        # A finished job's definition (arq:job:*) is gone, but its RESULT (JobResult inherits
        # JobDef) still carries the original function/args/kwargs — so a failed extraction can
        # be re-enqueued from those (its PDF bytes live in args) with no re-upload.
        info = await Job(job_id, pool).result_info()
        if info is None:
            return None
        return {"function": info.function, "args": list(info.args or []),
                "kwargs": dict(info.kwargs or {})}
    finally:
        await pool.aclose()


def job_args(job_id: str) -> dict | None:
    """The original function/args/kwargs of a (finished) job — for retry. None if the job's
    result has expired from Redis."""
    try:
        return asyncio.run(_job_args(job_id))
    except Exception:
        return None


async def _cancel_job(job_id: str) -> bool:
    from arq.jobs import Job
    pool = await create_pool(_fast_settings())
    try:
        # abort() drops a still-queued job from the queue, and (with allow_abort_jobs)
        # signals the worker to cancel one already running. Returns True once aborted.
        return bool(await Job(job_id, pool).abort(timeout=3))
    finally:
        await pool.aclose()


def cancel_job(job_id: str) -> bool:
    """Stop a queued or in-progress extraction. True if it was aborted."""
    try:
        return asyncio.run(_cancel_job(job_id))
    except Exception:
        return False


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
