"""Deterministic checks a workflow runs over a dataset: a registry of named rules, each a pure
function over records that returns FINDINGS — never data, never a verdict. A finding names the
record, the cell (a record-relative path), a code, a severity and a message; the workflow stores
them per run (``workflow_finding``), the review screen shows them on the card, the release
freezes them. A person's verdict stays a person's: a check may put a record in front of a
reviewer (``on_fail: flag``) or fail the run (``on_fail: fail``), but it never marks one verified
or flagged.

Checks are code in this repository, not user scripts: parameters are declared and typed, the
rule itself is reviewed like any other code.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Json

ON_FAIL = ("note", "flag", "fail")
SEVERITIES = ("error", "warn", "info")
SEV_RANK = {"info": 0, "warn": 1, "error": 2}
_PARAM_TYPES = {"list": list, "number": (int, float), "integer": int, "string": str, "boolean": bool, "table": list}


# ── the registry ──────────────────────────────────────────────────────────────

def check_schema(inputs: list[dict], params: dict) -> list[dict]:
    """The declared contract, re-checked on the CURRENT values (after corrections): required
    fields, numbers, options, ranges, tables — what ``preset_spec.validate_result`` found at
    extraction time, but on what the records hold now. Evidence and confidence are stored
    apart from the values, so their codes are ignored by default."""
    from . import preset_spec as ps
    ignore = set(params.get("ignore") or [])
    src = inputs[0]
    spec = src.get("spec") or {}
    key = (spec.get("entries") or {}).get("key") or "records"
    by_doc: dict[str, list[dict]] = {}
    for r in src.get("records") or []:
        by_doc.setdefault(r["document_id"], []).append(r)
    out: list[dict] = []
    head = re.compile(rf"^{re.escape(key)}\[(\d+)\](?:\.(.*))?$")
    for doc_id, recs in by_doc.items():
        recs.sort(key=lambda r: r["entry_index"])
        info = (src.get("documents") or {}).get(doc_id) or {}
        obj = {"paper_metadata": info.get("paper_metadata") or {}, key: [r["field_values"] for r in recs], "evidence": []}
        try:
            issues = ps.validate_result(obj, spec, info.get("params") or {})
        except Exception as exc:  # noqa: BLE001 - never fail the run over a side effect
            out.append({"record_id": None, "document_id": doc_id, "path": None, "code": "check_failed",
                        "severity": "warn", "message": f"the contract could not be checked: {exc}"[:300]})
            continue
        by_index = {r["entry_index"]: r for r in recs}
        for it in issues:
            code = it.get("code") or "issue"
            if code in ignore:
                continue
            path = it.get("path") or ""
            m = head.match(path)
            rec = by_index.get(int(m.group(1))) if m else None
            out.append({"record_id": rec["id"] if rec else None, "document_id": doc_id,
                        "path": (m.group(2) or "") if m else path, "code": code,
                        "severity": "error" if code in ("missing_required", "not_number", "not_in_options", "out_of_range",
                                                        "not_list", "not_table", "bad_path", "dangling_path", "not_object") else "warn",
                        "message": str(it.get("message") or code)[:300]})
    return out


SUB_TYPES = ("se", "t_stat", "p_value", "ci")
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _parse_printed(raw) -> float | None:
    """The number in a printed cell: stars, parentheses, brackets, daggers and a per-cent sign
    stripped, a unicode minus read as a minus."""
    s = str(raw).replace("−", "-")
    s = re.sub(r"[()\[\]*†‡§%]", " ", s)
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(5e-4, 5e-3 * max(abs(a), abs(b)))


def _isnum(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def check_regression_cells(inputs: list[dict], params: dict) -> list[dict]:
    """The printed cells of a regression column hang together: every standard-error (t, p, CI)
    row names a coefficient row and no coefficient has two; the parsed number is the printed
    one; stars agree with the stated level and with a p-value; a t-statistic agrees with
    coefficient over standard error; the N stated in the sample description is the N printed;
    columns of one table that report different Ns are noted; row indexes run from 0."""
    tol = float(params.get("t_tolerance") or 0.15)
    legend: dict[int, float] = {}
    for pair in params.get("star_legend") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and _isnum(pair[0]) and _isnum(pair[1]):
            legend[int(pair[0])] = float(pair[1])
    src = inputs[0]
    out: list[dict] = []
    by_table: dict[tuple, list] = {}

    def F(rec: dict, path: str | None, code: str, severity: str, message: str, **detail) -> None:
        out.append({"record_id": rec["id"], "document_id": rec["document_id"], "path": path, "code": code,
                    "severity": severity, "message": message, **({"detail": detail} if detail else {})})

    for r in src.get("records") or []:
        fv = r.get("field_values") or {}
        cells = [c for c in (fv.get("cells") or []) if isinstance(c, dict)]
        idx = {c["row_index"]: c for c in cells if _isnum(c.get("row_index"))}
        ris = [c.get("row_index") for c in cells]
        if cells and (any(not _isnum(x) for x in ris) or len(set(ris)) != len(ris) or sorted(ris) != list(range(len(ris)))):
            F(r, "cells", "row_index_gap", "warn", "row indexes are not unique and contiguous from 0")
        se_rows: dict[int, list[int]] = {}
        for i, c in enumerate(cells):
            rt, path = c.get("row_type"), f"cells[{i}]"
            if rt in SUB_TYPES:
                ref = c.get("refers_to")
                if not _isnum(ref) or ref not in idx:
                    F(r, f"{path}.refers_to", "se_dangling_ref", "error", f"the {rt} row refers to no coefficient row (refers_to {ref!r})")
                elif idx[ref].get("row_type") != "coefficient":
                    F(r, f"{path}.refers_to", "se_refers_noncoef", "error", f"the {rt} row refers to row {ref}, which is a {idx[ref].get('row_type')} row, not a coefficient")
                elif rt == "se":
                    se_rows.setdefault(int(ref), []).append(i)
            raw, nv = c.get("raw_text"), c.get("numeric_value")
            if raw is not None and _isnum(nv):
                parsed = _parse_printed(raw)
                if parsed is not None and not _close(parsed, float(nv)):
                    F(r, f"{path}.numeric_value", "numeric_mismatch", "error", f"numeric_value {nv} is not the printed {raw!r}", printed=parsed)
            stars, lvl = c.get("significance_stars"), c.get("significance_level")
            if _isnum(stars) and int(stars) > 0 and _isnum(lvl) and legend.get(int(stars)) is not None and not _close(float(lvl), legend[int(stars)]):
                F(r, f"{path}.significance_level", "stars_vs_level", "warn", f"{int(stars)} star{'s' if int(stars) != 1 else ''} but level {lvl} (the legend says {legend[int(stars)]})")
        for ref, rows in se_rows.items():
            if len(rows) > 1:
                F(r, f"cells[{rows[1]}].refers_to", "coef_many_se", "error", f"coefficient row {ref} has {len(rows)} standard-error rows")
        for i, c in enumerate(cells):
            rt, ref = c.get("row_type"), c.get("refers_to")
            if not (_isnum(ref) and ref in idx and idx[ref].get("row_type") == "coefficient"):
                continue
            coef = idx[ref].get("numeric_value")
            if rt == "t_stat":
                se_i = (se_rows.get(int(ref)) or [None])[0]
                se = cells[se_i].get("numeric_value") if se_i is not None else None
                t = c.get("numeric_value")
                if _isnum(coef) and _isnum(se) and _isnum(t) and se:
                    want = float(coef) / float(se)
                    if abs(float(t) - want) / max(abs(float(t)), 1.0) > tol:
                        F(r, f"cells[{i}].numeric_value", "t_vs_coef_se", "warn", f"t = {t} but coefficient / se = {want:.3f}", expected=round(want, 4))
            if rt == "p_value":
                stars, pv = idx[ref].get("significance_stars"), c.get("numeric_value")
                if _isnum(stars) and int(stars) > 0 and _isnum(pv) and legend.get(int(stars)) is not None and float(pv) > legend[int(stars)] + 1e-9:
                    F(r, f"cells[{i}].numeric_value", "stars_vs_p", "warn", f"p = {pv} but the coefficient carries {int(stars)} star{'s' if int(stars) != 1 else ''} (≤ {legend[int(stars)]})")
        n_cell = next((c for c in cells if c.get("row_type") == "statistic_n_obs"), None)
        n_val = n_cell.get("numeric_value") if n_cell else None
        m = re.search(r"\bN\s*=\s*([\d,]+)", str(fv.get("sample_restrictions") or ""))
        if m and _isnum(n_val):
            stated = float(m.group(1).replace(",", ""))
            if not _close(stated, float(n_val)):
                F(r, f"cells[{cells.index(n_cell)}].numeric_value", "n_obs_vs_sample", "warn", f"the sample description states N = {m.group(1)} but the column prints {n_val}")
        paper = r.get("paper") or {}
        key = (paper.get("doi") or paper.get("title") or r.get("document_id"), str(fv.get("table_number") or ""), str(fv.get("panel") or ""))
        by_table.setdefault(key, []).append((r, n_val if _isnum(n_val) else None))
    for (_paper, table, panel), items in by_table.items():
        ns = sorted({float(x) for _r, x in items if x is not None})
        if len(ns) > 1:
            for rec, _x in items:
                F(rec, "cells", "n_obs_varies", "info", f"the columns of {table or 'the table'}{' ' + panel if panel else ''} report different N ({', '.join(str(int(n)) if n.is_integer() else str(n) for n in ns)})")
    return out


def check_claims_vs_tables(inputs: list[dict], params: dict) -> list[dict]:
    """Each claim's cited point estimate against the transcribed table cells of the companion
    dataset (``crosscheck.statuses``): a resolved column with no cell equal to the estimate is an
    error, a cited exhibit no column matches is a warning; the rest passes."""
    from . import crosscheck
    src = inputs[0]
    regs = (src.get("companion") or {}).get("regressions") or []
    rows, doc_of = [], {}
    for r in src.get("records") or []:
        fv = r.get("field_values") or {}
        doc_of[r["id"]] = r["document_id"]
        paper = r.get("paper") or {}
        for k, res in enumerate(fv.get("results") or []):
            if not isinstance(res, dict):
                continue
            rows.append({"record_id": r["id"], "path": f"results[{k}]", "claim_id": fv.get("claim_id"), "result_id": res.get("result_id"),
                         "doi": paper.get("doi"), "title": paper.get("title"), "exhibit": res.get("exhibit"), "source_table": res.get("source_table"),
                         "panel": res.get("panel"), "column": res.get("column"), "point_estimate": res.get("point_estimate"),
                         "value_from": res.get("value_from")})
    out: list[dict] = []
    for v in crosscheck.statuses(rows, regs):
        if v["status"] == "mismatch":
            sev, code, path = "error", "estimate_mismatch", f"{v['path']}.point_estimate"
        elif v["status"] == "unlinked":
            sev, code, path = "warn", "exhibit_unlinked", f"{v['path']}.source_table"
        else:
            continue
        out.append({"record_id": v["record_id"], "document_id": doc_of.get(v["record_id"]), "path": path, "code": code, "severity": sev,
                    "message": v.get("detail") or v["status"], "companion_record_id": v.get("companion_record_id"),
                    "detail": {"status": v["status"], "regression_id": v.get("regression_id"), "table_coefficients": v.get("table_coefficients"),
                               "result_id": v.get("result_id")}})
    return out


REGISTRY: dict[str, dict] = {
    "schema": {"label": "the declared contract, re-checked on the current values",
               "applies_to": {"presets": "*", "unit": "entries"}, "inputs": 1,
               "params": {"ignore": {"type": "list", "default": ["uncited_value", "uncited_row", "missing_confidence"]}},
               "fn": check_schema},
    "regression-cells": {"label": "the printed cells of a regression column hang together",
                         "applies_to": {"presets": ["econ-regression-tables", "register-tables"], "unit": "entries"}, "inputs": 1,
                         "params": {"t_tolerance": {"type": "number", "default": 0.15},
                                    "star_legend": {"type": "table", "default": [[3, 0.01], [2, 0.05], [1, 0.1]]}},
                         "codes": {"se_dangling_ref": "error", "se_refers_noncoef": "error", "coef_many_se": "error", "numeric_mismatch": "error",
                                   "stars_vs_level": "warn", "stars_vs_p": "warn", "t_vs_coef_se": "warn", "n_obs_vs_sample": "warn",
                                   "n_obs_varies": "info", "row_index_gap": "warn"},
                         "fn": check_regression_cells},
    "claims-vs-tables": {"label": "point estimates against the transcribed table cells of the companion dataset",
                         "applies_to": {"presets": ["register-claims"], "unit": "results", "companion": ["register-tables", "econ-regression-tables"]},
                         "inputs": 1, "needs_companion": True, "params": {},
                         "codes": {"estimate_mismatch": "error", "exhibit_unlinked": "warn"},
                         "fn": check_claims_vs_tables},
}


def validate_step(st: dict, at: str, E, W, *, presets_of: dict[str, dict | None]) -> None:
    """A check step's declaration against the registry: the check exists, it reads the number
    of steps it takes, those steps' presets are ones it applies to, its parameters are declared
    and typed."""
    name = st.get("check")
    reg = REGISTRY.get(name) if isinstance(name, str) else None
    if reg is None:
        E(f"{at}.check must be one of {', '.join(sorted(REGISTRY))}"); return
    over = [o for o in (st.get("over") or []) if isinstance(o, str)]
    if len(over) != reg["inputs"]:
        E(f"{at}.over: {name!r} reads {reg['inputs']} step(s), not {len(over)}")
    applies = reg["applies_to"]
    for i, o in enumerate(over):
        pspec = presets_of.get(o)
        if not pspec:
            continue
        allowed = applies.get("companion") if i == 1 else applies.get("presets")
        if allowed != "*" and isinstance(allowed, list) and pspec.get("id") not in allowed:
            E(f"{at}.over[{i}]: {name!r} does not apply to {pspec.get('id')!r} (it reads {', '.join(allowed)})")
    params = st.get("params") or {}
    if not isinstance(params, dict):
        E(f"{at}.params must be an object"); return
    for k, v in params.items():
        decl = reg["params"].get(k)
        if decl is None:
            E(f"{at}.params: {name!r} has no parameter {k!r}"); continue
        want = _PARAM_TYPES.get(decl.get("type"), object)
        if not isinstance(v, want) or (decl.get("type") == "number" and isinstance(v, bool)):
            E(f"{at}.params.{k} must be a {decl.get('type')}")


def params_for(st: dict) -> dict:
    reg = REGISTRY[st["check"]]
    out = {k: d.get("default") for k, d in reg["params"].items()}
    out.update({k: v for k, v in (st.get("params") or {}).items() if k in reg["params"]})
    return out


# ── running a check ───────────────────────────────────────────────────────────

def load_input(conn: psycopg.Connection, dataset_id: str, schema_id: str | None, *,
               document_ids: list[str] | None = None) -> dict:
    """The records a check reads: those of ``document_ids`` (else every document of the dataset
    under ``schema_id``), with each document's parameters and paper metadata."""
    from . import records
    spec = records.schema_spec(conn, schema_id) if schema_id else None
    rows = conn.execute(
        """SELECT r.id::text, r.document_id::text, r.paper_id::text, r.entry_index, r.field_values,
                  p.doi, p.title, ed.params, ed.paper_metadata_raw
           FROM record r JOIN extraction_document ed ON ed.id = r.document_id
           LEFT JOIN paper p ON p.id = r.paper_id
           WHERE r.dataset_id = %s::uuid AND NOT COALESCE(r.screened_empty, false)
             AND (%s::text IS NULL OR ed.schema_id = %s)
             AND (%s::uuid[] IS NULL OR r.document_id = ANY(%s::uuid[]))
           ORDER BY r.document_id, r.entry_index""",
        (dataset_id, schema_id, schema_id, document_ids, document_ids)).fetchall()
    recs, docs = [], {}
    for rid, did, pid, ei, fv, doi, title, params, pmr in rows:
        recs.append({"id": rid, "document_id": did, "paper_id": pid, "entry_index": ei,
                     "field_values": fv if isinstance(fv, dict) else {}, "paper": {"doi": doi, "title": title}})
        docs.setdefault(did, {"params": params if isinstance(params, dict) else {},
                              "paper_metadata": pmr if isinstance(pmr, dict) else {}})
    return {"dataset_id": dataset_id, "schema_id": schema_id, "spec": spec, "records": recs, "documents": docs}


