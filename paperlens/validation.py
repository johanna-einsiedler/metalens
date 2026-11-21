"""The record behind a stamp.

A review step with a purpose (``audit``, ``monitor``, ``calibrate``) writes one row per dataset
when its gate closes: how many papers and units a person checked, how often they agreed with the
model (a Wilson interval beside it) and, when two or more coders checked the same units, how often
the coders agreed with each other — pinned to the preset version the units were made under, the
model that made them and the workflow contract the run followed. A preset's card shows the rows at
its current pin as its stamp; a pin change lapses the stamp until an audit at the new pin renews it;
monitoring rows extend the count. ``routed`` and ``adjudicate`` reviews never write one: the papers
they see are chosen, not drawn.
"""
from __future__ import annotations

import logging
import uuid

import psycopg
from psycopg.types.json import Json

from . import records
from . import workflow_spec as ws

_log = logging.getLogger("paperlens")

STAMPED = ("audit", "monitor", "calibrate")

_COLS = ("id::text, run_id::text, step_run_id::text, step_id, purpose, dataset_id::text, workflow_id, schema_id, model, unit, "
         "n_papers, n_units, n_checked, n_disagreed, agreement, agreement_ci, n_coders, human_agreement, human_n, asks, template_id, "
         "sampled, frame_n, created_at")


def _row(r) -> dict:
    keys = [c.split("::")[0].strip() for c in _COLS.split(",")]
    out = dict(zip(keys, r))
    if out.get("created_at") is not None:
        out["created_at"] = out["created_at"].isoformat()
    return out


# ── writing ───────────────────────────────────────────────────────────────────

def _units(items: list[dict]) -> list[dict]:
    """One unit per record: checked when any coder checked it, disagreed when any did (the badge's
    rule); the coders' own verdicts kept for the calibration."""
    per: dict[str, dict] = {}
    for it in items:
        u = per.setdefault(it["record_id"], {"record_id": it["record_id"], "document_id": it.get("document_id"), "checked": False, "disagreed": False, "coders": []})
        u["checked"] = u["checked"] or bool(it.get("checked"))
        u["disagreed"] = u["disagreed"] or bool(it.get("checked") and it.get("disagreed"))
        if it.get("coder_user_id") and it.get("checked"):
            u["coders"].append({"user_id": it["coder_user_id"], "disagreed": bool(it.get("disagreed"))})
    return list(per.values())


def _human_agreement(units: list[dict]) -> tuple[int, int]:
    """Over the units two or more coders checked: how many where every coder answered alike
    (all took the model's value, or none did)."""
    pairs = [u for u in units if len(u["coders"]) >= 2]
    return sum(1 for u in pairs if len({c["disagreed"] for c in u["coders"]}) == 1), len(pairs)


