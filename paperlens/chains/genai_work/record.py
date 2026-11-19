"""From the four pass outputs to the record — a port of the review project's ``split.py``
``finish``: the derived cells and the canonical ids (idempotent), the assembly (the study from
pass d; each task of pass a with its fields from d and its arms; per estimate of pass c a
treatment row and, per estimand's main estimate, a reference row), the quote verification
(every cell quote and every row quote checked against the page text, snapped to the paper's
wording when edited, table cells found apart), the ``_check`` marks and every check. The pass
outputs are copied first: the stored outputs stay as the passes wrote them.
"""
from __future__ import annotations

import copy
import re
from collections import Counter

from . import checks as K
from . import passes as PS
from . import taxonomy as T
from . import text as X

ROLES = ("main", "supporting", "robustness")
NUMERIC = ("integer", "number")
ALIASES = {"effect_type": {"difference": "mean_difference", "mean difference": "mean_difference", "difference in means": "mean_difference",
                           "raw difference": "mean_difference", "coefficient": "regression_coefficient", "ols coefficient": "regression_coefficient",
                           "did": "difference_in_differences", "diff-in-diff": "difference_in_differences", "percent": "percent_change",
                           "percentage change": "percent_change", "odds ratio": "ratio", "risk ratio": "ratio", "cohen's d": "standardized_difference",
                           "hedges' g": "standardized_difference", "standardized mean difference": "standardized_difference"}}
OTHER_RE = re.compile(r"^\s*other\s*[\(:\-—]\s*(.+?)\s*\)?\s*$", re.I)
_EST_NUMS = ("n_ref", "mean_ref", "sd_ref", "n_treat", "mean_treat", "sd_treat", "n_obs", "effect_value", "se", "ci_low", "ci_high", "p_value")


def normalise_code(level, name, value, proposed=None):
    """A category value the way the taxonomy spells it: "other (rank-biserial correlation)" ->
    other with the term in proposed; a plain synonym ("difference") -> its code; anything else
    as given (checks.categories flags it)."""
    v = T.field(name, level)
    if not isinstance(value, str) or not v.get("options"):
        return value, proposed
    codes = {o["code"] for o in v["options"]}
    if value in codes:
        return value, proposed
    m = OTHER_RE.match(value)
    if m and "other" in codes:
        return "other", proposed or m.group(1)
    alias = ALIASES.get(name, {}).get(value.strip().lower().replace("_", " "))
    if alias in codes:
        return alias, proposed
    low = value.strip().lower().replace(" ", "_")
    return (low if low in codes else value), proposed


def cell(level, name, value, quote=None, page=None, proposed=None):
    """A cell in the record's shape: wrapped with its quote (and proposed term) where the
    taxonomy says so, plain otherwise; category values normalised."""
    v = T.field(name, level)
    value, proposed = normalise_code(level, name, value, proposed)
    if not T.wrapped(v):
        return value
    d = {"value": value}
    if v["ev"] == "value" and not v["qg"]:
        d["quote"], d["page"] = quote or "", int(page or 0)
    if v["open"]:
        d["proposed"] = proposed
    return d


def _blank(level):
    return {v["field"]: ({"value": None, "quote": "", "page": 0} if T.wrapped(v) and v["ev"] == "value" and not v["qg"] else
                         {"value": None, "proposed": None} if T.wrapped(v) else None) for v in T.model_fields(level)}


def _source(src):
    src = src or {}
    parts = [src.get("exhibit"), f"panel {src['panel']}" if src.get("panel") else None,
             f"col {src['column']}" if src.get("column") else None, f"row {src['row']}" if src.get("row") else None]
    return ", ".join(p for p in parts if p) or None


def figure_quote(ex, src, est):
    """For an estimate read off a figure: the figure's printed numbers that carry the estimate's
    values, as one quote (at most six entries), so the numbers can be checked against the page
    the way a table row is."""
    want = ((src or {}).get("exhibit") or "").strip().lower()
    for fg in ex.get("figures") or []:
        if (fg.get("id") or "").strip().lower() != want or not fg.get("numbers"):
            continue
        row = ((src or {}).get("row") or "").strip().lower()
        rend = set()
        for k in _EST_NUMS:
            v = est.get(k)
            if v is not None:
                try:
                    rend |= {r for r in X._renderings(v) if len(r) >= 2 and r not in ("0", "00")}
                except Exception:  # noqa: BLE001
                    rend.add(str(v))
        by_row = [n for n in fg["numbers"] if row and row in str(n.get("label") or "").lower()]
        by_val = [n for n in fg["numbers"] if n not in by_row and any(r in str(n.get("value") or "") for r in rend)]
        picked, out = [], f"{fg['id']}:"
        for n in (by_row + by_val) or fg["numbers"]:
            entry = f" {n.get('label')}: {n.get('value')};"
            if len(X.NUM_TOKEN.findall(out + entry)) > 12:
                break
            picked.append(n)
            out += entry
        return out.rstrip(";"), fg.get("page")
    return None, None