def companion_input(conn: psycopg.Connection, dataset_id: str) -> dict | None:
    """The companion dataset's regression columns, for a check that reads them; None without a
    companion (the planner then blocks the step)."""
    from . import crosscheck, records
    d = records.get_dataset(conn, dataset_id) or {}
    cid = d.get("companion_dataset_id")
    if not cid:
        return None
    return {"dataset_id": cid, "regressions": crosscheck.regressions_of(conn, cid)}


def run_step(conn: psycopg.Connection, run_id: str, step_run_id: str, st: dict, inputs: list[dict]) -> dict:
    """Run one check step: call the rule, replace this step-run's findings, summarise. Returns
    ``{n_checked, findings, by_severity, by_code, status}`` — ``failed`` when ``on_fail: fail``
    and a finding reaches the step's severity."""
    reg = REGISTRY[st["check"]]
    findings = reg["fn"](inputs, params_for(st))
    rank = SEV_RANK[st.get("severity") or "error"]
    with conn.transaction():
        conn.execute("DELETE FROM workflow_finding WHERE step_run_id = %s::uuid", (step_run_id,))
        for f in findings:
            conn.execute(
                """INSERT INTO workflow_finding (id, run_id, step_run_id, step_id, "check", record_id, document_id,
                                                 companion_record_id, path, code, severity, message, detail)
                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s)""",
                (str(uuid.uuid4()), run_id, step_run_id, st["id"], st["check"], f.get("record_id"), f.get("document_id"),
                 f.get("companion_record_id"), f.get("path"), f.get("code"), f.get("severity") or "warn",
                 (f.get("message") or f.get("code") or "")[:1000], Json(f["detail"]) if f.get("detail") is not None else None))
    by_sev: dict[str, int] = {}
    by_code: dict[str, int] = {}
    for f in findings:
        by_sev[f.get("severity") or "warn"] = by_sev.get(f.get("severity") or "warn", 0) + 1
        by_code[f.get("code") or "issue"] = by_code.get(f.get("code") or "issue", 0) + 1
    hit = any(SEV_RANK.get(f.get("severity") or "warn", 0) >= rank for f in findings)
    n_checked = sum(len(i.get("records") or []) for i in inputs)
    return {"n_checked": n_checked, "findings": len(findings), "by_severity": by_sev, "by_code": by_code,
            "flagged_record_ids": sorted({f["record_id"] for f in findings if f.get("record_id") and SEV_RANK.get(f.get("severity") or "warn", 0) >= rank}),
            "status": "failed" if hit and st.get("on_fail") == "fail" else "done"}


