"""Review gates: what a workflow step asks a person to look at before the run goes on or the
dataset is released. A gate is opened on a step-run when the step completes; its ITEMS are the
records a person must give a verdict — every record of the step's output (``mode: all``), a
random draw of them (``mode: sample``: a size, a seed, strata), or the records a check put in
front of a reviewer — and it is SATISFIED when every item has a verdict. Verdicts are the
ordinary ones (``record.verification_status``, the verification events): the gate reads them,
it never writes them.

A draw is recorded in ``workflow_sample`` (never as a verification event: every consumer of the
event log would otherwise have to learn to skip a machine's act, and one that forgot would
inflate the badge). The outcome of a drawn record is read at verdict time from the events after
the draw: checked when the last verdict is verified or flagged, disagreed when a flag or a
value-changing correction followed the draw — the badge's own rules. Only the random items enter
the sample estimate; supplement items (a check's findings) never do.
"""
from __future__ import annotations

import hashlib
import math
import random
import uuid
from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Json

from . import workflow_spec as ws

VERDICTS = ("verified", "flagged")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ── the sampler (pure) ────────────────────────────────────────────────────────

def draw(frame: list[dict], *, unit: str = "entry", n: int | None = None, share: float | None = None,
         seed=None, stratify=(), supplement_ids=()) -> dict:
    """Draw the records a person reviews from ``frame`` (``[{record_id, document_id, paper_id,
    entry_index, strata: {field: value}}]``), a function of the seed and the frame only:
    the frame is sorted first, so the order it came in never changes the draw.

    ``unit: paper`` draws whole papers (every record of a drawn paper). The target is ``n`` or
    ``ceil(share · N)``, capped at N (then ``exhaustive``). Strata (the tuples of the
    ``stratify`` values) get proportional quotas, the remainder by largest fractional part, and
    at least one each when the target allows; one ``random.Random(seed)`` is consumed in key
    order. ``supplement_ids`` are appended as supplement items, never counted in the estimate."""
    frame = sorted((dict(x) for x in frame), key=lambda x: (str(x.get("paper_id") or ""), str(x.get("document_id") or ""),
                                                            int(x.get("entry_index") or 0), str(x.get("record_id"))))
    if unit == "paper":
        groups: dict[str, list[dict]] = {}
        for x in frame:
            groups.setdefault(str(x.get("paper_id") or x.get("document_id") or x["record_id"]), []).append(x)
        units = [{"key": k, "members": v, "strata": v[0].get("strata") or {}} for k, v in groups.items()]
    else:
        units = [{"key": x["record_id"], "members": [x], "strata": x.get("strata") or {}} for x in frame]
    N = len(units)
    target = min(N, int(n)) if n is not None else min(N, math.ceil(float(share or 0) * N))
    target = max(0, target)
    rng = random.Random(seed if seed is not None else 0)
    by_stratum: dict[tuple, list[dict]] = {}
    for u in units:
        key = tuple(("∅" if u["strata"].get(f) is None else str(u["strata"].get(f))) for f in (stratify or ()))
        by_stratum.setdefault(key, []).append(u)
    keys = sorted(by_stratum)
    quota: dict[tuple, int] = {}
    if N and target:
        raw = {k: target * len(by_stratum[k]) / N for k in keys}
        quota = {k: int(math.floor(raw[k])) for k in keys}
        rest = target - sum(quota.values())
        for k in sorted(keys, key=lambda k: (-(raw[k] - math.floor(raw[k])), k)):
            if rest <= 0:
                break
            if quota[k] < len(by_stratum[k]):
                quota[k] += 1; rest -= 1
        if target >= len(keys):                         # at least one per non-empty stratum
            for k in keys:
                if quota[k] == 0:
                    donor = max((kk for kk in keys if quota[kk] > 1), key=lambda kk: quota[kk], default=None)
                    if donor is None:
                        break
                    quota[donor] -= 1; quota[k] += 1
    drawn: list[dict] = []
    for k in keys:
        pool = by_stratum[k]
        take = min(quota.get(k, 0), len(pool))
        for u in (rng.sample(pool, take) if take else []):
            for m in u["members"]:
                drawn.append({"record_id": m["record_id"], "document_id": m.get("document_id"), "paper_id": m.get("paper_id"),
                              "entry_index": m.get("entry_index"), "stratum": {f: m.get("strata", {}).get(f) for f in (stratify or ())}, "kind": "random"})
    drawn.sort(key=lambda x: (str(x.get("paper_id") or ""), str(x.get("document_id") or ""), int(x.get("entry_index") or 0)))
    seen = {d["record_id"] for d in drawn}
    extra = [{"record_id": rid, "kind": "supplement"} for rid in supplement_ids if rid not in seen]
    items = [{**d, "ord": i} for i, d in enumerate(drawn)] + [{**e, "ord": len(drawn) + j} for j, e in enumerate(extra)]
    return {"unit": unit, "frame_n": N, "n_target": target, "seed": seed, "exhaustive": N <= target,
            "strata": {"|".join(k) or "all": {"N": len(by_stratum[k]), "n": quota.get(k, 0)} for k in keys},
            "items": [x for x in items if x["kind"] == "random"], "supplement": [x for x in items if x["kind"] == "supplement"]}


