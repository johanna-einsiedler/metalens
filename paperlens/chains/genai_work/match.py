"""The matchers of the GenAI-at-work chain: how k independent readings of one paper are folded
into the primary the later passes read — a port of the review project's ``stability.py`` (the
alignment of two readings by content, the per-estimate and per-design-cell comparison, the
majority over three readings) and ``match_runs.py`` (the model pairs the estimands the alignment
left unmatched). The first run is the primary; every further run is aligned to it. The primary
output is returned with ``_runs`` annotations on the units that differ (``found``, ``of``,
``alt`` — the contract the record card and the assisted import already read) and, for the
design, the settled cells adopted; the ``stability`` record carries the alignment, the
per-unit statuses, the issues (``runs_disagree``, ``not_in_all_runs``, ``task_count_differs``,
``runs_settled``, ``runs_unsettled``, ``runs_wording``) and the metrics.
"""
from __future__ import annotations

import copy
import difflib
import re
import unicodedata
from collections import Counter, defaultdict

from . import hashes

STUDY_FIELDS = ("scale", "research_design", "identification_strategy", "experimental_design", "unit_of_analysis", "data_source", "period_start_year",
                "period_end_year", "sample_size", "preregistered", "incentivized", "focus_topic", "focus_topic_secondary", "focus_dimension")
TASK_FIELDS = ("task_label", "soc_major_group", "task_structure", "task_embodiment")
ARM_FIELDS = ("control_type", "sample_population_type", "sample_expertise", "sample_representativeness", "ai_model", "ai_role", "is_agentic", "ai_measure")
NUMS = ("n_ref", "mean_ref", "sd_ref", "n_treat", "mean_treat", "sd_treat", "n_obs", "effect_value", "se", "ci_low", "ci_high", "p_value")
TEXT_FIELDS = {"ai_model"}
REASONS = ("not_in_primary", "split_or_merge", "other")
CHUNK = 60                       # B estimands per pairing request: the answer names each of them
MATCH_MAX_TOKENS = 48_000


