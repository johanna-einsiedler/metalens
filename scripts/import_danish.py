#!/usr/bin/env python3
"""Import the danish-register-econ extractions into Metalens as two datasets.

    uv run python scripts/import_danish.py --email me@example.org --password …            # all processed papers
    uv run python scripts/import_danish.py --artids dk_W2213841331 --replace              # one paper again
    uv run python scripts/import_danish.py --out /tmp/converted                           # only convert, write JSON, no server
    uv run python scripts/import_danish.py --base-url https://metalens.tech --email me@example.org \
        --only claims --claims-title "…"                                                    # to the hosted site; asks for the password
    … --claims-dataset <id> --resume                                                        # carry on after an interruption

The pipeline's per-paper files (stated claims + claim map + result claims, and the tables file)
become one ``register-claims`` and one ``register-tables`` result each — the canonical
single-paper shape ``{paper_metadata, <entries>, evidence}`` — and are posted with the PDF to
``POST /api/ingest-pdf``, so pages are rendered and quotes located exactly as for a live run.

What has no home in the presets is dropped: quote_verified / match_ratio / coverage, evidence
roles, checks, sessions and costs, the tables' inventory / classification / usage, cells'
statistic_detail / significance_level / col_index, and the result claims' definitions, register
sources and adjustment sets.
"""
from __future__ import annotations

# What actually made these records. It is stored where a live run stores its model, so a record
# imported here says the agent pipeline produced it rather than only "imported". The presets are
# `mode: "import"` for the same reason: no prompt in Metalens made this data.
PRODUCED_BY = "claude-opus-5 · danish-register-econ agent pipeline"

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

CORPUS = Path.home() / "Documents/research/44_automated_science/danish-register-econ/corpora/danish-register-econ"
PRESET_CLAIMS, PRESET_TABLES = "register-claims", "register-tables"

# ── the exhibit grammar (ported from pipeline/link_tables.py) ───────────────────────────────
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_ROMAN_RE = re.compile(r"^([IVX]{1,6})$")
_TOKEN_RE = re.compile(r"^([A-Z]?)[.\s-]*(\d{1,3})$")
_APPENDIX_ROMAN_RE = re.compile(r"^([A-Z])[.\s-]*([IVX]{1,6})$")
TABLE_RE = re.compile(r"tables?\s+([A-Za-z]?\.?\s?(?:[IVXivx]{1,6}|\d{1,3}))(?![A-Za-z0-9])", re.I)
PANEL_RE = re.compile(r"(?:panel|part)\s+([A-Z](?:\.[ivx]+|\d{1,2})?)(?![A-Za-z0-9])", re.I)
COL_RE = re.compile(r"col(?:umn)?s?\.?\s*((?:\(?\d+\)?(?:\s*[-,and]+\s*)?)+)", re.I)
FIG_REF_RE = re.compile(r"fig(?:ure)?s?\.?\s+([A-Z]?\d+)", re.I)


