"""The deterministic checks of the GenAI-at-work chain, pure functions over the record and the
pass outputs — a port of the review project's ``checks.py`` (the record: quotes found, numbers
in their quotes, the structure the rules require, categories in the taxonomy, plausibility
ranges, the effect against the arms' means, Hedges' g, GRIM) and ``pass_checks.py`` (the pass
files: do the cells the estimates cite exist in the transcribed exhibits, is every number where
the estimate says, which rows and columns of a table in use were never extracted, which cells of
the estimand grid are empty, which estimates duplicate a cell or each other, and whether the
reported statistics agree with each other, with the table and with the sentence that states
them). Each issue is ``{path, code, severity, message}``. No scipy: the Student t tail is a
regularised incomplete beta.
"""
from __future__ import annotations

import difflib
import math
import re
from collections import Counter, defaultdict

from . import taxonomy as T
from . import text as X
from .match import NUMS, norm

NUMERIC = ("integer", "number")
NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)*(?:\.\d+)?")
STAT_ROW = re.compile(r"^\s*(observations?|n\b|obs\.?\b|num(ber)? of|no\. of|r[-\s]?2|r[-\s]?squared|adj\.?\s*r|constant|intercept|mean of (the )?dep|f[-\s]?stat|controls?\b|fixed effects|\w+ fe\b|clusters?\b|sample\b|notes?\b|log[-\s]?lik|aic\b|bic\b|wald|chi)", re.I)
SPEC_COL = re.compile(r"^\s*(se|s\.?e\.?|std\.?|standard error|p[-\s]?value|p\b|t[-\s]?stat|t\b|z\b|ci\b|95|n\b|obs|df|\(se\))", re.I)


def issue(sev, path, code, msg):
    return {"severity": sev, "path": path, "code": code, "message": msg}


def value_of(cell):
    """The plain value of a cell, wrapped or not."""
    if isinstance(cell, dict) and "value" in cell:
        return cell["value"]
    return cell


# ── the record's cells and quotes ───────────────────────────────────────────────────────────

def cells(rec):
    """Every cell of a record as (path, variable, cell, parent) — study fields, then per task,
    its fields, arms and results."""
    out = []
    study = rec.get("study") or {}
    for v in T.model_fields("study"):
        out.append((f"study.{v['field']}", v, study.get(v["field"]), study))
    for i, t in enumerate(rec.get("tasks") or []):
        for v in T.model_fields("task"):
            out.append((f"tasks[{i}].{v['field']}", v, t.get(v["field"]), t))
        for j, a in enumerate(t.get("arms") or []):
            for v in T.model_fields("arm"):
                out.append((f"tasks[{i}].arms[{j}].{v['field']}", v, a.get(v["field"]), a))
        for j, r in enumerate(t.get("results") or []):
            for v in T.model_fields("result"):
                out.append((f"tasks[{i}].results[{j}].{v['field']}", v, r.get(v["field"]), r))
    return out


def quote_sites(rec):
    """Every quote of a record: (path, holder dict, quote key, page key, the values it backs)."""
    out = []
    for path, v, cell, parent in cells(rec):
        if T.wrapped(v) and v["ev"] == "value" and not v["qg"] and isinstance(cell, dict) and cell.get("quote"):
            out.append((path, cell, "quote", "page", [(v, cell.get("value"))]))
    for i, t in enumerate(rec.get("tasks") or []):
        for j, r in enumerate(t.get("results") or []):
            for grp, (qk, pk) in T.quote_groups().items():
                if r.get(qk):
                    backed = [(v, r.get(v["field"])) for v in T.model_fields("result") if v["qg"] == grp]
                    out.append((f"tasks[{i}].results[{j}].{grp}", r, qk, pk, backed))
    return out


# ── numbers in quotes ───────────────────────────────────────────────────────────────────────

SOFT = {"se", "ci_low", "ci_high", "p_value", "n", "n_obs"}


def numbers_in_quotes(rec, pages=None):
    issues = []
    for path, holder, qk, pk, backed in quote_sites(rec):
        for v, val in backed:
            text = holder.get(qk) or ""
            if holder.get(qk + "_cells"):
                text = text + " \n " + holder[qk + "_cells"]
            if v["type"] in NUMERIC and val is not None and not X.number_in_quote(val, text):
                at = path if path.endswith(v["field"]) else f"{path}:{v['field']}"
                if v["field"] in SOFT and pages is not None and X.number_on_page(val, int(holder.get(pk) or 0), pages):
                    issues.append({"path": at, "code": "number_on_page_not_in_quote", "severity": "warn",
                                   "message": f"{v['field']} = {val} is on the cited page but not in the quote"})
                elif v["field"] in ("n", "n_obs") and pages is not None:
                    issues.append({"path": at, "code": "n_not_in_quote", "severity": "warn",
                                   "message": f"{v['field']} = {val} is not in the quote (counts are often stated elsewhere)"})
                else:
                    issues.append({"path": at, "code": "number_not_in_quote", "severity": "fail",
                                   "message": f"{v['field']} = {val} does not appear in its quote"})
    return issues


# ── structure ───────────────────────────────────────────────────────────────────────────────

def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def estimand_key(task_id, r):
    return (task_id, value_of(r.get("arm")), value_of(r.get("vs_arm")), (value_of(r.get("measure")) or "").strip().lower(),
            (value_of(r.get("subgroup")) or "full sample").strip().lower(), (value_of(r.get("horizon")) or "").strip().lower())


NO_AI_CONTROLS = {None, "no_ai_concurrent", "no_ai_prior", "no_ai_after", "placebo_tool", "external_norm", "none"}
CONTRAST_FOR = {"no_ai_concurrent": {"ai_vs_no_ai"}, "no_ai_prior": {"pre_vs_post", "ai_vs_no_ai"}, "no_ai_after": {"pre_vs_post", "ai_vs_no_ai"},
                "placebo_tool": {"ai_vs_no_ai"}, "other_ai": {"ai_vs_ai", "ai_variant", "dose"},
                "expert_benchmark": {"vs_benchmark"}, "external_norm": {"vs_benchmark"}, "ai_alone": {"human_ai_vs_ai"}}
ROW_OWN_CONTRASTS = {"interaction", "association", "other", None}


