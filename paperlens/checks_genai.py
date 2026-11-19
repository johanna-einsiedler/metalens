"""The GenAI-at-work record checks: the deterministic rules the review project's pipeline runs
over an extracted record ("Record and checks"), ported to run over the records a dataset holds
under the `genai-work-extraction` preset — one entry per task, its arms and its result rows
(one reference row and one contrast row per estimate, the numbers as the paper prints them).

What the stored record supports, and this module checks: the record's STRUCTURE (ids, baselines,
contrasts, n / interval / p sanity, a second main row), PLAUSIBILITY and SCALE (signs,
non-positive spreads and counts, a per-cent change printed as a proportion or not supported by
the arm means, an implausible standardised difference, GRIM on integer counts), STATISTICS
WITHIN THE ROUNDING (the interval against the standard error, the p-value and the printed
significance against the standard error, the effect against the string it was read from), SD
VERSUS SE (a standard deviation that is a standard error, or the other way round), DUPLICATES
(the same numbers twice), and NUMBERS AGAINST THEIR QUOTES (every number in the quote it cites,
the quote located on the page). What it cannot check, because the pipeline's inputs for it never
reach Metalens: anything read from the exhibit transcriptions (the cited cell, the table's own
SE row, the star legend of a table), the t-statistic, the text quote's direction, the second
run. Those stay in the pipeline; their outcome arrives as the flags of a routed paper.

Codes, severities and messages keep the pipeline's names (`fail` → `error`), so a finding here
and a flag there read alike. The implied p-value uses the normal approximation (the pipeline
uses Student's t when it has the degrees of freedom).
"""
from __future__ import annotations

import math
import re

NUM_FIELDS = ("n", "n_obs", "mean", "sd", "effect_value", "se", "ci_low", "ci_high", "p_value")
SIG_FIELDS = ("effect_value", "se", "ci_low", "ci_high", "p_value", "mean", "sd")
SOFT = {"se", "ci_low", "ci_high", "p_value"}             # the pipeline lets these pass when the number is on the cited page
COUNT_KINDS = {"output_count", "error_count"}             # measure kinds whose means are means of integer counts (GRIM)
SMD_TYPES = {"standardized_difference"}
EFFECT_FIELDS = ("effect_value", "se", "ci_low", "ci_high", "p_value", "effect_reported", "p_reported")
STAR_DEFAULT = {3: 0.01, 2: 0.05, 1: 0.1}
KEY = ("arm", "vs_arm", "measure", "subgroup", "horizon")   # what makes two result rows the same estimand


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _s(x) -> str:
    return str(x if x is not None else "").strip()


def _decimals(printed, v: float | None) -> int:
    """Decimals of a printed number, else of the float itself (capped)."""
    if printed is not None:
        m = re.search(r"-?\d+[.,](\d+)", str(printed))
        if m:
            return len(m.group(1))
        if re.search(r"-?\d+", str(printed)):
            return 0
    if v is None:
        return 0
    s = f"{v:.10g}"
    return min(len(s.split(".")[1]) if "." in s else 0, 6)


def _renderings(v: float) -> set[str]:
    out: set[str] = set()
    for x in {v, abs(v)}:
        out.add(f"{x:g}")
        for d in (1, 2, 3, 4):
            out.add(f"{x:.{d}f}")
        out.add(f"{x:,.0f}"); out.add(f"{x:,.2f}")
        for y in (x * 100, x / 100):
            out.add(f"{y:g}"); out.add(f"{y:.1f}"); out.add(f"{y:.2f}")
        if float(x).is_integer():
            out.add(str(int(x)))
    return {r for r in out if r and r != "0" or v == 0}


