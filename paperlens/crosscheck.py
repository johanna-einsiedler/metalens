"""Two datasets that read the same papers from two sides, checked against each other.

A dataset may name a COMPANION dataset (``dataset.companion_dataset_id``) when a registered
check exists for the pair of presets. The first one: register-data claims against register-data
tables — every table result a claim cites (table, panel, column, point estimate, written by the
claims pass from the prose) is looked up in the companion's regression columns (transcribed
cell by cell by the tables pass), and the printed coefficient must agree. Two passes agreeing
on -1.292 is strong evidence both are right; disagreeing is a flag on one of them.

Statuses per result row
  exact        the exhibit resolves and a coefficient cell equals the estimate
  mismatch     the exhibit resolves but no coefficient cell equals the estimate → review both
  unlinked     no regression column of that paper matches the cited table / panel / column
  no_estimate  the claim cites the exhibit but carries no number to check
  figure       the result is a figure; the tables dataset has nothing to check it against
  stated       the number comes from the paper's prose, not from a printed table cell — the
               exhibit still says which analysis it is, but there is no cell to compare it to

The check runs on live data and on releases alike; a release freezes its rows (``snapshot
["crosscheck"]``) against the companion's latest release at the time, so a published page
never changes its verdicts under the reader.
"""
from __future__ import annotations

import re

from . import records

# (this preset, companion preset) → the row unit checked and the checker
CHECKS: dict[tuple[str, str], dict] = {
    ("register-claims", "register-tables"): {"unit": "results", "label": "point estimates against the transcribed table cells"},
}

# ── the exhibit grammar (shared with scripts/import_danish.py and the pipeline's link_tables.py) ──
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_ROMAN_RE = re.compile(r"^([IVX]{1,6})$")
_TOKEN_RE = re.compile(r"^([A-Z]?)[.\s-]*(\d{1,3})$")
_APPENDIX_ROMAN_RE = re.compile(r"^([A-Z])[.\s-]*([IVX]{1,6})$")
_PANEL_TOKEN = re.compile(r"^\s*(?:panels?|parts?)?\s*([A-Z])(?:\.?\s*([IVX]{1,4}|[0-9]{1,2}))?(?=[\s.:;,)\-–]|$)"
                          r"|^\s*(?:panels?|parts?)?\s*([0-9]{1,2})(?=[\s.:;,)\-–]|$)", re.I)


def _roman(s: str) -> int:
    total, prev = 0, 0
    for ch in reversed(s):
        v = _ROMAN[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def table_token(tok) -> str | None:
    """'Table II' → '2', 'Table A.I' → 'A1', 'Table A1' → 'A1', '(3)' → '3'; None when not a table token."""
    if tok is None:
        return None
    s = re.sub(r"^\s*TABLES?\s*", "", str(tok).upper().strip())
    s = re.sub(r"[()\[\]]", "", s).strip()
    m = _ROMAN_RE.match(s)
    if m:
        return str(_roman(m.group(1)))
    m = _TOKEN_RE.match(s)
    if m:
        return f"{m.group(1)}{int(m.group(2))}"
    m = _APPENDIX_ROMAN_RE.match(s)
    if m:
        return f"{m.group(1)}{_roman(m.group(2))}"
    return None


def panel_token(p) -> str:
    """'Panel A.i - Completed upper secondary' → 'A.I'; 'Panel A1 (amounts)' → 'A1'; 'A. Vocational' → 'A'."""
    if p in (None, ""):
        return ""
    m = _PANEL_TOKEN.match(str(p))
    if not m:
        return re.sub(r"[^A-Z0-9.]", "", str(p).upper())
    if m.group(3):
        return m.group(3)
    letter, sub = m.group(1).upper(), (m.group(2) or "").upper()
    if not sub:
        return letter
    return f"{letter}.{sub}" if sub[0] in "IVX" else f"{letter}{sub}"


def panels_compatible(a: str, b: str) -> bool:
    """A bare letter matches its sub-panels ('A' ~ 'A.I' ~ 'A1'); the side naming no sub-panel is the less specific one."""
    if not a or not b:
        return True
    return a == b or (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))


def column_digits(c) -> str:
    return re.sub(r"\D", "", str(c or ""))


def num_match(a, b) -> bool:
    if a is None or b is None:
        return False
    if a == b:
        return True
    return abs(a - b) <= max(5e-4, 5e-3 * max(abs(a), abs(b)))


def _paper_key(doi, title) -> str:
    return (doi or "").strip().lower() or re.sub(r"\W+", " ", (title or "").lower()).strip()


# ── the two sides ─────────────────────────────────────────────────────────────────────────────
def preset_of(schema_id: str | None) -> str:
    return (schema_id or "").split("@")[0]


def companion_preset(schema_id: str | None) -> str | None:
    """The preset a dataset of this preset may be checked against, or None (no check registered)."""
    me = preset_of(schema_id)
    return next((b for (a, b) in CHECKS if a == me), None)