def structure(rec):
    issues = []

    def fail(path, code, msg):
        issues.append({"path": path, "code": code, "severity": "fail", "message": msg})

    def warn(path, code, msg):
        issues.append({"path": path, "code": code, "severity": "warn", "message": msg})

    study = rec.get("study") or {}
    tasks = rec.get("tasks") or []
    if not any(t.get("results") for t in tasks):
        fail("tasks", "no_results", "no result row was extracted")
    for f in ("period_start_year", "period_end_year"):
        y = _num(value_of(study.get(f)))
        if y is not None and not 1990 <= y <= 2035:
            warn(f"study.{f}", "bad_date", f"{f} {y!r} is not a year")
    for f in ("period_start_month", "period_end_month"):
        m = _num(value_of(study.get(f)))
        if m is not None and not 1 <= m <= 12:
            warn(f"study.{f}", "bad_date", f"{f} {m!r} is not a month (1-12)")
    sample_size = _num(value_of(study.get("sample_size")))
    task_ids = [value_of(t.get("task_id")) for t in tasks]
    if len(set(task_ids)) != len(task_ids):
        fail("tasks", "duplicate_task_id", f"task ids repeat: {task_ids}")
    for i, t in enumerate(tasks):
        at = f"tasks[{i}]"
        tid = value_of(t.get("task_id"))
        arms = t.get("arms") or []
        results = t.get("results") or []
        arm_ids = [value_of(a.get("arm_id")) for a in arms]
        if len(set(arm_ids)) != len(arm_ids):
            fail(f"{at}.arms", "duplicate_arm_id", f"arm ids repeat: {arm_ids}")
        baselines = [a for a in arms if value_of(a.get("is_baseline")) is True]
        base_ids = {value_of(a.get("arm_id")) for a in baselines}
        if len(baselines) > 1:
            off = sorted({str(value_of(r.get("vs_arm"))) for r in results if value_of(r.get("vs_arm")) not in base_ids | {None}})
            if off:
                warn(f"{at}.arms", "several_baselines", f"{len(baselines)} reference arms and rows compared with a non-reference arm: {off[:4]}")
        contrasts = {value_of(r.get("contrast_type")) for r in results}
        if not baselines and contrasts & {"ai_vs_no_ai", "pre_vs_post"}:
            fail(f"{at}.arms", "no_baseline", "a no-AI contrast is reported but no arm is the baseline")
        ref_types = {value_of(a.get("arm_id")): value_of(a.get("control_type")) for a in baselines}
        for a in baselines:
            ref_type = value_of(a.get("control_type"))
            if ref_type is None:
                warn(f"{at}.arms", "no_control_type", "the reference arm has no control_type")
            if ref_type in NO_AI_CONTROLS and value_of(a.get("ai_role")) not in (None, "none"):
                warn(f"{at}.arms", "baseline_ai_role", "a no-AI reference arm has an AI role")
        for k, a in enumerate(arms):
            if value_of(a.get("is_baseline")) is not True and value_of(a.get("control_type")) is not None:
                fail(f"{at}.arms[{k}]", "control_type_on_treatment", "control_type is set on an arm that is not the reference arm")
            if _num(value_of(a.get("n_arm"))) is not None and value_of(a.get("sample_population_type")) is None:
                warn(f"{at}.arms[{k}]", "arm_sample_missing", "the arm has people but no population type")
        n_arms = [_num(value_of(a.get("n_arm"))) for a in arms]
        if sample_size and sample_size > 0 and value_of(study.get("experimental_design")) == "across_person" \
                and arms and all(n is not None for n in n_arms) and sum(n_arms) > sample_size * 1.02:
            fail(f"{at}.arms", "n_arm_exceeds_sample", f"sum of n_arm {sum(n_arms)} exceeds sample_size {sample_size}")
        by_arm = {value_of(a.get("arm_id")): a for a in arms}
        base_rows = {}
        mains = {}
        for j, r in enumerate(results):
            rat = f"{at}.results[{j}]"
            aid, vs = value_of(r.get("arm")), value_of(r.get("vs_arm"))
            if aid not in by_arm:
                fail(rat, "arm_unknown", f"arm {aid!r} is not an arm of the task")
            if vs is not None and vs not in by_arm:
                fail(rat, "vs_arm_unknown", f"vs_arm {vs!r} is not an arm of the task")
            if vs is not None and vs == aid:
                fail(rat, "vs_self", "vs_arm equals arm")
            eff = {k: _num(value_of(r.get(k))) for k in ("effect_value", "se", "ci_low", "ci_high", "p_value")}
            if vs is None:
                if any(x is not None for x in eff.values()) or value_of(r.get("effect_reported")):
                    fail(rat, "baseline_with_effect", "a baseline row (no vs_arm) carries effect fields")
                if value_of(r.get("contrast_type")) is not None:
                    warn(rat, "baseline_contrast", "a baseline row has a contrast_type")
                k = (value_of(r.get("measure")) or "").strip().lower(), (value_of(r.get("subgroup")) or "full sample").strip().lower(), (value_of(r.get("horizon")) or "").strip().lower()
                base_rows.setdefault((aid, k), r)
            else:
                ct = value_of(r.get("contrast_type"))
                if ct is None:
                    warn(rat, "no_contrast_type", "a treatment row without contrast_type")
                elif ref_types.get(vs) in CONTRAST_FOR and ct not in CONTRAST_FOR[ref_types[vs]] | ROW_OWN_CONTRASTS:
                    warn(rat, "contrast_mismatch", f"contrast_type {ct!r} against a reference arm of type {ref_types[vs]!r}")
                if eff["effect_value"] is not None and value_of(r.get("estimand_type")) is None:
                    warn(rat, "no_estimand_type", "a treatment row with an effect but no estimand_type")
            n, n_arm = _num(value_of(r.get("n"))), _num(value_of((by_arm.get(aid) or {}).get("n_arm")))
            n_obs = _num(value_of(r.get("n_obs")))
            if n is not None and n_obs is not None and n_obs < n:
                fail(rat, "n_obs_below_n", f"n_obs {n_obs} is below n {n}")
            if n is not None and n_arm is not None and n > n_arm * 1.02:
                warn(rat, "n_exceeds_arm", f"n {n} exceeds the arm's n_arm {n_arm}")
            if eff["ci_low"] is not None and eff["ci_high"] is not None and eff["ci_low"] > eff["ci_high"]:
                fail(rat, "ci_reversed", "ci_low is above ci_high")
            if eff["effect_value"] is not None and eff["ci_low"] is not None and eff["ci_high"] is not None \
                    and not (eff["ci_low"] - 1e-9 <= eff["effect_value"] <= eff["ci_high"] + 1e-9):
                fail(rat, "effect_outside_ci", "effect_value lies outside its interval")
            if eff["p_value"] is not None and not (0 <= eff["p_value"] <= 1):
                fail(rat, "p_out_of_range", f"p_value {eff['p_value']}")
            if value_of(r.get("is_main")) is True:
                k = estimand_key(tid, r)
                if k in mains:
                    fail(rat, "duplicate_main", "a second main row for the same estimand")
                mains[k] = j
        for j, r in enumerate(results):
            rat = f"{at}.results[{j}]"
            vs = value_of(r.get("vs_arm"))
            if vs is None:
                continue
            k = (value_of(r.get("measure")) or "").strip().lower(), (value_of(r.get("subgroup")) or "full sample").strip().lower(), (value_of(r.get("horizon")) or "").strip().lower()
            b = base_rows.get((vs, k))
            m_t, m_c = _num(value_of(r.get("mean"))), _num(value_of((b or {}).get("mean")))
            if m_t is not None and b is None:
                warn(rat, "no_baseline_row", "the arm mean has no baseline row with the same measure, subgroup and horizon")
            ev = _num(value_of(r.get("effect_value")))
            if m_t is not None and m_c is not None and ev is not None and value_of(r.get("effect_type")) == "mean_difference" \
                    and (m_t - m_c) * ev < 0 and abs(m_t - m_c) > 1e-9:
                fail(rat, "sign_disagrees", f"effect_value {ev} has the opposite sign of mean difference {m_t - m_c:g}")
    return issues


# ── plausibility ────────────────────────────────────────────────────────────────────────────

DIFF_TYPES = {"mean_difference", "regression_coefficient", "difference_in_differences", "interaction", "percent_change", "elasticity"}
COUNT_KINDS = {"output_count", "error_count"}


def _decimals(v, text):
    """Decimals of the number as printed in `text` (the shortest rendering found), else of the float itself."""
    for s in sorted(X._renderings(float(v)), key=len):
        if "." in s and re.search(r"(?<![\d.,])" + re.escape(s) + r"(?![\d])", text or ""):
            return len(s.split(".")[1])
    s = f"{float(v):g}"
    return len(s.split(".")[1]) if "." in s else 0


def grim_ok(mean, n, d):
    """Whether a mean of n integers can round to `mean` at d decimals (Brown & Heathers 2017); True when uninformative."""
    if n is None or n < 2 or d is None or n >= 10 ** d:
        return True
    k = round(mean * n)
    return any(abs(kk / n - mean) <= 0.5 * 10 ** -d + 1e-9 for kk in (k - 1, k, k + 1))


