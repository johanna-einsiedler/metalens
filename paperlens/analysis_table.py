"""The analysis table of a dataset: the flat, typed rows a dashboard is built on, with the
provenance of every row and the evidence of every cell.

A dataset's records are nested (an entry holds sub-entries, sub-entries hold tables); what a
meta-analyst plots is one level down: a loading, an effect size, a measure. ``units`` names
the row units a preset offers, ``build`` flattens the dataset into one of them:

    entries              one row per entry                       (every preset)
    <child>              one row per sub-entry                   (masem-direct: records)
    <table>              one row per row of an entry-level table (masem-indirect: factor_loadings)
    <child>.<table>      one row per table row in a sub-entry    (human-ai-collab: conditions.measures)

Parent values are repeated onto their rows. Columns are typed from the preset's declaration
and carry ROLES (measure, dimension, identifier, label, bound, dispersion) that tell the
dashboard templates — and the model that proposes a dashboard — what a column can be used for.

Provenance travels at two levels. Per ROW (cheap, embedded by reference): dataset, paper,
extraction model and date, verification status, which cells a human corrected. Per CELL
(bulky, fetched on demand by ``cell_evidence``): the quote, page and source label that support
the value, resolved exact → row → table → entry, exactly as the review screen does, with the
KIND of support named so a whole-table citation is never passed off as a citation of the value.

Derived columns (``display.analysis.derived``) are deterministic per-row formulas from a fixed
registry — a confidence interval, a standardised difference — never free-form and never
pooling; their evidence is the evidence of their inputs.
"""
from __future__ import annotations

import math
import re
from typing import Any

from psycopg.types.json import Json

from . import contract, records, storage

MAX_ROWS = 20000
_NUMERIC = ("number", "integer")
_ID_NAME = re.compile(r"(^|_)(id|item|no|index)$", re.I)
_BOUND_NAME = re.compile(r"(ci_?(lo|low|lower|hi|high|upper)|lcl|ucl|_lo$|_hi$)", re.I)
_DISP_NAME = re.compile(r"(^(se|sd)_|_(se|sd)$)", re.I)

SYSTEM_COLUMNS = [
    {"name": "_study", "label": "Study", "type": "string", "scope": "system", "help": "first author and year"},
    {"name": "_title", "label": "Paper title", "type": "string", "scope": "system", "help": "the title of the source paper"},
    {"name": "_doi", "label": "DOI", "type": "string", "scope": "system", "help": ""},
    {"name": "_year", "label": "Publication year", "type": "integer", "scope": "system", "help": ""},
    {"name": "_journal", "label": "Journal", "type": "string", "scope": "system", "help": ""},
    {"name": "_entry", "label": "Entry", "type": "string", "scope": "system", "help": "the entry the row belongs to"},
    {"name": "_status", "label": "Verification status", "type": "enum", "scope": "system", "help": "",
     "options": [{"value": v, "label": v} for v in ("verified", "unverified", "flagged")]},
]


# ── units ─────────────────────────────────────────────────────────────────────
def _analysis(spec: dict) -> dict:
    a = (spec.get("display") or {}).get("analysis")
    return a if isinstance(a, dict) else {}


def units(spec: dict | None) -> list[dict]:
    """The row units a preset offers, shallow to deep."""
    ent = (spec or {}).get("entries") or {}
    out = [{"id": "entries", "label": ent.get("label") or "Entry", "level": "entry", "child": None, "table": None}]
    for f in ent.get("fields") or []:
        if f.get("type") == "table":
            out.append({"id": f["name"], "label": f.get("label") or f["name"], "level": "entry_table", "child": None, "table": f["name"]})
    for ch in ent.get("children") or []:
        out.append({"id": ch["key"], "label": ch.get("label") or ch["key"], "level": "child", "child": ch["key"], "table": None})
        for f in ch.get("fields") or []:
            if f.get("type") == "table":
                out.append({"id": f"{ch['key']}.{f['name']}", "label": f.get("label") or f["name"],
                            "level": "child_table", "child": ch["key"], "table": f["name"]})
    return out


def default_unit(spec: dict | None) -> str:
    ids = [u["id"] for u in units(spec)]
    want = _analysis(spec or {}).get("default_unit") or ((spec or {}).get("display") or {}).get("grid_rows")
    return want if want in ids else "entries"


def _unit(spec: dict | None, unit_id: str | None) -> dict:
    us = units(spec)
    return next((u for u in us if u["id"] == (unit_id or default_unit(spec))), us[0])


