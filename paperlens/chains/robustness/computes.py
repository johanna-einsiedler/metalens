"""Deterministic steps shared by the pipeline and Metalens.

| compute | pipeline source | produces | reads |
|---|---|---|---|
| headlines | s11 `claim_chain_headlines.py`: a regression is a headline when at least one `main` estimate of a stated claim links to it | fields on the tables | the claims documents |
| grade | s18 `grade.py`: the reproduced values against the published cells at half the last printed digit, sign never ignored, a whole-table rescaling detected and recorded | fields on the tables | the reproduced documents |
| assemble | `metalens/sync/export.py` (the conversion of an extraction + claim files into the import shapes) | document | extract_session, stated_claims, claim_map — once the passes exist (plan step 5) |
| blind | s13 `blind.py` (`BLIND_POLICY` by row type; the leak test needs the package, so it stays in the pipeline) | document | — (plan step 3) |

The pipeline and Metalens hold the same regression in two shapes. The pipeline's
(``pipeline/schema.py``): ``regression_id`` is ``<artid>::<table>::<panel>::<column>``, ``cells``
carry ``row_index``, ``row_type``, ``raw_text``, ``numeric_value``, ``row_label``; a reproduced
target (``reproduce/<artid>.result.json``) has ``status``, ``method``, ``reason``, ``n_obs`` and
``cells`` with ``row_index`` and ``numeric_value``. The Metalens shape (the import presets
``robustness-regressions`` and ``reproduced-regressions``, written by ``sync/export.py``) is the
same with the ``<artid>::`` prefix stripped. The functions below take plain dicts of either
shape, so the stage script and the Metalens compute call the same code: ``grade_regressions``
is the whole of ``grade.py``'s cell comparison, ``headline_links`` the whole of
``claim_chain_headlines.py``'s per-paper rule.

A Metalens compute takes the engine's ``ComputeContext``: ``ctx.record`` is the document it
edits (``{document_id, records: [{id, entry_index, field_values}], paper_metadata}``),
``ctx.read(step)`` the document of the data step it reads in the same shape, ``ctx.params``
its parameters; it returns ``{records: {record id: {field: value}}, paper: {field: value},
findings: [...]}`` and the engine writes every edit through the audited path.
"""
from __future__ import annotations

import math
import re
import statistics

# ── what the re-estimation sees withheld (schema.BLIND_POLICY, by row type) ────────────────
# The published cells the agent must reproduce. N is kept (it identifies the regression), the
# Yes/No markers and panel headers are structure, not results.
BLIND_POLICY: dict[str, bool] = {
    "coefficient": True, "se": True, "p_value": True, "ci": True, "t_stat": True,
    "statistic_r2": True, "statistic_f": True, "statistic_other": True,
    "statistic_n_obs": False, "string": False, "panel_header": False,
}

# ── grading (s18) ──────────────────────────────────────────────────────────────────────────
# A ratio counts as a clean power of ten when its log10 is this close to a nonzero integer:
# 0.15 admits 10x / 100x / 1000x mismatches while rejecting the merely similar.
SCALE_LOG_TOL = 0.15
SCALE_MIN_CELLS = 4          # never infer a rescaling from one or two cells
PCT_BANDS = (0.001, 0.01, 0.05, 0.10)   # secondary "how close" bands, reported beside the verdict


def printed_tolerance(raw_text: str | None, value: float) -> float:
    """Half of the last printed digit. '0.033' -> 0.0005; '1,234' -> 0.5."""
    s = (raw_text or "").replace(",", "")
    m = re.search(r"\.(\d+)", s)
    if m:
        return 0.5 * 10 ** (-len(m.group(1)))
    if re.search(r"\d", s):
        return 0.5
    # No printed form to read: fall back to the value's own magnitude.
    return max(abs(value) * 1e-4, 1e-9)