def group_quotes(ex, est):
    """The other rows of a continuation group (one row label spanning several arms or sub-rows,
    e.g. the SD printed on the line below the mean): their printed cells as further quote parts."""
    src = est.get("source") or {}
    r = K.resolve_source(ex, src, K.first_number(est.get("effect_value"), est.get("cell_as_printed"), est.get("effect_reported")))
    if r.get("kind") != "tables" or r.get("row_i") is None:
        return []
    rows = r["table"]["panels"][r["panel_i"]]["rows"]
    lab = r["row_i"]
    while lab > 0 and rows[lab] and not str(rows[lab][0]).strip():
        lab -= 1
    idx = K.continuation_rows(rows, lab)
    if len(idx) < 2:
        return []
    pg = r["table"].get("page") or src.get("page")
    out = []
    for j in idx:
        if j == r["row_i"]:
            continue
        txt = " ".join(str(c).strip() for c in rows[j] if str(c).strip())
        if txt:
            out.append((txt, pg))
    return out


def _task_runs(ta: dict, td: dict):
    """The task's ``_runs`` from the estimands matcher (found in how many readings) and the
    design matcher (the cells the readings disagree on)."""
    a, d = ta.get("_runs"), td.get("_runs")
    if not a and not d:
        return None
    if not a:
        return d
    if not d:
        return a
    return {"found": min(a.get("found", 0), d.get("found", 0)), "of": max(a.get("of", 0), d.get("of", 0)), "alt": {**(a.get("alt") or {}), **(d.get("alt") or {})}}