def _defs(spec: dict | None, unit: dict) -> tuple[list, list, list]:
    """(entry scalar defs, child scalar defs, table column defs) for a unit."""
    ent = (spec or {}).get("entries") or {}
    scalars = lambda fields: [f for f in fields or [] if f.get("type") != "table"]   # noqa: E731
    child = next((c for c in ent.get("children") or [] if c.get("key") == unit["child"]), None)
    holder = (child or {}).get("fields") if unit["child"] else ent.get("fields")
    table = next((f for f in holder or [] if f.get("name") == unit["table"] and f.get("type") == "table"), None)
    cols = [c if isinstance(c, dict) else {"name": c} for c in (table or {}).get("columns") or []]
    return scalars(ent.get("fields")), scalars((child or {}).get("fields")), cols


# ── values ────────────────────────────────────────────────────────────────────
def num(v: Any) -> float | None:
    """A number out of what models write: 0.7, ".70", "1,200", "45%"; None when it is not one."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    s = str(v).strip().replace(",", "").replace("%", "").replace("−", "-").replace("–", "-")
    try:
        f = float(s)
        return f if math.isfinite(f) else None
    except ValueError:
        return None


def _cell(v: Any, ftype: str | None) -> Any:
    if ftype in _NUMERIC:
        return num(v)
    if isinstance(v, list):
        return "; ".join(str(x) for x in v if x not in (None, ""))
    if isinstance(v, dict):
        return None
    return v


def _study_label(authors: Any, year: Any, title: str | None) -> str:
    names = []
    for a in authors if isinstance(authors, list) else []:
        n = a.get("name") if isinstance(a, dict) else a
        if isinstance(n, str) and n.strip():
            names.append(n.strip())
    if names:
        def fam(n: str) -> str:
            """Family name from "Smith, J.", "Jin C-H" (initials last) or "Chang-Hyun Jin" (given name first)."""
            if "," in n:
                return n.split(",")[0].strip()
            parts = n.split()
            if len(parts) > 1 and re.fullmatch(r"[A-Z](?:[.\-]?[A-Z]){0,2}\.?", parts[-1]):
                return " ".join(parts[:-1])
            return parts[-1]
        who = fam(names[0]) + (" et al." if len(names) > 2 else f" & {fam(names[1])}" if len(names) == 2 else "")
    else:
        who = (title or "Untitled")[:40]
    return f"{who} ({year})" if year else who


def _entry_label(spec: dict | None, fv: dict, entry_index: int) -> str:
    ent = (spec or {}).get("entries") or {}
    idf = ent.get("id_field")
    if idf and fv.get(idf) not in (None, ""):
        return f"{ent.get('label') or 'Entry'} {fv[idf]}"
    return f"{ent.get('label') or 'Entry'} {entry_index + 1}"


# ── derived columns: a fixed registry of per-row formulas ─────────────────────
def _r_ci_fisher(i: dict) -> dict:
    r, n = i.get("r"), i.get("n")
    if r is None or n is None or n <= 3 or abs(r) >= 1:
        return {}
    z, se = math.atanh(r), 1 / math.sqrt(n - 3)
    return {"lo": math.tanh(z - 1.959964 * se), "hi": math.tanh(z + 1.959964 * se)}


def _hedges_g(i: dict) -> dict:
    m1, m2, s1, s2, n1, n2 = (i.get(k) for k in ("m1", "m2", "sd1", "sd2", "n1", "n2"))
    if None in (m1, m2, s1, s2, n1, n2) or n1 < 2 or n2 < 2:
        return {}
    df = n1 + n2 - 2
    sp = math.sqrt(((n1 - 1) * s1 ** 2 + (n2 - 1) * s2 ** 2) / df)
    if sp <= 0:
        return {}
    j = 1 - 3 / (4 * df - 1)
    g = j * (m1 - m2) / sp * (-1 if i.get("_flip") else 1)
    se = math.sqrt((n1 + n2) / (n1 * n2) + g ** 2 / (2 * (n1 + n2)))
    return {"g": g, "lo": g - 1.959964 * se, "hi": g + 1.959964 * se}


FORMULAS = {
    "r_ci_fisher": {"fn": _r_ci_fisher, "inputs": ("r", "n"), "outputs": ("lo", "hi"),
                    "text": "95% CI of a correlation via Fisher's z: tanh(atanh(r) ± 1.96 / sqrt(n − 3))"},
    "hedges_g": {"fn": _hedges_g, "inputs": ("m1", "m2", "sd1", "sd2", "n1", "n2"), "outputs": ("g", "lo", "hi"),
                 "text": "Hedges' g = J · (m1 − m2) / pooled SD, J = 1 − 3 / (4·df − 1), 95% CI g ± 1.96 · SE; "
                         "sign flipped where lower values are better"},
}
_OUT_LABEL = {"lo": "lower 95% bound", "hi": "upper 95% bound", "g": "Hedges' g"}


def _derived(spec: dict | None, unit_id: str) -> list[dict]:
    out = []
    for d in _analysis(spec or {}).get("derived") or []:
        f = FORMULAS.get((d or {}).get("formula"))
        if f and d.get("unit") == unit_id and isinstance(d.get("inputs"), dict) and d.get("name"):
            out.append({**d, "_f": f})
    return out


# ── columns ───────────────────────────────────────────────────────────────────
def _roles(c: dict, distinct: int, n: int, id_field: str | None, overrides: dict) -> list[str]:
    if c["name"] in overrides:
        return list(overrides[c["name"]])
    t, name = c.get("type"), c["name"]
    if c.get("derived"):
        return ["bound"] if c["derived"]["output"] in ("lo", "hi") else ["measure"]
    if t == "text":
        return []
    if name == id_field or name == "_entry":
        return ["identifier", "label"] + (["dimension"] if distinct <= 40 else [])
    if name in ("_study", "_title", "_doi"):
        return ["label", "identifier"] + (["dimension"] if distinct <= 40 else [])
    if t in _NUMERIC:
        if _BOUND_NAME.search(name):
            return ["bound"]
        if _DISP_NAME.search(name):
            return ["dispersion"]
        if _ID_NAME.search(name):
            return ["identifier"] + (["dimension"] if distinct <= 40 else [])
        return ["measure"] + (["dimension"] if t == "integer" and distinct <= 12 else [])
    if t in ("enum", "boolean", "multi", "list"):
        return ["dimension"]
    return ["dimension"] if distinct <= 30 or (n and distinct <= 0.3 * n) else ["label"]


def _column_list(spec: dict | None, unit: dict) -> list[dict]:
    e_defs, c_defs, t_defs = _defs(spec, unit)
    mk = lambda f, scope: {"name": f["name"], "label": f.get("label") or f["name"].replace("_", " "),   # noqa: E731
                           "type": f.get("type") or "string", "scope": scope, "help": f.get("help") or "",
                           "options": f.get("options") if isinstance(f.get("options"), list) else None}
    cols = [dict(c) for c in SYSTEM_COLUMNS]
    cols += [mk(f, "entry") for f in e_defs]
    cols += [mk(f, "child") for f in c_defs]
    cols += [mk(f, "row") for f in t_defs]
    for d in _derived(spec, unit["id"]):
        for o in d["_f"]["outputs"]:
            main = o == d["_f"]["outputs"][0] and o not in ("lo", "hi")
            cols.append({"name": d["name"] if main else f"{d['name']}_{o}",
                         "label": (d.get("label") or d["name"]) + ("" if main else f", {_OUT_LABEL.get(o, o)}"),
                         "type": "number", "scope": "derived", "help": d["_f"]["text"], "options": None,
                         "derived": {"id": d["name"], "formula": d["formula"], "output": o,
                                     "inputs": [v for k, v in d["inputs"].items() if not k.startswith("_")], "text": d["_f"]["text"]}})
    return cols


# ── complete rows only ───────────────────────────────────────────────────────
# A preset may declare what a row needs to be analysed at all:
#   display.analysis.complete = {"unit": "conditions.measures", "columns": [...], "label": "…"}
# (human–AI collaboration: a measure counts only with a human-alone, an AI-alone AND a human+AI
# mean). Nothing is deleted: review and export keep every row; dashboards see the complete ones,
# in every layout (an experiment or condition stays when at least one of its measures does), and
# say how much was left out.
def _complete_keys(spec: dict | None, recs_in: list[dict]) -> dict | None:
    cfg = _analysis(spec or {}).get("complete")
    if not isinstance(cfg, dict) or not isinstance(cfg.get("columns"), list) or not cfg["columns"]:
        return None
    unit = next((u for u in units(spec) if u["id"] == cfg.get("unit")), None)
    if unit is None:
        return None
    all_rows = flatten(spec, unit, recs_in)
    ok = {(r["r"], r["p"]) for r in all_rows if all(r["vals"].get(c) not in (None, "") for c in cfg["columns"])}
    return {"ok": ok, "n_all": len(all_rows), "all": {(r["r"], r["p"]) for r in all_rows}, "cfg": cfg, "unit": unit}


def _only_complete(rows: list[dict], complete: dict | None) -> list[dict]:
    if not complete:
        return rows
    by_rec: dict[int, list[str]] = {}
    for r, path in complete["ok"]:
        by_rec.setdefault(r, []).append(path)
    related = lambda a, b: a == b or a == "" or b == "" or b.startswith(a + ".") or a.startswith(b + ".")   # noqa: E731
    return [row for row in rows if any(related(row["p"], q) for q in by_rec.get(row["r"], ()))]


def _left_out(spec: dict | None, complete: dict | None, recs_out: list[dict], n_papers: int) -> dict | None:
    if not complete:
        return None
    cfg, labels = complete["cfg"], {c["name"]: c["label"] for c in _column_list(spec, complete["unit"])}
    kept_papers = {recs_out[r]["paper"] for r, _ in complete["ok"]}
    return {"rows": complete["n_all"] - len(complete["ok"]), "of": complete["n_all"], "papers": n_papers - len(kept_papers),
            "unit": complete["unit"]["label"], "needs": cfg.get("label") or ", ".join(labels.get(c, c) for c in cfg["columns"])}


# ── flattening ────────────────────────────────────────────────────────────────
def flatten(spec: dict | None, unit: dict, recs: list[dict]) -> list[dict]:
    """``recs``: [{idx, field_values, entry_index, sys{…}}] → rows {r, p, vals{name: value}}.
    Same semantics as the review grid (``spec.js gridRows``): parent values repeated, a parent
    without children still gives one row; plus the entry-level table unit the grid lacks."""
    e_defs, c_defs, t_defs = _defs(spec, unit)
    derived = _derived(spec, unit["id"])
    rows: list[dict] = []

    def emit(rec, path, *layers):
        vals = dict(rec["sys"])
        for defs, obj in layers:
            for f in defs:
                vals[f["name"]] = _cell((obj or {}).get(f["name"]), f.get("type"))
        for d in derived:
            ins = {k: (num(vals.get(v)) if not k.startswith("_") else None) for k, v in d["inputs"].items()}
            flip = d.get("flip")
            if isinstance(flip, dict):
                ins["_flip"] = vals.get(flip.get("column")) in (flip.get("in") or [])
            when = d.get("when")
            ok = not isinstance(when, dict) or vals.get(when.get("column")) in (when.get("in") or [])
            res = d["_f"]["fn"](ins) if ok else {}
            for o in d["_f"]["outputs"]:
                main = o == d["_f"]["outputs"][0] and o not in ("lo", "hi")
                v = res.get(o)
                vals[d["name"] if main else f"{d['name']}_{o}"] = round(v, 6) if v is not None else None
        rows.append({"r": rec["idx"], "p": path, "vals": vals})

    for rec in recs:
        fv = rec["field_values"] or {}
        if unit["level"] == "entry":
            emit(rec, "", (e_defs, fv)); continue
        if unit["level"] == "entry_table":
            trs = fv.get(unit["table"]) if contract.is_rows(fv.get(unit["table"])) else []
            if not trs:
                emit(rec, "", (e_defs, fv)); continue
            for r, tr in enumerate(trs):
                emit(rec, f"{unit['table']}[{r}]", (e_defs, fv), (t_defs, tr))
            continue
        kids = fv.get(unit["child"]) if contract.is_rows(fv.get(unit["child"])) else []
        if not kids:
            emit(rec, "", (e_defs, fv)); continue
        for j, kid in enumerate(kids):
            cpath = f"{unit['child']}[{j}]"
            if unit["level"] == "child":
                emit(rec, cpath, (e_defs, fv), (c_defs, kid)); continue
            trs = kid.get(unit["table"]) if contract.is_rows(kid.get(unit["table"])) else []
            if not trs:
                emit(rec, cpath, (e_defs, fv), (c_defs, kid)); continue
            for r, tr in enumerate(trs):
                emit(rec, f"{cpath}.{unit['table']}[{r}]", (e_defs, fv), (c_defs, kid), (t_defs, tr))
    return rows


def catalogue(spec: dict | None, unit: dict, rows: list[dict]) -> list[dict]:
    ent = (spec or {}).get("entries") or {}
    overrides = _analysis(spec or {}).get("roles") or {}
    out = []
    for c in _column_list(spec, unit):
        vals = [r["vals"].get(c["name"]) for r in rows]
        present = [v for v in vals if v not in (None, "")]
        distinct = len({str(v) for v in present})
        col = {**c, "n": len(present), "distinct": distinct}
        if c["type"] in _NUMERIC and present:
            col["min"], col["max"] = min(present), max(present)
        seen: list = []
        for v in present:
            if v not in seen:
                seen.append(v)
            if len(seen) >= 5:
                break
        col["samples"] = [s if not isinstance(s, str) else s[:60] for s in seen]
        col["roles"] = _roles(c, distinct, len(present), ent.get("id_field"), overrides)
        # what the numbers of this measure MEAN differs per row (accuracy, seconds, a score …):
        # the column that says so, so a chart can name the metric or warn that it mixes several
        units = _analysis(spec or {}).get("units") or {}
        if c["name"] in (units.get("columns") or []):
            col["unit_by"], col["unit_detail"] = units.get("by"), units.get("detail")
        out.append(col)
    return out


# ── loading ───────────────────────────────────────────────────────────────────
def _load(conn, dataset_id: str) -> list[tuple]:
    return conn.execute(
        """SELECT r.id::text, r.document_id::text, r.entry_index, r.field_values, r.verification_status,
                  r.extraction, r.schema_id, p.title, p.doi, p.year, p.journal, p.authors,
                  ed.filename, ed.created_at
           FROM record r
           LEFT JOIN paper p ON p.id = r.paper_id
           JOIN extraction_document ed ON ed.id = r.document_id
           WHERE r.dataset_id = %s::uuid AND NOT COALESCE(r.screened_empty, false)
           ORDER BY p.year NULLS LAST, r.document_id, r.entry_index""", (dataset_id,)).fetchall()


def _corrected(conn, record_ids: list[str]) -> dict[str, dict[str, dict]]:
    """record id → record-relative cell path → the latest value-changing correction."""
    if not record_ids:
        return {}
    out: dict[str, dict[str, dict]] = {}
    for rid, diff, at in conn.execute(
            """SELECT record_id::text, diff, created_at FROM verification_event
               WHERE record_id::text = ANY(%s) AND diff IS NOT NULL ORDER BY created_at""", (record_ids,)).fetchall():
        for d in diff if isinstance(diff, list) else []:
            if isinstance(d, dict) and d.get("field_path") and d.get("original_value") != d.get("final_value"):
                out.setdefault(rid, {})[d["field_path"]] = {
                    "original_value": d.get("original_value"), "final_value": d.get("final_value"),
                    "at": at.isoformat() if at else None}
    return out


def _cell_path(row_path: str, col: dict) -> str | None:
    """The record-relative path of one cell, for corrections and evidence."""
    scope = col.get("scope")
    if scope in ("system", "derived"):
        return None
    if scope == "entry":
        return col["name"]
    if scope == "child":
        m = re.match(r"^[^.\[]+\[\d+\]", row_path)
        return f"{m.group(0)}.{col['name']}" if m else None
    return f"{row_path}.{col['name']}" if row_path else None        # a table cell


# ── where the rows come from: the live dataset, or a frozen release ──────────
# Both give the same keys, so build() and cell_evidence() do not care which one they read.
def live_source(conn, dataset_id: str) -> dict | None:
    d = records.get_dataset(conn, dataset_id)
    if d is None:
        return None
    raw = _load(conn, dataset_id)
    # The dataset names the schema it was created with; the RECORDS are the data. When every
    # record has moved to one other version of the same preset (the preset gained a field and the
    # papers were re-extracted or re-imported), read them with that version — otherwise the new
    # columns would stay invisible. Mixed versions keep the dataset's own, as before.
    schema_id = d.get("schema_id") or next((r[6] for r in raw if r[6]), None)
    theirs = {r[6] for r in raw if r[6]}
    if len(theirs) == 1 and schema_id not in theirs:
        only = next(iter(theirs))
        if only.partition("@")[0] == (schema_id or "").partition("@")[0]:
            schema_id = only
    cred = records.dataset_credibility(conn, dataset_id)
    # when the DATA last changed: a paper added, a row verified or corrected, the dataset edited
    changed = conn.execute(
        """SELECT GREATEST(d.updated_at,
                           (SELECT max(r.created_at) FROM record r WHERE r.dataset_id = d.id),
                           (SELECT max(ve.created_at) FROM verification_event ve JOIN record r ON r.id = ve.record_id
                             WHERE r.dataset_id = d.id))
           FROM dataset d WHERE d.id = %s::uuid""", (dataset_id,)).fetchone()[0]
    return {"dataset": d, "raw": raw, "schema_id": schema_id,
            "spec": records.schema_spec(conn, schema_id) if schema_id else None,
            "corrected": _corrected(conn, [r[0] for r in raw]),
            "credibility": {"tier": cred.get("tier"), "label": cred.get("label")},
            "changed": changed.isoformat(timespec="seconds") if changed else None, "release": None}


def release_source(conn, release: dict) -> dict | None:
    """``release``: a row of releases.get(..., with_snapshot=True). Data, preset and badge are
    the frozen ones; the dataset's title, citation and links are read live (they are labels)."""
    d = records.get_dataset(conn, release["dataset_id"])
    if d is None:
        return None
    snap = release["snapshot"]
    raw = [(r["id"], r["doc"], r["entry_index"], r["field_values"], r["status"], r["extraction"], snap.get("schema_id"),
            r["title"], r["doi"], r["year"], r["journal"], r["authors"], r["filename"], r["created"]) for r in snap["records"]]
    cred = release.get("credibility") or {}
    return {"dataset": d, "raw": raw, "schema_id": snap.get("schema_id"), "spec": snap.get("spec"),
            "corrected": snap.get("corrected") or {}, "credibility": {"tier": cred.get("tier"), "label": cred.get("label")},
            "changed": release["created_at"],
            "release": {"number": release["number"], "created_at": release["created_at"], "content_sha": release["content_sha"]}}


