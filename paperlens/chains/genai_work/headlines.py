"""One headline estimate per unit — outcome kind × task × arm pair — of a record (the user's
rule, 18 Nov 2026), a port of the review project's ``headlines.py``, and the composite groups
of ``validation_imports.composite_groups``.

Candidates are the unit's contrast rows. Out first: interaction coefficients and rows at a
pre-treatment horizon. Then tiers, best first: a composite or overall measure when a quality or
time unit has one beside other measures (the components then leave the pooled models), the
full sample before a subgroup, an intention-to-treat estimate before a usage-adjusted one.
Within the best tier the abstract's claim decides when the headline plan names exactly one
candidate; otherwise a fixed order: role (main, supporting, robustness), main-text exhibit
before appendix, the unit's primary horizon, the first outcome the paper defines, the first
estimate id. Every unit records which rule chose.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from . import taxonomy as T

FULL = {"", "full sample", "all", "overall", "pooled", "whole sample", "entire sample", "all participants", "total", "none", "main sample", "everyone",
        "all workers", "all subjects"}
PRE = re.compile(r"\bpre[- ]?(treatment|period|test|intervention|deployment)|baseline period|before (the )?(treatment|ai|intervention)", re.I)
APPENDIX = re.compile(r"appendix|\b[A-Z]\d{1,2}\b|supplement|online", re.I)
COMPOSITE = re.compile(r"\b(composite|index|overall|aggregate|aggregated|combined|summary score|total score|global (score|rating)|average of|mean of|sum of)\b", re.I)
ROLE_RANK = {"main": 0, "supporting": 1, "robustness": 2}
AGGREGATE = re.compile(r"\b(composite|overall|aggregate|aggregated|combined|average|mean|total|summary|global|sum)\b", re.I)
COMPOSITE_KINDS = {"productivity_quality", "productivity_time"}
ITT = {"itt", "not_applicable", None, ""}
RULE = "composite > full sample > ITT; the abstract's claim when it names one; else role, main text, primary horizon, first outcome, first estimate"


def val(c):
    return c.get("value") if isinstance(c, dict) else c


def nat(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s or ""))]


def plan_mains(plan, task, kind, arm, vs):
    """The estimate ids the plan's decision names for the unit that holds this arm pair (its `main` list), or None."""
    if not plan:
        return None
    dec = {d["unit"]: d for d in plan.get("decisions") or []}
    for u in plan.get("units") or []:
        if u.get("task") == task and u.get("category") == kind and any(list(p) == [arm, vs] for p in u.get("arm_pairs") or []):
            d = dec.get(u["id"]) or {}
            return list(d.get("main") or []), d.get("branch")
    return None


def composites(rows, outcomes):
    """The composite outcomes of a unit: measures whose name or description says so, when the unit has other measures."""
    oids = {val(r.get("outcome")) for r in rows}
    if len(oids) < 2:
        return set()
    out = set()
    for o in oids:
        oc = outcomes.get(o) or {}
        text = f"{val(oc.get('measure')) or ''} {val(oc.get('how_measured')) or ''} " + " ".join(str(val(r.get("measure")) or "") for r in rows if val(r.get("outcome")) == o)
        if COMPOSITE.search(text):
            out.add(o)
    return out