def record_from_gate(conn: psycopg.Connection, step_run: dict, header: dict, items: list[dict]) -> list[dict]:
    """Called once, when a gate is first seen satisfied: the rows for the datasets its units belong
    to (a joint review over two sides writes one per side). Idempotent per (step-run, dataset)."""
    purpose = header.get("purpose")
    if purpose not in STAMPED or header.get("overridden_by"):
        return []
    from . import workflows
    run = workflows.get_run(conn, step_run["run_id"])
    if run is None:
        return []
    spec = run.get("workflow") or {}
    step = ws.steps_by_id(spec).get(step_run["step_id"]) or {}
    over = [o for o in (step.get("over") or (step_run.get("outputs") or {}).get("over") or []) if isinstance(o, str)]
    units = _units(items)
    if not units:
        return []
    srs = {x["step_id"]: x for x in workflows.step_runs(conn, run["id"])}
    ds_of = dict(conn.execute("SELECT id::text, dataset_id::text FROM record WHERE id = ANY(%s::uuid[])", ([u["record_id"] for u in units],)).fetchall())
    asks_of: dict[str, list] = {}
    for o, fields in (step.get("asks") or {}).items():
        try:
            asks_of[workflows.dataset_for_step(conn, run, o)] = list(fields or [])
        except Exception:  # noqa: BLE001 - a side without a dataset: no asks to record
            pass
    out = []
    for d_id in sorted({d for d in ds_of.values() if d}):
        mine = [u for u in units if ds_of.get(u["record_id"]) == d_id]
        checked = [u for u in mine if u["checked"]]
        if not checked:
            continue
        disagreed = sum(1 for u in checked if u["disagreed"])
        ds = records.get_dataset(conn, d_id) or {}
        model = next((srs[o].get("model") for o in over if srs.get(o) and srs[o].get("model")), None) or run.get("model")
        same, pairs = _human_agreement(mine) if int(header.get("coders") or 0) >= 2 else (0, 0)
        n_checked, agreed = len(checked), len(checked) - disagreed
        row = {"id": str(uuid.uuid4()), "run_id": run["id"], "step_run_id": step_run["id"], "step_id": step_run["step_id"], "purpose": purpose,
               "dataset_id": d_id, "workflow_id": run.get("contract") or (ws.workflow_id(spec) if spec.get("id") else run.get("workflow_id")),
               "schema_id": ds.get("schema_id"), "model": model, "unit": header.get("unit"),
               "n_papers": len({u.get("document_id") for u in checked if u.get("document_id")}), "n_units": len(mine),
               "n_checked": n_checked, "n_disagreed": disagreed,
               "agreement": round(agreed / n_checked, 4), "agreement_ci": records._wilson(agreed, n_checked),   # noqa: SLF001
               "n_coders": int(header.get("coders") or 0) or None,
               "human_agreement": round(same / pairs, 4) if pairs else None, "human_n": pairs or None,
               "asks": asks_of.get(d_id), "template_id": header.get("template_id") or header.get("template"),
               "sampled": header.get("mode") == "sample", "frame_n": header.get("frame_n")}
        with conn.transaction():
            conn.execute(
                """INSERT INTO validation (id, run_id, step_run_id, step_id, purpose, dataset_id, workflow_id, schema_id, model, unit,
                                           n_papers, n_units, n_checked, n_disagreed, agreement, agreement_ci, n_coders, human_agreement, human_n,
                                           asks, template_id, sampled, frame_n)
                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (step_run_id, dataset_id) DO NOTHING""",
                (row["id"], row["run_id"], row["step_run_id"], row["step_id"], purpose, d_id, row["workflow_id"], row["schema_id"], model, row["unit"],
                 row["n_papers"], row["n_units"], n_checked, disagreed, row["agreement"], Json(row["agreement_ci"]), row["n_coders"],
                 row["human_agreement"], row["human_n"], Json(row["asks"]) if row["asks"] is not None else None, row["template_id"],
                 row["sampled"], row["frame_n"]))
        out.append(row)
    return out


# ── reading ───────────────────────────────────────────────────────────────────

def _select(conn, where: str, args: tuple) -> list[dict]:
    return [_row(r) for r in conn.execute(f"SELECT {_COLS} FROM validation WHERE {where} ORDER BY created_at DESC", args).fetchall()]


def for_dataset(conn: psycopg.Connection, dataset_id: str) -> list[dict]:
    if not records._is_uuid(dataset_id or ""):   # noqa: SLF001
        return []
    return _select(conn, "dataset_id = %s::uuid", (dataset_id,))


def latest_by_step(conn: psycopg.Connection, dataset_id: str) -> dict[str, dict]:
    """The newest row per review step of the dataset — the Workflow card's line per step."""
    out: dict[str, dict] = {}
    for r in for_dataset(conn, dataset_id):
        out.setdefault(r["step_id"], r)
    return out


def _pool(rows: list[dict]) -> dict | None:
    """Several rows as one count: papers and checks summed, the agreement pooled with its interval."""
    if not rows:
        return None
    checked = sum(int(r["n_checked"] or 0) for r in rows)
    disagreed = sum(int(r["n_disagreed"] or 0) for r in rows)
    agreed = checked - disagreed
    hn = sum(int(r["human_n"] or 0) for r in rows if r.get("human_agreement") is not None)
    hs = sum(round(float(r["human_agreement"]) * int(r["human_n"] or 0)) for r in rows if r.get("human_agreement") is not None)
    return {"n": len(rows), "n_papers": sum(int(r["n_papers"] or 0) for r in rows), "n_checked": checked, "n_disagreed": disagreed,
            "agreement": round(agreed / checked, 4) if checked else None, "agreement_ci": records._wilson(agreed, checked) if checked else None,   # noqa: SLF001
            "human_agreement": round(hs / hn, 4) if hn else None, "human_n": hn or None,
            "since": min(r["created_at"] for r in rows), "until": max(r["created_at"] for r in rows),
            "models": sorted({r["model"] for r in rows if r.get("model")}), "coders": max((int(r["n_coders"] or 0) for r in rows), default=0) or None}