def number_in_text(v: float, text: str) -> bool:
    """Does the number appear in the text, bounded by non-digits, in any usual rendering —
    a dropped leading zero (.45), a decimal comma and thousands separators allowed, the sign ignored."""
    if not text:
        return False
    t = text.replace("−", "-").replace(" ", "").replace(" ", "")
    t_nocomma = re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", t)
    for r in _renderings(v):
        for hay in (t, t_nocomma, t.replace(",", "."), t_nocomma.replace(",", ".")):
            pat = re.escape(r)
            if re.search(rf"(?<![\d.]){pat}(?![\d])", hay):
                return True
            if r.startswith("0.") and re.search(rf"(?<![\d.]){re.escape(r[1:])}(?![\d])", hay):
                return True
    return False


def _implied_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def _z_bounds(ev: float, se: float, dec_ev: int, dec_se: int) -> tuple[float, float]:
    """The |z| range a printed effect and standard error allow, each off by half a unit of its last digit."""
    h_ev, h_se = 0.5 * 10 ** -dec_ev, 0.5 * 10 ** -dec_se
    a = abs(ev)
    lo = max(a - h_ev, 0.0) / (se + h_se)
    hi = (a + h_ev) / max(se - h_se, 1e-12)
    return lo, hi


def _cutoff_disagreement(p: float, implied: float) -> bool:
    for cut in (0.01, 0.05, 0.1):
        if (implied < cut) != (p < cut) and abs(implied - cut) > 0.3 * cut and abs(p - cut) > 0.3 * cut:
            return True
    return p > 0.1 and implied > 0.1 and (p / implied > 3 or implied / p > 3)


def _parse_p(reported: str) -> tuple[str, float | None] | None:
    """The printed significance: ('stars', n), ('lt', cut), ('eq', p), ('ns', None), else None."""
    s = _s(reported).lower().replace("−", "-")
    if not s:
        return None
    if re.fullmatch(r"\**", s) and s:
        return "stars", float(len(s))
    stars = len(s) - len(s.rstrip("*"))
    if stars and re.fullmatch(r"[\d.,<>=≤≥p\s]*\*+", s):
        return "stars", float(stars)
    if s in ("n.s.", "ns", "n.s", "not significant"):
        return "ns", None
    m = re.search(r"[<≤]\s*0?[.,](\d+)", s)
    if m:
        return "lt", float("0." + m.group(1))
    m = re.search(r"(?:p\s*=?\s*)?0?[.,](\d+)", s)
    if m:
        return "eq", float("0." + m.group(1))
    return None


class _Out:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, rec: dict | None, doc: str, path: str | None, code: str, severity: str, message: str, **detail) -> None:
        self.items.append({"record_id": rec["id"] if rec else None, "document_id": doc, "path": path, "code": code,
                           "severity": severity, "message": message[:300], **({"detail": detail} if detail else {})})


# ── the rules ─────────────────────────────────────────────────────────────────

def _check_dates(pm: dict, doc: str, out: _Out) -> None:
    for f in ("period_start_year", "period_end_year"):
        y = _num(pm.get(f))
        if y is not None and not 1990 <= y <= 2035:
            out.add(None, doc, f"paper_metadata.{f}", "bad_date", "warn", f"{f} = {y:g} is outside 1990–2035")
    for f in ("period_start_month", "period_end_month"):
        mth = _num(pm.get(f))
        if mth is not None and not 1 <= mth <= 12:
            out.add(None, doc, f"paper_metadata.{f}", "bad_date", "warn", f"{f} = {mth:g} is not a month")


