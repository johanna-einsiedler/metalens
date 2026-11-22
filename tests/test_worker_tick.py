"""The worker's cron tick and the sync ``enqueue`` on a running event loop.

On the live chain run (22 Nov 2026) the two-minute tick re-queued a dropped compute job; the
run's advance then queued the next papers through ``worker.enqueue``, whose ``asyncio.run``
refused the tick's running loop. The None that came back read as "no queue", so the advance
computed every pending paper itself — on the worker's event loop, in one transaction, under the
run's lock — and every poll of the run waited on that lock until it failed. No Redis or
Postgres needed here: the queue and the database calls are stubbed.
"""
from __future__ import annotations

import asyncio
import threading

from paperlens import localmode, records, workflows
from paperlens import worker as wk


def _fake_queue(monkeypatch) -> list[tuple]:
    sent: list[tuple] = []

    async def fake_enqueue(task, *args, **kwargs):
        sent.append((task, args))
        return f"job-{len(sent)}"

    monkeypatch.setattr(wk, "_enqueue", fake_enqueue)
    monkeypatch.setattr(localmode, "inline_jobs", lambda: False)
    return sent


def test_enqueue_reaches_the_queue_from_sync_code_on_a_running_loop(monkeypatch) -> None:
    sent = _fake_queue(monkeypatch)
    assert wk.enqueue("compute_job", "run", "step", "sha") == "job-1"           # plain sync code, no loop

    async def on_the_loop():
        return wk.enqueue("compute_job", "run", "step", "sha2")                  # sync code called from a coroutine
    assert asyncio.run(on_the_loop()) == "job-2"
    assert [s[1][2] for s in sent] == ["sha", "sha2"]


def test_the_tick_does_its_database_work_off_the_event_loop(monkeypatch) -> None:
    sent = _fake_queue(monkeypatch)
    seen: dict = {}

    class _Conn:
        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(records, "connect", lambda: _Conn())
    run, job = {"id": "r1"}, {"job_id": "old", "pdf_sha256": "a" * 64}
    monkeypatch.setattr(workflows, "reconcile_candidates", lambda conn: [(run, job)])
    monkeypatch.setattr(workflows, "step_job_candidates", lambda conn: [])

    async def gone(job_id):
        return {"job_id": job_id, "status": "JobStatus.not_found"}
    monkeypatch.setattr(wk, "_job_status", gone)

    def apply(conn, r, j, status):                    # what the requeue's advance does: queue the next paper
        seen["thread"] = threading.get_ident()
        seen["queued"] = wk.enqueue("compute_job", r["id"], "extraction", j["pdf_sha256"])
        return "requeued"
    monkeypatch.setattr(workflows, "apply_job_status", apply)

    async def tick():
        seen["loop_thread"] = threading.get_ident()
        return await wk.workflow_tick_task({})
    out = asyncio.run(tick())
    assert out["checked"] == 1 and out["requeued"] == 1
    assert seen["thread"] != seen["loop_thread"]                                 # not on the event loop
    assert seen["queued"] == "job-1" and len(sent) == 1                          # the queue took it; nothing ran inline
    assert seen["closed"] is True