def check_for(schema_id: str | None, companion_schema_id: str | None) -> dict | None:
    return CHECKS.get((preset_of(schema_id), preset_of(companion_schema_id)))


def regressions_of(conn, companion_id: str, release: dict | None = None) -> list[dict]:
    """The companion's regression columns with their cells, from live records or a release snapshot."""
    from . import analysis_table
    src = analysis_table.release_source(conn, release) if release else analysis_table.live_source(conn, companion_id)
    if src is None:
        return []
    out = []
    for (rid, _doc, _ei, fv, _status, _ex, _sid, title, doi, _year, _journal, _authors, _fn, _created) in src["raw"]:
        fv = fv or {}
        out.append({"record_id": rid, "paper": _paper_key(doi, title), "regression_id": fv.get("regression_id"),
                    "table": table_token(fv.get("table_number")), "panel": panel_token(fv.get("panel")), "column": column_digits(fv.get("column")),
                    "coefficients": [float(c["numeric_value"]) for c in (fv.get("cells") or [])
                                     if isinstance(c, dict) and c.get("row_type") == "coefficient" and isinstance(c.get("numeric_value"), (int, float))]})
    return out


def statuses(claim_rows: list[dict], regressions: list[dict]) -> list[dict]:
    """Pure: one verdict per claim result row. A row is {record_id, path, claim_id, result_id, doi, title,
    exhibit, source_table, panel, column, point_estimate}."""
    by_paper: dict[str, list[dict]] = {}
    for r in regressions:
        by_paper.setdefault(r["paper"], []).append(r)
    out = []
    for row in claim_rows:
        base = {k: row.get(k) for k in ("record_id", "path", "claim_id", "result_id", "source_table", "panel", "column", "point_estimate")}
        kind, whence = row.get("exhibit") or "table", row.get("value_from") or "printed"
        if whence != "printed":
            out.append({**base, "status": "stated", "regression_id": None, "table_coefficients": [],
                        "detail": ("the paper states this number in its prose, not in the exhibit — the analysis is "
                                   f"{row.get('source_table') or 'not identified'}" if whence == "text" else "the paper reports no number here")}); continue
        if kind in ("figure", "none"):
            out.append({**base, "status": "figure", "regression_id": None, "table_coefficients": [],
                        "detail": "a figure: nothing to transcribe against" if kind == "figure"
                                  else "the paper shows no exhibit for this estimate — its method cannot be read off one"}); continue
        tok, pan, col = table_token(row.get("source_table")), panel_token(row.get("panel")), column_digits(row.get("column"))
        cands = [r for r in by_paper.get(_paper_key(row.get("doi"), row.get("title")), [])
                 if tok and r["table"] == tok and (not col or r["column"] == col) and panels_compatible(pan, r["panel"])]
        pt = row.get("point_estimate")
        if not cands:
            out.append({**base, "status": "unlinked", "regression_id": None, "table_coefficients": [],
                        "detail": f"no regression column for {row.get('source_table') or '?'}{' ' + str(row.get('panel')) if row.get('panel') else ''}{' ' + str(row.get('column')) if row.get('column') else ''} in the companion"}); continue
        if pt is None:
            out.append({**base, "status": "no_estimate", "regression_id": cands[0]["regression_id"], "table_coefficients": cands[0]["coefficients"][:6],
                        "detail": "the claim carries no estimate to check"}); continue
        hit = next((r for r in cands if any(num_match(float(pt), c) for c in r["coefficients"])), None)
        if hit:
            out.append({**base, "status": "exact", "regression_id": hit["regression_id"], "table_coefficients": [c for c in hit["coefficients"] if num_match(float(pt), c)][:1],
                        "detail": f"a coefficient cell of {hit['regression_id']} equals {pt}"})
        else:
            out.append({**base, "status": "mismatch", "regression_id": cands[0]["regression_id"], "table_coefficients": cands[0]["coefficients"][:6],
                        "detail": f"{len(cands)} column{'s' if len(cands) != 1 else ''} resolve but no coefficient cell equals {pt}"})
    return out


def summary(rows: list[dict]) -> dict:
    out = {"exact": 0, "mismatch": 0, "unlinked": 0, "no_estimate": 0, "figure": 0, "stated": 0}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def claim_rows_from_table(table: dict) -> list[dict]:
    """The rows the check needs, from an analysis table of the checked unit."""
    at = {c["name"]: k for k, c in enumerate(table["columns"])}
    get = lambda row, name: row["v"][at[name]] if name in at else None   # noqa: E731
    recs = table["records"]
    return [{"record_id": recs[row["r"]]["id"], "path": row["p"], "claim_id": get(row, "claim_id"), "result_id": get(row, "result_id"),
             "doi": get(row, "_doi"), "title": get(row, "_title"), "exhibit": get(row, "exhibit"), "source_table": get(row, "source_table"),
             "panel": get(row, "panel"), "column": get(row, "column"), "point_estimate": get(row, "point_estimate")}
            for row in table["rows"] if row["p"]]                    # an entry without results has no row to check


