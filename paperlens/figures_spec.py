"""Figure-spec grammar for the analysis/dashboard builder.

One figure is the single structured artifact the LLM produces; the same spec drives
both the D3 render (front-end) and — for the papers entry path — the derived
extraction schema. This module owns the meta-prompts, the bounded grammar, and a
tolerant validate/repair pass so a slightly-off LLM response never 500s.
"""
from __future__ import annotations

import re
from typing import Any

from .contract import parse_result_json

CHART_KINDS = frozenset({
    "bar", "grouped_bar", "stacked_bar", "line", "scatter", "histogram", "forest",
})
AGGREGATES = frozenset({"none", "count", "mean", "median", "sum"})
VAR_TYPES = frozenset({"quant", "categorical", "temporal", "ordinal"})
_SUFFICIENCY = frozenset({"ok", "partial", "insufficient"})


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "figure"


# ── the grammar, described for the model ──────────────────────────────────────
_GRAMMAR = """Return ONLY one JSON object (no prose, no markdown fences):
{ "figures": [ FIGURE, ... ] }  — 3 to 6 figures.

Each FIGURE:
{
  "id": short-slug, "title": string, "question": the research question it answers,
  "chart_kind": one of "bar" | "grouped_bar" | "stacked_bar" | "line" | "scatter"
              | "histogram" | "forest",
  "encodings": { "x": {"var": NAME}|null, "y": {"var": NAME}|null,
                 "color": {"var": NAME}|null, "facet": {"var": NAME}|null,
                 "size": {"var": NAME}|null, "error": {"lo": NAME, "hi": NAME}|null },
  "transform": { "aggregate": "none"|"count"|"mean"|"median"|"sum",
                 "group_by": [NAME,...], "bin": {"var": NAME, "maxbins": int}|null },
  "scales": { "x": {"type":"linear"|"log"|"band"|"time","zero":bool},
              "y": {"type":"linear"|"log"|"band","zero":bool} },
  "required_variables": [ { "name": NAME, "type":"quant"|"categorical"|"temporal"|"ordinal",
                            "unit": string|null, "description": string,
                            "grounding": string|null, "optional": bool } ],
  "data_sufficiency": "ok"|"partial"|"insufficient"
}

RULES:
- Every NAME used in any encoding MUST also appear in required_variables[].name.
- Respect each variable's TYPE (numeric vs categorical), given below. A CATEGORICAL
  variable goes on the category axis (for a bar, that is encodings.y) or on color/facet;
  a NUMERIC variable is the measure (the value axis / x for a bar, y for line, both for
  scatter). NEVER put a numeric variable on the category axis, and NEVER aggregate
  (mean/median/sum) a categorical/text variable — that yields an empty chart.
- aggregate:"count" counts records per categorical group and needs NO value variable.
  aggregate:"mean"/"median"/"sum" REQUIRE a numeric value variable.
- scatter: x and y BOTH numeric. histogram: x numeric. line: x numeric or temporal,
  y a numeric measure. forest: x = numeric point estimate, error = {lo,hi} numeric CI
  bounds, y = a categorical study/row label.
- Prefer variables that genuinely answer the goal; keep figures distinct."""


def _classify(field_keys, sample_records) -> tuple[list[str], list[str]]:
    """Split variables into (numeric, categorical) from the sample values, so the model
    encodes each in its correct role."""
    def isnum(v):
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            return True
        if isinstance(v, str):
            try:
                float(v.replace(",", "").replace("%", "").strip())
                return True
            except ValueError:
                return False
        return False
    numeric, categorical = [], []
    for k in sorted(set(field_keys)):
        vals = [s[k] for s in (sample_records or []) if isinstance(s, dict) and s.get(k) is not None]
        frac = (sum(isnum(v) for v in vals) / len(vals)) if vals else 0.0
        (numeric if frac >= 0.6 else categorical).append(k)
    return numeric, categorical


def dataset_prompt(goals: str, field_keys: list[str], sample_records: list[dict]) -> str:
    import json
    numeric, categorical = _classify(field_keys, sample_records)
    numeric = numeric + ["year"]                 # paper-level extras
    categorical = categorical + ["primary_topic"]
    samples = json.dumps((sample_records or [])[:15], default=str)[:6000]
    return (
        "You are designing a dashboard of figures over an existing extracted dataset.\n\n"
        f"USER GOAL:\n{goals or '(none stated — propose the most useful standard figures)'}\n\n"
        f"NUMERIC variables (use as measures / values to aggregate): {', '.join(numeric) or '(none)'}\n"
        f"CATEGORICAL variables (use as categories / color / facet): {', '.join(categorical) or '(none)'}\n\n"
        f"SAMPLE RECORDS (field_values):\n{samples}\n\n"
        "Only reference variables listed above, each in its correct role. " + _GRAMMAR
    )


def papers_prompt(goals: str) -> str:
    return (
        "You are designing a dashboard of figures to build from a set of academic papers "
        "(page images are attached). Propose figures that answer the goal, and for EACH "
        "required variable set `grounding` to where you see that value in the papers (e.g. "
        "\"Table 3, column 'β'\"); if a variable is not clearly present, set its grounding to "
        "null and mark the figure's data_sufficiency accordingly.\n\n"
        f"USER GOAL:\n{goals or '(none stated — propose the most useful standard figures)'}\n\n"
        + _GRAMMAR
    )