def hedges_g(m_t, m_c, s_t, s_c, n_t, n_c):
    if None in (m_t, m_c, s_t, s_c, n_t, n_c) or n_t < 2 or n_c < 2 or s_t < 0 or s_c < 0:
        return None
    pooled = ((n_t - 1) * s_t ** 2 + (n_c - 1) * s_c ** 2) / (n_t + n_c - 2)
    if pooled <= 0:
        return None
    return (m_t - m_c) / pooled ** 0.5 * (1 - 3 / (4 * (n_t + n_c) - 9))


def plausibility(rec):
    """Ranges per field, the p-value against the interval, the effect against the two arms'
    means on its own scale, Hedges' g from the arms' statistics, GRIM on means of integer counts."""
    issues = []

    def add(sev, path, code, msg):
        issues.append({"path": path, "code": code, "severity": sev, "message": msg})

    for ti, t in enumerate(rec.get("tasks") or []):
        at = f"tasks[{ti}]"
        results = t.get("results") or []
        base_rows = {}
        for r in results:
            if value_of(r.get("vs_arm")) is None:
                k = (value_of(r.get("measure")) or "").strip().lower(), (value_of(r.get("subgroup")) or "full sample").strip().lower(), (value_of(r.get("horizon")) or "").strip().lower()
                base_rows.setdefault((value_of(r.get("arm")), k), r)
        for j, r in enumerate(results):
            rat = f"{at}.results[{j}]"
            se, sd, n, n_obs = (_num(value_of(r.get(k))) for k in ("se", "sd", "n", "n_obs"))
            ev, lo, hi, p = (_num(value_of(r.get(k))) for k in ("effect_value", "ci_low", "ci_high", "p_value"))
            m_t, et = _num(value_of(r.get("mean"))), value_of(r.get("effect_type"))
            if se is not None and se <= 0:
                if se == 0 and re.fullmatch(r"\(?\s*[-−]?0[\s.0]*\)?", str(value_of(r.get("_se_cell_as_printed")) or "")):
                    add("warn", f"{rat}.se", "se_rounds_to_zero", "the SE is printed as zero: unusable as a variance (the CI or p, if printed, is not)")
                else:
                    add("fail", f"{rat}.se", "se_nonpositive", f"se = {se}")
            if sd is not None and sd < 0:
                add("fail", f"{rat}.sd", "sd_negative", f"sd = {sd}")
            for f, v in (("n", n), ("n_obs", n_obs)):
                if v is not None and v <= 0:
                    add("fail", f"{rat}.{f}", "n_nonpositive", f"{f} = {v}")
            if et == "ratio":
                if ev is not None and ev <= 0:
                    add("fail", f"{rat}.effect_value", "ratio_nonpositive", f"a ratio of {ev}")
                if (lo is not None and lo <= 0) or (hi is not None and hi <= 0):
                    add("fail", f"{rat}.ci_low", "ratio_nonpositive", f"a ratio with an interval bound ≤ 0: [{lo}, {hi}]")
            if et == "percent_change" and ev is not None and ev < -100:
                add("fail", f"{rat}.effect_value", "percent_below_minus_100", f"a percent change of {ev}")
            if et == "standardized_difference" and ev is not None and abs(ev) > 3:
                add("warn", f"{rat}.effect_value", "implausible_smd", f"a standardised difference of {ev}: check the scale and the denominator")
            if p is not None and lo is not None and hi is not None and (et in DIFF_TYPES or et == "ratio") and abs(p - 0.05) > 0.01:
                null = 1.0 if et == "ratio" else 0.0
                covers = lo < null < hi
                if p < 0.05 and covers:
                    add("warn", f"{rat}.p_value", "p_ci_inconsistent", f"p = {p} but the interval [{lo}, {hi}] covers {null:g}")
                if p > 0.05 and not covers and lo != null and hi != null:
                    hint = " (it covers 1: a ratio coded as a difference?)" if et != "ratio" and lo < 1 < hi else ""
                    add("warn", f"{rat}.p_value", "p_ci_inconsistent", f"p = {p} but the interval [{lo}, {hi}] excludes {null:g}{hint}")
            if "sign corrected:" in str(value_of(r.get("result_notes")) or ""):
                add("info", f"{rat}.effect_value", "sign_corrected", "the sign of the percent change was corrected from the two arms' means (see result_notes)")
            vs = value_of(r.get("vs_arm"))
            if vs is None:
                if m_t is not None and n is not None and value_of(r.get("measure_kind")) in COUNT_KINDS:
                    d = _decimals(m_t, value_of(r.get("stats_quote")) or "")
                    if not grim_ok(m_t, n_obs or n, d):
                        add("warn", f"{rat}.mean", "grim_inconsistent", f"a mean of {m_t} over {n_obs or n} integer counts cannot round to {d} decimals (GRIM)")
                continue
            k = (value_of(r.get("measure")) or "").strip().lower(), (value_of(r.get("subgroup")) or "full sample").strip().lower(), (value_of(r.get("horizon")) or "").strip().lower()
            b = base_rows.get((vs, k)) or {}
            m_c, s_c, n_c = (_num(value_of(b.get(x))) for x in ("mean", "sd", "n"))
            if m_t is not None and n is not None and value_of(r.get("measure_kind")) in COUNT_KINDS:
                d = _decimals(m_t, value_of(r.get("stats_quote")) or "")
                if not grim_ok(m_t, n_obs or n, d):
                    add("warn", f"{rat}.mean", "grim_inconsistent", f"a mean of {m_t} over {n_obs or n} integer counts cannot round to {d} decimals (GRIM)")
            if m_t is not None and m_c not in (None, 0) and ev is not None:
                diff = m_t - m_c
                if et == "percent_change":
                    implied = diff / abs(m_c) * 100
                    tol = max(2.0, 0.25 * abs(implied))
                    if abs(abs(implied) - abs(ev)) > tol:
                        if abs(abs(implied) - abs(ev) * 100) <= max(2.0, 0.1 * abs(implied)):
                            add("fail", f"{rat}.effect_value", "percent_as_proportion", f"effect_value {ev} is a proportion: the means imply {implied:.1f} percent")
                        else:
                            add("warn", f"{rat}.effect_value", "percent_vs_means", f"a percent change of {ev} but the means {m_t} vs {m_c} imply {implied:.1f}")
                    elif implied * ev < 0 and abs(ev) > tol / 2:
                        add("fail", f"{rat}.effect_value", "sign_disagrees", f"a percent change of {ev:+g} but the means go from {m_c} to {m_t} ({implied:+.1f}%): a reduction printed as a positive number")
                elif et == "mean_difference" and abs(diff) > 1e-9:
                    d = max(_decimals(m_t, value_of(r.get("stats_quote")) or ""), _decimals(m_c, value_of(b.get("stats_quote")) or ""))
                    if abs(abs(ev) - abs(diff)) > max(0.05 * abs(diff), 1.5 * 10 ** -d):
                        hint = " (a proportion where the means are percent?)" if abs(abs(ev) * 100 - abs(diff)) <= max(0.05 * abs(diff), 1.5 * 10 ** -d) else ""
                        add("warn", f"{rat}.effect_value", "effect_vs_means", f"a raw difference of {ev} but the means {m_t} vs {m_c} differ by {diff:g}{hint}")
            g = hedges_g(m_t, m_c, sd, s_c, n, n_c)
            if g is not None and abs(g) > 3:
                add("warn", f"{rat}.sd", "implausible_g", f"Hedges' g from the arms' means, SDs and n is {g:.2f}: an SD this small beside the means is usually a standard error")
    return issues