def build(conn, dataset_id: str, unit_id: str | None = None, *, owner: bool, release: dict | None = None, crosscheck: bool = True,
          vocabulary: bool = True) -> dict | None:
    src = release_source(conn, release) if release else live_source(conn, dataset_id)
    if src is None:
        return None
    d, raw, schema_id, spec = src["dataset"], src["raw"], src["schema_id"], src["spec"]
    unit = _unit(spec, unit_id)

    papers: list[dict] = []
    paper_idx: dict[tuple, int] = {}
    recs_out: list[dict] = []
    recs_in: list[dict] = []
    for (rid, doc_id, ei, fv, status, extraction, _sid, title, doi, year, journal, authors, filename, created) in raw:
        key = (doi or title or doc_id,)
        if key not in paper_idx:
            paper_idx[key] = len(papers)
            papers.append({"title": title, "doi": doi, "year": year, "journal": journal,
                           "study": _study_label(authors, year, title)})
        ex = extraction if isinstance(extraction, dict) else {}
        rec = {"id": rid, "paper": paper_idx[key], "entry_index": ei, "entry_label": _entry_label(spec, fv or {}, ei),
               "status": status, "model": ex.get("resolved_model") or ex.get("model"),
               "extracted_at": (ex.get("date") or (created if isinstance(created, str) else created.isoformat() if created else None) or "")[:10] or None}
        if owner:                                           # the bright wall: documents are the owner's only
            rec["document_id"], rec["filename"] = doc_id, filename
        recs_out.append(rec)
        p = papers[paper_idx[key]]
        recs_in.append({"idx": len(recs_out) - 1, "field_values": fv, "entry_index": ei,
                        "sys": {"_study": p["study"], "_title": p["title"], "_doi": p["doi"], "_year": p["year"], "_journal": p["journal"],
                                "_entry": rec["entry_label"], "_status": status}})

    complete = _complete_keys(spec, recs_in)
    rows = _only_complete(flatten(spec, unit, recs_in), complete)
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    cols = catalogue(spec, unit, rows)
    if crosscheck and (d.get("companion_dataset_id") or (release and (release.get("snapshot") or {}).get("crosscheck"))):   # checked against a companion: one verdict per row
        from . import crosscheck as _cc
        verdicts = _cc.column_values(conn, d, unit["id"], rows, recs_out, release)
        if verdicts is not None:
            for row in rows:
                row["vals"]["_crosscheck"] = verdicts.get((recs_out[row["r"]]["id"], row["p"]))
            present = [row["vals"]["_crosscheck"] for row in rows if row["vals"].get("_crosscheck")]
            cols.append({**_cc.CHECK_COLUMN, "n": len(present), "distinct": len(set(present)), "samples": sorted(set(present))[:5], "roles": ["dimension"]})
    if vocabulary:                                           # a column harmonised into concepts: two columns per committed vocabulary
        from . import vocabulary as _voc
        for concept_col, polarity_col, col_name, mapping in _voc.extra_columns(conn, dataset_id, [c["name"] for c in cols], release):
            for row in rows:
                hit = mapping.get(_voc.norm(row["vals"].get(col_name))) if row["vals"].get(col_name) not in (None, "") else None
                row["vals"][concept_col["name"]], row["vals"][polarity_col["name"]] = (hit[0], hit[1]) if hit else (None, None)
            for cdef in (concept_col, polarity_col):
                present = [row["vals"][cdef["name"]] for row in rows if row["vals"].get(cdef["name"])]
                cols.append({**cdef, "n": len(present), "distinct": len(set(present)), "samples": sorted(set(present))[:5]})
    corrected = src["corrected"]
    wire = []
    for row in rows:
        item = {"r": row["r"], "p": row["p"], "v": [row["vals"].get(c["name"]) for c in cols]}
        fixes = corrected.get(recs_out[row["r"]]["id"])
        if fixes:
            hit = [k for k, c in enumerate(cols) if _cell_path(row["p"], c) in fixes]
            if hit:
                item["c"] = hit
        wire.append(item)

    all_units = []
    for u in units(spec):
        all_units.append({"id": u["id"], "label": u["label"], "level": u["level"], "default": u["id"] == default_unit(spec),
                          "n": len(rows) if u["id"] == unit["id"] else len(_only_complete(flatten(spec, u, recs_in), complete))})   # how many rows each layout has
    as_of = conn.execute("SELECT now()").fetchone()[0]
    return {
        "dataset": {"id": dataset_id, "title": d.get("title"), "version": d.get("version") or 1,
                    "visibility": d.get("visibility"), "published_url": d.get("published_url"),
                    "citation": d.get("citation"), "schema_id": schema_id,
                    "n_papers": len({recs_out[r["r"]]["paper"] for r in rows}) if complete else len(papers),
                    "left_out": _left_out(spec, complete, recs_out, len(papers)), "data_updated_at": src["changed"],
                    "release": src["release"],              # None = the live dataset
                    "author": d.get("cite_as"),             # None when the dataset is published anonymously
                    "preset": _public_preset(conn, schema_id),
                    "credibility": src["credibility"]},
        "unit": {"id": unit["id"], "label": unit["label"], "level": unit["level"]},
        "units": all_units, "as_of": as_of.isoformat(timespec="seconds"),
        "n_rows": len(wire), "truncated": truncated, "viewer": {"owner": bool(owner)},
        "columns": cols, "papers": papers, "records": recs_out, "rows": wire,
    }