def _seed_for(run_id: str, step_id: str) -> int:
    return int(hashlib.sha256(f"{run_id}:{step_id}".encode()).hexdigest()[:8], 16)


def _rank(document_ids: list[str], rows: list[tuple]) -> dict:
    """Keys for a paper-major order: papers in the order their first document appears, then the
    document's position in the list (a joint stage lists its steps' documents step by step)."""
    doc_idx = {did: k for k, did in enumerate(document_ids)}
    paper_rank: dict = {}
    for did in document_ids:
        pid = next((r[2] for r in rows if r[1] == did), None)
        if pid is not None and pid not in paper_rank:
            paper_rank[pid] = len(paper_rank)
    return {"doc": doc_idx, "paper": paper_rank}


def _frame(conn, document_ids: list[str], stratify, *, unit: str) -> list[dict]:
    if not document_ids:
        return []
    rows = conn.execute(
        """SELECT r.id::text, r.document_id::text, r.paper_id::text, r.entry_index, r.field_values, ed.paper_metadata_raw,
                  p.year, p.journal
           FROM record r JOIN extraction_document ed ON ed.id = r.document_id LEFT JOIN paper p ON p.id = r.paper_id
           WHERE r.document_id = ANY(%s::uuid[]) AND NOT COALESCE(r.screened_empty, false)
           ORDER BY r.document_id, r.entry_index""", (document_ids,)).fetchall()
    out = []
    for rid, did, pid, ei, fv, pmr, year, journal in rows:
        fv = fv if isinstance(fv, dict) else {}
        pmr = pmr if isinstance(pmr, dict) else {}
        strata = {}
        for f in stratify or ():
            if unit == "paper":
                strata[f] = {"year": year, "journal": journal}.get(f, pmr.get(f))
            else:
                v = fv.get(f)
                strata[f] = v if not isinstance(v, (dict, list)) else None
        out.append({"record_id": rid, "document_id": did, "paper_id": pid, "entry_index": ei, "strata": strata})
    rk = _rank(document_ids, rows)
    out.sort(key=lambda x: (rk["paper"].get(x["paper_id"], 10**9), rk["doc"].get(x["document_id"], 10**9), x["entry_index"] or 0, x["record_id"]))
    return out


# ── opening a gate ────────────────────────────────────────────────────────────