def detect_scale(pairs: list[tuple[float, float]]) -> tuple[float, str]:
    """(factor, note) from (published, reproduced) pairs. 1.0 when none found."""
    ratios = [r / p for p, r in pairs
              if p not in (0.0, None) and r not in (0.0, None)
              and math.isfinite(p) and math.isfinite(r)]
    if len(ratios) < SCALE_MIN_CELLS:
        return 1.0, ""
    med = statistics.median(ratios)
    if med <= 0 or not math.isfinite(med):
        return 1.0, ""
    lg = math.log10(med)
    n = round(lg)
    if n != 0 and abs(lg - n) <= SCALE_LOG_TOL:
        return 10.0 ** n, f"reproduced values are 10^{n} x published"
    return 1.0, ""


def grade_regressions(truth: list[dict], targets: list[dict], *, cls_of: dict[str, str] | None = None,
                      apply_scale: bool = True) -> dict:
    """The cell comparison of ``grade.py``, on plain dicts: ``truth`` the published regressions
    (``regression_id``, ``cells``), ``targets`` the reproduced ones (``regression_id``, ``status``,
    ``method``, ``reason``, ``n_obs``, ``cells``), ``cls_of`` the result class per regression id
    (``unclassified`` when absent). A positional join on ``(regression_id, row_index)``; a
    withheld cell (``BLIND_POLICY``) with a published number is graded, the rest is not.

    Returns ``{"factor", "scale_note", "rows": [one per graded cell], "per_reg": {id: {...}}}``
    — the rows and the per-regression dicts exactly as ``grade.py`` writes them."""
    cls_of = cls_of or {}
    by_id = {r["regression_id"]: r for r in truth}

    # Pass 1: collect comparable pairs so scale can be judged on the whole paper.
    pairs: list[tuple[float, float]] = []
    for t in targets:
        tr = by_id.get(t["regression_id"])
        if t.get("status") != "ok" or tr is None:
            continue
        cells = {c["row_index"]: c for c in tr.get("cells") or []}
        for fc in t.get("cells") or []:
            tc = cells.get(fc.get("row_index"))
            if (tc is None or tc.get("numeric_value") is None or fc.get("numeric_value") is None
                    or not BLIND_POLICY.get(tc.get("row_type"), True)):
                continue
            pairs.append((tc["numeric_value"], fc["numeric_value"]))

    factor, scale_note = detect_scale(pairs) if apply_scale else (1.0, "")

    rows: list[dict] = []
    per_reg: dict[str, dict] = {}
    for t in targets:
        rid = t["regression_id"]
        tr = by_id.get(rid)
        if tr is None:
            continue
        rc = cls_of.get(rid, "unclassified")
        reg = per_reg.setdefault(rid, {
            "regression_id": rid, "result_class": rc,
            "status": t.get("status"), "method": t.get("method"), "reason": t.get("reason"),
            "n_cells": 0, "n_match": 0, "n_sign_flip": 0, "n_missing": 0,
            "n_obs_published": None, "n_obs_achieved": t.get("n_obs"),
            "sample_mismatch": False,
        })
        # Sample size is given to the agent, so it is evidence only when the ACHIEVED N is
        # compared against the published one.
        pub_n = next((c.get("numeric_value") for c in tr.get("cells") or []
                      if c.get("row_type") == "statistic_n_obs"), None)
        reg["n_obs_published"] = pub_n
        if pub_n is not None and t.get("n_obs") is not None and abs(pub_n - float(t["n_obs"])) > 0.5:
            reg["sample_mismatch"] = True

        if t.get("status") != "ok":
            continue

        cells = {c["row_index"]: c for c in tr.get("cells") or []}
        filled = {c.get("row_index"): c for c in t.get("cells") or []}
        for ri, tc in cells.items():
            if tc.get("numeric_value") is None or not BLIND_POLICY.get(tc.get("row_type"), True):
                continue
            reg["n_cells"] += 1
            fc = filled.get(ri)
            if fc is None or fc.get("numeric_value") is None:
                reg["n_missing"] += 1
                rows.append({"regression_id": rid, "row_index": ri,
                             "row_type": tc.get("row_type"), "row_label": tc.get("row_label"),
                             "published": tc["numeric_value"], "reproduced": None,
                             "verdict": "missing", "result_class": rc})
                continue

            got = fc["numeric_value"] * factor
            tol = printed_tolerance(tc.get("raw_text"), tc["numeric_value"])
            diff = got - tc["numeric_value"]
            ok = abs(diff) <= tol + 1e-12
            sign_flip = (not ok and tc["numeric_value"] != 0
                         and abs(abs(got) - abs(tc["numeric_value"])) <= tol + 1e-12
                         and (got < 0) != (tc["numeric_value"] < 0))
            reg["n_match"] += ok
            reg["n_sign_flip"] += sign_flip
            rel = abs(diff) / abs(tc["numeric_value"]) if tc["numeric_value"] else None
            rows.append({
                "regression_id": rid, "row_index": ri,
                "row_type": tc.get("row_type"), "row_label": tc.get("row_label"),
                "published": tc["numeric_value"], "reproduced": got,
                "tolerance": tol, "abs_diff": abs(diff), "rel_diff": rel,
                "verdict": "match" if ok else ("sign_flip" if sign_flip else "mismatch"),
                "result_class": rc,
            })
    return {"factor": factor, "scale_note": scale_note, "rows": rows, "per_reg": per_reg}