def _public_preset(conn, schema_id: str | None) -> dict | None:
    """The preset a dataset was extracted with, when ANYONE may read it (a built-in, or a
    personal preset its owner made public): {id, title}. None otherwise."""
    from . import presets
    pid = (schema_id or "").split("@")[0]
    if not pid:
        return None
    builtin = presets.load_all().get(presets.resolve_id(pid))
    if builtin is not None:
        if (builtin.get("meta") or {}).get("hidden"):
            return None
        return {"id": pid, "title": (builtin.get("meta") or {}).get("title") or pid}
    own = records.get_personal_preset(conn, pid)
    if not own or own.get("visibility") != "public":
        return None
    return {"id": pid, "title": own.get("title") or pid}


# ── evidence of cells ─────────────────────────────────────────────────────────
_CORE_PREFIX = re.compile(r"^[^.\[]+(?:\._table)?\[\d+\]\.?")
_KIND_WORDS = {"exact": "exact value", "row": "table row", "table": "whole table", "entry": "entry-level", "none": "no evidence recorded"}


def _rel(path: str | None) -> str:
    return _CORE_PREFIX.sub("", path or "", count=1) if _CORE_PREFIX.match(path or "") else (path or "")


def _lookup_order(cell: str) -> list[tuple[str, str]]:
    """(relative path, kind) from the most specific support to the entry as a whole."""
    out: list[tuple[str, str]] = []
    cur = cell
    first = True
    while cur:
        is_row = cur.endswith("]")
        out.append((cur, "row" if is_row else ("exact" if first else "table")))
        first = False
        cur = re.sub(r"\[\d+\]$", "", cur) if is_row else (cur.rsplit(".", 1)[0] if "." in cur else "")
    out.append(("", "entry"))
    return out


