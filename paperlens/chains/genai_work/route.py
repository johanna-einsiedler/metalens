"""Paper-level routing after the extraction — the ``route`` compute, a port of the review
project's ``route.py``, ``pass_checks.triage`` and ``validation_imports.collect_flags`` (the
user's rule, 18 Nov 2026): a paper goes to a coder only when its record contradicts an
inclusion criterion deterministically or when a headline estimate scores high on the triage
(2 per failed check, 1 per warning, 1 per field the readings disagree on, 2 when a reading
lacks it). The compute edits the extraction document's paper fields ``flags`` (the checks'
fails and warns on headline estimates and fails on other main rows, the readings'
disagreements, the criteria the record contradicts — each with its page), ``routed``,
``route_reasons`` and ``triage_score``. It recomputes the record and its issues from the pass
outputs, so the flags are the project's; a coder's own flags (the check workflow's
corrections) are not read in this version.
"""
from __future__ import annotations

import re
from collections import defaultdict

from . import document as D
from . import headlines as H
from . import record as R
from . import text as X
from .checks import value_of

NO_AI = {"no_ai_concurrent", "no_ai_prior", "no_ai_after", "placebo_tool"}
THRESHOLD = 6
SOFT_CODES = {"number_on_page_not_in_quote", "n_not_in_quote"}
EST_LEVEL_SKIP = {"grid_gap", "exhibit_row_unreferenced", "exhibit_column_unreferenced", "exhibit_unverified", "estimand_unmapped", "no_main",
                  "runs_settled", "runs_unsettled", "runs_wording", "not_in_options", "other_without_term", "kind_off_performance",
                  "kind_finding_mismatch", "low_confidence", "no_results", "bad_date", "duplicate_task_id", "duplicate_arm_id",
                  "several_baselines", "no_baseline", "no_control_type", "baseline_ai_role", "control_type_on_treatment",
                  "arm_sample_missing", "n_arm_exceeds_sample", "task_count_differs", "not_in_all_runs", "runs_disagree"}
CRITERION_PATHS = {"C1_no_noai_reference": ("tasks[0].arms[0]", "tasks["), "C2_no_performance_outcome": ("tasks[0].outcomes", "tasks[0].results", "tasks["),
                   "C4_not_identified": ("study.research_design", "study.identification_strategy", "study."),
                   "C5_all_sizes_below_200": ("study.sample_size", "tasks[0].arms[0].n_arm", "tasks[")}


def _num(x):
    x = value_of(x)
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x not in (-1,) and x > 0 else None


def criteria(rec, es=None):
    """[(code, detail)] for one record and its estimands: the inclusion criteria the record contradicts."""
    out = []
    study = rec.get("study") or {}
    tasks = rec.get("tasks") or []
    arms = [a for t in tasks for a in (t.get("arms") or [])]
    if arms and not any(value_of(a.get("is_baseline")) is True and value_of(a.get("control_type")) in NO_AI for a in arms):
        kinds = sorted({str(value_of(a.get("control_type"))) for a in arms if value_of(a.get("is_baseline")) is True})
        out.append(("C1_no_noai_reference", f"no reference arm with a no-AI control type (reference arms: {', '.join(kinds) or 'none'})"))
    classes = {value_of(r.get("outcome_class")) for t in tasks for r in (t.get("results") or [])}
    classes |= {o.get("outcome_class") for o in ((es or {}).get("outcomes") or [])}
    if classes - {None} and "performance" not in classes:
        out.append(("C2_no_performance_outcome", f"outcome classes: {', '.join(sorted(str(c) for c in classes - {None}))}"))
    rd, ident = value_of(study.get("research_design")), value_of(study.get("identification_strategy"))
    if rd == "descriptive" or ident == "none":
        out.append(("C4_not_identified", f"research_design {rd!r}, identification_strategy {ident!r}"))
    sizes = [_num(study.get("sample_size"))] + [_num(a.get("n_arm")) for a in arms] + [_num(r.get("n")) for t in tasks for r in (t.get("results") or [])]
    sizes = [s for s in sizes if s is not None]
    if sizes and max(sizes) < 200:
        out.append(("C5_all_sizes_below_200", f"largest extracted size {max(sizes):g} (study sample_size {value_of(study.get('sample_size'))})"))
    elif not sizes:
        out.append(("C5_size_unknown", "no sample size, arm size or row n anywhere in the record"))
    return out