def closeness(rows: list[dict]) -> dict[str, int]:
    """How many graded cells fall within each secondary band (``grade.py``'s ``closeness``)."""
    return {f"within_{int(b * 1000)}permille" if b < 0.01 else f"within_{int(b * 100)}pct":
            sum(1 for x in rows if x.get("rel_diff") is not None and x["rel_diff"] <= b)
            for b in PCT_BANDS}


def reproduction_of(reg: dict | None) -> str:
    """One word per regression for the record: what the re-estimation made of it."""
    if reg is None:
        return "not_returned"
    if reg.get("status") != "ok":
        return "not_attempted" if reg.get("method") == "not_attempted" else "failed"
    if not reg.get("n_cells"):
        return "no_cells"
    if reg.get("n_sign_flip"):
        return "sign_flip"
    if reg["n_match"] == reg["n_cells"]:
        return "match"
    return "partial" if reg["n_match"] else "mismatch"


# ── headline regressions by the claim chain (s11) ──────────────────────────────────────────

def headline_links(stated: dict, mp: dict | None, excl: set[str] | None = None) -> dict[str, dict]:
    """The per-paper rule of ``claim_chain_headlines.py`` on the pipeline's claim files: for every
    edge or comparison claim, each ``main`` match of the claim map links its regression →
    ``{regression_id: {"n_main", "claims": set, "pointed"}}``. ``pointed`` when the abstract or
    introduction points to the estimate by number or by reference. Regressions in ``excl`` (the
    tables adjudicated not to be regression tables) are left out."""
    excl = excl or set()
    types = {c["id"]: c.get("type") for c in stated.get("claims") or []}
    head: dict[str, dict] = {}
    for c in (mp or {}).get("claims") or []:
        if types.get(c["id"]) not in ("edge", "comparison"):
            continue
        for x in c.get("matches") or []:
            if x.get("role") != "main" or x["regression_id"] in excl:
                continue
            h = head.setdefault(x["regression_id"], {"n_main": 0, "claims": set(), "pointed": False})
            h["n_main"] += 1; h["claims"].add(c["id"])
            if (x.get("pointed_to") or "none") != "none":
                h["pointed"] = True
    return head


def _key(table, panel, column) -> tuple[str, str, str]:
    return tuple(str(x or "").strip().lower() for x in (table, panel, column))


POINTED_BY_REFERENCE = "points to this estimate by reference"     # the marker sync/export.py writes into a result's ``why``