def assemble(es, em, de, ex=None):
    """The usual record from the passes: the study from d; each task of a with its fields from
    d and its arms (a's label, reference flag, control type, n and what it got over d's sample
    and AI fields); per estimate of c a treatment row, and per estimand's main estimate a
    reference row with the reference arm's printed statistics."""
    outs = {o["outcome_id"]: o for o in es.get("outcomes") or []}
    by_est = {e["estimand_id"]: e for e in es.get("estimands") or []}
    ref_type = {a["arm_id"]: a.get("control_type") for a in es.get("arms") or [] if a.get("is_reference")}
    fig_ids = {(fg.get("id") or "").strip().lower() for fg in (ex or {}).get("figures") or []}
    d_tasks = {t.get("task_id"): t for t in de.get("tasks") or []}
    rec = {"study": de.get("study") or _blank("study"), "tasks": []}
    for ta in es.get("tasks") or []:
        tid = ta["task_id"]
        td = d_tasks.get(tid) or _blank("task")
        t = {k: v for k, v in td.items() if k not in ("arms", "_runs")}
        t["task_id"] = tid
        if not t.get("task_name"):
            t["task_name"] = ta.get("name")
        used = {e.get("outcome_id") for e in es.get("estimands") or [] if e.get("task_id") == tid}
        t["outcomes"] = [{"outcome_id": o["outcome_id"], "outcome_name": o.get("measure"), "how_measured": o.get("how_measured"),
                          "outcome_unit": o.get("unit"), "outcome_direction": o.get("direction"), "outcome_type": o.get("outcome_class"),
                          "outcome_finding": o.get("finding_type"), "outcome_measure_kind": o.get("measure_kind"),
                          "outcome_measured_on": o.get("measured_on")}
                         for o in es.get("outcomes") or [] if o["outcome_id"] in used]
        d_arms = {a.get("arm_id"): a for a in (td.get("arms") or [])}
        t["arms"] = []
        for aa in es.get("arms") or []:
            if tid not in (aa.get("task_ids") or []):
                continue
            a = dict(d_arms.get(aa["arm_id"]) or _blank("arm"))
            a["arm_id"], a["arm_label"], a["is_baseline"] = aa["arm_id"], aa.get("label"), bool(aa.get("is_reference"))
            a["control_type"] = cell("arm", "control_type", aa.get("control_type") if aa.get("is_reference") else None, aa.get("quote"), aa.get("page"))
            a["n_arm"] = cell("arm", "n_arm", aa.get("n"), aa.get("n_quote") or aa.get("quote"), aa.get("n_page") or aa.get("page"))
            a["arm_description"] = cell("arm", "arm_description", aa.get("treatment"), aa.get("quote"), aa.get("page"))
            if not (a.get("sample_population") or {}).get("value"):
                a["sample_population"] = cell("arm", "sample_population", aa.get("sample"), aa.get("quote"), aa.get("page"))
            a.setdefault("confidence", {"arm": {"level": "medium", "notes": "from the estimand pass"}})
            t["arms"].append(a)
        arm_quote = {a["arm_id"]: (a.get("quote"), a.get("page")) for a in es.get("arms") or []}
        t["results"] = []
        ref_done = set()
        for est in em.get("estimates") or []:
            e = by_est.get(est.get("estimand_id"))
            if not e or e.get("task_id") != tid:
                continue
            o = outs.get(e.get("outcome_id")) or {}
            role = est.get("role") if est.get("role") in ROLES else "supporting"
            base = {"measure": o.get("measure"), "measure_unit": o.get("unit"), "outcome_class": o.get("outcome_class"),
                    "finding_type": cell("result", "finding_type", o.get("finding_type"), proposed=o.get("finding_type_proposed")),
                    "measure_kind": cell("result", "measure_kind", o.get("measure_kind"), proposed=o.get("measure_kind_proposed")),
                    "direction": o.get("direction"),
                    "subgroup": e.get("subgroup") or "full sample", "horizon": e.get("horizon"), "measured_on": o.get("measured_on"),
                    "statistic": None, "cluster_level": est.get("cluster_level"), "estimated": False, "source": _source(est.get("source")),
                    "outcome": e.get("outcome_id"), "estimate": est.get("estimate_id"), "estimand_id": e["estimand_id"],
                    "confidence": {"results": {"level": "medium", "notes": f"{role}; mapped from {(est.get('source') or {}).get('exhibit')}"}}}
            key = (e.get("vs_arm_id"), e.get("outcome_id"), e.get("subgroup"), e.get("horizon"))
            has_ref = any(est.get(k) is not None for k in ("n_ref", "mean_ref", "sd_ref"))
            fq, fpg = figure_quote(ex or {}, est.get("source"), est)
            exq, expg = (fq, fpg or est.get("page")) if fq else (est.get("quote"), est.get("page"))
            parts = [(exq, expg)] + group_quotes(ex or {}, est) + ([(est["text_quote"], est.get("text_page"))] if est.get("text_quote") else [])
            runs = est.get("_runs")
            est = dict(est, quote=" | ".join(q for q, _ in parts if q), page=expg, _parts=parts)
            if key not in ref_done and (role == "main" or has_ref):
                ref_done.add(key)
                q, pg = (est.get("quote"), est.get("page")) if has_ref else arm_quote.get(e.get("vs_arm_id"), (None, None))
                ref = dict(base)
                ref.update({"arm": e.get("vs_arm_id"), "vs_arm": None, "contrast_type": cell("result", "contrast_type", None),
                            "estimand_type": None, "n": est.get("n_ref"), "n_obs": None, "mean": est.get("mean_ref"), "sd": est.get("sd_ref"),
                            "effect_reported": None, "effect_value": None, "effect_type": cell("result", "effect_type", None), "effect_units": None,
                            "se": None, "ci_low": None, "ci_high": None, "p_value": None, "p_reported": None, "is_main": role == "main",
                            "result_notes": "reference arm" + ("" if has_ref else "; no reference statistics printed"),
                            "stats_quote": q, "stats_page": pg, "effect_quote": None, "effect_page": None, "estimate_id": est.get("estimate_id"), "role": "reference",
                            "_quote_parts": est.get("_parts") if has_ref else []})
                t["results"].append(ref)
            p_value, p_reported = est.get("p_value"), est.get("p_reported")
            if p_reported and re.search(r"[<>≤≥*]", str(p_reported)):
                p_value = None
            ct = e.get("contrast_type")
            etype = est.get("estimand_type") or e.get("estimand_type") or ("itt" if ct in ("ai_vs_no_ai", "pre_vs_post", "ai_vs_ai", "ai_variant", "dose")
                                                                           else "not_applicable" if ct in ("association", "vs_benchmark") else None)
            ev, lo, hi = est.get("effect_value"), est.get("ci_low"), est.get("ci_high")
            notes = f"{role}: {est.get('specification') or ''}".strip() + (f" — {est['why_role']}" if role == "main" and est.get("why_role") else "")
            reversed_sign = ref_type.get(e.get("vs_arm_id")) == "no_ai_after" and est.get("sign_as_printed") == "effect_of_removal"
            if reversed_sign:
                ev = -ev if ev is not None else None
                lo, hi = (-hi if hi is not None else None), (-lo if lo is not None else None)
                notes += " — sign reversed: the paper reports the effect of removal"
            corrected = False
            if not reversed_sign and est.get("effect_type") == "percent_change" and ev is not None \
                    and isinstance(est.get("mean_treat"), (int, float)) and isinstance(est.get("mean_ref"), (int, float)) and est["mean_ref"] != 0:
                implied = (est["mean_treat"] - est["mean_ref"]) / abs(est["mean_ref"]) * 100
                tol = max(2.0, 0.25 * abs(implied))
                if abs(abs(implied) - abs(ev)) <= tol and implied * ev < 0 and abs(ev) > tol / 2:
                    ev = -ev
                    lo, hi = (-hi if hi is not None else None), (-lo if lo is not None else None)
                    notes += f" — sign corrected: printed {est['effect_value']:+g}, the means go from {est['mean_ref']:g} to {est['mean_treat']:g} ({implied:+.1f}%)"
                    corrected = True
            estimated = reversed_sign or corrected or ((est.get("source") or {}).get("exhibit") or "").strip().lower() in fig_ids
            row = dict(base)
            row.update({"arm": e.get("arm_id"), "vs_arm": e.get("vs_arm_id"), "contrast_type": cell("result", "contrast_type", ct, proposed=e.get("contrast_type_proposed")),
                        "estimand_type": etype, "n": est.get("n_treat"), "n_obs": est.get("n_obs"), "mean": est.get("mean_treat"),
                        "sd": est.get("sd_treat"), "effect_reported": est.get("effect_reported"), "effect_value": ev,
                        "effect_type": cell("result", "effect_type", est.get("effect_type"), proposed=est.get("effect_type_proposed")), "effect_units": est.get("effect_units"),
                        "se": est.get("se"), "ci_low": lo, "ci_high": hi, "p_value": p_value,
                        "p_reported": p_reported, "is_main": role == "main", "estimated": estimated,
                        "_cell_as_printed": est.get("cell_as_printed"), "_se_cell_as_printed": est.get("se_cell_as_printed"),
                        "result_notes": notes,
                        "stats_quote": est.get("quote"), "stats_page": est.get("page"), "effect_quote": est.get("quote"), "effect_page": est.get("page"),
                        "estimate_id": est.get("estimate_id"), "role": role, "_quote_parts": est.get("_parts")})
            if runs:
                row["_runs"] = runs
            t["results"].append(row)
        t.setdefault("confidence", {"task": {"level": "medium", "notes": "from the estimand pass"}, "complexity": {"level": "low", "notes": "not scored"}})
        tr = _task_runs(ta, td)
        if tr:
            t["_runs"] = tr
        rec["tasks"].append(t)
    return rec


