"""The extraction job's body, without Redis: a transient provider failure re-queues the job
with backoff and refunds nothing yet; a lasting failure, or an abort, refunds the credits the run
was charged; a run that made fewer calls than planned refunds the difference; the model call
runs in a worker thread so the event loop stays free; the stage a job reports is advisory."""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import threading

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from arq import Retry  # noqa: E402

from paperlens import providers, worker as wk  # noqa: E402


class _Conn:
    def close(self) -> None:
        pass


class _RateLimited(Exception):
    status_code = 429


class _Fake:
    """A fake Redis holding the progress key."""
    def __init__(self) -> None:
        self.kv: dict = {}

    async def set(self, k, v, ex=None):
        self.kv[k] = v


def _body(monkeypatch, run, *, refunds: list, job_try=1, use_credits=True, redis=None):
    monkeypatch.setattr(wk.records, "connect", lambda: _Conn())
    from paperlens import credits
    monkeypatch.setattr(credits, "refund", lambda conn, uid, **kw: refunds.append((uid, kw.get("n", 1))))
    ctx = {"job_id": "job-1", "job_try": job_try, "redis": redis}
    return wk._extract_body(ctx, base64.b64encode(b"%PDF").decode(), "prompt", model="m", api_key="k",
                            use_credits=use_credits, credit_user_id="user-1", credit_cost=3, run_extraction=run)


def test_a_transient_failure_requeues_then_refunds_on_the_last_try(monkeypatch) -> None:
    refunds: list = []

    def run(*a, **k):
        raise _RateLimited("429 rate limit")
    assert providers.is_transient(_RateLimited("x")) and not providers.is_transient(ValueError("bad key"))
    with pytest.raises(Retry) as info:
        asyncio.run(_body(monkeypatch, run, refunds=refunds, job_try=1))
    assert info.value.defer_score == 30_000 and refunds == []          # arq keeps the credits: the job comes back
    with pytest.raises(Retry) as info:
        asyncio.run(_body(monkeypatch, run, refunds=refunds, job_try=2))
    assert info.value.defer_score == 60_000 and refunds == []
    with pytest.raises(_RateLimited):                                   # the last try gives up and gives back
        asyncio.run(_body(monkeypatch, run, refunds=refunds, job_try=3))
    assert refunds == [("user-1", 3)]


def test_a_lasting_failure_refunds_at_once(monkeypatch) -> None:
    refunds: list = []

    def run(*a, **k):
        raise ValueError("Error code: 401 - bad key")
    with pytest.raises(ValueError):
        asyncio.run(_body(monkeypatch, run, refunds=refunds, job_try=1))
    assert refunds == [("user-1", 3)]
    refunds.clear()                                                      # an own-key run has nothing to refund
    with pytest.raises(ValueError):
        asyncio.run(_body(monkeypatch, run, refunds=refunds, job_try=1, use_credits=False))
    assert refunds == []


def test_an_abort_refunds_and_the_call_runs_off_the_loop(monkeypatch) -> None:
    refunds: list = []
    seen: dict = {}

    def run(*a, **k):
        seen["thread"] = threading.current_thread().name
        return {"document_id": "d", "n_calls": 1}
    out = asyncio.run(_body(monkeypatch, run, refunds=refunds))
    assert out["document_id"] == "d" and seen["thread"] != threading.main_thread().name
    assert refunds == [("user-1", 2)]                                   # planned 3 calls, made 1

    async def aborted():
        task = asyncio.ensure_future(_body(monkeypatch, lambda *a, **k: (asyncio.sleep(0), {"document_id": "d"})[1], refunds=refunds))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    refunds.clear()
    asyncio.run(aborted())
    assert refunds == [("user-1", 3)]


def test_the_stage_a_job_reports_is_kept_for_the_status(monkeypatch) -> None:
    redis = _Fake()
    asyncio.run(_body(monkeypatch, lambda *a, **k: {"document_id": "d", "n_calls": 3}, refunds=[], redis=redis))
    assert redis.kv == {"arq:progress:job-1": '{"stage": "extracting"}'}