def estimate_issues(rec, issues):
    """[(estimate_id, code, severity, message, field)]: every issue that addresses one estimate, by its record row or by its id."""
    by_path = {}
    for i, t in enumerate(rec.get("tasks") or []):
        for j, r in enumerate(t.get("results") or []):
            by_path[f"tasks[{i}].results[{j}]"] = r
    known = {r.get("estimate") or r.get("estimate_id") for r in by_path.values()}
    out = []
    for iss in issues:
        path = iss.get("path") or ""
        m = re.match(r"(tasks\[\d+\]\.results\[\d+\])(?:\.([A-Za-z_]+))?", path)
        if m:
            r = by_path.get(m.group(1))
            eid = r and (r.get("estimate") or r.get("estimate_id"))
            field = m.group(2)
        else:
            m = re.match(r"estimates\[([^\]]+)\]", path)
            eid = m.group(1) if m else None
            field = None
        if eid and eid in known:
            out.append((eid, iss.get("code"), iss.get("severity"), iss.get("message"), field))
    return out


def triage(rec, issues):
    """One row per estimate: the fail and warn codes that bear on it, the fields its readings
    disagree on, whether every reading found it, and the score."""
    by_path, rows, meta = {}, {}, {}
    for i, t in enumerate(rec.get("tasks") or []):
        for j, r in enumerate(t.get("results") or []):
            eid = r.get("estimate_id") or r.get("estimate")
            by_path[f"tasks[{i}].results[{j}]"] = eid
            if eid:
                rows.setdefault(eid, []).append(r)
                meta.setdefault(eid, (t.get("task_id"), r.get("estimand_id")))
    agg: dict = defaultdict(lambda: {"fail": set(), "warn": set()})
    for iss in issues:
        if iss["code"] in EST_LEVEL_SKIP or iss["severity"] not in ("fail", "warn"):
            continue
        m = re.match(r"(tasks\[\d+\]\.results\[\d+\])", iss.get("path") or "")
        eid = by_path.get(m.group(1)) if m else None
        if eid is None:
            m = re.match(r"estimates\[([^\]]+)\]", iss.get("path") or "")
            eid = m.group(1) if m else None
        if eid is None or eid not in rows:
            continue
        agg[eid][iss["severity"]].add(iss["code"])
    out = []
    for eid, rs in rows.items():
        alt, found, of = set(), None, None
        for r in rs:
            rn = r.get("_runs") or {}
            alt |= set((rn.get("alt") or {}).keys())
            if rn:
                found, of = rn.get("found"), rn.get("of")
        a = agg.get(eid) or {"fail": set(), "warn": set()}
        try:
            missing = found is not None and of is not None and int(found) < int(of)
        except (TypeError, ValueError):
            missing = False
        est = next((r for r in rs if r.get("role") != "reference"), rs[0])
        out.append({"estimate_id": eid, "task_id": meta[eid][0], "estimand_id": meta[eid][1], "role": est.get("role"), "is_main": value_of(est.get("is_main")),
                    "fails": sorted(a["fail"]), "warns": sorted(a["warn"]), "unstable_fields": sorted(alt), "runs_found": found, "runs_of": of,
                    "score": 2 * len(a["fail"]) + len(a["warn"]) + len(alt) + (2 if missing else 0)})
    return out


def collect_flags(fin, heads, crit):
    """The flags of a paper in the routed order — the checks' fails and warns on headline
    estimates and fails on other main rows (SOFT_CODES left out), the readings' disagreements,
    the criteria the record contradicts — each with group, estimate_id, kind, code, message,
    source, extracted and note."""
    rec, es = fin["record"], fin["passes"]["estimands"]
    by_est = D.contrast_rows(rec)
    estimands = {z.get("estimand_id"): z for z in es.get("estimands") or []}
    heads_ids = set(heads.get("headlines") or [])
    mains = {eid for eid, r in by_est.items() if value_of(r.get("is_main")) is True}

    def group_of(eid):
        r = by_est.get(eid)
        z = estimands.get((r or {}).get("estimand_id"))
        return D.unit_text(es, z) if z else (f"{r.get('_task')} · {value_of(r.get('arm'))} vs {value_of(r.get('vs_arm'))}" if r else "paper")
    flags = []
    for eid, code, sev, msg, field in estimate_issues(rec, fin["issues"]):
        if not (eid in heads_ids or (eid in mains and sev == "fail")) or code in SOFT_CODES:
            continue
        r = by_est.get(eid) or {}
        kind = "unstable" if code in ("runs_disagree", "not_in_all_runs") else ("check_fail" if sev == "fail" else "check_warn")
        m_ = re.match(r"^([a-z_]+) = ([-−]?[\d.,]+)", msg or "")
        fld = m_.group(1) if m_ and m_.group(1) in r else (field if field in r else "effect_value")
        extracted = value_of(r.get(fld))
        if extracted is None and m_:
            extracted = m_.group(2)
        flags.append({"group": group_of(eid), "estimate_id": eid, "kind": kind, "code": code, "message": msg, "source": value_of(r.get("source")),
                      "extracted": None if extracted is None else str(extracted), "note": None})
    for code, detail in crit:
        if code != "C5_size_unknown":
            flags.append({"group": "paper", "estimate_id": None, "kind": "criterion", "code": code, "message": detail, "source": "the record", "extracted": None, "note": None})
    order = {"criterion": 0, "coder_flag": 1, "check_fail": 2, "unstable": 3, "check_warn": 4}
    flags.sort(key=lambda f: (0 if f["group"] == "paper" else 1, f["group"], order.get(f["kind"], 9), str(f["estimate_id"]), str(f["code"])))
    return flags