def open_gate(conn: psycopg.Connection, step_run: dict, step: dict, *, record_ids: list[str] | None = None) -> dict:
    """Open the gate of a completed step-run as its declaration says. Writes the header into
    ``workflow_step_run.review`` (and the draw into ``workflow_sample``) and returns it. ``mode:
    none`` writes an empty, satisfied gate; ``record_ids`` narrows the gate to named records
    (what a check put in front of a reviewer)."""
    rv = ws.review_of(step)
    docs = list((step_run.get("outputs") or {}).get("document_ids") or [])
    header = {"mode": rv.get("mode", "none"), "unit": rv.get("unit", "entry"), "blocks": rv.get("blocks", "release"),
              "opened_at": _now(), "document_ids": docs, "satisfied_at": None, "overridden_by": None,
              **({"record_ids": list(record_ids)} if record_ids is not None else {})}
    if header["mode"] == "none":
        header["satisfied_at"] = header["opened_at"]
    elif header["mode"] == "sample":
        seed = rv.get("seed")
        if seed is None:
            seed = _seed_for(step_run["run_id"], step_run["step_id"])
        elif isinstance(seed, str):
            seed = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16)
        frame = _frame(conn, docs, rv.get("stratify") or (), unit=header["unit"])
        d = draw(frame, unit=header["unit"], n=rv.get("n"), share=rv.get("share"), seed=seed, stratify=rv.get("stratify") or ())
        pos = {f["record_id"]: k for k, f in enumerate(frame)}                   # paper-major, step by step within a paper
        d["items"] = [dict(it, ord=k) for k, it in enumerate(sorted(d["items"], key=lambda it: pos.get(it["record_id"], 10**9)))]
        with conn.transaction():
            conn.execute("DELETE FROM workflow_sample WHERE step_run_id = %s::uuid", (step_run["id"],))
            with conn.cursor() as cur:                      # one pipelined batch, not a round trip per item
                cur.executemany(
                    """INSERT INTO workflow_sample (id, run_id, step_run_id, step_id, record_id, document_id, kind, stratum, ord)
                       VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s::uuid, %s::uuid, 'random', %s, %s)""",
                    [(str(uuid.uuid4()), step_run["run_id"], step_run["id"], step_run["step_id"], it["record_id"], it.get("document_id"),
                      Json(it.get("stratum") or {}), it["ord"]) for it in d["items"]])
        header.update({"n": rv.get("n"), "share": rv.get("share"), "seed": seed, "stratify": list(rv.get("stratify") or []),
                       "frame_n": d["frame_n"], "n_target": d["n_target"], "drawn": len(d["items"]), "exhaustive": d["exhaustive"],
                       "strata": d["strata"], "drawn_at": header["opened_at"]})
        if not d["items"]:
            header["satisfied_at"] = header["opened_at"]
    conn.execute("UPDATE workflow_step_run SET review = %s WHERE id = %s::uuid", (Json(header), step_run["id"]))
    return header


# ── reading a gate ────────────────────────────────────────────────────────────

def _events(conn, record_ids: list[str]) -> dict[str, list[tuple]]:
    """Per record, its verification events in order: (status, diff, created_at, verifier_kind)."""
    if not record_ids:
        return {}
    rows = conn.execute(
        """SELECT record_id::text, status, diff, created_at, verifier_kind FROM verification_event
           WHERE record_id = ANY(%s::uuid[]) ORDER BY created_at, id""", (record_ids,)).fetchall()
    out: dict[str, list[tuple]] = {}
    for rid, st, diff, at, kind in rows:
        out.setdefault(rid, []).append((st, diff, at, kind))
    return out


def outcome(events: list[tuple], drawn_at: datetime | None) -> dict:
    """What a reviewer did with a drawn record, from its events after the draw: ``checked``
    when the last verdict is verified or flagged (an ``unverified`` withdraws it), ``disagreed``
    when a flag or a value-changing correction followed the draw. A reader's opinion
    (``verifier_kind`` community) counts for neither."""
    from .records import _diff_changes_value
    last, disagreed = None, False
    for st, diff, at, kind in events or []:
        if kind == "community":
            continue
        if drawn_at is not None and at is not None and at < drawn_at:
            continue
        if st in ("verified", "flagged", "unverified"):
            last = st
        if st == "flagged" or _diff_changes_value(diff):
            disagreed = True
    return {"status": last, "checked": last in VERDICTS, "disagreed": disagreed}