def units_of(rec, plan=None, left_out=None):
    """The units of a record with their headline: [{kind, task, arm, vs_arm, headline, rule, tier, candidates, composite, excluded, plan}].
    A unit with an AI-alone arm on either side is not among them (no person did the work on one
    side: outside the with-versus-without-AI analysis); it is appended to `left_out` when a list is given."""
    out = []
    for t in rec.get("tasks") or []:
        tid = t.get("task_id")
        outcomes = {val(o.get("outcome_id")): o for o in t.get("outcomes") or []}
        alone = {val(a.get("arm_id")) for a in t.get("arms") or [] if T.ai_alone_arm(a)}
        groups = defaultdict(list)
        for r in t.get("results") or []:
            if not val(r.get("vs_arm")):
                continue
            kind = val(r.get("finding_type")) or val(r.get("outcome_class")) or "other"
            groups[(kind, val(r.get("arm")), val(r.get("vs_arm")))].append(r)
        for (kind, arm, vs), rows in sorted(groups.items(), key=lambda kv: (nat(kv[0][1]), nat(kv[0][2]), kv[0][0])):
            if arm in alone or vs in alone:
                if left_out is not None:
                    left_out.append({"kind": kind, "task": tid, "arm": arm, "vs_arm": vs, "candidates": len(rows)})
                continue
            unit = {"kind": kind, "task": tid, "arm": arm, "vs_arm": vs, "candidates": len(rows), "headline": None, "rule": None, "tier": None,
                    "composite": None, "excluded": [], "plan": None}
            cands = [r for r in rows if val(r.get("contrast_type")) != "interaction" and val(r.get("effect_type")) != "interaction"
                     and not PRE.search(str(val(r.get("horizon")) or ""))]
            if not cands:
                unit["rule"] = "no candidate (interaction or pre-treatment rows only)"
                out.append(unit)
                continue
            comp = composites(cands, outcomes) if kind in COMPOSITE_KINDS else set()
            tier = lambda r: (0 if val(r.get("outcome")) in comp else 1 if comp else 0,  # noqa: E731
                              0 if (val(r.get("subgroup")) or "").strip().lower() in FULL else 1,
                              0 if val(r.get("estimand_type")) in ITT else 1)
            best = min(tier(r) for r in cands)
            top = [r for r in cands if tier(r) == best]
            unit["tier"] = {"composite": best[0] == 0 and bool(comp), "full_sample": best[1] == 0, "itt": best[2] == 0}
            if comp and best[0] == 0:
                unit["composite"] = sorted(comp)[0] if len(comp) == 1 else sorted(comp)
                unit["excluded"] = [r.get("estimate_id") for r in rows if val(r.get("outcome")) not in comp and r.get("estimate_id")]
            pm = plan_mains(plan, tid, kind, arm, vs)
            chosen, rule = None, None
            if pm is not None:
                ids, branch = pm
                hit = [r for r in top if r.get("estimate_id") in set(ids)]
                unit["plan"] = {"branch": branch, "named": len(ids), "in_record": sum(1 for i in ids if any(r.get("estimate_id") == i for r in rows)), "in_tier": len(hit)}
                if len(hit) == 1:
                    chosen, rule = hit[0], "claim"
            if chosen is None:
                hz = Counter(str(val(r.get("horizon")) or "") for r in top).most_common()
                top_n = max(n for _, n in hz)
                primary = sorted([h for h, n in hz if n == top_n], key=nat)[0]
                top.sort(key=lambda r: (ROLE_RANK.get(r.get("role"), 3), 1 if APPENDIX.search(str(val(r.get("source")) or "")) else 0,
                                        0 if str(val(r.get("horizon")) or "") == primary else 1, nat(val(r.get("outcome"))), nat(r.get("estimate_id"))))
                chosen, rule = top[0], ("composite" if unit["tier"]["composite"] else "order")
            unit["headline"], unit["rule"] = chosen.get("estimate_id"), rule
            unit["outcome"], unit["measure"], unit["horizon"] = val(chosen.get("outcome")), val(chosen.get("measure")), val(chosen.get("horizon"))
            out.append(unit)
    return out


def headlines(rec, plan=None) -> dict:
    """The project's ``headlines.json`` for a record: the units, the headline ids, the rows the
    composite tier excludes and the units left out for an AI-alone arm."""
    left_out: list = []
    units = units_of(rec, plan, left_out)
    return {"rule": RULE, "units": units, "headlines": [u["headline"] for u in units if u["headline"]],
            "excluded": sorted({e for u in units for e in u["excluded"]}), "ai_alone_units": left_out}


GENERIC = {"score", "scores", "scale", "rating", "ratings", "rated", "index", "overall", "average", "mean", "total", "composite", "combined",
           "aggregate", "aggregated", "summary", "measure", "measures", "level", "rate", "number", "percent", "percentage", "share", "task",
           "tasks", "final", "global", "self", "reported", "perceived", "with", "from", "that", "this", "their", "between", "across", "based"}


def composite_groups(es):
    """The outcomes the extraction lists that aggregate other listed outcomes — an index, a
    composite, an overall or average score — with the components found among the outcomes of
    the same tasks: a measure the index's description names, or one sharing the index's own
    noun. Returns ({index_id: [component ids]}, {component_id: [index ids]})."""
    outs = es.get("outcomes") or []
    tasks_of = defaultdict(set)
    for x in es.get("estimands") or []:
        tasks_of[x.get("outcome_id")].add(x.get("task_id"))
    words = lambda t: {w for w in re.findall(r"[a-z][a-z\-]{3,}", str(t or "").lower()) if w not in GENERIC}  # noqa: E731
    is_index = lambda o: COMPOSITE.search(str(o.get("measure") or "")) and not re.search(r"\boverall,", str(o.get("measure") or ""), re.I)  # noqa: E731
    indexes = [o for o in outs if is_index(o)]
    groups, part_of = {}, defaultdict(list)
    for o in indexes:
        oid = o.get("outcome_id")
        desc = str(o.get("how_measured") or "").lower()
        noun = words(o.get("measure"))
        aggregate = bool(AGGREGATE.search(str(o.get("measure") or "")))
        comps = []
        for c in outs:
            cid = c.get("outcome_id")
            if cid == oid or c in indexes or (tasks_of[oid] and tasks_of[cid] and not tasks_of[oid] & tasks_of[cid]):
                continue
            cw = words(c.get("measure"))
            hits = sum(1 for w in cw if re.search(rf"\b{re.escape(w)}", desc))
            named = bool(cw) and hits >= max(1, -(-2 * len(cw) // 3))
            shared = aggregate and bool(noun & cw) and c.get("measure_kind") == o.get("measure_kind") and c.get("outcome_class") == o.get("outcome_class")
            if named or shared:
                comps.append(cid)
                part_of[cid].append(oid)
        groups[oid] = comps
    return groups, part_of
