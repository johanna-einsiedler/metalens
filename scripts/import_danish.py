#!/usr/bin/env python3
"""Import the danish-register-econ extractions into Metalens as two datasets.

    uv run python scripts/import_danish.py --email me@example.org --password …            # all processed papers
    uv run python scripts/import_danish.py --artids dk_W2213841331 --replace              # one paper again
    uv run python scripts/import_danish.py --out /tmp/converted                           # only convert, write JSON, no server

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
def convert_claims(stated: dict, cmap: dict, results: dict, tables: dict, meta: dict, offset: int) -> tuple[dict, list[str]]:
    """stated_claims + claim_map + result claims (+ the tables file for the cited cells) → one
    register-claims result. Returns (result, warnings)."""
    warnings: list[str] = []
    regs = {r["regression_id"]: r for r in (tables.get("regressions") or [])}
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
        for j, mt in enumerate(m.get("matches") or []):
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
    offset = page_offset(stated)
    meta = paper_metadata(artid, papers, stated)
    claims, w1 = convert_claims(stated, cmap, load_results(corpus, artid), tables, meta, offset)
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
    c = httpx.Client(base_url=a.base_url, timeout=600.0)
    if a.email:
        r = c.post("/api/auth/login", json={"email": a.email, "password": a.password or ""})
        if r.status_code != 200:
            print("login failed:", r.text[:200]); return 2
    ids = {}
    for key, pid, title in (("claims", PRESET_CLAIMS, "Register-data claims (Danish)"), ("tables", PRESET_TABLES, "Register-data tables (Danish)")):
        given = a.claims_dataset if key == "claims" else a.tables_dataset
        if given:
            ids[key] = given; continue
        r = c.post("/api/datasets", json={"title": title, "schema_id": f"{pid}@v1", "visibility": "private",
                                          "description": "Economics papers using Danish register data — imported from the danish-register-econ pipeline."})
        r.raise_for_status(); ids[key] = r.json()["id"]; print(f"created dataset {ids[key]} ({title})")
    for artid in artids:
        pdf = (a.corpus / "pdfs" / f"{artid}.pdf").read_bytes()
        sha = hashlib.sha256(pdf).hexdigest()
        claims, tabs, warns = convert_paper(a.corpus, artid, papers)
        for w in warns:
            print(f"  ! {artid}: {w}")
        for key, pid, obj in (("claims", PRESET_CLAIMS, claims), ("tables", PRESET_TABLES, tabs)):
            if a.replace:
                dup = c.post("/api/documents/check-duplicates", json={"hashes": [sha], "preset_id": pid}).json().get("duplicates") or {}
                for d in dup.get(sha) or []:
                    c.delete(f"/api/documents/{d['document_id'] if isinstance(d, dict) and 'document_id' in d else d.get('id')}")
            r = c.post("/api/ingest-pdf", files={"pdf": (f"{artid}.pdf", pdf, "application/pdf")},
                       data={"result": json.dumps(obj, ensure_ascii=False), "schema_id": f"{pid}@v1", "dataset_id": ids[key]})
            if r.status_code != 200:
                print(f"  ✗ {artid} {key}: HTTP {r.status_code} {r.text[:300]}"); continue
            j = r.json()
            n = len(obj["claims"]) if key == "claims" else len(obj["regressions"])
            print(f"  ✓ {artid} {key}: {n} entries → document {j.get('document_id') or j.get('id')} · issues {len(j.get('issues') or [])}")
    print("datasets:", ids)
    return 0


if __name__ == "__main__":
    sys.exit(main())