# ── the quotes ──────────────────────────────────────────────────────────────────────────────

def verify_quotes(rec, pages, rpages):
    """Check every quote against the page text; snap an edited quote to the paper's passage.
    Edits the record in place (snapped quotes replace the model's, the model's kept under
    ``model_quote``). Returns the list of quote records."""
    out = []
    for path, holder, qk, pk, backed in K.quote_sites(rec):
        q, pg = holder.get(qk) or "", int(holder.get(pk) or 0)
        st = X.check_quote(q, pg, pages)
        rec_q = {"path": path, "quote": q, "page": pg, "check": st}
        if st in ("not_found", "empty"):
            hit = X.snap_quote(q, pg, rpages) if st == "not_found" else None
            if hit and any(v["type"] in NUMERIC and val is not None and X.number_in_quote(val, q) and not X.number_in_quote(val, hit[0])
                           for v, val in backed):
                hit = None
            if hit:
                holder["model_" + qk] = q
                holder[qk], holder[pk] = hit[0], hit[1]
                rec_q.update(model_quote=q, quote=hit[0], page=hit[1], check="snapped")
            else:
                tp = X.table_cells(q, pg, pages)
                if tp is not None:
                    rec_q["check"], rec_q["page"] = "table_cells", tp
                    holder[pk] = tp
        out.append(rec_q)
    return out


def verify_parts(rec, quotes, pages, rpages):
    """A row quote made of parts — an exhibit's numbers and the sentence that states them — is
    found when each part is: its quote record then says ``fragments``."""
    by_path = {q["path"]: q for q in quotes}
    for i, t in enumerate(rec.get("tasks") or []):
        for j, r in enumerate(t.get("results") or []):
            parts = r.get("_quote_parts") or []
            if len(parts) < 2:
                continue
            ok = all(PS.check_passage({"q": q, "p": pg}, pages, rpages, "q", "p")["check"] in X.FOUND for q, pg in parts if q)
            combined = " | ".join(q for q, _ in parts if q)
            for grp, (qk, pk) in T.quote_groups().items():
                if r.get(qk) is None:
                    continue
                r[qk] = combined
                r.pop("model_" + qk, None)
                q = by_path.get(f"tasks[{i}].results[{j}].{grp}")
                if q:
                    q["quote"] = combined
                    if ok:
                        q["check"] = "fragments"
                        q.pop("model_quote", None)