def categories(rec):
    """Values of coded fields in the taxonomy, or `other` with a proposed term."""
    issues = []
    for path, v, cell, parent in cells(rec):
        if v["type"] not in ("enum", "multi") or cell is None:
            continue
        val = value_of(cell)
        if val is None:
            continue
        codes = {o["code"] for o in v["options"]}
        vals = val if isinstance(val, list) else [val]
        for x in vals:
            if x not in codes:
                issues.append({"path": path, "code": "not_in_options", "severity": "fail", "message": f"{x!r} is not a category of {v['field']}"})
            elif x == "other" and v["open"] and not (isinstance(cell, dict) and (cell.get("proposed") or "").strip()):
                issues.append({"path": path, "code": "other_without_term", "severity": "warn", "message": f"{v['field']} is other without a proposed term"})
    return issues


def confidence(rec):
    """Low ratings, as issues (they route the paper)."""
    issues = []

    def scan(node, at):
        for g, c in ((node or {}).get("confidence") or {}).items():
            if (c or {}).get("level") == "low":
                issues.append({"path": f"{at}.confidence.{g}", "code": "low_confidence", "severity": "fail", "message": (c or {}).get("notes") or "low confidence"})
    scan(rec.get("study"), "study")
    for i, t in enumerate(rec.get("tasks") or []):
        scan(t, f"tasks[{i}]")
        for j, a in enumerate(t.get("arms") or []):
            scan(a, f"tasks[{i}].arms[{j}]")
        for j, r in enumerate(t.get("results") or []):
            scan(r, f"tasks[{i}].results[{j}]")
    return issues


def quote_issues(quotes):
    return [{"path": q["path"], "code": "quote_not_found", "severity": "fail",
             "message": f"quote not found in the PDF text (p. {q['page']}): \"{q['quote'][:120]}\""}
            for q in quotes if q["check"] not in X.FOUND_ALL]


def measure_kinds(rec):
    """A performance outcome's finding type follows from its measure kind; a kind on an outcome of
    another class is spurious."""
    issues = []
    kf = T.kind_finding()
    for ti, t in enumerate(rec.get("tasks") or []):
        for ri, r in enumerate(t.get("results") or []):
            kind, cls, ft = value_of(r.get("measure_kind")), value_of(r.get("outcome_class")), value_of(r.get("finding_type"))
            at = f"tasks[{ti}].results[{ri}]"
            if kind and cls != "performance":
                issues.append({"path": f"{at}.measure_kind", "code": "kind_off_performance", "severity": "warn", "message": f"measure_kind {kind!r} on a {cls} outcome"})
            elif kind in kf and ft != kf[kind]:
                issues.append({"path": f"{at}.finding_type", "code": "kind_finding_mismatch", "severity": "warn",
                               "message": f"finding_type {ft!r} does not follow from measure_kind {kind!r} ({kf[kind]})"})
    return issues


def record_issues(rec, quotes, pages=None):
    """Every check over the assembled record (the project's ``checks.run_all``)."""
    return quote_issues(quotes) + numbers_in_quotes(rec, pages) + structure(rec) + plausibility(rec) + categories(rec) + measure_kinds(rec) + confidence(rec)


# ── the pass files: sources ─────────────────────────────────────────────────────────────────

def best_match(target, options, floor=0.85):
    """The index of the option matching the target: exact, then substring either way, then difflib."""
    t = norm(target)
    if not t:
        return None, None
    opts = [norm(o) for o in options]
    for i, o in enumerate(opts):
        if o == t:
            return i, "exact"
    for i, o in enumerate(opts):
        if o and (t in o or o in t) and min(len(o), len(t)) >= 3:
            return i, "substring"
    scores = [(difflib.SequenceMatcher(None, t, o).ratio(), i) for i, o in enumerate(opts) if o]
    if scores:
        r, i = max(scores)
        if r >= floor:
            return i, "fuzzy"
    return None, None


def exhibit_of(ex, name):
    n = norm(name).replace("tab. ", "table ")
    for kind in ("tables", "figures"):
        for t in ex.get(kind) or []:
            if norm(t.get("id")) == n:
                return kind, t
    for kind in ("tables", "figures"):
        for t in ex.get(kind) or []:
            a, b = norm(t.get("id")), n
            if a and b and (a in b or b in a):
                return kind, t
    return None, None


def first_number(*vals):
    """The first value that is a number, or the first number printed in the first non-empty string (an effect cell "0.28*")."""
    for v in vals:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    for v in vals:
        if isinstance(v, str) and v.strip():
            m = NUM_RE.search(v.replace("−", "-").replace("–", "-"))
            if m:
                try:
                    return float(m.group(0).replace(",", "."))
                except ValueError:
                    return None
            return None
    return None


def continuation_rows(rows, ri):
    """The row ri and the rows under it whose first cell is empty: one row label spanning several arms or sub-rows."""
    out = [ri]
    for j in range(ri + 1, len(rows)):
        r = rows[j]
        if not r or str(r[0]).strip() or not any(str(c).strip() for c in r[1:]):
            break
        out.append(j)
    return out


def resolve_source(ex, src, value=None):
    """{kind, table, panel_i, row_i, col_i, cell, codes}: where an estimate's source points in
    the exhibits. `value` (the effect as printed) picks the row when the cited row label spans
    several continuation rows, or when the label is in a later cell."""
    out = {"kind": None, "table": None, "panel_i": None, "row_i": None, "col_i": None, "cell": None, "codes": []}
    if not isinstance(src, dict) or not src.get("exhibit"):
        return out
    if norm(src["exhibit"]) in ("text", "main text", "body text", "abstract", "appendix text", "notes", "footnote"):
        out["kind"] = "text"
        return out
    kind, t = exhibit_of(ex, src["exhibit"])
    if t is None:
        out["codes"].append("source_exhibit_unknown")
        return out
    out["kind"], out["table"] = kind, t
    if kind != "tables":
        return out
    panels = t.get("panels") or []
    if not panels:
        out["codes"].append("source_row_unresolved")
        return out
    pi = 0
    if src.get("panel") and len(panels) > 1:
        i, _ = best_match(src["panel"], [p.get("panel") or "" for p in panels], 0.6)
        if i is not None:
            pi = i
    out["panel_i"] = pi
    p = panels[pi]
    rows, header = p.get("rows") or [], p.get("header") or []
    if src.get("row"):
        ri, how = best_match(src["row"], [r[0] if r else "" for r in rows])
        if ri is None:
            for j, q in enumerate(panels):
                rj, _ = best_match(src["row"], [r[0] if r else "" for r in q.get("rows") or []])
                if rj is not None:
                    pi, p, rows, header, ri = j, q, q.get("rows") or [], q.get("header") or [], rj
                    out["panel_i"] = j
                    break
        if ri is None:
            out["codes"].append("source_row_unresolved")
        out["row_i"] = ri
    if src.get("column") and header:
        ci, _ = best_match(src["column"], header, 0.7)
        if ci is None:
            out["codes"].append("source_column_unresolved")
        out["col_i"] = ci
    if out["row_i"] is not None and out["col_i"] is not None:
        cands = continuation_rows(rows, out["row_i"])
        if len(cands) > 1 and value not in (None, ""):
            hit = [j for j in cands if out["col_i"] < len(rows[j]) and X.number_in_quote(value, str(rows[j][out["col_i"]]))]
            if hit:
                out["row_i"] = hit[0]
                out["codes"].append("source_row_continuation")
            else:
                lab = norm(src.get("row") or "")
                named = [j for j in cands if any(norm(c) and norm(c) in lab for c in rows[j][1:] if not NUM_RE.search(str(c)))]
                if named:
                    out["row_i"] = named[0]
                    out["codes"].append("source_row_continuation")
        if out["col_i"] < len(rows[out["row_i"]]):
            out["cell"] = rows[out["row_i"]][out["col_i"]]
    tp = t.get("page")
    if src.get("page") and tp and abs(int(src["page"]) - int(tp)) > 1:
        out["codes"].append("source_page_off")
    return out