def headline_links_import(claims: list[dict], tables: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """The same rule on the import shapes: ``claims`` the records of a register-claims document
    (``claim_id``, ``type``, ``results[]`` with ``role``, ``source_table``, ``panel``, ``column``,
    ``carries_stated_number``, ``why``), ``tables`` the records of a tables document
    (``regression_id``, ``table_number``, ``panel``, ``column``). A result links by its table,
    panel and column, since the import carries no regression id. Returns the links per
    regression id and the main results that matched no column."""
    cols: dict[tuple[str, str, str], str] = {}
    for r in tables:
        cols.setdefault(_key(r.get("table_number"), r.get("panel"), r.get("column")), r.get("regression_id"))
    head: dict[str, dict] = {}
    unlinked: list[dict] = []
    for c in claims:
        if c.get("type") not in ("edge", "comparison"):
            continue
        for j, x in enumerate(c.get("results") or []):
            if x.get("role") != "main":
                continue
            rid = cols.get(_key(x.get("source_table"), x.get("panel"), x.get("column")))
            if rid is None:
                unlinked.append({"claim_id": c.get("claim_id"), "index": j, "result_id": x.get("result_id"),
                                 "source_table": x.get("source_table"), "panel": x.get("panel"), "column": x.get("column")})
                continue
            h = head.setdefault(rid, {"n_main": 0, "claims": set(), "pointed": False})
            h["n_main"] += 1; h["claims"].add(c.get("claim_id"))
            if x.get("carries_stated_number") is True or POINTED_BY_REFERENCE in str(x.get("why") or ""):
                h["pointed"] = True
    return head, unlinked


# ── the Metalens computes ──────────────────────────────────────────────────────────────────

def headlines(ctx) -> dict:
    """Edits on the tables document: ``headline``, ``n_main``, ``main_of`` and ``pointed`` per
    regression from the claims document's main results; the paper's ``n_headline``; a finding
    per main result that names no column of the tables."""
    tables = (ctx.record or {}).get("records") or []
    claims_doc = ctx.read("claims")
    claims = [r["field_values"] for r in claims_doc.get("records") or []]
    head, unlinked = headline_links_import(claims, [r["field_values"] for r in tables])
    out: dict = {"records": {}, "paper": {}, "findings": []}
    n_head = 0
    for r in tables:
        fv = r["field_values"]
        h = head.get(fv.get("regression_id"))
        is_head = h is not None
        n_head += is_head
        patch = {"headline": is_head, "n_main": h["n_main"] if h else 0,
                 "main_of": sorted(x for x in h["claims"] if x) if h else [], "pointed": bool(h and h["pointed"])}
        if any(fv.get(k) != v for k, v in patch.items()):
            out["records"][r["id"]] = patch
    out["paper"] = {"n_headline": n_head, "n_main_links": sum(h["n_main"] for h in head.values()), "n_main_unlinked": len(unlinked)}
    by_claim = {r["field_values"].get("claim_id"): r for r in claims_doc.get("records") or []}
    for u in unlinked:
        cr = by_claim.get(u["claim_id"])
        out["findings"].append({"record_id": cr["id"] if cr else None, "document_id": claims_doc.get("document_id"),
                                "path": f"results[{u['index']}]", "code": "main_unlinked", "severity": "warn",
                                "message": f"main result {u['result_id'] or ''} of claim {u['claim_id']} names {u['source_table'] or '?'}"
                                           f"{(' ' + u['panel']) if u['panel'] else ''} column {u['column'] or '?'}, which is no column of the tables"})
    ctx.log(f"headlines: {n_head} of {len(tables)} regressions carry a main estimate; {len(unlinked)} main results unlinked")
    return out


def grade(ctx) -> dict:
    """Edits on the tables document from the reproduced document: per regression
    ``reproduction`` (match, partial, mismatch, sign_flip, failed, not_attempted, not_returned,
    no_cells), ``cells_graded``, ``cells_matched``, ``sign_flips``, ``sample_mismatch`` and
    ``failure_reason``; the paper's totals; a finding per cell that did not reproduce."""
    tables = (ctx.record or {}).get("records") or []
    truth = [r["field_values"] for r in tables]
    rep = ctx.read("reproduced")
    targets = [r["field_values"] for r in rep.get("records") or []]
    cls_of = {fv.get("regression_id"): ("headline" if fv.get("headline") is True else "normal") for fv in truth if fv.get("headline") is not None}
    g = grade_regressions(truth, targets, cls_of=cls_of, apply_scale=bool(ctx.params.get("apply_scale", True)))
    per_reg = g["per_reg"]
    out: dict = {"records": {}, "paper": {}, "findings": []}
    for r in tables:
        fv = r["field_values"]
        reg = per_reg.get(fv.get("regression_id"))
        patch = {"reproduction": reproduction_of(reg), "cells_graded": reg["n_cells"] if reg else 0,
                 "cells_matched": reg["n_match"] if reg else 0, "sign_flips": reg["n_sign_flip"] if reg else 0,
                 "sample_mismatch": bool(reg and reg["sample_mismatch"]), "failure_reason": (reg or {}).get("reason")}
        if any(fv.get(k) != v for k, v in patch.items()):
            out["records"][r["id"]] = patch
        if reg and reg.get("sample_mismatch"):
            out["findings"].append({"record_id": r["id"], "path": "cells", "code": "sample_mismatch", "severity": "warn",
                                    "message": f"N achieved {reg['n_obs_achieved']} against N printed {reg['n_obs_published']}"})
    by_rid = {r["field_values"].get("regression_id"): r for r in tables}
    for row in g["rows"]:
        if row["verdict"] == "match":
            continue
        r = by_rid.get(row["regression_id"])
        if r is None:
            continue
        cells = r["field_values"].get("cells") or []
        i = next((k for k, c in enumerate(cells) if c.get("row_index") == row["row_index"]), None)
        path = f"cells[{i}].numeric_value" if i is not None else "cells"
        if row["verdict"] == "missing":
            msg = f"{row['row_label'] or row['row_type']}: published {row['published']}, no value reproduced"
        else:
            msg = f"{row['row_label'] or row['row_type']}: published {row['published']}, reproduced {row['reproduced']:.6g} (tolerance {row['tolerance']:.3g})"
        out["findings"].append({"record_id": r["id"], "path": path, "code": f"cell_{row['verdict']}",
                                "severity": {"sign_flip": "error", "mismatch": "warn", "missing": "info"}[row["verdict"]], "message": msg})
    n_cells = sum(x["n_cells"] for x in per_reg.values()); n_match = sum(x["n_match"] for x in per_reg.values())
    out["paper"] = {"reproduced_total": len(targets), "reproduced_ok": sum(1 for x in per_reg.values() if x["status"] == "ok"),
                    "n_cells_graded": n_cells, "n_cells_matched": n_match, "match_rate": (n_match / n_cells) if n_cells else None,
                    "n_sign_flips": sum(x["n_sign_flip"] for x in per_reg.values()),
                    "sample_mismatches": sum(1 for x in per_reg.values() if x["sample_mismatch"]),
                    "scale_note": g["scale_note"] or None}
    ctx.log(f"grade: {n_match}/{n_cells} cells at printed precision over {len(per_reg)} regressions; "
            f"sign flips {out['paper']['n_sign_flips']}" + (f"; {g['scale_note']}" if g["scale_note"] else ""))
    return out


def registrations() -> dict[str, dict]:
    return {
        "headlines": {"kind": "compute", "label": "Headline regressions by the claim chain", "produces": "fields",
                      "reads": ["claims"], "fn": headlines},
        "grade": {"kind": "compute", "label": "Grade the reproduced values at printed precision", "produces": "fields",
                  "reads": ["reproduced"], "params": {"apply_scale": {"type": "boolean", "default": True}}, "fn": grade},
    }