def runs_issues(rec):
    """The readings' disagreements on the result rows, from the ``_runs`` annotations the matchers
    (or the seeder) put on the estimates: a row another reading gave other numbers for
    (``runs_disagree``), a row a reading did not find (``not_in_all_runs``)."""
    out = []
    for i, t in enumerate(rec.get("tasks") or []):
        for j, r in enumerate(t.get("results") or []):
            rn = r.get("_runs")
            if not rn or not K.value_of(r.get("vs_arm")):
                continue
            eid = r.get("estimate_id") or r.get("estimate")
            alt = rn.get("alt") or {}
            if alt:
                out.append({"severity": "warn", "path": f"tasks[{i}].results[{j}]", "code": "runs_disagree",
                            "message": f"estimate {eid}: another reading gives " + ", ".join(f"{k}={vs[0][0]}" for k, vs in sorted(alt.items()) if vs)})
            found, of = rn.get("found"), rn.get("of")
            if isinstance(found, int) and isinstance(of, int) and found < of:
                out.append({"severity": "warn", "path": f"tasks[{i}].results[{j}]", "code": "not_in_all_runs",
                            "message": f"estimate {eid} has no counterpart in {of - found} of {of} readings"})
    return out


def mark_checks(rec, quotes):
    """``_check`` on every wrapped cell with a quote: what the review shows beside the value."""
    qcheck = {q["path"]: q["check"] for q in quotes}
    for path, v, c, _ in K.cells(rec):
        if isinstance(c, dict) and c.get("quote") is not None and path in qcheck:
            c["_check"] = qcheck[path]


# ── finish ──────────────────────────────────────────────────────────────────────────────────

def finish(ex, es, em, de, P: X.Pages) -> dict:
    """The record from the four pass outputs (copied; the derivations and the canonical ids
    applied), its quotes verified, every check run: ``{record, quotes, issues, exhibit_coverage,
    unmatched_reasons, passes: {exhibits, estimands, estimates, design}}`` — the passes as the
    checks and the document builder read them (canonical ids)."""
    ex, es, em, de = (copy.deepcopy(x) for x in (ex, es, em, de))
    pages, rpages = P.pages, P.rpages
    PS.derive_findings(es); PS.derive_contrast(es); PS.derive_roles(em)
    PS.canon_ids(es, em, de)
    rec = assemble(es, em, de, ex)
    quotes = verify_quotes(rec, pages, rpages)
    verify_parts(rec, quotes, pages, rpages)
    mark_checks(rec, quotes)
    issues = K.record_issues(rec, quotes, pages)
    pass_issues, pass_summary = K.pass_issues(ex, es, em)
    issues += pass_issues + K.cell_checks(ex, em) + runs_issues(rec)
    corrected = {r.get("estimate_id") for t in rec["tasks"] for r in t["results"] if "sign corrected:" in (r.get("result_notes") or "")}
    est_of = lambda i: (re.match(r"estimates\[([^\]]+)\]", i["path"]) or [None, None])[1]  # noqa: E731
    issues = [i for i in issues if not (i["code"] == "sign_vs_text" and est_of(i) in corrected)]
    unverified = {est_of(i) for i in issues if i["code"] == "sign_vs_text" and est_of(i)}
    for t in rec["tasks"]:
        for r in t["results"]:
            if r.get("estimate_id") in unverified and r.get("role") != "reference" and "sign unverified" not in (r.get("result_notes") or ""):
                r["result_notes"] = (r.get("result_notes") or "") + " — sign unverified: the sentence that states it says the opposite"
    reasons = Counter((x.get("reason") or "unstated") for x in em.get("unmatched_estimands") or [])
    return {"record": rec, "quotes": quotes, "issues": issues, "exhibit_coverage": pass_summary["exhibit_coverage"], "unmatched_reasons": dict(reasons),
            "quotes_failed": sum(q["check"] not in X.FOUND for q in quotes),
            "issues_fail": sum(i["severity"] == "fail" for i in issues), "issues_warn": sum(i["severity"] == "warn" for i in issues),
            "passes": {"exhibits": ex, "estimands": es, "estimates": em, "design": de}}