_SPAN_COLS = "id::text, record_id::text, document_id::text, entry_index, field_path, snippet, page, source, child_rid, row_rid, context"


def evidence_rows(conn, doc_ids: list[str]) -> list[tuple]:
    """The evidence spans of documents, in citation order (also what a release snapshot keeps)."""
    if not doc_ids:
        return []
    return conn.execute(f"SELECT {_SPAN_COLS} FROM evidence_span WHERE document_id::text = ANY(%s) ORDER BY ord",
                        (sorted(set(doc_ids)),)).fetchall()


def cell_evidence(conn, dataset_id: str, unit_id: str | None, cells: list[dict], *, release: dict | None = None,
                  max_cells: int | None = 300, read_layout: bool = True) -> list[dict]:
    """[{record_id, path, column}] → the evidence of each cell. Record ids outside the dataset
    are ignored. Only quotes, pages and source labels leave: never geometry, page images or
    who edited what. With ``release`` everything is read from that release's snapshot.
    ``max_cells=None`` / ``read_layout=False`` are for the static export of a whole release: every
    cell at once, and table context only where it is already known (no PDF is opened)."""
    cells = [c for c in cells if isinstance(c, dict) and c.get("record_id") and c.get("column")][:max_cells]
    want = sorted({c["record_id"] for c in cells if records._is_uuid(c["record_id"])})   # noqa: SLF001
    if not want:
        return []
    if release:
        snap = release["snapshot"]
        recs = {r["id"]: (r["field_values"] or {}, r["entry_index"], r["doc"], snap.get("schema_id")) for r in snap["records"] if r["id"] in set(want)}
        spec = snap.get("spec")
    else:
        recs = {rid: (fv or {}, ei, doc, sid) for rid, fv, ei, doc, sid in conn.execute(
            """SELECT id::text, field_values, entry_index, document_id::text, schema_id FROM record
               WHERE dataset_id = %s::uuid AND id::text = ANY(%s)""", (dataset_id, want)).fetchall()}
        schema_id = next((v[3] for v in recs.values() if v[3]), None)
        spec = records.schema_spec(conn, schema_id) if schema_id else None
    if not recs:
        return []
    unit = _unit(spec, unit_id)
    cols = {c["name"]: c for c in _column_list(spec, unit)}

    docs = {v[2] for v in recs.values()}
    spans = ([(e["id"], e["rec"], e["doc"], e["entry_index"], e["field_path"], e["snippet"], e["page"], e["source"],
               e["child_rid"], e["row_rid"], e.get("context")) for e in release["snapshot"]["evidence"] if e["doc"] in docs]
             if release else evidence_rows(conn, list(docs)))
    frozen = {e["id"]: e for e in release["snapshot"]["evidence"]} if release else {}
    index: dict[str, dict[str, list[dict]]] = {rid: {} for rid in recs}
    by_doc_entry = {(v[2], v[1]): rid for rid, v in recs.items()}
    for sid, rid, doc, ei, fp, snippet, page, source, crid, rrid, context in spans:
        owner_rid = rid if rid in recs else by_doc_entry.get((doc, ei))
        if owner_rid is None:
            continue
        rel = _rel(contract.live_path(fp, crid, rrid, recs[owner_rid][0]))
        index[owner_rid].setdefault(rel, []).append({"snippet": snippet, "page": page, "source": source,
                                                     "_id": sid, "_doc": doc, "_context": context})

    fixes = (release["snapshot"].get("corrected") or {}) if release else _corrected(conn, list(recs))

    pdfs: dict[str, bytes | None] = {}

    def with_context(items: list[dict], kind: str) -> list[dict]:
        """The public shape of evidence items; a quoted TABLE ROW also says what its numbers
        mean (caption + the header above each number), read from the PDF once and then stored."""
        out = []
        for it in items[:3]:
            ctx = it.get("_context")
            if ctx is None and read_layout and kind in ("row", "table") and it.get("page") and it.get("snippet"):
                doc = it["_doc"]
                if doc not in pdfs:
                    try:
                        store = storage.get_store(); key = storage.pdf_key(doc)
                        pdfs[doc] = store.get(key) if store.exists(key) else None
                    except Exception:  # noqa: BLE001 - no PDF, no context
                        pdfs[doc] = None
                if pdfs[doc]:
                    try:
                        from . import pdf_utils
                        ctx = pdf_utils.table_context(pdfs[doc], it["page"], it["snippet"])
                    except Exception:  # noqa: BLE001 - layout reading is best effort
                        ctx = {}
                    it["_context"] = ctx
                    if release:                             # a release is immutable: remember it for this process only
                        if it["_id"] in frozen:
                            frozen[it["_id"]]["context"] = ctx
                    else:
                        with conn.transaction():
                            conn.execute("UPDATE evidence_span SET context = %s WHERE id = %s::uuid", (Json(ctx), it["_id"]))
            pub = {"snippet": it["snippet"], "page": it["page"], "source": it["source"]}
            if ctx:
                pub["context"] = ctx
            out.append(pub)
        return out

    def one(rid: str, row_path: str, col: dict) -> dict:
        cp = _cell_path(row_path, col)
        if cp is None:
            return {"kind": "none", "kind_label": _KIND_WORDS["none"], "items": [], "corrected": None}
        # a table cell is supported by its ROW at best (the path grammar stops at the row)
        start = row_path if col.get("scope") == "row" else cp
        for rel, kind in _lookup_order(start):
            items = index.get(rid, {}).get(rel)
            if items:
                return {"kind": kind, "kind_label": _KIND_WORDS[kind], "items": with_context(items, kind), "corrected": fixes.get(rid, {}).get(cp)}
        return {"kind": "none", "kind_label": _KIND_WORDS["none"], "items": [], "corrected": fixes.get(rid, {}).get(cp)}

    out = []
    for c in cells:
        rid, path, col = c["record_id"], c.get("path") or "", cols.get(c["column"])
        if rid not in recs or col is None:
            continue
        if col.get("derived"):                              # a derived value rests on its inputs
            parts = [{"column": n, "label": cols[n]["label"], **one(rid, path, cols[n])} for n in col["derived"]["inputs"] if n in cols]
            out.append({"record_id": rid, "path": path, "column": c["column"], "kind": "derived",
                        "kind_label": "computed from extracted values", "formula": col["derived"]["text"], "inputs": parts,
                        "items": [], "corrected": None})
            continue
        out.append({"record_id": rid, "path": path, "column": c["column"], **one(rid, path, col)})
    return out