def study_quote(quotes, prefixes):
    """The record's first quote whose path starts with one of `prefixes`, else its first quote at all."""
    qs = [q for q in quotes or [] if q.get("quote")]
    for pre in prefixes:
        for q in qs:
            if str(q.get("path") or "").startswith(pre):
                return q.get("quote"), q.get("page")
    return (qs[0].get("quote"), qs[0].get("page")) if qs else (None, None)


def flag_rows(fin, heads, crit, pages) -> list[dict]:
    """The ``flags`` table of the extraction contract: ``collect_flags`` with flag_id and page."""
    rec = fin["record"]
    by_est = D.contrast_rows(rec)
    rows = []
    for k, f in enumerate(collect_flags(fin, heads, crit)):
        est = by_est.get(f["estimate_id"]) if f["estimate_id"] else None
        quotes = [(q, pg) for q, pg in (D.row_quotes(est) if est else []) if q]
        if not quotes:
            q, pg = study_quote(fin["quotes"], CRITERION_PATHS.get(f["code"]) or ("study.",))
            quotes = [(q, pg)] if q else []
        rows.append({"flag_id": f"F{k + 1}", "group": f["group"], "estimate_id": f["estimate_id"], "kind": f["kind"], "code": f["code"],
                     "message": f["message"] + (f" — {f['note']}" if f.get("note") else ""), "source": f["source"], "extracted": f["extracted"],
                     "page": X.actual_page(quotes[0][0], D._int(quotes[0][1]) or 0, pages) if quotes else None})
    return rows


def decide(fin, heads, threshold=THRESHOLD, pages=None) -> dict:
    """The paper's routing: the criteria, the triage of its headline estimates, the flags."""
    rec = fin["record"]
    crit = criteria(rec, fin["passes"]["estimands"])
    tri = triage(rec, fin["issues"])
    heads_ids = set(heads.get("headlines") or [])
    hits = sorted(((t["estimate_id"], t["score"]) for t in tri if t["estimate_id"] in heads_ids and t["score"] >= threshold), key=lambda x: -x[1])
    reasons = sorted({code for code, _ in crit if code != "C5_size_unknown"} | ({"triage_headline"} if hits else set()))
    return {"criteria": crit, "triage": tri, "triage_hits": hits, "route_reasons": reasons, "routed": bool(reasons),
            "triage_score": max((t["score"] for t in tri), default=None), "flags": flag_rows(fin, heads, crit, pages or {})}


def route(ctx) -> dict:
    ex, es, em, de = (ctx.read(n) for n in ("exhibits", "estimands", "estimates", "design"))
    P = X.Pages(ctx.pages)
    fin = R.finish(ex, es, em, de, P)
    heads = H.headlines(fin["record"], ctx.reads.get("headline_plan"))
    out = decide(fin, heads, int(ctx.params.get("threshold") or THRESHOLD), P.pages)
    ctx.log(f"route: {'routed' if out['routed'] else 'included as extracted'}; reasons {', '.join(out['route_reasons']) or 'none'}; {len(out['flags'])} flag(s); "
            f"triage max {out['triage_score']}")
    findings = [{"path": "study", "code": code, "severity": "info" if code == "C5_size_unknown" else "warn", "message": detail, "detail": {"source": "criterion"}} for code, detail in out["criteria"]]
    findings += [{"path": f"estimates[{eid}]", "code": "triage_headline", "severity": "warn", "message": f"headline estimate {eid} scores {score}", "detail": {"score": score}} for eid, score in out["triage_hits"]]
    return {"paper": {"flags": out["flags"], "routed": out["routed"], "route_reasons": out["route_reasons"], "triage_score": out["triage_score"]}, "records": {}, "findings": findings}


def registrations() -> dict[str, dict]:
    return {
        "route": {"kind": "compute", "label": "Route the paper: criteria, triage, flags", "produces": "fields",
                  "reads": ["exhibits", "estimands", "estimates", "design", "headline_plan"],
                  "params": {"threshold": {"type": "integer", "default": THRESHOLD}}, "fn": route},
    }