def norm(s) -> str:
    """The stability normaliser (not the quote one): lower case, a true minus, no significance stars, one space."""
    s = str(s or "").lower().replace("−", "-")
    s = re.sub(r"[\*†‡§]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def sim(a, b) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


# ── the assignment ──────────────────────────────────────────────────────────────────────────

def assignment(S: list[list[float]]) -> list[tuple[int, int]]:
    """The pairs (i, j) that maximise the total of S[i][j], each row and column at most once
    (the Hungarian method on the rectangular matrix; min(rows, cols) pairs). Pure Python, so the
    chain needs no scipy; the same optimum the project's scipy call found."""
    n, m = len(S), len(S[0]) if S else 0
    if not n or not m:
        return []
    flipped = n > m
    if flipped:
        S = [[S[i][j] for i in range(n)] for j in range(m)]
        n, m = m, n
    INF = float("inf")
    cost = [[-S[i][j] for j in range(m)] for i in range(n)]
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [INF] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    pairs = [(p[j] - 1, j - 1) for j in range(1, m + 1) if p[j]]
    if flipped:
        pairs = [(i, j) for j, i in pairs]
    return sorted(pairs)


def greedy(prim, run, text, floor, ident=None, extra=None) -> dict:
    """One-to-one alignment of two lists by text similarity: {primary index: run index}. The
    assignment maximises the total similarity; pairs under the floor are dropped. Among equally
    similar pairs the pair with the same id wins, so identical runs align as identity."""
    if not prim or not run:
        return {}
    same = (lambda p, r: 0.05 if ident and ident(p) == ident(r) else 0.0)
    S = [[sim(text(p), text(r)) + same(p, r) + (extra(p, r) if extra else 0.0) for r in run] for p in prim]
    pairs = [(S[i][j], prim[i]["_i"], run[j]["_i"]) for i, j in assignment(S)]
    return {i: j for sc, i, j in pairs if sc >= floor}


def value_of(x):
    v = x.get("value") if isinstance(x, dict) else x
    return tuple(sorted(map(str, v))) if isinstance(v, list) else v


def align_lists(prim, run, passes, ident, by_order=True, extra=None) -> dict:
    """Several passes with ever looser texts, the unmatched remainder aligned by order when both
    sides have the same number left (two runs number the same things the same way)."""
    out: dict = {}
    for text, floor in passes:
        left_p = [x for x in prim if x["_i"] not in out]
        left_r = [x for x in run if x["_i"] not in set(out.values())]
        out.update(greedy(left_p, left_r, text, floor, ident, extra))
    left_p = [x for x in prim if x["_i"] not in out]
    left_r = [x for x in run if x["_i"] not in set(out.values())]
    if by_order and left_p and len(left_p) == len(left_r):
        out.update({a["_i"]: b["_i"] for a, b in zip(left_p, left_r)})
    return out


FULL = {"", "full sample", "all", "overall", "pooled", "whole sample", "entire sample", "all participants", "total", "none", "main sample", "everyone",
        "all workers", "all subjects"}
DEFAULT_HORIZON = re.compile(r"^(post[- ]?(treatment|deployment|introduction|intervention|period|rollout|adoption)|overall|pooled|none|main|end of (the )?(experiment|study|task))$")


def sg(v) -> str:
    """A subgroup, "" for the full sample however it is worded."""
    return "" if norm(v) in FULL else norm(v)


def hz(v) -> str:
    """A horizon, "" for the default (the post-treatment effect) however it is worded."""
    n = norm(v)
    return "" if n in FULL or DEFAULT_HORIZON.match(n) else n


def exh(v) -> str:
    """An exhibit name as a key: NFKC (ligatures), lower case, no spaces."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(v or ""))).lower()


def src_key(e) -> tuple:
    s = e.get("source") or {}
    if not s.get("exhibit") or exh(s.get("exhibit")) == "text":
        return ("text", str(s.get("page") or ""), "", "")
    return (exh(s.get("exhibit")), norm(s.get("panel")), norm(s.get("row")), norm(s.get("column")))


def align(pes: dict, res: dict) -> dict:
    """The run's ids in terms of the primary's: tasks, arms, outcomes; then the estimands by
    structure and the wording of subgroup and horizon. Both estimands outputs are annotated in
    place (``_i``, ``_s``): hand in copies."""
    for lst in (pes["tasks"], res["tasks"], pes["arms"], res["arms"], pes["outcomes"], res["outcomes"]):
        for i, x in enumerate(lst):
            x["_i"] = i
    tmap = align_lists(pes["tasks"], res["tasks"], [(lambda t: t.get("name", ""), 0.5), (lambda t: f"{t.get('name', '')} {t.get('description', '')} {t.get('sample', '')}", 0.4)],
                       lambda t: t.get("task_id"))
    task_id = {t["_i"]: t["task_id"] for t in pes["tasks"]}
    rtask_id = {t["_i"]: t["task_id"] for t in res["tasks"]}
    tid_map = {rtask_id[j]: task_id[i] for i, j in tmap.items()}
    omap = align_lists(pes["outcomes"], res["outcomes"],
                       [(lambda o: o.get("measure", ""), 0.75), (lambda o: f"{o.get('measure', '')} {o.get('how_measured', '')}", 0.6),
                        (lambda o: f"{o.get('measure', '')} {o.get('how_measured', '')} {o.get('finding_type', '')} {o.get('measure_kind', '')}", 0.45)],
                       lambda o: o.get("outcome_id"), by_order=len(pes["outcomes"]) == len(res["outcomes"]))
    oid_map = {res["outcomes"][j]["outcome_id"]: pes["outcomes"][i]["outcome_id"] for i, j in omap.items()}

    def usage(E, om):
        u = defaultdict(set)
        for x in E["estimands"]:
            o = om.get(x.get("outcome_id"), f"?{x.get('outcome_id')}")
            u[x.get("arm_id")].add(("t", o))
            u[x.get("vs_arm_id")].add(("c", o))
        return u
    pu, ru = usage(pes, {o["outcome_id"]: o["outcome_id"] for o in pes["outcomes"]}), usage(res, oid_map)
    jacc = lambda a, b: (len(a & b) / len(a | b)) if (a or b) else 0.0  # noqa: E731
    use_sim = lambda a, b: 0.5 * jacc(pu.get(a.get("arm_id"), set()), ru.get(b.get("arm_id"), set()))  # noqa: E731
    amap: dict = {}
    arm_passes = [(lambda a: a.get("label", ""), 0.5), (lambda a: f"{a.get('label', '')} {a.get('treatment', '')}", 0.4)]
    for ref in (True, False):
        amap.update(align_lists([a for a in pes["arms"] if bool(a.get("is_reference")) == ref], [a for a in res["arms"] if bool(a.get("is_reference")) == ref],
                                arm_passes, lambda a: a.get("arm_id"), by_order=False, extra=use_sim))
    amap.update(align_lists([a for a in pes["arms"] if a["_i"] not in amap], [a for a in res["arms"] if a["_i"] not in set(amap.values())],
                            arm_passes, lambda a: a.get("arm_id"), extra=use_sim))
    aid_map = {res["arms"][j]["arm_id"]: pes["arms"][i]["arm_id"] for i, j in amap.items()}
    struct = lambda x, tm, am, om: (tm.get(x.get("task_id"), f"?{x.get('task_id')}"), am.get(x.get("arm_id"), f"?{x.get('arm_id')}"),  # noqa: E731
                                    am.get(x.get("vs_arm_id"), f"?{x.get('vs_arm_id')}"), om.get(x.get("outcome_id"), f"?{x.get('outcome_id')}"))
    ident = lambda d: {k: k for k in d}  # noqa: E731
    pid = (ident({t["task_id"] for t in pes["tasks"]}), ident({a["arm_id"] for a in pes["arms"]}), ident({o["outcome_id"] for o in pes["outcomes"]}))
    for x in pes["estimands"]:
        x["_s"] = struct(x, *pid)
    for x in res["estimands"]:
        x["_s"] = struct(x, tid_map, aid_map, oid_map)
    wording = lambda x: (sg(x.get("subgroup")), hz(x.get("horizon")))  # noqa: E731
    emap: dict = {}
    for pks in {x["_s"] for x in pes["estimands"]}:
        if any("?" in str(part) for part in pks):
            continue
        a = [x for x in pes["estimands"] if x["_s"] == pks]
        b = [x for x in res["estimands"] if x["_s"] == pks]
        if not b:
            continue
        for i, x in enumerate(a):
            x["_i"] = i
        for j, y in enumerate(b):
            y["_i"] = j
        bw: dict = {}
        for y in b:
            bw.setdefault(wording(y), []).append(y)
        m: dict = {}
        for x in a:
            cand = [y for y in bw.get(wording(x), []) if y["_i"] not in m.values()]
            if cand:
                m[x["_i"]] = cand[0]["_i"]
        m.update(align_lists([x for x in a if x["_i"] not in m], [y for y in b if y["_i"] not in set(m.values())],
                             [(lambda x: f"{x.get('subgroup') or ''} | {x.get('horizon') or ''}", 0.45)], lambda x: x.get("estimand_id")))
        emap.update({a[i]["estimand_id"]: b[j]["estimand_id"] for i, j in m.items()})
    rev = set(emap.values())
    return {"tasks": tmap, "arms": amap, "outcomes": omap, "tid_map": tid_map, "aid_map": aid_map, "oid_map": oid_map, "estimands": emap,
            "estimands_only_primary": [x["estimand_id"] for x in pes["estimands"] if x["estimand_id"] not in emap],
            "estimands_only_run": [x["estimand_id"] for x in res["estimands"] if x["estimand_id"] not in rev]}


def identity_alignment(es: dict) -> dict:
    """The alignment of two runs that read the same estimands (the engine hands every run the
    primary's): every id maps to itself."""
    ids = lambda key, lst: {x[key]: x[key] for x in es.get(lst) or []}  # noqa: E731
    return {"tid_map": ids("task_id", "tasks"), "aid_map": ids("arm_id", "arms"), "oid_map": ids("outcome_id", "outcomes"),
            "estimands": ids("estimand_id", "estimands"), "estimands_only_primary": [], "estimands_only_run": []}


# ── the estimates ───────────────────────────────────────────────────────────────────────────

def compare_estimates(pem: dict, rem: dict, al: dict) -> tuple[dict, int]:
    """Per primary estimate: stable, numbers_differ (with the differing fields) or missing_in_run;
    plus how many estimates the run has over."""
    rest = defaultdict(list)
    for e in rem["estimates"]:
        rest[e.get("estimand_id")].append(e)
    out: dict = {}
    extras = 0
    rinv = {v: k for k, v in al["estimands"].items()}
    used: set = set()

    def same_val(c, e):
        a, b = c.get("effect_value"), e.get("effect_value")
        if a in (None, "") or b in (None, ""):
            return False
        try:
            fa, fb = float(str(a).replace(",", "")), float(str(b).replace(",", ""))
            return abs(fa - fb) <= 1e-9 * max(1.0, abs(fa), abs(fb))
        except ValueError:
            return norm(a) == norm(b)
    row_key = lambda x: (exh((x.get("source") or {}).get("exhibit")), norm((x.get("source") or {}).get("row")))  # noqa: E731

    def pick(cands, e):
        free = [c for c in cands if id(c) not in used]
        m = next((c for c in free if src_key(c) == src_key(e)), None)
        if m is None:
            m = next((c for c in free if same_val(c, e)), None)
        if m is None and row_key(e)[1]:
            same_row = [c for c in free if row_key(c) == row_key(e)]
            m = same_row[0] if len(same_row) == 1 else None
        return m
    all_run = rem["estimates"]
    for e in pem["estimates"]:
        rid_est = al["estimands"].get(e.get("estimand_id"))
        m = pick(rest.get(rid_est, []) if rid_est else [], e)
        other = False
        if m is None and src_key(e)[0]:
            if src_key(e)[0] == "text":
                free = [c for c in all_run if id(c) not in used and src_key(c)[0] == "text" and same_val(c, e)]
                if len(free) > 1:
                    free = [c for c in free if src_key(c) == src_key(e)]
            else:
                free = [c for c in all_run if id(c) not in used and src_key(c) == src_key(e) and (same_val(c, e) or c.get("effect_value") in (None, ""))]
                if not free:
                    free = [c for c in all_run if id(c) not in used and row_key(c) == row_key(e) and same_val(c, e)]
            if len(free) == 1:
                m, other = free[0], True
        if m is None:
            out[e["estimate_id"]] = {"status": "missing_in_run", "diff": {}}
            continue
        used.add(id(m))
        diff = {k: m.get(k) for k in NUMS + ("role",) if str(e.get(k) if e.get(k) is not None else "") != str(m.get(k) if m.get(k) is not None else "")}
        out[e["estimate_id"]] = {"status": "numbers_differ" if diff else "stable", "diff": diff, "run_estimate": m.get("estimate_id"), "other_estimand": other}
    for eid_r, cands in rest.items():
        extras += sum(1 for c in cands if id(c) not in used and eid_r in rinv)
    return out, extras


# ── the design ──────────────────────────────────────────────────────────────────────────────

def compare_design(pd: dict, rd: dict, al: dict) -> dict:
    out: dict = {"study": {}, "tasks": {}, "arms": {}, "raw": {"study": {}, "tasks": {}, "arms": {}}}
    ps, rs = pd.get("study") or {}, rd.get("study") or {}
    for f in STUDY_FIELDS:
        if f in ps or f in rs:
            out["study"][f] = (value_of(ps.get(f)), value_of(rs.get(f)))
            out["raw"]["study"][f] = rs.get(f)
    ptasks = {t.get("task_id"): t for t in pd.get("tasks") or []}
    rtasks = {t.get("task_id"): t for t in rd.get("tasks") or []}
    rinv_t = {v: k for k, v in al["tid_map"].items()}
    rinv_a = {v: k for k, v in al["aid_map"].items()}
    for tid, t in ptasks.items():
        rt = rtasks.get(rinv_t.get(tid))
        if rt:
            out["tasks"][tid] = {f: (value_of(t.get(f)), value_of(rt.get(f))) for f in TASK_FIELDS if f in t or f in rt}
            out["raw"]["tasks"][tid] = {f: rt.get(f) for f in TASK_FIELDS if f in t or f in rt}
            parms = {a.get("arm_id"): a for a in t.get("arms") or []}
            rarms = {a.get("arm_id"): a for a in rt.get("arms") or []}
            for aid, a in parms.items():
                ra = rarms.get(rinv_a.get(aid))
                if ra:
                    out["arms"][f"{tid}/{aid}"] = {f: (value_of(a.get(f)), value_of(ra.get(f))) for f in ARM_FIELDS if f in a or f in ra}
                    out["raw"]["arms"][f"{tid}/{aid}"] = {f: ra.get(f) for f in ARM_FIELDS if f in a or f in ra}
    return out


def design_agreement(des: dict) -> dict:
    agree = lambda d: (sum(1 for a, b in d.values() if a == b), len(d))  # noqa: E731
    s_a, s_n = agree(des["study"])
    t_a = t_n = a_a = a_n = 0
    for d in des["tasks"].values():
        x, y = agree(d)
        t_a += x
        t_n += y
    for d in des["arms"].values():
        x, y = agree(d)
        a_a += x
        a_n += y
    return {"study": [s_a, s_n], "tasks": [t_a, t_n], "arms": [a_a, a_n]}


def text_same(a, b) -> bool:
    """Two free-text cells name the same thing: equal after normalisation, or one contains the other."""
    x, y = norm(a), norm(b)
    return x == y or (bool(x) and bool(y) and (x in y or y in x))


def majority(pd: dict, per_run: list) -> dict:
    """Design cells over the primary and every run (three or more readings): per cell the votes,
    the adopted value and whether the readings agree, are settled (a majority with a dissent) or
    unsettled (no majority). Free-text cells worded differently count as agreement (wording)."""
    out: dict = {"study": {}, "tasks": {}, "arms": {}}

    def decide(prim_raw, votes_raw, field=None):
        if field in TEXT_FIELDS:
            vals = [value_of(prim_raw)] + [value_of(r) for _, r in votes_raw]
            same = all(text_same(vals[0], v) for v in vals[1:])
            return {"status": "agree" if same else "wording", "adopted": prim_raw, "from": "primary", "votes": {str(v): 1 for v in vals}, "of": len(vals)}
        vals = [(value_of(prim_raw), prim_raw, "primary")] + [(value_of(r), r, name) for name, r in votes_raw]
        c = Counter(v for v, _, _ in vals)
        top, n = c.most_common(1)[0]
        if len(c) == 1:
            return {"status": "agree", "adopted": prim_raw, "from": "primary", "votes": {str(k): v for k, v in c.items()}, "of": len(vals)}
        if n >= 2:
            src = next((raw, name) for v, raw, name in vals if v == top)
            return {"status": "settled", "adopted": src[0], "from": src[1], "votes": {str(k): v for k, v in c.items()}, "of": len(vals)}
        return {"status": "unsettled", "adopted": None, "from": None, "votes": {str(k): v for k, v in c.items()}, "of": len(vals)}
    ps = pd.get("study") or {}
    for f in STUDY_FIELDS:
        votes = [(name, d["raw"]["study"][f]) for name, d in per_run if f in d["raw"]["study"]]
        if f in ps or votes:
            out["study"][f] = decide(ps.get(f), votes, f)
    for t in pd.get("tasks") or []:
        tid = t.get("task_id")
        for f in TASK_FIELDS:
            votes = [(name, d["raw"]["tasks"][tid][f]) for name, d in per_run if tid in d["raw"]["tasks"] and f in d["raw"]["tasks"][tid]]
            if votes:
                out["tasks"].setdefault(tid, {})[f] = decide(t.get(f), votes, f)
        for a in t.get("arms") or []:
            key = f"{tid}/{a.get('arm_id')}"
            for f in ARM_FIELDS:
                votes = [(name, d["raw"]["arms"][key][f]) for name, d in per_run if key in d["raw"]["arms"] and f in d["raw"]["arms"][key]]
                if votes:
                    out["arms"].setdefault(key, {})[f] = decide(a.get(f), votes, f)
    return out


def set_cell(container: dict, f: str, raw, source: str) -> None:
    """A design cell takes the adopted value (the primary's quote stays); raw is the run's cell."""
    v = raw.get("value") if isinstance(raw, dict) else raw
    cur = container.get(f)
    if isinstance(cur, dict):
        cur["value"] = v
        cur["settled_by"] = source
    else:
        container[f] = {"value": v, "settled_by": source}


def apply_majority(de: dict, maj: dict, n_runs: int) -> tuple[list, Counter]:
    """The design output follows the majority: settled cells adopted, ``_runs`` on the study, the
    tasks and the arms, and the issues (runs_settled: an uncertainty marker; runs_unsettled: for a
    coder; runs_wording: free text worded differently)."""
    issues: list = []
    summary: Counter = Counter()

    def apply(container, cells, path):
        alt = {}
        found_min = n_runs + 1
        for f, d in cells.items():
            summary[d["status"]] += 1
            if d["status"] == "agree":
                continue
            others = {k: v for k, v in d["votes"].items() if k != str(value_of(d["adopted"]))} if d["adopted"] is not None else d["votes"]
            alt[f] = [[k, v] for k, v in others.items()]
            if d["status"] == "wording":
                issues.append({"severity": "info", "path": f"{path}.{f}", "code": "runs_wording", "message": "free text worded differently: " + " | ".join(d["votes"])})
                continue
            if d["status"] == "settled":
                if d["from"] != "primary":
                    set_cell(container, f, d["adopted"], d["from"])
                found_min = min(found_min, d["votes"][str(value_of(d["adopted"]))])
                issues.append({"severity": "info", "path": f"{path}.{f}", "code": "runs_settled",
                               "message": f"{value_of(d['adopted'])!r} by {d['votes'][str(value_of(d['adopted']))]} of {d['of']} readings; also "
                                          + ", ".join(f"{k} ({v})" for k, v in others.items())})
            else:
                found_min = 1
                issues.append({"severity": "fail", "path": f"{path}.{f}", "code": "runs_unsettled",
                               "message": f"{d['of']} readings, no majority: " + ", ".join(f"{k} ({v})" for k, v in d["votes"].items())})
        container["_runs"] = {"found": (n_runs + 1 if found_min == n_runs + 2 else found_min), "of": n_runs + 1, "alt": alt}
    apply(de.setdefault("study", {}), maj["study"], "study")
    for ti, t in enumerate(de.get("tasks") or []):
        tid = t.get("task_id")
        apply(t, maj["tasks"].get(tid, {}), f"tasks[{ti}]")
        for ai, a in enumerate(t.get("arms") or []):
            apply(a, maj["arms"].get(f"{tid}/{a.get('arm_id')}", {}), f"tasks[{ti}].arms[{ai}]")
    return issues, summary


def apply_two_runs(de: dict, des: dict) -> list:
    """Two readings only: ``_runs`` with the run's value as the alternative and a runs_disagree
    warning per differing cell (the project's two-run annotation)."""
    issues: list = []

    def alt(pairs):
        return {f: [[b if not isinstance(b, tuple) else list(b), 1]] for f, (a, b) in pairs.items() if a != b}

    def apply(container, pairs, path, matched=True):
        container["_runs"] = {"found": 2 if matched else 1, "of": 2, "alt": alt(pairs)}
        for f in container["_runs"]["alt"]:
            issues.append({"severity": "warn", "path": f"{path}.{f}", "code": "runs_disagree", "message": f"the runs give {pairs[f][0]!r} and {pairs[f][1]!r}"})
    apply(de.setdefault("study", {}), des["study"], "study")
    for ti, t in enumerate(de.get("tasks") or []):
        tid = t.get("task_id")
        apply(t, des["tasks"].get(tid, {}), f"tasks[{ti}]", tid in des["tasks"])
        for ai, a in enumerate(t.get("arms") or []):
            key = f"{tid}/{a.get('arm_id')}"
            apply(a, des["arms"].get(key, {}), f"tasks[{ti}].arms[{ai}]", key in des["arms"])
    return issues


# ── the model pairs the estimands the alignment left unmatched ──────────────────────────────

def describe(E: dict, x: dict) -> str:
    """One estimand in words, from its own reading's lists."""
    tasks = {t.get("task_id"): t for t in E.get("tasks") or []}
    arms = {a.get("arm_id"): a for a in E.get("arms") or []}
    outs = {o.get("outcome_id"): o for o in E.get("outcomes") or []}
    t, a, v, o = tasks.get(x.get("task_id")) or {}, arms.get(x.get("arm_id")) or {}, arms.get(x.get("vs_arm_id")) or {}, outs.get(x.get("outcome_id")) or {}
    arm = lambda z: f"{z.get('label') or z.get('arm_id') or '?'}" + (f" ({z.get('treatment')})" if z.get("treatment") else "")  # noqa: E731
    unit = f" ({o.get('unit')})" if o.get("unit") else ""
    return (f"{x.get('estimand_id')} | task: {t.get('name') or x.get('task_id')} | arms: {arm(a)} vs {arm(v)} | outcome: {o.get('measure') or x.get('outcome_id')}{unit}"
            f" | subgroup: {x.get('subgroup') or 'full sample'} | horizon: {x.get('horizon') or 'post-treatment'} | {str(x.get('statement') or '')[:220]}")


def unmatched(pes: dict, res: dict, al: dict) -> tuple[list, list]:
    only_p, only_r = set(al["estimands_only_primary"]), set(al["estimands_only_run"])
    A = [x for x in pes.get("estimands") or [] if x.get("estimand_id") in only_p]
    B = [x for x in res.get("estimands") or [] if x.get("estimand_id") in only_r]
    return A, B


def user_text(title, pes: dict, res: dict, A: list, B: list) -> str:
    return (f"Title: {title}\n\n# List A (first reading): {len(A)} unpaired estimands\n\n" + "\n".join(describe(pes, x) for x in A)
            + f"\n\n# List B (second reading): {len(B)} unpaired estimands\n\n" + "\n".join(describe(res, x) for x in B))


def validate(ans: dict, A: list, B: list) -> tuple[list, list, list]:
    """Pairs within the lists, each id once; every B id with a reason."""
    a_ids, b_ids = {x["estimand_id"] for x in A}, {x["estimand_id"] for x in B}
    pairs, rejected, used_a, used_b = [], [], set(), set()
    for p in ans.get("pairs") or []:
        a, b = str(p.get("a")), str(p.get("b"))
        if a in a_ids and b in b_ids and a not in used_a and b not in used_b:
            pairs.append({"a": a, "b": b, "why": p.get("why")})
            used_a.add(a)
            used_b.add(b)
        else:
            rejected.append(p)
    reasons = {str(u.get("b")): u for u in ans.get("b_unpaired") or []}
    run_only = []
    for b in sorted(b_ids - used_b):
        u = reasons.get(b) or {}
        reason = u.get("reason") if u.get("reason") in REASONS else "other"
        run_only.append({"estimand_id": b, "reason": reason, "why": u.get("why") or ("not given" if not u else "")})
    return pairs, rejected, run_only


def apply_match(al: dict, pairs: dict) -> int:
    """The model's pairs on top of the deterministic alignment (in place); how many were added."""
    taken_b = set(al["estimands"].values())
    n = 0
    for a, b in pairs.items():
        if a not in al["estimands"] and b not in taken_b:
            al["estimands"][a] = b
            taken_b.add(b)
            n += 1
    only_p = set(al["estimands_only_primary"]) - set(al["estimands"])
    only_r = set(al["estimands_only_run"]) - taken_b
    al["estimands_only_primary"] = [x for x in al["estimands_only_primary"] if x in only_p]
    al["estimands_only_run"] = [x for x in al["estimands_only_run"] if x in only_r]
    return n


def pair_leftovers(ctx, pes: dict, res: dict, al: dict) -> dict:
    """The estimands the alignment left unmatched on both sides, paired by the model (one request
    per CHUNK of the run's list, the primary's list whole each time); the pairs are applied to
    the alignment. Returns the pairing record."""
    A, B = unmatched(pes, res, al)
    out = {"a_unpaired": len(A), "b_unpaired": len(B), "pairs": {}, "model_pairs": [], "rejected": [], "run_only": [], "primary_only": [x["estimand_id"] for x in A]}
    if not B:
        return out
    if not A:
        out["run_only"] = [{"estimand_id": x["estimand_id"], "reason": "not_in_primary", "why": "the first reading has no unpaired estimand left"} for x in B]
        return out
    system, schema = hashes.prompt("match_runs"), hashes.schema("match_runs")
    ans = {"pairs": [], "b_unpaired": []}
    for i in range(0, len(B), CHUNK):
        part = ctx.call(system, user_text(ctx.title or "", pes, res, A, B[i:i + CHUNK]), schema, max_tokens=MATCH_MAX_TOKENS)
        ans["pairs"] += part.get("pairs") or []
        ans["b_unpaired"] += part.get("b_unpaired") or []
    pairs, rejected, run_only = validate(ans, A, B)
    out.update(pairs={p["a"]: p["b"] for p in pairs}, model_pairs=pairs, rejected=rejected, run_only=run_only,
               primary_only=[x["estimand_id"] for x in A if x["estimand_id"] not in {p["a"] for p in pairs}])
    n = apply_match(al, out["pairs"])
    ctx.log(f"estimands: {len(A)} and {len(B)} unpaired after the alignment; the model paired {n}, {len(rejected)} pair(s) rejected")
    return out


# ── the matchers ────────────────────────────────────────────────────────────────────────────

def _public(al: dict) -> dict:
    return {k: v for k, v in al.items() if k in ("tid_map", "aid_map", "oid_map", "estimands", "estimands_only_primary", "estimands_only_run")}


def match_exhibits(ctx, runs: list[dict]) -> dict:
    """The exhibits pass has no alignment in the project: the first reading is the primary; the
    stability record counts the tables and figures each run found and those named alike."""
    primary = copy.deepcopy(runs[0])
    names = lambda ex, key: {exh(x.get("name") or x.get("id")) for x in ex.get(key) or []}  # noqa: E731
    per_run = []
    for r in runs[1:]:
        per_run.append({"tables": {"primary": len(runs[0].get("tables") or []), "run": len(r.get("tables") or []), "both": len(names(runs[0], "tables") & names(r, "tables"))},
                        "figures": {"primary": len(runs[0].get("figures") or []), "run": len(r.get("figures") or []), "both": len(names(runs[0], "figures") & names(r, "figures"))}})
    return {"primary": primary, "stability": {"runs": per_run, "issues": []}}


def match_estimands(ctx, runs: list[dict]) -> dict:
    """Every further reading aligned to the first by content (tasks, arms, outcomes, estimands);
    what the alignment leaves unmatched on both sides goes to the model once. The primary is the
    first reading, its tasks annotated with ``_runs``; the stability record carries each
    alignment, the pairing and the metrics."""
    primary = copy.deepcopy(runs[0])
    per_run, issues = [], []
    n = len(runs)
    matched_tasks: Counter = Counter()
    for i, r in enumerate(runs[1:], 1):
        pes, res = copy.deepcopy(runs[0]), copy.deepcopy(r)
        al = align(pes, res)
        pairing = pair_leftovers(ctx, pes, res, al)
        for tid in al["tid_map"].values():
            matched_tasks[tid] += 1
        per_run.append({"run": i, "alignment": _public(al), "pairing": pairing,
                        "metrics": {"tasks_primary": len(pes["tasks"]), "tasks_run": len(res["tasks"]), "tasks_matched": len(al["tasks"]),
                                    "arms_primary": len(pes["arms"]), "arms_matched": len(al["arms"]), "outcomes_primary": len(pes["outcomes"]), "outcomes_matched": len(al["outcomes"]),
                                    "estimands_both": len(al["estimands"]), "estimands_only_primary": len(al["estimands_only_primary"]), "estimands_only_run": len(al["estimands_only_run"]),
                                    "estimands_model_paired": len(pairing["pairs"]), "estimands_run_only_missed": sum(x["reason"] == "not_in_primary" for x in pairing["run_only"])}})
        if len(pes["tasks"]) != len(res["tasks"]):
            issues.append({"severity": "fail", "path": "tasks", "code": "task_count_differs", "message": f"{len(pes['tasks'])} tasks in the primary run, {len(res['tasks'])} in run {i + 1}"})
    for ti, t in enumerate(primary.get("tasks") or []):
        found = 1 + matched_tasks.get(t.get("task_id"), 0)
        t["_runs"] = {"found": found, "of": n, "alt": {}}
        if found < n:
            issues.append({"severity": "warn", "path": f"tasks[{ti}]", "code": "not_in_all_runs", "message": f"task {t.get('task_id')} has no counterpart in {n - found} of {n} runs"})
    return {"primary": primary, "stability": {"runs": per_run, "issues": issues}}


def match_estimates(ctx, runs: list[dict]) -> dict:
    """Every further reading's estimates compared with the first's, estimand by estimand (the runs
    read the same estimands, so the alignment is the identity): stable, numbers_differ or
    missing_in_run per primary estimate, ``_runs`` on each with the run's numbers as the
    alternative; the stability record carries the statuses and the counts."""
    primary = copy.deepcopy(runs[0])
    es = ctx.read("estimands")
    al = identity_alignment(es)
    per_run, issues = [], []
    n = len(runs)
    by_est: dict = defaultdict(lambda: {"found": 1, "alt": defaultdict(list)})
    for i, r in enumerate(runs[1:], 1):
        est, extras = compare_estimates(runs[0], r, al)
        st = Counter(v["status"] for v in est.values())
        per_run.append({"run": i, "estimates": est,
                        "metrics": {"estimates_primary": len(est), "estimates_stable": st["stable"], "estimates_differ": st["numbers_differ"], "estimates_missing": st["missing_in_run"],
                                    "estimates_extra_in_run": extras, "estimates_other_estimand": sum(1 for v in est.values() if v.get("other_estimand")),
                                    "share_unstable_estimates": ((st["numbers_differ"] + st["missing_in_run"]) / len(est)) if est else None}})
        for eid, s in est.items():
            if s["status"] != "missing_in_run":
                by_est[eid]["found"] += 1
            for k, v in s["diff"].items():
                by_est[eid]["alt"][k].append(v)
    for ei, e in enumerate(primary.get("estimates") or []):
        b = by_est.get(e.get("estimate_id")) or {"found": 1, "alt": {}}
        alt = {k: [[v, c] for v, c in Counter(map(str, vs)).items()] for k, vs in b["alt"].items()}
        e["_runs"] = {"found": b["found"], "of": n, "alt": alt}
        if alt:
            issues.append({"severity": "warn", "path": f"estimates[{ei}]", "code": "runs_disagree",
                           "message": f"estimate {e.get('estimate_id')}: another run gives " + ", ".join(f"{k}={vs[0][0]}" for k, vs in alt.items())})
        if b["found"] < n:
            issues.append({"severity": "warn", "path": f"estimates[{ei}]", "code": "not_in_all_runs", "message": f"estimate {e.get('estimate_id')} has no counterpart in {n - b['found']} of {n} runs"})
    return {"primary": primary, "stability": {"runs": per_run, "issues": issues}}


def match_design(ctx, runs: list[dict]) -> dict:
    """The design cells over every reading (the runs read the same tasks and arms, so the
    alignment is the identity): with three or more readings each cell follows the majority —
    settled cells adopted into the primary with ``settled_by``, unsettled ones flagged for a
    coder; with two, the differing cells are warned about. ``_runs`` on the study, the tasks and
    the arms; the stability record carries the per-run comparison and the majority."""
    primary = copy.deepcopy(runs[0])
    es = ctx.read("estimands")
    al = identity_alignment(es)
    per_run = [(f"run{i}", compare_design(runs[0], r, al)) for i, r in enumerate(runs[1:], 1)]
    comparison = [{"run": i, "agreement": design_agreement(des)} for i, (_, des) in enumerate(per_run, 1)]
    if len(per_run) >= 2:
        maj = majority(runs[0], per_run)
        issues, summary = apply_majority(primary, maj, len(per_run))
        stability = {"runs": comparison, "majority": maj, "design_majority": {"runs": [name for name, _ in per_run], **dict(summary)}, "issues": issues}
    else:
        issues = apply_two_runs(primary, per_run[0][1]) if per_run else []
        stability = {"runs": comparison, "issues": issues}
    return {"primary": primary, "stability": stability}


def registrations() -> dict[str, dict]:
    return {
        "match_exhibits": {"kind": "match", "label": "Fold the exhibits readings (the first is the primary)", "fn": match_exhibits},
        "match_estimands": {"kind": "match", "label": "Align the estimands readings by content; the model pairs the rest", "calls_per_paper": 1, "fn": match_estimands},
        "match_estimates": {"kind": "match", "label": "Compare the estimates readings estimand by estimand", "fn": match_estimates},
        "match_design": {"kind": "match", "label": "Settle the design cells by majority", "fn": match_design},
    }