def _rows(rows) -> list[dict]:
    return [{"id": r[0], "run_id": r[1], "step_id": r[2], "check": r[3], "record_id": r[4], "document_id": r[5],
             "companion_record_id": r[6], "path": r[7], "code": r[8], "severity": r[9], "message": r[10], "detail": r[11]}
            for r in rows]


_SEL = ('f.id::text, f.run_id::text, f.step_id, f."check", f.record_id::text, f.document_id::text, f.companion_record_id::text, '
        'f.path, f.code, f.severity, f.message, f.detail')


def for_document(conn: psycopg.Connection, document_id: str) -> list[dict]:
    """The findings on a document's records from the newest run that has any — what the review
    screen shows on the card."""
    run = conn.execute(
        """SELECT f.run_id::text FROM workflow_finding f JOIN workflow_run r ON r.id = f.run_id
           WHERE f.document_id = %s::uuid AND r.status <> 'cancelled' ORDER BY r.created_at DESC LIMIT 1""",
        (document_id,)).fetchone()
    if not run:
        return []
    return _rows(conn.execute(f"SELECT {_SEL} FROM workflow_finding f WHERE f.run_id = %s::uuid AND f.document_id = %s::uuid ORDER BY f.severity, f.code",
                              (run[0], document_id)).fetchall())


