"""Review gates: what a workflow step asks a person to look at before the run goes on or the
dataset is released. A gate is opened on a step-run when the step completes; its ITEMS are the
records a person must give a verdict (every record of the step's output for ``mode: all``; a
random draw for ``mode: sample``, which a later release adds); it is SATISFIED when every item
is verified or flagged. Verdicts are the ordinary ones (``record.verification_status``): the
gate reads them, it never writes them.
"""
from __future__ import annotations

from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Json

from . import workflow_spec as ws

VERDICTS = ("verified", "flagged")
_ACTIVE = ("running", "awaiting_review", "needs_key", "done")   # runs whose gates count (prefix match on status)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def open_gate(conn: psycopg.Connection, step_run: dict, step: dict, *, record_ids: list[str] | None = None) -> dict:
    """Open the gate of a completed step-run as its declaration says. Writes the header into
    ``workflow_step_run.review`` and returns it. ``mode: none`` writes an empty, satisfied gate;
    ``record_ids`` narrows the gate to named records (what a check put in front of a reviewer)."""
    rv = ws.review_of(step)
    docs = list((step_run.get("outputs") or {}).get("document_ids") or [])
    header = {"mode": rv.get("mode", "none"), "unit": rv.get("unit", "entry"), "blocks": rv.get("blocks", "release"),
              "opened_at": _now(), "document_ids": docs, "satisfied_at": None, "overridden_by": None,
              **({"record_ids": list(record_ids)} if record_ids is not None else {})}
    if header["mode"] == "none":
        header["satisfied_at"] = header["opened_at"]
    elif header["mode"] == "sample":
        # the sampler arrives with the review-modes release; until then a declared sample is
        # refused at plan time, so this branch is a safety net, reviewed whole
        header["mode"] = "all"
        header["note"] = "sampled review is not available yet: reviewed whole"
    conn.execute("UPDATE workflow_step_run SET review = %s WHERE id = %s::uuid", (Json(header), step_run["id"]))
    return header


def items(conn: psycopg.Connection, step_run: dict) -> list[dict]:
    """The records a gate asks about, with their current verdict."""
    header = step_run.get("review") or {}
    if header.get("mode", "none") == "none":
        return []
    rec_ids = header.get("record_ids")
    docs = list(header.get("document_ids") or (step_run.get("outputs") or {}).get("document_ids") or [])
    if rec_ids is not None:
        rows = conn.execute(
            """SELECT r.id::text, r.document_id::text, r.paper_id::text, r.entry_index, r.verification_status
               FROM record r WHERE r.id = ANY(%s::uuid[]) ORDER BY r.document_id, r.entry_index""", (list(rec_ids),)).fetchall()
        kind = "supplement"
    elif docs:
        rows = conn.execute(
            """SELECT r.id::text, r.document_id::text, r.paper_id::text, r.entry_index, r.verification_status
               FROM record r WHERE r.document_id = ANY(%s::uuid[]) AND NOT COALESCE(r.screened_empty, false)
               ORDER BY r.document_id, r.entry_index""", (docs,)).fetchall()
        kind = "all"
    else:
        return []
    return [{"record_id": rid, "document_id": did, "paper_id": pid, "entry_index": ei, "status": st,
             "checked": st in VERDICTS, "kind": kind} for rid, did, pid, ei, st in rows]


def state(conn: psycopg.Connection, step_run: dict) -> dict:
    """``{mode, blocks, total, checked, pending, satisfied, satisfied_at, overridden_by}``."""
    header = step_run.get("review") or {}
    mode = header.get("mode", "none")
    if mode == "none" or not header:
        return {"mode": "none", "blocks": header.get("blocks", "release"), "total": 0, "checked": 0, "pending": 0,
                "satisfied": True, "satisfied_at": header.get("satisfied_at"), "overridden_by": None}
    its = items(conn, step_run)
    checked = sum(1 for i in its if i["checked"])
    pending = len(its) - checked
    satisfied = pending == 0 or bool(header.get("overridden_by"))
    if pending == 0 and not header.get("satisfied_at"):
        header["satisfied_at"] = _now()
        conn.execute("UPDATE workflow_step_run SET review = %s WHERE id = %s::uuid", (Json(header), step_run["id"]))
        step_run["review"] = header
    return {"mode": mode, "blocks": header.get("blocks", "release"), "total": len(its), "checked": checked, "pending": pending,
            "satisfied": satisfied, "satisfied_at": header.get("satisfied_at"), "overridden_by": header.get("overridden_by"),
            "documents": sorted({i["document_id"] for i in its if not i["checked"]})}


def override(conn: psycopg.Connection, step_run: dict, who: str | None) -> dict:
    header = dict(step_run.get("review") or {})
    header["overridden_by"] = who or "owner"
    header["overridden_at"] = _now()
    conn.execute("UPDATE workflow_step_run SET review = %s WHERE id = %s::uuid", (Json(header), step_run["id"]))
    step_run["review"] = header
    return header


def open_gates(conn: psycopg.Connection, dataset_id: str) -> list[dict]:
    """Every gate of the dataset's runs that still asks for verdicts: step-runs of runs not
    cancelled or failed, not superseded, whose review is not ``none``."""
    from . import workflows
    rows = conn.execute(
        """SELECT sr.id::text FROM workflow_step_run sr JOIN workflow_run r ON r.id = sr.run_id
           WHERE r.dataset_id = %s::uuid AND r.status NOT IN ('cancelled', 'failed', 'planned')
             AND sr.superseded_by IS NULL AND sr.review ->> 'mode' IS NOT NULL AND sr.review ->> 'mode' <> 'none'
           ORDER BY r.created_at DESC, sr.ordinal""", (dataset_id,)).fetchall()
    out = []
    for (srid,) in rows:
        sr = workflows.get_step_run(conn, srid)
        if sr is None:
            continue
        st = state(conn, sr)
        out.append({"run_id": sr["run_id"], "step_run_id": sr["id"], "step_id": sr["step_id"], "kind": sr["kind"], **st})
    return out


def blocking_release(gates: list[dict]) -> list[dict]:
    return [g for g in gates if g.get("blocks") == "release" and not g.get("satisfied")]
