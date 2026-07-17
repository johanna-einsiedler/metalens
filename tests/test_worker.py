"""Arq queue integration — enqueue -> burst worker -> result.

Uses the MASEM fixture (no DOI) so the chained enrichment never fires and the
test is fully deterministic + offline. Skips cleanly without Redis/Postgres.
"""
from __future__ import annotations

import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402

try:
    from arq import create_pool
    from arq.worker import Worker
    from paperlens import records, worker as wk
    _IMPORTS_OK = True
except Exception:  # pragma: no cover
    _IMPORTS_OK = False


def _ready() -> bool:
    if not _IMPORTS_OK or not wk.redis_available():
        return False
    try:
        c = records.connect(); c.close()
        return True
    except Exception:
        return False


def _burst_worker() -> "Worker":
    return Worker(functions=[wk.ingest_task, wk.enrich_paper_task],
                  redis_settings=wk.redis_settings(), burst=True,
                  handle_signals=False, poll_delay=0.0)


async def _drain() -> None:
    await _burst_worker().run_check()


def test_arq_enqueue_burst_result() -> None:
    if not _ready():
        import pytest
        pytest.skip("no Redis/Postgres available")

    async def _go():
        pool = await create_pool(wk.redis_settings())
        await pool.flushall()  # isolate from stale jobs (dev redis is ephemeral)
        job = await pool.enqueue_job("ingest_task", fixtures.MASEM_JSON)
        await _burst_worker().run_check()         # process the queue, raise on failure
        res = await job.result(timeout=5)
        await pool.aclose()
        return res

    res = asyncio.run(_go())
    assert res["n_records"] == 1 and res["doi"] is None
    assert res["document_id"] and res["paper_id"]


def test_sync_enqueue_and_status_helpers() -> None:
    if not _ready():
        import pytest
        pytest.skip("no Redis/Postgres available")

    asyncio.run(_flush())
    job_id = wk.enqueue("ingest_task", fixtures.MASEM_JSON)
    assert job_id, "enqueue returned a job id"
    asyncio.run(_drain())
    st = wk.job_status(job_id)
    assert st is not None and st["success"] is True
    assert st["result"]["n_records"] == 1


async def _flush() -> None:
    pool = await create_pool(wk.redis_settings())
    try:
        await pool.flushall()
    finally:
        await pool.aclose()


def _main() -> int:
    if not _IMPORTS_OK:
        print("  SKIP  worker tests: arq/psycopg import failed")
        return 0
    failures = 0
    for label, fn in [
        ("worker:enqueue-burst-result", test_arq_enqueue_burst_result),
        ("worker:sync-helpers", test_sync_enqueue_and_status_helpers),
    ]:
        try:
            fn()
            print(f"  PASS  {label}")
        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ == "Skipped":
                print(f"  SKIP  {label}: {exc}")
                continue
            failures += 1
            print(f"  FAIL  {label}: {exc!r}")
    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