def for_run(conn: psycopg.Connection, run_id: str, step_id: str | None = None) -> list[dict]:
    return _rows(conn.execute(
        f"SELECT {_SEL} FROM workflow_finding f WHERE f.run_id = %s::uuid AND (%s::text IS NULL OR f.step_id = %s) ORDER BY f.step_id, f.severity, f.code",
        (run_id, step_id, step_id)).fetchall())


def frozen(conn: psycopg.Connection, dataset_id: str) -> dict | None:
    """The findings of the dataset's newest finished run, for a release snapshot."""
    run = conn.execute(
        """SELECT id::text FROM workflow_run WHERE dataset_id = %s::uuid AND status = 'done'
             AND EXISTS (SELECT 1 FROM workflow_finding f WHERE f.run_id = workflow_run.id)
           ORDER BY created_at DESC LIMIT 1""", (dataset_id,)).fetchone()
    if not run:
        return None
    rows = for_run(conn, run[0])
    by_sev: dict[str, int] = {}
    for f in rows:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    return {"run_id": run[0], "n": len(rows), "by_severity": by_sev, "findings": rows}


def summary_for_dataset(conn: psycopg.Connection, dataset_id: str) -> dict | None:
    fz = frozen(conn, dataset_id)
    if not fz:
        return None
    by_check: dict[str, int] = {}
    for f in fz["findings"]:
        by_check[f["check"]] = by_check.get(f["check"], 0) + 1
    return {"run_id": fz["run_id"], "findings": fz["n"], "by_severity": fz["by_severity"], "by_check": by_check}


def is_json(x: Any) -> bool:
    return isinstance(x, (dict, list, str, int, float, bool)) or x is None