def row_numbers(row):
    return " ".join(str(c) for c in (row or [])[1:])


def check_sources(ex, em):
    """Per estimate: the cited cell exists and prints the effect; every number sits in the row or its SE sibling."""
    issues, resolved = [], {}
    for e in em.get("estimates") or []:
        eid, src = e.get("estimate_id"), e.get("source") or {}
        r = resolve_source(ex, src, first_number(e.get("effect_value"), e.get("cell_as_printed"), e.get("effect_reported")))
        resolved[eid] = r
        path = f"estimates[{eid}]"
        for code in r["codes"]:
            if code == "source_row_continuation":
                continue
            issues.append(issue("fail" if code == "source_exhibit_unknown" else "warn", path, code, f"{code.replace('_', ' ')}: {src}"))
        if r["kind"] != "tables" or r["row_i"] is None:
            continue
        rows = r["table"]["panels"][r["panel_i"]]["rows"]
        row = rows[r["row_i"]]
        sib = rows[r["row_i"] + 1] if r["row_i"] + 1 < len(rows) and (norm(rows[r["row_i"] + 1][0]).endswith("(se)") or norm(rows[r["row_i"] + 1][0]) == norm(row[0]) + " (se)") else None
        lab_i = r["row_i"]
        while lab_i > 0 and rows[lab_i] and not str(rows[lab_i][0]).strip():
            lab_i -= 1
        group = [rows[j] for j in continuation_rows(rows, lab_i) if j != r["row_i"]]
        ev = e.get("effect_value")
        if r["cell"] is not None and ev not in (None, "") and not X.number_in_quote(ev, str(r["cell"])):
            issues.append(issue("fail", path, "source_cell_mismatch", f"effect {ev} is not printed in the cited cell “{r['cell']}” ({src.get('exhibit')}, row “{row[0]}”, column “{src.get('column')}”)"))
        hay = " ".join([row_numbers(row)] + [row_numbers(g) for g in group] + ([row_numbers(sib)] if sib else []))
        for k in ("se", "ci_low", "ci_high", "p_value", "mean_ref", "mean_treat", "sd_ref", "sd_treat"):
            v = e.get(k)
            if v in (None, ""):
                continue
            if not X.number_in_quote(v, hay):
                issues.append(issue("warn", path, "number_not_in_source_row", f"{k} = {v} is not in the cited row “{row[0]}” nor its SE row"))
    return issues, resolved


def coefficient_rows(panel):
    """Indices of the rows that carry coefficients: rows followed by an SE row, else rows with 2+ numbers not a statistics row."""
    rows = panel.get("rows") or []
    out, has_se = [], False
    for i, r in enumerate(rows):
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if nxt and (norm(nxt[0]).endswith("(se)") or (len(nxt) > 1 and all(re.fullmatch(r"\s*[\(\[][^\)\]]*[\)\]]\s*", str(c)) for c in nxt[1:] if str(c).strip()))):
            out.append(i)
            has_se = True
    if has_se:
        return out, True
    for i, r in enumerate(rows):
        if r and not STAT_ROW.search(str(r[0])) and sum(1 for c in r[1:] if NUM_RE.search(str(c))) >= 2 and not norm(r[0]).endswith("(se)"):
            out.append(i)
    return out, False


def exhibit_coverage(ex, em, resolved):
    """Rows and columns of a panel in use that no estimate references."""
    issues = []
    ref = defaultdict(set)
    for eid, r in resolved.items():
        if r["kind"] == "tables" and r["row_i"] is not None:
            ref[(r["table"]["id"], r["panel_i"])].add((r["row_i"], r["col_i"]))
    orphan = " ".join(norm(o.get("where")) for o in em.get("orphan_numbers") or [])
    tot = used = 0
    for t in ex.get("tables") or []:
        for pi, p in enumerate(t.get("panels") or []):
            coef, _ = coefficient_rows(p)
            hits = ref.get((t["id"], pi), set())
            hit_rows = {ri for ri, _ in hits}
            if not (hit_rows & set(coef)):
                continue
            rows, header = p.get("rows") or [], p.get("header") or []
            tot += len(coef)
            used += len(set(coef) & hit_rows)
            for ri in coef:
                if ri not in hit_rows:
                    label = str(rows[ri][0])
                    sev = "info" if norm(label) and norm(label) in orphan else "warn"
                    issues.append(issue(sev, f"exhibits[{t['id']}].panels[{pi}].rows[{ri}]", "exhibit_row_unreferenced",
                                        f"row “{label}” of {t['id']}{(' ' + p['panel']) if p.get('panel') else ''} carries coefficients but no estimate cites it"))
            spec_cols = [ci for ci, h in enumerate(header) if ci > 0 and not SPEC_COL.search(str(h))]
            hit_cols = {ci for _, ci in hits if ci is not None}
            for ci in spec_cols:
                if ci not in hit_cols:
                    issues.append(issue("warn", f"exhibits[{t['id']}].panels[{pi}].header[{ci}]", "exhibit_column_unreferenced",
                                        f"column “{header[ci]}” of {t['id']}{(' ' + p['panel']) if p.get('panel') else ''} is never cited although the panel is in use"))
        if t.get("check") not in (None, "", "table_cells", "empty") and t.get("cells_checked"):
            issues.append(issue("warn", f"exhibits[{t['id']}]", "exhibit_unverified", f"{t['id']}: {t.get('cells_found')} of {t.get('cells_checked')} transcribed cells found on the page"))
    return issues, {"coefficient_rows_in_use": tot, "referenced": used, "ratio": (used / tot) if tot else None}


def grid_gaps(es, em):
    issues = []
    ests = em.get("estimates") or []
    by_estimand = defaultdict(list)
    for e in ests:
        by_estimand[e.get("estimand_id")].append(e)
    known = {x.get("estimand_id") for x in es.get("estimands") or []}
    unmatched = {x.get("estimand_id") for x in em.get("unmatched_estimands") or []}
    for e in ests:
        if e.get("estimand_id") not in known:
            issues.append(issue("fail", f"estimates[{e.get('estimate_id')}]", "estimate_unknown_estimand", f"estimand {e.get('estimand_id')} is not in pass a"))
    for x in es.get("estimands") or []:
        eid = x.get("estimand_id")
        if eid not in by_estimand and eid not in unmatched:
            issues.append(issue("warn", f"estimands[{eid}]", "estimand_unmapped", f"no estimate and not declared unmatched: {str(x.get('statement'))[:100]}"))
        elif eid in by_estimand and not any(e.get("role") == "main" for e in by_estimand[eid]):
            issues.append(issue("warn", f"estimands[{eid}]", "no_main", f"{len(by_estimand[eid])} estimates, none marked main"))
    arms = es.get("arms") or []
    refs = [a for a in arms if a.get("is_reference")]
    pairs = {(x.get("task_id"), x.get("arm_id"), x.get("vs_arm_id"), x.get("outcome_id")) for x in es.get("estimands") or []}
    for t in es.get("tasks") or []:
        tid = t.get("task_id")
        t_arms = [a for a in arms if tid in (a.get("task_ids") or [])] or arms
        t_refs = [a for a in t_arms if a.get("is_reference")] or refs
        t_out = {x.get("outcome_id") for x in es.get("estimands") or [] if x.get("task_id") == tid}
        for a in t_arms:
            if a.get("is_reference"):
                continue
            for r in t_refs:
                for o in t_out:
                    if (tid, a.get("arm_id"), r.get("arm_id"), o) not in pairs:
                        issues.append(issue("info", f"estimands[{tid}]", "grid_gap", f"task {tid}: no estimand for {a.get('arm_id')} vs {r.get('arm_id')} on {o}"))
    return issues