def _pct(x) -> str:
    return f"{round(100 * float(x))}%"


def stamp(rows: list[dict], *, current_schema_id: str | None = None) -> dict | None:
    """What a preset (or a workflow's step) carries, from its validation rows at the newest pin
    among them: the audit (the latest at that pin), the monitoring since (pooled), the calibration;
    ``current`` says whether that pin is the one asked about, ``lapsed`` that it is an older one."""
    if not rows:
        return None
    pin = rows[0]["schema_id"]
    at_pin = [r for r in rows if r["schema_id"] == pin]
    audits = [r for r in at_pin if r["purpose"] == "audit"]
    audit = audits[0] if audits else None
    monitors = [r for r in at_pin if r["purpose"] == "monitor" and (audit is None or r["created_at"] >= audit["created_at"])]
    calibs = [r for r in at_pin if r["purpose"] == "calibrate"]
    headline = _pool(([audit] if audit else []) + monitors)
    cal = _pool(calibs)
    parts = []
    if audit:
        parts.append(f"audited on {audit['n_papers']} paper{'s' if audit['n_papers'] != 1 else ''}")
    if monitors:
        parts.append(f"monitored on {sum(int(r['n_papers'] or 0) for r in monitors)} more")
    if headline and headline["agreement"] is not None:
        lo, hi = headline["agreement_ci"] or (None, None)
        parts.append(f"{_pct(headline['agreement'])} agree" + (f" ({_pct(lo)}–{_pct(hi)})" if lo is not None else ""))
    if cal and cal["human_agreement"] is not None:
        parts.append(f"coders agree {_pct(cal['human_agreement'])}")
    elif headline and headline["human_agreement"] is not None:
        parts.append(f"coders agree {_pct(headline['human_agreement'])}")
    current = current_schema_id is None or pin == current_schema_id
    return {"schema_id": pin, "current": current, "lapsed": not current, "audit": audit, "monitor": _pool(monitors), "calibrate": cal,
            "n_papers": headline["n_papers"] if headline else 0, "agreement": headline["agreement"] if headline else None,
            "agreement_ci": headline["agreement_ci"] if headline else None, "models": headline["models"] if headline else [],
            "coders": headline["coders"] if headline else None, "label": " · ".join(parts) + (" · at an earlier version" if not current else "")}


def for_preset(conn: psycopg.Connection, preset_id: str, *, current_schema_id: str | None) -> dict:
    """The preset's stamp: the rows at every pin of it, the newest pin's as the stamp (lapsed when
    the preset moved on), the older pins listed."""
    rows = _select(conn, "schema_id LIKE %s", (f"{preset_id}@%",))
    pins: list[dict] = []
    for pin in sorted({r["schema_id"] for r in rows}, key=lambda p: max(r["created_at"] for r in rows if r["schema_id"] == p), reverse=True):
        s = stamp([r for r in rows if r["schema_id"] == pin], current_schema_id=current_schema_id)
        if s:
            pins.append(s)
    return {"stamp": stamp(rows, current_schema_id=current_schema_id), "pins": pins}


def for_workflow(conn: psycopg.Connection, workflow_base_id: str) -> dict:
    """Per review step of a workflow (any contract of it): the stamp its rows give."""
    rows = _select(conn, "workflow_id LIKE %s", (f"{workflow_base_id}@%",))
    steps: dict[str, dict] = {}
    for sid in sorted({r["step_id"] for r in rows}):
        s = stamp([r for r in rows if r["step_id"] == sid])
        if s:
            steps[sid] = s
    return {"steps": steps, "n": len(rows)}