def _check_task(rec: dict, doc: str, evidence: list[dict], out: _Out) -> None:
    fv = rec.get("field_values") or {}
    arms = [a for a in (fv.get("arms") or []) if isinstance(a, dict)]
    rows = [r for r in (fv.get("results") or []) if isinstance(r, dict)]
    arm_ids = [_s(a.get("arm_id")) for a in arms if _s(a.get("arm_id"))]
    by_arm = {_s(a.get("arm_id")): a for a in arms}
    seen_arm: set[str] = set()
    for k, a in enumerate(arms):
        aid = _s(a.get("arm_id"))
        if aid and aid in seen_arm:
            out.add(rec, doc, f"arms[{k}].arm_id", "duplicate_arm_id", "error", f"arm id {aid!r} is used twice in this task")
        seen_arm.add(aid)
        if a.get("is_baseline") is not True and a.get("control_type") not in (None, ""):
            out.add(rec, doc, f"arms[{k}].control_type", "control_type_on_treatment", "error", f"arm {aid!r} is not the reference arm but has a control type")
        if a.get("is_baseline") is True and a.get("control_type") in (None, ""):
            out.add(rec, doc, f"arms[{k}].control_type", "no_control_type", "warn", f"the reference arm {aid!r} has no control type")
        if _num(a.get("n_arm")) is not None and a.get("sample_population_type") in (None, ""):
            out.add(rec, doc, f"arms[{k}].sample_population_type", "arm_sample_missing", "warn", f"arm {aid!r} has an n but no sample population type")
    if not rows:
        out.add(rec, doc, "results", "no_results", "error", "no result row was extracted for this task")
        return
    has_baseline = any(a.get("is_baseline") is True for a in arms)
    refs: dict[tuple, dict] = {}                           # reference rows by (arm, measure, subgroup, horizon)
    for r in rows:
        if _s(r.get("vs_arm")) == "":
            refs.setdefault((_s(r.get("arm")), _s(r.get("measure")), _s(r.get("subgroup")), _s(r.get("horizon"))), r)
    mains: dict[tuple, int] = {}
    ev_by_path = {e["field_path"]: e for e in evidence}
    for j, r in enumerate(rows):
        P = f"results[{j}]"
        arm, vs = _s(r.get("arm")), _s(r.get("vs_arm"))
        if arm and arm_ids and arm not in arm_ids:
            out.add(rec, doc, f"{P}.arm", "arm_unknown", "error", f"row names arm {arm!r}, which the task does not have")
        if vs and arm_ids and vs not in arm_ids:
            out.add(rec, doc, f"{P}.vs_arm", "vs_arm_unknown", "error", f"row compares with arm {vs!r}, which the task does not have")
        if vs and vs == arm:
            out.add(rec, doc, f"{P}.vs_arm", "vs_self", "error", "an arm compared with itself")
        ct = _s(r.get("contrast_type"))
        if ct in ("ai_vs_no_ai", "pre_vs_post") and not has_baseline:
            out.add(rec, doc, f"{P}.contrast_type", "no_baseline", "error", f"a {ct} contrast, but no arm of the task is the baseline")
        ev_v, se, lo, hi, p = (_num(r.get(k)) for k in ("effect_value", "se", "ci_low", "ci_high", "p_value"))
        n, n_obs, mean, sd = (_num(r.get(k)) for k in ("n", "n_obs", "mean", "sd"))
        if not vs:                                          # a reference row: statistics only
            if any(r.get(k) not in (None, "") for k in EFFECT_FIELDS):
                out.add(rec, doc, P, "baseline_with_effect", "error", "a reference row (no vs_arm) carries effect fields")
            if ct:
                out.add(rec, doc, f"{P}.contrast_type", "baseline_contrast", "warn", "a reference row has a contrast type")
        else:
            if not ct:
                out.add(rec, doc, f"{P}.contrast_type", "no_contrast_type", "warn", "a treatment row without a contrast type")
            if ev_v is not None and not _s(r.get("estimand_type")):
                out.add(rec, doc, f"{P}.estimand_type", "no_estimand_type", "warn", "an effect without an estimand type")
            if r.get("is_main") is True:
                key = tuple(_s(r.get(k)) for k in KEY)
                if key in mains:
                    out.add(rec, doc, f"{P}.is_main", "duplicate_main", "error", f"a second main row for the same estimand (first at results[{mains[key]}])")
                else:
                    mains[key] = j
        # n, interval, p
        if n is not None and n_obs is not None and n_obs < n:
            out.add(rec, doc, f"{P}.n_obs", "n_obs_below_n", "error", f"n_obs = {n_obs:g} is below n = {n:g}")
        a_n = _num((by_arm.get(arm) or {}).get("n_arm"))
        if n is not None and a_n is not None and n > 1.02 * a_n:
            out.add(rec, doc, f"{P}.n", "n_exceeds_arm", "warn", f"n = {n:g} exceeds the arm's n_arm = {a_n:g}")
        if lo is not None and hi is not None and lo > hi:
            out.add(rec, doc, f"{P}.ci_low", "ci_reversed", "error", f"the interval is reversed ({lo:g} > {hi:g})")
        elif ev_v is not None and lo is not None and hi is not None and not (lo - 1e-9 <= ev_v <= hi + 1e-9):
            out.add(rec, doc, f"{P}.effect_value", "effect_outside_ci", "error", f"effect {ev_v:g} lies outside its interval [{lo:g}, {hi:g}]")
        if p is not None and not 0 <= p <= 1:
            out.add(rec, doc, f"{P}.p_value", "p_out_of_range", "error", f"p = {p:g} is outside [0, 1]")
        for k, v in (("n", n), ("n_obs", n_obs)):
            if v is not None and v <= 0:
                out.add(rec, doc, f"{P}.{k}", "n_nonpositive", "error", f"{k} = {v:g}")
        if sd is not None and sd < 0:
            out.add(rec, doc, f"{P}.sd", "sd_negative", "error", f"sd = {sd:g}")
        et = _s(r.get("effect_type"))
        if se is not None and se <= 0:
            if se == 0 and ev_v is not None:
                out.add(rec, doc, f"{P}.se", "se_rounds_to_zero", "warn", "the standard error is printed as zero")
            else:
                out.add(rec, doc, f"{P}.se", "se_nonpositive", "error", f"se = {se:g}")
        if et == "ratio":
            for k, v in (("effect_value", ev_v), ("ci_low", lo), ("ci_high", hi)):
                if v is not None and v <= 0:
                    out.add(rec, doc, f"{P}.{k}", "ratio_nonpositive", "error", f"a ratio of {v:g}")
        if et == "percent_change" and ev_v is not None and ev_v < -100:
            out.add(rec, doc, f"{P}.effect_value", "percent_below_minus_100", "error", f"a per-cent change of {ev_v:g}")
        if et in SMD_TYPES and ev_v is not None and abs(ev_v) > 3:
            out.add(rec, doc, f"{P}.effect_value", "implausible_smd", "warn", f"a standardised difference of {ev_v:g}")
        # p against the interval
        if p is not None and 0 <= p <= 1 and lo is not None and hi is not None and abs(p - 0.05) > 0.01:
            null = 1.0 if et == "ratio" else 0.0
            excludes = not (lo <= null <= hi)
            if excludes != (p < 0.05):
                out.add(rec, doc, f"{P}.p_value", "p_ci_inconsistent", "warn",
                        f"p = {p:g} but the 95% interval [{lo:g}, {hi:g}] {'excludes' if excludes else 'includes'} {null:g}")
        # the effect against the string it was read from
        er = _s(r.get("effect_reported"))
        if ev_v is not None and er and re.search(r"\d", er) and not number_in_text(ev_v, er):
            out.add(rec, doc, f"{P}.effect_value", "effect_reported_mismatch", "error", f"effect {ev_v:g} is not in the reported string {er!r}")
        # statistics within the rounding
        if ev_v is not None and se is not None and se > 0:
            d_ev, d_se = _decimals(er or None, ev_v), _decimals(None, se)
            z_lo, z_hi = _z_bounds(ev_v, se, d_ev, d_se)
            p_min, p_max = _implied_p(z_hi), _implied_p(z_lo)
            if lo is not None and hi is not None and hi > lo:
                h = 0.5 * 10 ** -max(_decimals(None, lo), _decimals(None, hi)); h_se = 0.5 * 10 ** -d_se
                r_lo, r_hi = max(hi - lo - 2 * h, 0) / (2 * (se + h_se)), (hi - lo + 2 * h) / (2 * max(se - h_se, 1e-12))
                log_ratio = (math.log(hi) - math.log(lo)) / (2 * se) if et == "ratio" and lo > 0 else None
                if (r_hi < 1.9 or r_lo > 2.3) and not (log_ratio is not None and 1.9 <= log_ratio <= 2.3):
                    hint = " (a 90% interval gives 1.64)" if r_lo <= 1.645 <= r_hi else (f" (log scale {log_ratio:.2f})" if log_ratio is not None else "")
                    out.add(rec, doc, f"{P}.ci_low", "ci_se_inconsistent", "warn",
                            f"the interval's half-width is {(hi - lo) / (2 * se):.2f} standard errors, not about 1.96{hint}")
            if p is not None and 0 < p < 1 and _cutoff_disagreement(p, p_min) and _cutoff_disagreement(p, p_max):
                out.add(rec, doc, f"{P}.p_value", "p_se_inconsistent", "warn",
                        f"p = {p:g}, but effect over standard error implies p between {p_min:.3g} and {p_max:.3g}")
            sig = _parse_p(r.get("p_reported"))
            if sig:
                kind, v = sig
                bad = False
                if kind == "lt" and v:
                    bad = p_min > 2 * v
                elif kind == "stars":
                    cut = STAR_DEFAULT.get(int(v))
                    bad = bool(cut) and p_min > 2 * cut
                elif kind == "ns":
                    bad = p_max < 0.025
                if bad:
                    out.add(rec, doc, f"{P}.p_reported", "stars_se_inconsistent", "warn",
                            f"the printed significance {_s(r.get('p_reported'))!r} does not fit effect over standard error (implied p ≥ {p_min:.3g})")
        # the arm means behind a contrast row
        ref = refs.get((vs, _s(r.get("measure")), _s(r.get("subgroup")), _s(r.get("horizon")))) if vs else None
        m_t, s_t, n_t = mean, sd, n
        m_c = _num(ref.get("mean")) if ref else None
        s_c = _num(ref.get("sd")) if ref else None
        n_c = _num(ref.get("n")) if ref else None
        if vs and ev_v is not None and m_t is not None and m_c not in (None, 0):
            diff = m_t - m_c
            if et == "percent_change":
                implied = diff / abs(m_c) * 100
                tol = max(2.0, 0.25 * abs(implied))
                if abs(abs(implied) - abs(ev_v)) > tol:
                    if abs(abs(implied) - abs(ev_v) * 100) <= max(2.0, 0.1 * abs(implied)):
                        out.add(rec, doc, f"{P}.effect_value", "percent_as_proportion", "error",
                                f"a per-cent change of {ev_v:g} where the means imply {implied:.1f}%: printed as a proportion")
                    else:
                        out.add(rec, doc, f"{P}.effect_value", "percent_vs_means", "warn",
                                f"a per-cent change of {ev_v:g} where the arm means imply {implied:.1f}%")
                elif implied * ev_v < 0 and abs(ev_v) > tol / 2:
                    out.add(rec, doc, f"{P}.effect_value", "sign_disagrees", "error",
                            f"the sign of the per-cent change ({ev_v:g}) is opposite to the means' ({implied:.1f}%)")
            elif et == "mean_difference" and abs(diff) > 1e-9:
                d = max(_decimals(None, m_t), _decimals(None, m_c))
                if abs(abs(ev_v) - abs(diff)) > max(0.05 * abs(diff), 1.5 * 10 ** -d):
                    hint = " (a proportion where the means are per cent?)" if abs(abs(ev_v) * 100 - abs(diff)) <= max(0.05 * abs(diff), 1.5 * 10 ** -d) else ""
                    out.add(rec, doc, f"{P}.effect_value", "effect_vs_means", "warn",
                            f"a mean difference of {ev_v:g} where the arm means differ by {diff:g}{hint}")
                elif diff * ev_v < 0:
                    out.add(rec, doc, f"{P}.effect_value", "sign_disagrees", "error",
                            f"the sign of the mean difference ({ev_v:g}) is opposite to treatment minus control ({diff:g})")
            if m_t is not None and m_c is not None and s_t and s_c and n_t and n_c and n_t >= 2 and n_c >= 2:
                sp = math.sqrt(((n_t - 1) * s_t ** 2 + (n_c - 1) * s_c ** 2) / (n_t + n_c - 2))
                if sp > 0 and abs((m_t - m_c) / sp) > 3:
                    out.add(rec, doc, f"{P}.mean", "implausible_g", "warn", f"the arms' means and spreads give |g| = {abs((m_t - m_c) / sp):.1f}")
        # SD versus SE
        if vs and se is not None and se > 0 and s_t is not None and s_c is not None and s_t >= 0 and s_c >= 0:
            if n_t and n_c and n_t >= 2 and n_c >= 2:
                naive = math.sqrt(s_t ** 2 / n_t + s_c ** 2 / n_c)
                as_se = math.sqrt(s_t ** 2 + s_c ** 2)
                if as_se > 0 and abs(se - as_se) / se < 0.1 and naive > 0 and abs(se - naive) / se > 0.5:
                    out.add(rec, doc, f"{P}.sd", "sd_looks_like_se", "error",
                            f"the arms' 'sd' combine to the standard error ({as_se:.3g}): standard errors recorded as standard deviations")
                elif et == "mean_difference" and naive > 0 and not 0.5 <= se / naive <= 2.0:
                    out.add(rec, doc, f"{P}.se", "se_vs_sd_inconsistent", "warn",
                            f"se = {se:g} against {naive:.3g} from the arms' sd and n")
            if max(s_t, s_c) < se * (1 - 1e-9):
                out.add(rec, doc, f"{P}.se", "sd_below_se", "warn", f"both arms' sd ({max(s_t, s_c):g}) are below the standard error ({se:g})")
        # GRIM on means of integer counts
        if mean is not None and n is not None and n >= 1 and _s(r.get("measure_kind")) in COUNT_KINDS:
            snippet = (ev_by_path.get(f"{P}.mean") or {}).get("snippet")
            d = _decimals(snippet if snippet and number_in_text(mean, snippet) else None, mean)
            if 0 < d <= 2:
                k = round(mean * n)
                if abs(k / n - mean) > 0.5 * 10 ** -d + 1e-9:
                    out.add(rec, doc, f"{P}.mean", "grim_inconsistent", "warn",
                            f"a mean of {mean:g} over n = {n:g} integer counts is not a possible mean at this rounding")
        # numbers against their quotes
        for k in NUM_FIELDS:
            v = _num(r.get(k))
            e = ev_by_path.get(f"{P}.{k}")
            if v is None or not e:
                continue
            if e.get("located") is False:
                out.add(rec, doc, f"{P}.{k}", "quote_not_found", "warn", f"the quote for {k} was not located on page {e.get('page')}")
            if not number_in_text(v, e.get("snippet") or ""):
                if k in ("n", "n_obs"):
                    out.add(rec, doc, f"{P}.{k}", "n_not_in_quote", "warn", f"{k} = {v:g} does not appear in its quote")
                elif k in SOFT:
                    out.add(rec, doc, f"{P}.{k}", "number_not_in_quote", "warn", f"{k} = {v:g} does not appear in its quote")
                else:
                    out.add(rec, doc, f"{P}.{k}", "number_not_in_quote", "error", f"{k} = {v:g} does not appear in its quote")
    # duplicates: the same numbers twice within an estimand, or across estimands from different cells
    seen_sig: dict[tuple, int] = {}
    for j, r in enumerate(rows):
        if not _s(r.get("vs_arm")):
            continue
        key = tuple(_s(r.get(k)) for k in KEY)
        sig = tuple(_num(r.get(k)) for k in NUM_FIELDS)
        if any(v is not None for v in sig):
            prev = seen_sig.get((key, sig))
            if prev is not None:
                out.add(rec, doc, f"results[{j}]", "duplicate_estimate", "warn", f"the same numbers as results[{prev}] for the same estimand")
            else:
                seen_sig[(key, sig)] = j
    by_num: dict[tuple, list[tuple[int, tuple, str]]] = {}
    for j, r in enumerate(rows):
        if not _s(r.get("vs_arm")):
            continue
        sig = tuple(_num(r.get(k)) for k in SIG_FIELDS)
        if sum(v is not None for v in sig) >= 2:
            by_num.setdefault(sig, []).append((j, tuple(_s(r.get(k)) for k in KEY), _s(r.get("source"))))
    for sig, hits in by_num.items():
        keys = {h[1] for h in hits}
        sources = {h[2] for h in hits}
        if len(keys) > 1 and not (len(sources) == 1 and next(iter(sources))):
            for j, _k, _src in hits[1:]:
                out.add(rec, doc, f"results[{j}]", "duplicate_numbers_across_estimands", "warn",
                        f"the same numbers as results[{hits[0][0]}] under another estimand")