def num(v):
    try:
        return float(str(v).replace(",", "").replace("−", "-"))
    except (TypeError, ValueError):
        return None


SIG_FIELDS = ("effect_value", "se", "ci_low", "ci_high", "p_value", "mean_treat", "sd_treat", "mean_ref")


def duplicates(em, resolved):
    issues = []
    by_cell = defaultdict(list)
    for e in em.get("estimates") or []:
        r = resolved.get(e.get("estimate_id")) or {}
        if r.get("kind") == "tables" and r.get("row_i") is not None and r.get("col_i") is not None:
            by_cell[(r["table"]["id"], r["panel_i"], r["row_i"], r["col_i"])].append(e)
    same_cell = set()
    for key, es_ in by_cell.items():
        if len(es_) > 1:
            same = len({e.get("estimand_id") for e in es_}) == 1
            same_cell |= {e.get("estimate_id") for e in es_}
            issues.append(issue("warn" if same else "fail", f"estimates[{es_[0].get('estimate_id')}]", "duplicate_source_cell",
                                f"{len(es_)} estimates cite the same cell ({key[0]}, row {key[2]}, column {key[3]}): " + ", ".join(str(e.get("estimate_id")) for e in es_)))
    by_est = defaultdict(list)
    for e in em.get("estimates") or []:
        by_est[e.get("estimand_id")].append(e)
    for eid, es_ in by_est.items():
        seen = {}
        for e in es_:
            sig = tuple(e.get(k) for k in NUMS)
            if any(v not in (None, "") for v in sig) and sig in seen:
                issues.append(issue("warn", f"estimates[{e.get('estimate_id')}]", "duplicate_estimate", f"same numbers as {seen[sig]} under estimand {eid}"))
            seen.setdefault(sig, e.get("estimate_id"))
    by_sig = defaultdict(list)
    for e in em.get("estimates") or []:
        sig = tuple(num(e.get(k)) for k in SIG_FIELDS)
        if sum(v is not None for v in sig) >= 2:
            by_sig[sig].append(e)
    for sig, es_ in by_sig.items():
        ids = {e.get("estimand_id") for e in es_}
        if len(ids) > 1 and not all(e.get("estimate_id") in same_cell for e in es_):
            shown = ", ".join(f"{k} = {v:g}" for k, v in zip(SIG_FIELDS, sig) if v is not None)
            issues.append(issue("warn", f"estimates[{es_[0].get('estimate_id')}]", "duplicate_numbers_across_estimands",
                                f"the same numbers ({shown}) under estimands {', '.join(sorted(str(i) for i in ids))}: " + ", ".join(str(e.get("estimate_id")) for e in es_)))
    return issues


# ── the statistics ──────────────────────────────────────────────────────────────────────────

SIGNED_TYPES = {"mean_difference", "regression_coefficient", "difference_in_differences", "interaction", "percent_change", "elasticity"}
STAR_DEFAULT = {3: 0.01, 2: 0.05, 1: 0.1}
_CUT = r"(0?\.\d+|\d+(?:\.\d+)?\s*%)"
STAR_LIST = re.compile(r"(\*{1,3})\s*,\s*(\*{1,3})\s*(?:,|and|&)\s*(\*{1,3})[^.;]*?" + _CUT + r"\s*,\s*" + _CUT + r"\s*,?\s*(?:and|&)?\s*" + _CUT, re.I)
STAR_CONV = re.compile(r"(\*{1,3})\s*[:=]?\s*(?:denotes?|indicates?|significan\w*|p)?\s*(?:<|≤|at|below|the)?\s*(?:the\s+)?" + _CUT, re.I)
STAR_CONV2 = re.compile(r"p\s*(?:<|≤)\s*" + _CUT + r"\s*[:=]?\s*\(?\s*(\*{1,3})", re.I)
PAREN_SE = re.compile(r"\b(standard errors?|s\.?\s?e\.?s?|SEs?)\b[^.;]{0,50}\b(in|within)\s+(parentheses|brackets|parenthesis)|\b(parentheses|brackets)\b[^.;]{0,50}\b(standard errors?|SEs?)\b", re.I)
PAREN_SD = re.compile(r"\b(standard deviations?|s\.?\s?d\.?s?|SDs?)\b[^.;]{0,50}\b(in|within)\s+(parentheses|brackets|parenthesis)|\b(parentheses|brackets)\b[^.;]{0,50}\b(standard deviations?|SDs?)\b", re.I)
SPREAD_LABEL = re.compile(r"\b(sd|s\.\s?d\.|std\.?|standard deviation|se|s\.\s?e\.|standard error|sem|std\.? ?err\w*|std\.? ?dev\w*|95% ci|confidence interval)\b", re.I)
PAREN_NUM = re.compile(r"[\(\[]\s*([-−]?\d[\d.,]*)\s*[\)\]]")
UP_WORDS = re.compile(r"\b(increas\w*|rais\w*|higher|rose|grew|grow\w*|boost\w*)\b", re.I)
DOWN_WORDS = re.compile(r"\b(reduc\w*|decreas\w*|lower\w*|fewer|declin\w*|dropp?\w*|fell|fall\w*|shrank)\b", re.I)
NEGATION = re.compile(r"\b(no|not|never|neither|nor|without)\b|n't", re.I)
STAT_RE = re.compile(r"^\s*(?:(F|chi[- ]?2|chi[- ]?sq\w*|wald|χ2?|[tz](?:[- ]?(?:stat\w*|value|ratio))?)\s*(?:\([^)]*\))?\s*[=:]?\s*)?([-−]?\d+(?:\.\d+)?)\s*$", re.I)


def _cut(s):
    try:
        return float(s.replace("%", "").strip()) / (100 if "%" in s else 1)
    except (TypeError, ValueError):
        return None


def star_convention(text):
    """{stars: cut} from a table's notes; None when the notes do not state one (two cuts at least)."""
    conv = {}
    m = STAR_LIST.search(text or "")
    if m:
        for stars, cut in zip(m.group(1, 2, 3), m.group(4, 5, 6)):
            conv.setdefault(len(stars), _cut(cut))
    else:
        for m in STAR_CONV.finditer(text or ""):
            conv.setdefault(len(m.group(1)), _cut(m.group(2)))
        for m in STAR_CONV2.finditer(text or ""):
            conv.setdefault(len(m.group(2)), _cut(m.group(1)))
    conv = {k: v for k, v in conv.items() if v is not None and 0 < v < 0.5}
    return conv if len(conv) >= 2 else None


def parse_p(s):
    """('stars', k) | ('lt', cut) | ('eq', p) | ('ns', None) | None from a printed significance."""
    if s in (None, ""):
        return None
    s = str(s).strip().replace("−", "-")
    if re.fullmatch(r"\*{1,3}", s):
        return ("stars", len(s))
    m = re.search(r"(?:^|\bp\s*)(?:<|≤|<=|below)\s*" + _CUT, s, re.I)
    if m:
        return ("lt", _cut(m.group(1)))
    m = re.fullmatch(r"[\[\(]?\s*(?:p\s*[=:]?\s*)?(0?\.\d+|1\.0+|0|1)\s*[\]\)]?", s, re.I)
    if m:
        return ("eq", float(m.group(1)))
    if re.fullmatch(r"n\.?\s?s\.?|not significant|insignificant", s, re.I):
        return ("ns", None)
    return None