# ── what the dataset page and the release read ───────────────────────────────────────────────
def companion_of(conn, dataset: dict) -> dict | None:
    """{id, title, schema_id, release} of the companion dataset, or None."""
    cid = dataset.get("companion_dataset_id")
    if not cid:
        return None
    c = records.get_dataset(conn, cid)
    if c is None:
        return None
    from . import releases
    latest = releases.latest(conn, cid)
    return {"id": c["id"], "title": c.get("title"), "schema_id": c.get("schema_id"), "release": latest["number"] if latest else None}


def frozen(conn, dataset_id: str) -> dict | None:
    """The verdicts to freeze into a release of ``dataset_id``: against the companion's latest
    release (or its live data when it has none, flagged)."""
    from . import analysis_table, releases
    d = records.get_dataset(conn, dataset_id)
    if not d or not d.get("companion_dataset_id"):
        return None
    chk = check_for(d.get("schema_id"), (records.get_dataset(conn, d["companion_dataset_id"]) or {}).get("schema_id"))
    if not chk:
        return None
    table = analysis_table.build(conn, dataset_id, chk["unit"], owner=False, crosscheck=False)
    if table is None:
        return None
    rel = releases.latest(conn, d["companion_dataset_id"], with_snapshot=True)
    rows = statuses(claim_rows_from_table(table), regressions_of(conn, d["companion_dataset_id"], rel))
    return {"companion_dataset_id": d["companion_dataset_id"], "companion_release": rel["number"] if rel else None,
            "companion_content_sha": rel["content_sha"] if rel else None, "unit": chk["unit"], "rows": rows}


def run(conn, dataset_id: str, *, release: dict | None = None) -> dict | None:
    """The full report: companion, summary, one row per checked result. On a release: the frozen
    rows. Live: computed now against the companion's latest release (or live data)."""
    d = records.get_dataset(conn, dataset_id)
    if d is None:
        return None
    comp = companion_of(conn, d)
    applies = companion_preset(d.get("schema_id"))
    if release is not None and isinstance((release.get("snapshot") or {}).get("crosscheck"), dict):
        fz = release["snapshot"]["crosscheck"]
        c = records.get_dataset(conn, fz["companion_dataset_id"]) or {}
        return {"applies": True, "companion_preset": applies, "companion": {"id": fz["companion_dataset_id"], "title": c.get("title"), "release": fz.get("companion_release")},
                "frozen": True, "unit": fz["unit"], "summary": summary(fz["rows"]), "rows": fz["rows"]}
    if not comp:
        return {"applies": bool(applies), "companion_preset": applies, "companion": None, "frozen": False, "summary": None, "rows": []}
    fz = frozen(conn, dataset_id)
    if fz is None:
        return {"applies": bool(applies), "companion_preset": applies, "companion": comp, "frozen": False, "summary": None, "rows": [],
                "note": "no check is registered for these two presets"}
    return {"applies": True, "companion_preset": applies, "companion": comp, "frozen": False, "unit": fz["unit"],
            "companion_unreleased": fz["companion_release"] is None, "summary": summary(fz["rows"]), "rows": fz["rows"]}


def column_values(conn, dataset: dict, unit_id: str, table_rows: list[dict], recs_out: list[dict], release: dict | None) -> dict[tuple, str] | None:
    """{(record id, path): status} for the checked unit of a dataset with a companion — from the
    release's frozen rows, or computed live. None when the check does not apply."""
    if release is not None:                       # a release keeps its verdicts even after the companion is gone
        fz = (release.get("snapshot") or {}).get("crosscheck")
        return {(r["record_id"], r["path"]): r["status"] for r in fz["rows"]} if fz and fz.get("unit") == unit_id else None
    if not dataset.get("companion_dataset_id"):
        return None
    comp = records.get_dataset(conn, dataset["companion_dataset_id"])
    chk = check_for(dataset.get("schema_id"), (comp or {}).get("schema_id"))
    if not chk or chk["unit"] != unit_id:
        return None
    from . import releases
    rel = releases.latest(conn, dataset["companion_dataset_id"], with_snapshot=True)
    regs = regressions_of(conn, dataset["companion_dataset_id"], rel)
    rows = [{"record_id": recs_out[row["r"]]["id"], "path": row["p"], "doi": row["vals"].get("_doi"), "title": row["vals"].get("_title"),
             "exhibit": row["vals"].get("exhibit"), "source_table": row["vals"].get("source_table"), "panel": row["vals"].get("panel"),
             "column": row["vals"].get("column"), "point_estimate": row["vals"].get("point_estimate")} for row in table_rows if row["p"]]
    return {(r["record_id"], r["path"]): r["status"] for r in statuses(rows, regs)}


CHECK_COLUMN = {"name": "_crosscheck", "label": "Cross-check", "type": "enum", "scope": "check",
                "help": "the claim's point estimate against the companion tables dataset: exact, mismatch, unlinked, no_estimate, figure",
                "options": ["exact", "mismatch", "unlinked", "no_estimate", "figure"]}