def _drawn_at(header: dict) -> datetime | None:
    raw = header.get("drawn_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def items(conn: psycopg.Connection, step_run: dict) -> list[dict]:
    """The records a gate asks about, in queue order, each with its verdict."""
    header = step_run.get("review") or {}
    mode = header.get("mode", "none")
    if mode == "none" or header.get("draft"):
        return []
    if mode == "sample":
        rows = conn.execute(
            """SELECT s.record_id::text, s.document_id::text, r.paper_id::text, r.entry_index, r.verification_status, s.kind, s.stratum, s.ord
               FROM workflow_sample s JOIN record r ON r.id = s.record_id
               WHERE s.step_run_id = %s::uuid ORDER BY s.ord""", (step_run["id"],)).fetchall()
        ev = _events(conn, [r[0] for r in rows])
        when = _drawn_at(header)
        out = []
        for rid, did, pid, ei, st, kind, stratum, ord_ in rows:
            o = outcome(ev.get(rid), when)
            out.append({"record_id": rid, "document_id": did, "paper_id": pid, "entry_index": ei, "status": st, "kind": kind,
                        "stratum": stratum or {}, "ord": ord_, "checked": o["checked"], "disagreed": o["disagreed"]})
        return out
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
        rk = _rank(docs, rows)
        rows.sort(key=lambda r: (rk["paper"].get(r[2], 10**9), rk["doc"].get(r[1], 10**9), r[3] or 0))
        kind = "all"
    else:
        return []
    ev = _events(conn, [r[0] for r in rows])
    return [{"record_id": rid, "document_id": did, "paper_id": pid, "entry_index": ei, "status": st, "kind": kind, "ord": k,
             "checked": st in VERDICTS, "disagreed": outcome(ev.get(rid), None)["disagreed"]}
            for k, (rid, did, pid, ei, st) in enumerate(rows)]


def state(conn: psycopg.Connection, step_run: dict) -> dict:
    """``{mode, blocks, total, checked, pending, disagreed, satisfied, satisfied_at, overridden_by, …}``."""
    header = step_run.get("review") or {}
    mode = header.get("mode", "none")
    if mode == "none" or not header:
        return {"mode": "none", "blocks": header.get("blocks", "release"), "total": 0, "checked": 0, "pending": 0, "disagreed": 0,
                "satisfied": True, "satisfied_at": header.get("satisfied_at"), "overridden_by": None}
    draft = header.get("draft")
    if draft:                                                   # a draft step: the review IS the commit
        from . import derived
        d = derived.get(conn, draft.get("id")) if draft.get("kind") == "derived" else None
        done = bool(d and d["status"] == "committed")
        if done and not header.get("satisfied_at"):
            header["satisfied_at"] = d.get("committed_at") or _now()
            conn.execute("UPDATE workflow_step_run SET review = %s WHERE id = %s::uuid", (Json(header), step_run["id"]))
            step_run["review"] = header
        return {"mode": mode, "blocks": header.get("blocks", "release"), "unit": "draft", "total": 1, "checked": int(done), "pending": int(not done),
                "disagreed": 0, "satisfied": done or bool(header.get("overridden_by")), "satisfied_at": header.get("satisfied_at"),
                "overridden_by": header.get("overridden_by"), "documents": [], "draft": {**draft, "status": d["status"] if d else "missing"}}
    its = items(conn, step_run)
    checked = sum(1 for i in its if i["checked"])
    pending = len(its) - checked
    satisfied = pending == 0 or bool(header.get("overridden_by"))
    if pending == 0 and not header.get("satisfied_at"):
        header["satisfied_at"] = _now()
        conn.execute("UPDATE workflow_step_run SET review = %s WHERE id = %s::uuid", (Json(header), step_run["id"]))
        step_run["review"] = header
    out = {"mode": mode, "blocks": header.get("blocks", "release"), "unit": header.get("unit", "entry"), "total": len(its), "checked": checked,
           "pending": pending, "disagreed": sum(1 for i in its if i["checked"] and i["disagreed"]), "satisfied": satisfied,
           "satisfied_at": header.get("satisfied_at"), "overridden_by": header.get("overridden_by"),
           "documents": sorted({i["document_id"] for i in its if not i["checked"] and i.get("document_id")})}
    if mode == "sample":
        out.update({k: header.get(k) for k in ("frame_n", "n_target", "seed", "stratify", "exhaustive", "strata", "drawn")})
    return out


def override(conn: psycopg.Connection, step_run: dict, who: str | None) -> dict:
    header = dict(step_run.get("review") or {})
    header["overridden_by"] = who or "owner"
    header["overridden_at"] = _now()
    conn.execute("UPDATE workflow_step_run SET review = %s WHERE id = %s::uuid", (Json(header), step_run["id"]))
    step_run["review"] = header
    return header


def open_gates(conn: psycopg.Connection, dataset_id: str) -> list[dict]:
    """Every gate of the dataset's runs that asks for verdicts: step-runs of runs not cancelled,
    failed or merely planned, not superseded, whose review is not ``none``."""
    from . import workflows
    rows = conn.execute(
        """SELECT sr.id::text FROM workflow_step_run sr JOIN workflow_run r ON r.id = sr.run_id
           WHERE (r.dataset_id = %s::uuid OR (r.group_id IS NOT NULL AND r.group_id = (SELECT group_id FROM dataset WHERE id = %s::uuid)))
             AND r.status NOT IN ('cancelled', 'failed', 'planned')
             AND sr.superseded_by IS NULL AND sr.review ->> 'mode' IS NOT NULL AND sr.review ->> 'mode' <> 'none'
           ORDER BY r.created_at DESC, sr.ordinal""", (dataset_id, dataset_id)).fetchall()
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


# ── the queue (what a reviewer works through) ─────────────────────────────────

def queue(conn: psycopg.Connection, run: dict, step_run: dict) -> dict:
    """The gate of one step as a worklist: its items in draw order with their verdicts, the
    documents they sit in, the progress, and what comes next when every item has a verdict."""
    header = step_run.get("review") or {}
    st = state(conn, step_run)
    if header.get("draft"):
        return {"run_id": run["id"], "dataset_id": run["dataset_id"], "step": step_run["step_id"], "kind": step_run["kind"], "mode": st["mode"],
                "unit": "draft", "blocks": st["blocks"], "draft": st.get("draft"), "items": [], "documents": [],
                "progress": {"drawn": 1, "checked": st["checked"], "pending": st["pending"], "disagreed": 0}, "satisfied": st["satisfied"],
                "next": "release" if st["blocks"] == "release" else "resume", "run_status": run["status"]}
    its = items(conn, step_run)
    docs: dict[str, dict] = {}
    for it in its:
        d = docs.setdefault(it.get("document_id") or "", {"document_id": it.get("document_id"), "n_items": 0, "n_checked": 0, "first_ord": it["ord"]})
        d["n_items"] += 1
        d["n_checked"] += 1 if it["checked"] else 0
    datasets: list[str] = []
    if docs:
        rows = conn.execute(
            """SELECT ed.id::text, ed.filename, p.title, ed.step_id, ed.paper_id::text,
                      (SELECT r.dataset_id::text FROM record r WHERE r.document_id = ed.id AND r.dataset_id IS NOT NULL LIMIT 1)
               FROM extraction_document ed LEFT JOIN paper p ON p.id = ed.paper_id WHERE ed.id = ANY(%s::uuid[])""",
            ([k for k in docs if k],)).fetchall()
        by_id = ws.steps_by_id(run.get("workflow") or {})
        for did, fn, title, step_id, pid, ds in rows:
            d = docs.get(did)
            if d is None:
                continue
            d.update({"filename": fn, "title": title, "step_id": step_id, "paper_id": pid, "dataset_id": ds,
                      "role": (by_id.get(step_id) or {}).get("dataset"), "step_label": (by_id.get(step_id) or {}).get("label")})
            if ds and ds not in datasets:
                datasets.append(ds)
        papers = {d.get("paper_id") for d in docs.values()}
        n_papers_done = len({d.get("paper_id") for d in docs.values() if d["n_checked"] >= d["n_items"]})
    else:
        papers, n_papers_done = set(), 0
    done = st["pending"] == 0
    nxt = None
    if done:
        nxt = ({"action": "resume"} if run["status"] == f"awaiting_review:{step_run['step_id']}"
               else {"action": "release", "url": f"/dataset?id={run['dataset_id']}#workflow"})
    return {"run_id": run["id"], "dataset_id": run["dataset_id"], "step": step_run["step_id"], "kind": step_run["kind"], "mode": header.get("mode"),
            "unit": header.get("unit", "entry"), "blocks": header.get("blocks", "release"),
            "draw": {k: header.get(k) for k in ("frame_n", "n_target", "seed", "stratify", "strata", "exhaustive", "drawn_at") if k in header},
            "items": its, "documents": sorted(docs.values(), key=lambda d: d["first_ord"]), "datasets": datasets or [run["dataset_id"]],
            "joint": step_run["kind"] == "review",
            "progress": {"drawn": len(its), "checked": st["checked"], "pending": st["pending"], "disagreed": st["disagreed"],
                         "papers": len(papers), "papers_done": n_papers_done},
            "satisfied": st["satisfied"], "next": nxt, "run_status": run["status"]}


# ── the dataset's sample estimate (for the badge) ─────────────────────────────

def dataset_sample(conn: psycopg.Connection, dataset_id: str) -> dict | None:
    """Over every random draw of the dataset's runs (not cancelled or failed, step-runs not
    superseded), joined to live records: what was drawn, checked, disagreed, and the error rate
    with its Wilson interval. Ad-hoc audits never enter; supplement items never enter."""
    from .records import _wilson
    rows = conn.execute(
        """SELECT sr.id::text, sr.run_id::text, sr.step_id, sr.review
           FROM workflow_step_run sr JOIN workflow_run r ON r.id = sr.run_id
           WHERE r.dataset_id = %s::uuid AND r.status NOT IN ('cancelled', 'failed', 'planned') AND sr.superseded_by IS NULL
             AND sr.review ->> 'mode' = 'sample' ORDER BY r.created_at""", (dataset_id,)).fetchall()
    if not rows:
        return None
    steps, frame, drawn, checked, disagreed = [], 0, 0, 0, 0
    for srid, run_id, step_id, header in rows:
        sr = {"id": srid, "run_id": run_id, "step_id": step_id, "review": header or {}, "outputs": {}}
        its = [i for i in items(conn, sr) if i["kind"] == "random"]
        c = sum(1 for i in its if i["checked"]); d = sum(1 for i in its if i["checked"] and i["disagreed"])
        f = int((header or {}).get("frame_n") or 0)
        steps.append({"run_id": run_id, "step": step_id, "unit": (header or {}).get("unit"), "frame": f, "drawn": len(its), "checked": c, "disagreed": d,
                      "seed": (header or {}).get("seed"), "stratify": (header or {}).get("stratify") or [], "drawn_at": (header or {}).get("drawn_at"),
                      "sampling_fraction": round(len(its) / f, 4) if f else None})
        frame += f; drawn += len(its); checked += c; disagreed += d
    n_records = conn.execute("SELECT count(*) FROM record WHERE dataset_id = %s::uuid AND NOT COALESCE(screened_empty, false)", (dataset_id,)).fetchone()[0]
    fractions = [s["sampling_fraction"] for s in steps if s["sampling_fraction"]]
    out = {"frame": frame, "drawn": drawn, "checked": checked, "pending": drawn - checked, "disagreed": disagreed,
           "error_rate": round(disagreed / checked, 4) if checked else None, "error_ci": _wilson(disagreed, checked) if checked else None,
           "agreement": round((checked - disagreed) / checked, 4) if checked else None, "agreement_ci": _wilson(checked - disagreed, checked) if checked else None,
           "coverage": round(frame / n_records, 4) if n_records else None, "steps": steps}
    if fractions and max(fractions) > 2 * min(fractions) and checked:
        weighted = sum((s["frame"] / frame) * (s["disagreed"] / s["checked"]) for s in steps if s["checked"] and frame)
        out["error_rate_weighted"] = round(weighted, 4)
    return out