def _betacf(a, b, x, max_iter=300, eps=3e-14):
    """The continued fraction of the incomplete beta function (Numerical Recipes)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / (c if abs(c) > tiny else tiny)
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / (c if abs(c) > tiny else tiny)
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a, b, x):
    """The regularised incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1.0 - x) / b


def t_two_sided(z, df):
    """The two-sided tail of Student's t at |z| with df degrees of freedom (scipy's 2·t.sf)."""
    z = abs(float(z))
    if df <= 0:
        return math.erfc(z / math.sqrt(2))
    return betainc(df / 2.0, 0.5, df / (df + z * z))


def implied_p(ev, se, n_obs=None):
    """Two-sided p from effect/se: Student t with n_obs − 2 degrees of freedom when n_obs is known, else normal."""
    z = abs(ev / se)
    if n_obs and n_obs > 2:
        return t_two_sided(z, n_obs - 2)
    return math.erfc(z / math.sqrt(2))


def _dec(printed, v):
    """Decimals of a number as printed (the string when given, else the float's own)."""
    m = re.search(r"[-−]?\d+\.(\d+)", str(printed or ""))
    if m:
        return len(m.group(1))
    s = f"{float(v):g}"
    return len(s.split(".")[1]) if "." in s else 0


def z_bounds(e, ev, se):
    """The range of |effect/se| allowed by the rounding of the two printed numbers (half a unit of the last digit each)."""
    h_ev = 0.5 * 10 ** -_dec(e.get("cell_as_printed") or e.get("effect_reported"), ev)
    h_se = 0.5 * 10 ** -_dec(e.get("se_cell_as_printed"), se)
    a = abs(ev)
    lo = max(a - h_ev, 0.0) / (se + h_se)
    hi = (a + h_ev) / max(se - h_se, 1e-12)
    return lo, hi


def p_range(e, ev, se, n_obs):
    """[smallest, largest] two-sided p consistent with the rounding of effect and se."""
    z_lo, z_hi = z_bounds(e, ev, se)
    return implied_p(z_hi * se, se, n_obs), implied_p(z_lo * se, se, n_obs)


def cutoff_disagreement(p, implied):
    """Whether a reported p and the implied one fall on different sides of 0.01, 0.05 or 0.1 (with a margin), or differ threefold above 0.1."""
    for cut in (0.01, 0.05, 0.1):
        if (implied < cut) != (p < cut) and abs(implied - cut) > 0.3 * cut and abs(p - cut) > 0.3 * cut:
            return True
    return p > 0.1 and implied > 0.1 and (p / implied > 3 or implied / p > 3)


def significance_issues(e, path, p, implied, conv):
    """The printed significance against the p the effect and its standard error imply."""
    out = []
    rep = parse_p(e.get("p_reported"))
    if rep is None:
        tail = re.search(r"(\*{1,3})\s*$", str(e.get("cell_as_printed") or e.get("effect_reported") or ""))
        if tail:
            rep = ("stars", len(tail.group(1)))
    if rep is None or implied is None:
        return out
    kind, v = rep
    used = conv or STAR_DEFAULT
    p_min, p_max = implied
    shown = f"p ≈ {p_min:.3g}" if p_min == p_max else f"p between {p_min:.3g} and {p_max:.3g}"
    if kind == "eq" and p is None and v is not None:
        if cutoff_disagreement(v, p_min) and cutoff_disagreement(v, p_max):
            out.append(issue("warn", path, "p_se_inconsistent", f"p printed as {e.get('p_reported')!r} but effect/se implies {shown}"))
    elif kind == "lt" and v:
        if p_min > 2 * v:
            out.append(issue("warn", path, "stars_se_inconsistent", f"p < {v:g} printed but effect/se implies {shown}"))
    elif kind == "stars":
        cut = used.get(v)
        if cut and p_min > 2 * cut:
            out.append(issue("warn", path, "stars_se_inconsistent", f"{'*' * v} (p < {cut:g}{'' if conv else ', the usual convention'}) but effect/se implies {shown}"))
        nxt = used.get(v + 1) if conv else None
        if nxt and p_max < nxt / 2:
            out.append(issue("warn", path, "stars_se_inconsistent", f"{'*' * v} but effect/se implies {shown}, below the {'*' * (v + 1)} cut {nxt:g}"))
    elif kind == "ns" and p_max < 0.025:
        out.append(issue("warn", path, "stars_se_inconsistent", f"printed as not significant but effect/se implies {shown}"))
    return out


def spread_issues(e, path, ev, se, r):
    """Standard deviations against standard errors, a ± value with no label, the parentheses convention."""
    out = []
    sd_t, sd_c, n_t, n_c = (num(e.get(k)) for k in ("sd_treat", "sd_ref", "n_treat", "n_ref"))
    sds = [x for x in (sd_t, sd_c) if x is not None]
    if se and se > 0 and sd_t is not None and sd_c is not None and sd_t >= 0 and sd_c >= 0:
        if n_t and n_c and n_t >= 2 and n_c >= 2:
            naive = math.sqrt(sd_t ** 2 / n_t + sd_c ** 2 / n_c)
            as_se = math.sqrt(sd_t ** 2 + sd_c ** 2)
            if as_se > 0 and abs(se - as_se) / se < 0.1 and naive > 0 and abs(se - naive) / se > 0.5:
                out.append(issue("fail", path, "sd_looks_like_se", f"se {se:g} equals sqrt(sd_treat² + sd_ref²): the values in sd_treat/sd_ref ({sd_t:g}, {sd_c:g}) are standard errors of the arm means, not SDs"))
            elif e.get("effect_type") == "mean_difference" and naive > 0 and not 0.5 <= se / naive <= 2.0:
                out.append(issue("warn", path, "se_vs_sd_inconsistent", f"se {se:g} but the arms' SDs and n give sqrt(sd²/n + sd²/n) = {naive:.3g}"))
        if max(sd_t, sd_c) < se * (1 - 1e-9):
            out.append(issue("warn", path, "sd_below_se", f"both SDs ({sd_t:g}, {sd_c:g}) are below the SE of the difference {se:g}: an SD of the outcome cannot be, an SE of a mean can"))
    printed = " ".join(str(e.get(k) or "") for k in ("cell_as_printed", "se_cell_as_printed", "effect_reported", "quote"))
    t = r.get("table") if r.get("kind") == "tables" else None
    notes = f"{(t or {}).get('caption') or ''} {(t or {}).get('notes') or ''}"
    if "±" in printed and (sds or se is not None):
        labels = notes
        if t is not None and r.get("row_i") is not None:
            p_ = t["panels"][r["panel_i"]]
            row = p_["rows"][r["row_i"]]
            labels += " " + " ".join(p_.get("header") or []) + " " + str(row[0] if row else "")
        if not SPREAD_LABEL.search(labels + " " + str(e.get("quote") or "")):
            out.append(issue("warn", path, "pm_unlabelled", "a ± value with no SD or SE label in the table: check which it is before it enters a standardised difference"))
    if t is not None and r.get("row_i") is not None:
        se_paren, sd_paren = bool(PAREN_SE.search(notes)), bool(PAREN_SD.search(notes))
        if se_paren != sd_paren:
            rows = t["panels"][r["panel_i"]]["rows"]
            ri = r["row_i"]
            cells_ = " ".join(str(c) for c in rows[ri][1:]) + (" " + " ".join(str(c) for c in rows[ri + 1][1:]) if ri + 1 < len(rows) else "")
            parens = [num(m.group(1)) for m in PAREN_NUM.finditer(cells_.replace("−", "-"))]
            parens = [x for x in parens if x is not None]
            if se_paren and sds:
                hit = [x for x in sds if any(abs(x - y) <= 1e-9 * max(1, abs(y)) for y in parens)]
                if hit:
                    out.append(issue("fail", path, "sd_from_se_parentheses", f"the table's note says standard errors are in parentheses; the parenthesised {hit[0]:g} was read as an SD"))
            if sd_paren and se is not None and any(abs(se - y) <= 1e-9 * max(1, abs(y)) for y in parens):
                out.append(issue("fail", path, "se_from_sd_parentheses", f"the table's note says standard deviations are in parentheses; the parenthesised {se:g} was read as an SE"))
    return out