# ── tolerant validate / repair ────────────────────────────────────────────────
# Real LLMs freely use synonyms ("bar chart", "scatterplot") and alias keys
# ("field"/"column" instead of "var"), so we NORMALIZE aggressively and only ever
# drop a figure that isn't an object — never lose a usable suggestion.
_CHART_SYNONYMS = {
    "bar": "bar", "barchart": "bar", "column": "bar", "columnchart": "bar", "hbar": "bar",
    "groupedbar": "grouped_bar", "groupbar": "grouped_bar", "clusteredbar": "grouped_bar",
    "multibar": "grouped_bar", "groupedcolumn": "grouped_bar",
    "stackedbar": "stacked_bar", "stackbar": "stacked_bar", "stackedcolumn": "stacked_bar",
    "line": "line", "linechart": "line", "trend": "line", "timeseries": "line", "area": "line",
    "scatter": "scatter", "scatterplot": "scatter", "point": "scatter", "bubble": "scatter", "dot": "scatter",
    "histogram": "histogram", "hist": "histogram", "distribution": "histogram", "density": "histogram",
    "forest": "forest", "forestplot": "forest", "ci": "forest", "errorbar": "forest",
    "coefficient": "forest", "coefplot": "forest", "whisker": "forest", "interval": "forest",
}
_AGG_SYNONYMS = {"none": "none", "count": "count", "n": "count", "cnt": "count", "frequency": "count",
                 "mean": "mean", "avg": "mean", "average": "mean", "sum": "sum", "total": "sum",
                 "median": "median", "med": "median"}
_VAR_ALIASES = ("var", "field", "column", "name", "value", "key")


def _norm_kind(k: Any) -> str:
    key = re.sub(r"[^a-z]", "", str(k).lower()) if k is not None else ""
    return _CHART_SYNONYMS.get(key, "bar")   # default unknown → bar (never drop on kind)


def _chan_var(val: Any) -> str | None:
    if isinstance(val, str):
        return val or None
    if isinstance(val, dict):
        for a in _VAR_ALIASES:
            if val.get(a):
                return str(val[a])
    return None


def _norm_encodings(raw: Any) -> tuple[dict, set[str]]:
    raw = raw if isinstance(raw, dict) else {}
    enc, names = {}, set()
    for ch in ("x", "y", "color", "facet", "size"):
        v = _chan_var(raw.get(ch))
        enc[ch] = {"var": v} if v else None
        if v:
            names.add(v)
    err = raw.get("error") if isinstance(raw.get("error"), dict) else {}
    lo = err.get("lo") or err.get("low") or err.get("lower") or err.get("ci_low") or err.get("min")
    hi = err.get("hi") or err.get("high") or err.get("upper") or err.get("ci_high") or err.get("max")
    enc["error"] = {"lo": lo, "hi": hi} if (lo or hi) else None
    if lo:
        names.add(str(lo))
    if hi:
        names.add(str(hi))
    return enc, names


def _coerce_figure(fig: Any) -> dict:
    if not isinstance(fig, dict):
        raise ValueError("figure is not an object")
    kind = _norm_kind(fig.get("chart_kind"))
    enc, varnames = _norm_encodings(fig.get("encodings"))

    reqs = fig.get("required_variables")
    reqs = [r for r in reqs if isinstance(r, dict) and r.get("name")] if isinstance(reqs, list) else []
    have = {r["name"] for r in reqs}
    for v in varnames:                       # every encoding var must be declared
        if v not in have:
            reqs.append({"name": v, "type": "quant", "unit": None,
                         "description": v, "grounding": None, "optional": False})
    for r in reqs:
        if r.get("type") not in VAR_TYPES:
            r["type"] = "quant"
        r.setdefault("unit", None); r.setdefault("description", r["name"])
        r.setdefault("grounding", None); r["optional"] = bool(r.get("optional", False))

    transform = fig.get("transform") if isinstance(fig.get("transform"), dict) else {}
    transform["aggregate"] = _AGG_SYNONYMS.get(str(transform.get("aggregate")).lower(), "count")
    transform["group_by"] = transform.get("group_by") if isinstance(transform.get("group_by"), list) else []
    if not isinstance(transform.get("bin"), dict):
        transform["bin"] = None

    suff = fig.get("data_sufficiency")
    if suff not in _SUFFICIENCY:
        suff = "ok"

    return {
        "id": _slug(fig.get("id") or fig.get("title") or kind),
        "title": str(fig.get("title") or "Figure"),
        "question": str(fig.get("question") or ""),
        "chart_kind": kind,
        "encodings": enc,
        "transform": transform,
        "scales": fig.get("scales") if isinstance(fig.get("scales"), dict) else {},
        "required_variables": reqs,
        "data_sufficiency": suff,
    }


def validate_figures(raw: Any) -> tuple[list[dict], list[dict]]:
    """Return (figures, dropped). Never raises. Coerces/repairs where cheap; drops a
    figure only when fundamentally malformed (bad/absent chart_kind, not an object)."""
    figs = raw.get("figures") if isinstance(raw, dict) else raw
    if not isinstance(figs, list):
        return [], [{"index": None, "reason": "no figures array in model output"}]
    out, dropped = [], []
    for i, f in enumerate(figs):
        try:
            out.append(_coerce_figure(f))
        except Exception as exc:              # noqa: BLE001 — one bad figure never sinks the rest
            dropped.append({"index": i, "reason": str(exc)})
    return out, dropped


def parse_and_validate(text: str) -> tuple[list[dict], list[dict]]:
    return validate_figures(parse_result_json(text))