def roman_to_int(s: str) -> int:
    total, prev = 0, 0
    for ch in reversed(s):
        v = _ROMAN[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def norm_table_token(tok) -> str | None:
    """'Table II' → '2', 'A.I' → 'A1', 'Table A1' → 'A1', '(3)' → '3'; None when not a table token."""
    if tok is None:
        return None
    s = re.sub(r"^\s*TABLES?\s*", "", str(tok).upper().strip())
    s = re.sub(r"[()\[\]]", "", s).strip()
    m = _ROMAN_RE.match(s)
    if m:
        return str(roman_to_int(m.group(1)))
    m = _TOKEN_RE.match(s)
    if m:
        return f"{m.group(1)}{int(m.group(2))}"
    m = _APPENDIX_ROMAN_RE.match(s)
    if m:
        return f"{m.group(1)}{roman_to_int(m.group(2))}"
    return None


_HORIZON = re.compile(r"(years?\s*[-–—]?\s*\d+\s*(?:[-–—to]+\s*\d+)?|short[- ]run|long[- ]run|post[- ]\w+\s+period|first\s+\w+\s+years?)", re.I)


def horizon_of(src: str) -> str | None:
    """The window an estimate covers, when the printed reference names one ('Table 3, Panel A.
    Years 0-1' → 'years 0-1'). Never invented: only what the paper's own label says."""
    m = _HORIZON.search(src or "")
    return re.sub(r"\s+", " ", m.group(1)).strip().lower() if m else None


def parse_source_table(s: str) -> dict:
    """'Table 3, Panel A, column (2)' → {exhibit: table, source_table: 'Table 3', panel: 'A', column: '(2)'};
    'Figure 4, Panel A …' → {exhibit: figure, source_table: 'Figure 4', panel: 'A', column: None}."""
    s = s or ""
    t = TABLE_RE.search(s)
    f = FIG_REF_RE.search(s)
    panel = None
    pm = PANEL_RE.search(s)
    if pm:
        panel = pm.group(1).upper()
    c = COL_RE.search(s)
    cols = re.findall(r"\d+", c.group(1)) if c else []
    if t and (not f or t.start() < f.start()):
        return {"exhibit": "table", "source_table": f"Table {norm_table_token(t.group(1))}", "panel": panel, "column": f"({cols[0]})" if cols else None}
    if f:
        return {"exhibit": "figure", "source_table": f"Figure {f.group(1).upper()}", "panel": panel, "column": None}
    return {"exhibit": "table", "source_table": s.strip()[:60] or None, "panel": panel, "column": f"({cols[0]})" if cols else None}


# ── small helpers ────────────────────────────────────────────────────────────────────────────
def _val(x):
    return x.get("value") if isinstance(x, dict) and "value" in x else x


def _num(x):
    x = _val(x)
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


def _int_page(p):
    try:
        return int(str(p).strip())
    except (TypeError, ValueError):
        return None


def page_offset(stated: dict) -> int:
    """PDF page index minus printed page label, by majority over the stated claims' evidence."""
    diffs = [e["pdf_page"] - _int_page(e["page"]) for c in stated.get("claims") or [] for e in c.get("evidence") or []
             if e.get("pdf_page") and _int_page(e.get("page")) is not None]
    return Counter(diffs).most_common(1)[0][0] if diffs else 0


def _pdf_page(printed, offset: int, fallback: int = 1) -> int:
    p = _int_page(printed)
    return max(1, p + offset) if p is not None else fallback


def paper_metadata(artid: str, papers: dict, stated: dict) -> dict:
    row = papers.get(artid) or {}
    authors = None
    return {"title": row.get("title") or artid, "doi": row.get("doi") or None, "year": int(row["year"]) if str(row.get("year") or "").isdigit() else None,
            "authors": authors, "journal": row.get("journal") or None}


# ── the claims chain ─────────────────────────────────────────────────────────────────────────
def _figure_panels(figures: dict) -> dict:
    """{"<artid>::F2::B1": (figure, panel)} from the pipeline's figures file."""
    out = {}
    for f in (figures or {}).get("figures") or []:
        for p in f.get("panels") or []:
            if f.get("figure_id") and p.get("panel_id"):
                out[f"{f['figure_id']}::{p['panel_id']}"] = (f, p)
    return out


def link_result(mt: dict, regs: dict, panels: dict, offset: int, path: str, k: int) -> tuple[dict | None, list[dict], str | None]:
    """One claim-map (v1) link → (result row, evidence items, warning). The link names the
    regression column (or figure panel) by id and the coefficient by its row index; the value is
    the transcribed cell, so the number is the one printed in the table."""
    from paperlens import horizons
    rid, tr = mt.get("regression_id"), mt.get("target_row")
    joined = _num((mt.get("join") or {}).get("cell_value"))
    horizon = mt.get("horizon")
    hz = horizons.parse(horizon) if horizon else None
    why = mt.get("why")
    if mt.get("derivation"):
        why = f"{why or ''} Derivation: {mt['derivation']}".strip()
    row = {"result_id": f"R{k + 1}", "estimand": mt.get("estimand"), "role": mt.get("role") or "supporting",
           "subgroup": mt.get("subgroup"), "horizon": horizon,
           "horizon_lo": hz[0] if hz else None, "horizon_hi": hz[1] if hz else None, "horizon_kind": hz[2] if hz else None,
           "treatment_relation": mt.get("treatment_relation"), "outcome_relation": mt.get("outcome_relation"),
           "sign_consistent": mt.get("sign_consistent"), "why": why}
    items: list[dict] = []
    reg = regs.get(rid)
    if reg is not None:
        cells = reg.get("cells") or []
        cell = next((c for c in cells if c.get("row_index") == tr), None)
        se = next((c for c in cells if c.get("refers_to") == tr and c.get("row_type") == "se" and c.get("numeric_value") is not None), None)
        pt = _num((cell or {}).get("numeric_value"))
        row.update({"exhibit": "table", "source_table": reg.get("table_number"), "panel": reg.get("panel"), "column": reg.get("column"),
                    "row_label": (cell or {}).get("row_label") or ((reg.get("target_regressors") or [{}])[0]).get("name"),
                    "outcome_variable": reg.get("outcome_variable"), "regressor": (cell or {}).get("row_label"),
                    "value_from": "printed" if pt is not None else "absent", "point_estimate": pt,
                    "estimate_se": _num(se["numeric_value"]) if se else None})
        if cell is not None:                            # the table line: label and value as printed
            items.append({"snippet": f"{cell.get('row_label') or ''} {cell.get('raw_text') or ''}".strip(), "page": _pdf_page(reg.get("page"), offset),
                          "source": reg.get("table_number"), "field": [path] + ([f"{path}.point_estimate"] if pt is not None else [])})
        warn = None if cell is not None else f"{rid}: no cell at row {tr}"
    elif rid in panels:
        fig, panel = panels[rid]
        ann = next((a for a in panel.get("printed_annotations") or [] if joined is not None and _num(a.get("value")) is not None
                    and abs(_num(a["value"]) - joined) <= max(5e-4, 5e-3 * abs(joined))), None)
        row.update({"exhibit": "figure", "source_table": fig.get("figure_number"), "panel": panel.get("panel_id"), "column": None,
                    "row_label": (ann or {}).get("text"), "outcome_variable": (((panel.get("axes") or {}).get("y") or {}).get("label")),
                    "regressor": None, "value_from": "printed" if ann else "absent",
                    "point_estimate": _num(ann.get("value")) if ann else None,     # a figure's number only when it PRINTS it
                    "estimate_se": _num(ann.get("se")) if ann else None})
        if fig.get("caption_verbatim"):                 # the caption names the figure on its page
            items.append({"snippet": fig["caption_verbatim"], "page": _int_page(fig.get("page")) or 1, "source": fig.get("figure_number"), "field": [path]})
        warn = None
    else:
        return None, [], f"{rid}: neither a transcribed regression column nor a figure panel"
    if mt.get("pointer_quote") and mt.get("pointer_quote_verified"):     # the sentence that points to this estimate
        items.append({"snippet": mt["pointer_quote"], "page": mt.get("pointer_pdf_page") or 1, "source": "pointer", "field": [path]})
    return row, items, warn


def convert_claims(stated: dict, cmap: dict, results: dict, tables: dict, meta: dict, offset: int, figures: dict | None = None) -> tuple[dict, list[str]]:
    """stated_claims + claim_map + result claims (+ the tables file for the cited cells) → one
    register-claims result. Returns (result, warnings)."""
    warnings: list[str] = []
    regs = {r["regression_id"]: r for r in (tables.get("regressions") or [])}
    panels = _figure_panels(figures or {})
    maps = {m["id"]: m for m in (cmap.get("claims") or [])}
    claims, evidence = [], []
    for i, sc in enumerate(stated.get("claims") or []):
        ev = sc.get("evidence") or []
        anchor_item = next((e for e in ev if e.get("role") == "anchor"), None) or (ev[0] if ev else None)
        anchor_text = (sc.get("anchor") or {}).get("quote") or (anchor_item or {}).get("quote") or ""
        anchor_page = (anchor_item or {}).get("pdf_page") or _pdf_page((anchor_item or {}).get("page"), offset)
        scope = sc.get("scope") or {}
        m = maps.get(sc["id"]) or {}
        # the introduction sentence: the stated claim's own introduction quote first, else the map's
        intro = next((e for e in ev if e.get("section") == "introduction" and e.get("role") in ("gives_direction", "names_variables", "gives_magnitude", "scope")), None) \
            or next((e for e in ev if e.get("section") == "introduction"), None) \
            or next((e for e in m.get("introduction_evidence") or [] if e.get("section") == "introduction"), None) \
            or next((e for e in m.get("introduction_evidence") or [] if e.get("section") == "body"), None)
        notes = [x for x in (sc.get("support_note"), m.get("unmatched_reason")) if x] + [f"flag: {f}" for f in sc.get("flags") or []]
        claim = {
            "claim_id": sc["id"], "type": sc.get("type"), "statement": sc.get("statement"), "cause": sc.get("cause"), "effect": sc.get("effect"),
            "sign": sc.get("sign"), "anchor_quote": anchor_text, "intro_sentence": (intro or {}).get("quote"),
            "support": sc.get("support") or "explicit", "refined_by_introduction": bool(sc.get("refined_by_introduction")),
            "scope_setting": scope.get("setting"), "scope_period": scope.get("period"), "scope_population": scope.get("population"),
            "scope_identification": scope.get("identification"), "magnitude_stated": sc.get("magnitude_stated"),
            "qualifies": list(sc.get("qualifies") or []), "moderator": sc.get("moderator"), "relation": sc.get("relation"),
            "notes": "; ".join(notes) or None, "results": [],
            "confidence": {"claim": {"level": "high" if sc.get("support") == "explicit" else "medium" if sc.get("support") == "assembled" else "low", "notes": ""},
                           "scope": {"level": "high", "notes": ""}}}
        paths = [f"claims[{i}]", f"claims[{i}].anchor_quote"] + ([f"claims[{i}].sign"] if sc.get("sign") else []) \
            + ([f"claims[{i}].magnitude_stated"] if sc.get("magnitude_stated") and sc["magnitude_stated"] in anchor_text else [])
        evidence.append({"snippet": anchor_text, "page": anchor_page, "source": "abstract", "field": paths})
        if intro:
            ipaths = [f"claims[{i}].intro_sentence"] + ([f"claims[{i}].magnitude_stated"] if sc.get("magnitude_stated") and sc["magnitude_stated"] in intro.get("quote", "") and f"claims[{i}].magnitude_stated" not in paths else [])
            evidence.append({"snippet": intro["quote"], "page": intro.get("pdf_page") or _pdf_page(intro.get("page"), offset), "source": intro.get("section") or "introduction", "field": ipaths})
        cited_mag = any(f"claims[{i}].magnitude_stated" in it["field"] for it in evidence[-2:])
        for e in ev:                                    # the other quotes identify the claim as a whole (and may hold the stated magnitude)
            if e is anchor_item or e is intro or not e.get("quote"):
                continue
            fields = [f"claims[{i}]"]
            if sc.get("magnitude_stated") and not cited_mag and sc["magnitude_stated"] in e["quote"]:
                fields.append(f"claims[{i}].magnitude_stated"); cited_mag = True
            evidence.append({"snippet": e["quote"], "page": e.get("pdf_page") or _pdf_page(e.get("page"), offset), "source": e.get("section") or None, "field": fields})
        for mt in m.get("matches") or []:
            j = len(claim["results"])                   # the row's index as stored, so evidence paths stay aligned
            if mt.get("regression_id"):                 # claim map v1: the link names the column and the row itself
                row, items, warn = link_result(mt, regs, panels, offset, f"claims[{i}].results[{j}]", j)
                if warn:
                    warnings.append(f"{sc['id']}: {warn}")
                if row is not None:
                    row["confidence"] = {"results": {"level": m.get("confidence") or "medium", "notes": ""}}
                    claim["results"].append(row); evidence.extend(items)
                continue
            rc = results.get(mt.get("claim_id"))
            if rc is None:
                warnings.append(f"{sc['id']}: match {mt.get('claim_id')} is not among the result claims"); continue
            est, rel = rc.get("estimation") or {}, rc.get("relationship") or {}
            src = str(_val(est.get("source_table")) or "")
            where = parse_source_table(src)
            rid = est.get("source_table_id")
            reg = regs.get(rid) if rid else None
            mag = rel.get("magnitude") or {}
            pt, se = _num(mag.get("point_estimate")), _num(mag.get("se"))
            cell = None
            if reg is not None:
                cells = reg.get("cells") or []
                targets = {t.get("row_index") for t in reg.get("target_regressors") or []}
                coef = [c for c in cells if c.get("row_type") == "coefficient" and c.get("numeric_value") is not None]
                cell = next((c for c in coef if c.get("row_index") in targets and pt is not None and abs(float(c["numeric_value"]) - pt) <= max(5e-4, 5e-3 * abs(pt))), None) \
                    or next((c for c in coef if pt is not None and abs(float(c["numeric_value"]) - pt) <= max(5e-4, 5e-3 * abs(pt))), None) \
                    or next((c for c in coef if c.get("row_index") in targets), None)
                where = {"exhibit": "table", "source_table": reg.get("table_number") or where["source_table"], "panel": reg.get("panel") or where["panel"], "column": reg.get("column") or where["column"]}
            row = {"result_id": mt["claim_id"], "role": mt.get("role") or "supporting", "exhibit": where["exhibit"], "source_table": where["source_table"],
                   "panel": where["panel"], "column": where["column"], "row_label": (cell or {}).get("row_label") or ((reg or {}).get("target_regressors") or [{}])[0].get("name"),
                   "subgroup": None, "horizon": horizon_of(src),   # the old extraction records no subgroup; the horizon is in the printed reference
                   "outcome_variable": (reg or {}).get("outcome_variable") or (rc.get("outcome") or {}).get("label"), "regressor": (rc.get("treatment") or {}).get("label"),
                   "point_estimate": pt, "estimate_se": se, "treatment_relation": mt.get("treatment_relation"), "outcome_relation": mt.get("outcome_relation"),
                   "sign_consistent": mt.get("sign_consistent"), "why": mt.get("why"),
                   "confidence": {"results": {"level": m.get("confidence") or "medium", "notes": ""}}}
            claim["results"].append(row)
            rpaths = [f"claims[{i}].results[{j}]"] + ([f"claims[{i}].results[{j}].point_estimate"] if pt is not None else [])
            if cell is not None:                        # the table line: label and value as printed
                evidence.append({"snippet": f"{cell.get('row_label')} {cell.get('raw_text')}".strip(), "page": _pdf_page(reg.get("page"), offset),
                                 "source": src or where["source_table"], "field": rpaths})
            else:                                       # a figure annotation or an uncited estimate: the quote that holds the number, else the source line
                quotes = (rc.get("provenance") or {}).get("quotes") or []
                q = next((q for q in quotes if pt is not None and str(pt).rstrip("0").rstrip(".") in (q.get("text") or "")), None) or (quotes[0] if quotes else None)
                if q:
                    evidence.append({"snippet": q["text"], "page": _pdf_page(q.get("page"), offset), "source": src or where["source_table"],
                                     "field": rpaths if (pt is not None and str(pt).rstrip("0").rstrip(".") in q.get("text", "")) else rpaths[:1]})
                else:
                    warnings.append(f"{sc['id']}/{mt['claim_id']}: no quote for the estimate")
        claims.append(claim)
    out = {"paper_metadata": {**meta, "abstract_source": "printed" if claims else "none"}, "claims": claims, "evidence": evidence}
    return out, warnings


# ── the tables ───────────────────────────────────────────────────────────────────────────────
_CELL_KEEP = ("row_label", "row_index", "column_label", "raw_text", "numeric_value", "row_type", "refers_to", "significance_stars")
_MODEL = {"ols": "OLS", "2sls": "2SLS", "iv": "2SLS", "first stage": "first_stage", "first_stage": "first_stage", "reduced form": "reduced_form", "reduced_form": "reduced_form",
          "did": "diff_in_diff", "diff-in-diff": "diff_in_diff", "difference-in-differences": "diff_in_diff", "diff_in_diff": "diff_in_diff", "dd": "diff_in_diff",
          "event study": "event_study", "event_study": "event_study", "rd": "RD", "rdd": "RD", "probit": "probit_logit", "logit": "probit_logit", "poisson": "poisson", "quantile": "quantile"}


def _model_type(v) -> str | None:
    if not v:
        return None
    s = str(v).strip()
    low = s.lower()
    for k, m in _MODEL.items():
        if low == k or low.startswith(k + " ") or low.startswith(k + "-") or low.startswith(k + " ("):
            return m
    return s[:40]     # allow_other: kept as the paper's own words


def convert_tables(tables: dict, meta: dict, offset: int) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    regressions, evidence = [], []
    for k, r in enumerate(tables.get("regressions") or []):
        rid = str(r.get("regression_id") or "")
        rid = rid.split("::", 1)[1] if rid.count("::") >= 3 else rid
        spec = r.get("spec") or {}
        ent = {"regression_id": rid, "table_number": r.get("table_number"), "table_title": r.get("table_title"), "panel": r.get("panel"), "column": r.get("column"),
               "column_label": next((c.get("column_label") for c in r.get("cells") or [] if c.get("column_label") and c.get("column_label") != r.get("column")), None),
               "outcome_variable": r.get("outcome_variable"), "target_regressors": [t.get("name") for t in r.get("target_regressors") or [] if t.get("name")],
               "model_type": _model_type(spec.get("model_type")), "fixed_effects": list(spec.get("fixed_effects") or []), "controls": list(spec.get("controls") or []),
               "iv": spec.get("iv"), "weights_type": spec.get("weights_type") if spec.get("weights_type") in ("none", "aweight", "pweight", "fweight") else ("unknown" if spec.get("weights_type") else None),
               "weights_variable": spec.get("weights_variable"), "cluster_level": spec.get("cluster_level"),
               "se_type": spec.get("se_type") if spec.get("se_type") in ("conventional", "robust", "clustered", "bootstrap") else ("unknown" if spec.get("se_type") else None),
               "sample_restrictions": spec.get("sample_restrictions"), "unit_of_observation": spec.get("unit_of_observation"), "time_period": spec.get("time_period"),
               "outcome_construction": spec.get("outcome_construction"), "data_construction_steps": list(spec.get("data_construction_steps") or []),
               "reproduction_notes": spec.get("reproduction_notes"), "notes": r.get("notes"),
               "cells": [{c2: c.get(c2) for c2 in _CELL_KEEP} for c in r.get("cells") or []],
               "confidence": {"spec": {"level": "high", "notes": ""}, "cells": {"level": "high", "notes": ""}}}
        for c in ent["cells"]:
            if c["row_type"] not in ("coefficient", "se", "t_stat", "p_value", "ci", "statistic_r2", "statistic_n_obs", "statistic_f", "statistic_other", "string", "panel_header"):
                c["row_type"] = "string" if c.get("numeric_value") is None else "statistic_other"
            c["significance_stars"] = int(c.get("significance_stars") or 0)
        regressions.append(ent)
        page = _pdf_page(r.get("page"), offset)
        own = [e for e in r.get("evidence") or [] if e.get("snippet")]
        if own:
            evidence.append({"snippet": own[0]["snippet"], "page": _pdf_page(own[0].get("page"), offset, page), "source": r.get("table_number"),
                             "field": [f"regressions[{k}]", f"regressions[{k}].cells"]})
        else:
            evidence.append({"snippet": r.get("table_title") or r.get("table_number") or "", "page": page, "source": r.get("table_number"),
                             "field": [f"regressions[{k}]", f"regressions[{k}].cells"]})
    return {"paper_metadata": dict(meta), "regressions": regressions, "evidence": evidence}, warnings


# ── the corpus on disk ───────────────────────────────────────────────────────────────────────
def load_papers(corpus: Path) -> dict:
    with (corpus / "papers_selected.csv").open(newline="", encoding="utf-8") as f:
        return {r["artid"]: r for r in csv.DictReader(f)}


def load_results(corpus: Path, artid: str) -> dict:
    """{claim_id: result claim}: the waiting copy first, else the promoted database."""
    p = corpus / "extractions" / "_waiting" / "claims" / f"{artid}.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        return {c["claim_id"]: c for c in (d.get("claims") or (d.get("final") or {}).get("claims") or [])}
    db = corpus / "extractions" / "claims_db.jsonl"
    out = {}
    if db.exists():
        for line in db.read_text(encoding="utf-8").splitlines():
            if line.strip():
                c = json.loads(line)
                if c.get("artid") == artid:
                    out[c["claim_id"]] = c
    return out


def convert_paper(corpus: Path, artid: str, papers: dict) -> tuple[dict, dict, list[str]]:
    ex = corpus / "extractions"
    stated = json.loads((ex / "stated_claims" / f"{artid}.json").read_text(encoding="utf-8"))
    cmap = json.loads((ex / "claim_map" / f"{artid}.json").read_text(encoding="utf-8")) if (ex / "claim_map" / f"{artid}.json").exists() else {}
    tables = json.loads((ex / "tables" / f"{artid}.json").read_text(encoding="utf-8")) if (ex / "tables" / f"{artid}.json").exists() else {}
    figures = json.loads((ex / "figures" / f"{artid}.json").read_text(encoding="utf-8")) if (ex / "figures" / f"{artid}.json").exists() else {}
    offset = page_offset(stated)
    meta = paper_metadata(artid, papers, stated)
    claims, w1 = convert_claims(stated, cmap, load_results(corpus, artid), tables, meta, offset, figures)
    tabs, w2 = convert_tables(tables, meta, offset)
    return claims, tabs, w1 + w2


def processed_artids(corpus: Path) -> list[str]:
    ex = corpus / "extractions"
    return sorted(p.stem for p in (ex / "stated_claims").glob("dk_*.json") if (ex / "tables" / p.name).exists() and (corpus / "pdfs" / f"{p.stem}.pdf").exists())


# ── the server ───────────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--email"); ap.add_argument("--password")
    ap.add_argument("--claims-dataset", help="dataset id for the claims (created when omitted)")
    ap.add_argument("--tables-dataset", help="dataset id for the tables (created when omitted)")
    ap.add_argument("--artids", nargs="*")
    ap.add_argument("--replace", action="store_true", help="delete the paper's earlier import first")
    ap.add_argument("--out", type=Path, help="write the converted JSON here and stop (no server)")
    ap.add_argument("--only", choices=("claims", "tables"), help="import one of the two datasets only")
    ap.add_argument("--claims-title", default="Register-data claims (Danish)", help="title of a claims dataset created here")
    ap.add_argument("--resume", action="store_true",
                    help="skip papers the target dataset already holds (by file name), after removing duplicate copies")
    ap.add_argument("--wait", type=float, default=40, help="minutes to wait for a paper the server is still processing (default 40)")
    a = ap.parse_args()
    papers = load_papers(a.corpus)
    artids = a.artids or processed_artids(a.corpus)
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        for artid in artids:
            claims, tabs, warns = convert_paper(a.corpus, artid, papers)
            (a.out / f"{artid}.claims.json").write_text(json.dumps(claims, ensure_ascii=False, indent=1), encoding="utf-8")
            (a.out / f"{artid}.tables.json").write_text(json.dumps(tabs, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{artid}: {len(claims['claims'])} claims, {sum(len(c['results']) for c in claims['claims'])} results, {len(tabs['regressions'])} regressions"
                  + (f"; {len(warns)} warnings: " + " | ".join(warns[:3]) if warns else ""))
        return 0
    import httpx
    import time
    POST_TIMEOUT = 900.0
    c = httpx.Client(base_url=a.base_url, timeout=httpx.Timeout(POST_TIMEOUT, connect=30.0))
    if a.email:
        if a.password is None:                          # asked for, so it stays out of the shell history
            import getpass
            a.password = getpass.getpass(f"Metalens password for {a.email}: ")
        r = c.post("/api/auth/login", json={"email": a.email, "password": a.password or ""})
        if r.status_code != 200:
            print("login failed:", r.text[:200]); return 2
    ids = {}
    kinds = [k for k in ("claims", "tables") if not a.only or k == a.only]
    for key, pid, title in (("claims", PRESET_CLAIMS, a.claims_title), ("tables", PRESET_TABLES, "Register-data tables (Danish)")):
        if key not in kinds:
            continue
        given = a.claims_dataset if key == "claims" else a.tables_dataset
        if given:
            ids[key] = given; continue
        r = c.post("/api/datasets", json={"title": title, "schema_id": f"{pid}@v1", "visibility": "private",
                                          "description": "Economics papers using Danish register data — imported from the danish-register-econ pipeline."})
        r.raise_for_status(); ids[key] = r.json()["id"]; print(f"created dataset {ids[key]} ({title})")
    def docs_of(dataset_id: str) -> list[dict]:
        """The documents a dataset holds, with file names and ids (its owner sees them)."""
        try:
            r = c.get(f"/api/datasets/{dataset_id}/overview", timeout=60.0)
        except httpx.TransportError:
            return []
        return (r.json().get("documents") or []) if r.status_code == 200 else []

    def held(dataset_id: str) -> set[str]:
        return {d.get("filename") for d in docs_of(dataset_id) if d.get("filename")}

    def strays(pid: str) -> int:
        """A paper posted twice (a retry while the first request was still being processed) is kept
        once by the server — the dataset refuses a second copy of the same PDF — but the second
        document stays in the account, outside every dataset, with its PDF and pages stored. Delete
        exactly those: documents of this preset, named like one of these papers, in none of the
        account's datasets."""
        names = {f"{x}.pdf" for x in artids}
        mine = c.get("/api/documents", params={"limit": 5000}, timeout=120.0).json().get("documents") or []
        in_a_dataset: set[str] = set()
        for d in c.get("/api/datasets", timeout=60.0).json().get("datasets") or []:
            docs = c.get("/api/documents", params={"limit": 5000, "dataset": d["id"]}, timeout=120.0).json().get("documents") or []
            in_a_dataset |= {x["document_id"] for x in docs}
        removed = 0
        for d in mine:
            if d.get("filename") in names and (d.get("schema_id") or "").startswith(f"{pid}@") and d["document_id"] not in in_a_dataset:
                rr = c.delete(f"/api/documents/{d['document_id']}", timeout=120.0)
                removed += rr.status_code == 200
                print(f"  − {d['filename']}: stray copy {d['document_id']} " + ("removed" if rr.status_code == 200 else f"NOT removed (HTTP {rr.status_code})"))
        return removed

    def dedupe(dataset_id: str) -> int:
        """Two documents of the same paper inside one dataset (should the server ever let it
        happen): the oldest copy stays, the others are deleted."""
        by: dict[str, list[dict]] = {}
        for d in docs_of(dataset_id):
            if d.get("filename"):
                by.setdefault(d["filename"], []).append(d)
        removed = 0
        for fn, copies in sorted(by.items()):
            for extra in sorted(copies, key=lambda d: d.get("created_at") or "")[1:]:
                rr = c.delete(f"/api/documents/{extra['document_id']}", timeout=120.0)
                ok = rr.status_code == 200
                removed += ok
                print(f"  − {fn}: duplicate copy {extra['document_id']} " + ("removed" if ok else f"NOT removed (HTTP {rr.status_code})"))
        return removed

    def wait_for(dataset_id: str, filename: str) -> bool:
        """The server got the paper and is still working on it: wait until it appears, never post it twice."""
        deadline = time.time() + a.wait * 60
        while time.time() < deadline:
            time.sleep(30)
            if filename in held(dataset_id):
                return True
        return False

    if a.resume:
        for k in kinds:
            n = dedupe(ids[k]) + strays(PRESET_CLAIMS if k == "claims" else PRESET_TABLES)
            if n:
                print(f"removed {n} extra cop{'y' if n == 1 else 'ies'} of {k} papers")
    done = {k: (held(ids[k]) if a.resume else set()) for k in kinds}
    failed: list[str] = []
    pending: list[str] = []
    for artid in artids:
        if all(f"{artid}.pdf" in done[k] for k in kinds):
            print(f"  · {artid}: already imported"); continue
        pdf = (a.corpus / "pdfs" / f"{artid}.pdf").read_bytes()
        sha = hashlib.sha256(pdf).hexdigest()
        claims, tabs, warns = convert_paper(a.corpus, artid, papers)
        for w in warns:
            print(f"  ! {artid}: {w}")
        for key, pid, obj in (("claims", PRESET_CLAIMS, claims), ("tables", PRESET_TABLES, tabs)):
            if key not in kinds or f"{artid}.pdf" in done[key]:
                continue
            if a.replace:
                dup = c.post("/api/documents/check-duplicates", json={"hashes": [sha], "preset_id": pid}).json().get("duplicates") or {}
                for d in dup.get(sha) or []:
                    c.delete(f"/api/documents/{d['document_id'] if isinstance(d, dict) and 'document_id' in d else d.get('id')}")
            fname, r = f"{artid}.pdf", None
            for attempt in range(3):
                try:
                    r = c.post("/api/ingest-pdf", files={"pdf": (fname, pdf, "application/pdf")},
                               data={"result": json.dumps(obj, ensure_ascii=False), "schema_id": f"{pid}@v1",
                                     "dataset_id": ids[key], "produced_by": PRODUCED_BY})
                except (httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                    r = "busy"                          # the server has the paper and is still working on it
                except httpx.TransportError as exc:     # it never reached the server: post again
                    print(f"  … {artid} {key}: {exc.__class__.__name__}, posting again")
                    time.sleep(15 * (attempt + 1))
                    if fname in held(ids[key]):
                        r = "landed"; break
                    continue
                if r != "busy" and r.status_code == 504:
                    r = "busy"                          # the gateway gave up; the app may still be working
                if r == "busy":
                    print(f"  … {artid} {key}: still being processed on the server, waiting (up to {a.wait:g} min)")
                    r = "landed" if wait_for(ids[key], fname) else "pending"
                    break
                if r.status_code in (502, 503):         # the app was unavailable (a restart): post again
                    print(f"  … {artid} {key}: HTTP {r.status_code}, posting again")
                    time.sleep(30 * (attempt + 1))
                    if fname in held(ids[key]):
                        r = "landed"; break
                    continue
                break
            if r == "landed":
                print(f"  ✓ {artid} {key}: landed"); continue
            if r == "pending":
                pending.append(artid)
                print(f"  ⧗ {artid} {key}: not there after {a.wait:g} min; --resume later picks it up"); continue
            if r is None or r.status_code != 200:
                failed.append(artid)
                print(f"  ✗ {artid} {key}: " + (f"HTTP {r.status_code} {r.text[:300]}" if r is not None else "no answer")); continue
            j = r.json()
            n = len(obj["claims"]) if key == "claims" else len(obj["regressions"])
            print(f"  ✓ {artid} {key}: {n} entries → document {j.get('document_id') or j.get('id')} · issues {len(j.get('issues') or [])}")
    if a.resume:                                        # copies still running when this run began have landed by now:
        for k in kinds:                                 # clean up after them too, then say what is missing
            n = dedupe(ids[k]) + strays(PRESET_CLAIMS if k == "claims" else PRESET_TABLES)
            if n:
                print(f"removed {n} extra cop{'y' if n == 1 else 'ies'} of {k} papers at the end")
            have = held(ids[k])
            missing = [x for x in artids if f"{x}.pdf" not in have]
            print(f"{k}: {len(artids) - len(missing)} of {len(artids)} papers in the dataset"
                  + (f"; missing: {' '.join(missing)}" if missing else ""))
            pending = sorted(set(pending) | set(missing))
    print("datasets:", ids)
    if pending:
        print(f"{len(pending)} papers were still being processed: {' '.join(pending)}")
    if failed or pending:
        print(f"run again with --claims-dataset/--tables-dataset and --resume to finish ({len(failed)} failed, {len(pending)} pending)")
    return 1 if failed or pending else 0


if __name__ == "__main__":
    sys.exit(main())