def sign_issue(e, path, ev):
    """The sign of the effect against the sentence that states it, when that sentence uses words of one direction only."""
    tq = str(e.get("text_quote") or "")
    et = e.get("effect_type")
    if not tq or ev in (None, 0) or e.get("sign_as_printed") == "effect_of_removal" or not (et in SIGNED_TYPES or et == "ratio"):
        return None
    up, down = bool(UP_WORDS.search(tq)), bool(DOWN_WORDS.search(tq))
    ctx = " ".join(str(e.get(k) or "") for k in ("effect_units",)) + " " + " ".join(str((e.get("source") or {}).get(k) or "") for k in ("row", "column"))
    if up == down or NEGATION.search(tq) or UP_WORDS.search(ctx) or DOWN_WORDS.search(ctx):
        return None
    sign = (ev - 1) if et == "ratio" else ev
    if (sign > 0 and down) or (sign < 0 and up):
        return issue("warn", path, "sign_vs_text", f"effect {ev:g} but the sentence says it went {'down' if down else 'up'}: “{tq[:100]}”")
    return None


def stats_consistency(em, resolved):
    issues = []
    for e in em.get("estimates") or []:
        eid = e.get("estimate_id")
        path = f"estimates[{eid}]"
        ev, se, lo, hi, p, n_obs = (num(e.get(k)) for k in ("effect_value", "se", "ci_low", "ci_high", "p_value", "n_obs"))
        r = resolved.get(eid) or {}
        t = r.get("table") if r.get("kind") == "tables" else None
        if se and se > 0 and lo is not None and hi is not None and hi > lo:
            h = 0.5 * 10 ** -max(_dec(None, lo), _dec(None, hi))
            h_se = 0.5 * 10 ** -_dec(e.get("se_cell_as_printed"), se)
            ratio = (hi - lo) / (2 * se)
            r_lo, r_hi = max(hi - lo - 2 * h, 0) / (2 * (se + h_se)), (hi - lo + 2 * h) / (2 * max(se - h_se, 1e-12))
            log_ratio = (math.log(hi) - math.log(lo)) / (2 * se) if e.get("effect_type") == "ratio" and lo > 0 else None
            if (r_hi < 1.9 or r_lo > 2.3) and not (log_ratio is not None and 1.9 <= log_ratio <= 2.3):
                hint = " (a 90% interval gives 1.64)" if r_lo <= 1.645 <= r_hi else (f" (log scale {log_ratio:.2f})" if log_ratio is not None else "")
                issues.append(issue("warn", path, "ci_se_inconsistent", f"(ci_high − ci_low)/(2·se) = {ratio:.2f}{hint}"))
        implied = p_range(e, ev, se, n_obs) if se and se > 0 and ev is not None else None
        if implied is not None and p is not None and cutoff_disagreement(p, implied[0]) and cutoff_disagreement(p, implied[1]):
            issues.append(issue("warn", path, "p_se_inconsistent", f"p = {p} but effect/se implies p between {implied[0]:.3g} and {implied[1]:.3g}"))
        if implied is not None:
            issues += significance_issues(e, path, p, implied, star_convention(f"{t.get('caption') or ''} {t.get('notes') or ''}") if t else None)
        m = STAT_RE.match(str(e.get("statistic") or "").replace("−", "-"))
        if m and se and se > 0 and ev is not None and not (m.group(1) or "").lower().startswith(("f", "chi", "wald", "χ")):
            t_rep = abs(float(m.group(2)))
            z_lo, z_hi = z_bounds(e, ev, se)
            if t_rep < z_lo - max(0.15, 0.1 * z_lo) or t_rep > z_hi + max(0.15, 0.1 * z_hi):
                issues.append(issue("warn", path, "t_se_inconsistent", f"statistic {e.get('statistic')!r} but effect/se = {abs(ev / se):.2f}"))
        er = e.get("effect_reported")
        if ev is not None and er not in (None, "") and not X.number_in_quote(e.get("effect_value"), str(er)):
            issues.append(issue("fail", path, "effect_reported_mismatch", f"effect_value {e.get('effect_value')} is not in effect_reported “{er}”"))
        issues += spread_issues(e, path, ev, se, r)
        si = sign_issue(e, path, ev)
        if si:
            issues.append(si)
        if t is not None and r.get("row_i") is not None and r.get("col_i") is not None:
            rows = t["panels"][r["panel_i"]]["rows"]
            ri, ci = r["row_i"], r["col_i"]
            notes = f"{t.get('caption') or ''} {t.get('notes') or ''}"
            sd_in_parens = bool(PAREN_SD.search(notes)) and not PAREN_SE.search(notes)
            sib = rows[ri + 1] if ri + 1 < len(rows) and (norm(rows[ri + 1][0]).endswith("(se)") or (len(rows[ri + 1]) > ci and re.fullmatch(r"\s*\([^\)]*\)\s*", str(rows[ri + 1][ci] or "")))) else None
            if sib is not None and ci < len(sib) and not sd_in_parens:
                cell_ = str(sib[ci])
                tv = num(re.sub(r"[\(\)\[\]\*]", "", cell_))
                if se is not None and tv is not None and abs(tv - se) > 1e-6 * max(1, abs(se)) and not X.number_in_quote(e.get("se"), cell_):
                    issues.append(issue("fail", path, "se_differs_from_table", f"se {e.get('se')} but the table prints “{cell_}”"))
                elif se is None and tv is not None:
                    issues.append(issue("warn", path, "se_missing_from_table", f"the table prints an SE “{cell_}” that the estimate does not carry"))
            if n_obs is not None:
                for row in rows:
                    if row and re.match(r"^\s*(observations?|n\b|obs\.?\b|no\. of obs)", str(row[0]), re.I) and ci < len(row):
                        tv = num(row[ci])
                        if tv is not None and abs(tv - n_obs) > 0.5:
                            issues.append(issue("warn", path, "n_obs_differs_from_table", f"n_obs {e.get('n_obs')} but the Observations row prints {row[ci]}"))
                        break
    return issues


def pass_issues(ex, es, em):
    """Every check over the pass outputs (the project's ``pass_checks.run_all``); returns (issues, summary)."""
    issues, resolved = check_sources(ex, em)
    cov_issues, cov = exhibit_coverage(ex, em, resolved)
    issues += cov_issues + grid_gaps(es, em) + duplicates(em, resolved) + stats_consistency(em, resolved)
    return issues, {"exhibit_coverage": cov, "codes": dict(Counter(i["code"] for i in issues))}


def cell_checks(ex, em):
    """The cell the model says it copied (cell_as_printed) against the transcribed cell its source resolves to."""
    out = []
    for i, est in enumerate(em.get("estimates") or []):
        cp = est.get("cell_as_printed")
        if not cp:
            continue
        r = resolve_source(ex, est.get("source"), first_number(est.get("effect_value"), cp, est.get("effect_reported")))
        if r.get("cell") is not None and norm(str(r["cell"])) != norm(str(cp)):
            out.append({"path": f"estimates[{i}]", "code": "cell_text_mismatch", "severity": "warn",
                        "message": f"cell_as_printed {str(cp)[:30]!r} differs from the transcribed cell {str(r['cell'])[:30]!r}"})
    return out