def check_genai_record(inputs: list[dict], params: dict) -> list[dict]:
    """The pipeline's record checks over the dataset's tasks (see the module docstring)."""
    src = inputs[0]
    out = _Out()
    by_doc: dict[str, list[dict]] = {}
    for r in src.get("records") or []:
        by_doc.setdefault(r["document_id"], []).append(r)
    evidence = src.get("evidence") or {}
    for doc_id, recs in by_doc.items():
        recs.sort(key=lambda r: r.get("entry_index") or 0)
        pm = ((src.get("documents") or {}).get(doc_id) or {}).get("paper_metadata") or {}
        _check_dates(pm, doc_id, out)
        seen: dict[str, dict] = {}
        for rec in recs:
            tid = _s((rec.get("field_values") or {}).get("task_id"))
            if tid and tid in seen:
                out.add(rec, doc_id, "task_id", "duplicate_task_id", "error", f"task id {tid!r} is used twice in this paper")
            seen.setdefault(tid, rec)
            _check_task(rec, doc_id, evidence.get(rec["id"]) or [], out)
    return out.items


CODES = {
    "no_results": "error", "duplicate_task_id": "error", "duplicate_arm_id": "error", "control_type_on_treatment": "error",
    "no_control_type": "warn", "arm_sample_missing": "warn", "bad_date": "warn",
    "arm_unknown": "error", "vs_arm_unknown": "error", "vs_self": "error", "no_baseline": "error", "baseline_with_effect": "error",
    "baseline_contrast": "warn", "no_contrast_type": "warn", "no_estimand_type": "warn", "duplicate_main": "error",
    "n_obs_below_n": "error", "n_exceeds_arm": "warn", "ci_reversed": "error", "effect_outside_ci": "error", "p_out_of_range": "error",
    "n_nonpositive": "error", "sd_negative": "error", "se_rounds_to_zero": "warn", "se_nonpositive": "error", "ratio_nonpositive": "error",
    "percent_below_minus_100": "error", "implausible_smd": "warn", "p_ci_inconsistent": "warn", "effect_reported_mismatch": "error",
    "ci_se_inconsistent": "warn", "p_se_inconsistent": "warn", "stars_se_inconsistent": "warn",
    "percent_as_proportion": "error", "percent_vs_means": "warn", "sign_disagrees": "error", "effect_vs_means": "warn", "implausible_g": "warn",
    "sd_looks_like_se": "error", "se_vs_sd_inconsistent": "warn", "sd_below_se": "warn", "grim_inconsistent": "warn",
    "quote_not_found": "warn", "number_not_in_quote": "error", "n_not_in_quote": "warn",
    "duplicate_estimate": "warn", "duplicate_numbers_across_estimands": "warn",
}
