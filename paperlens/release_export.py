"""A dataset release as STATIC FILES: what a dashboard needs, readable without Metalens.

Dashboards built with the Metalens builder read a release through the API. A dashboard someone
writes themselves (their own JavaScript + D3, hosted wherever they like) reads the same release
from these files — from the datasets repository on GitHub, a Zenodo deposit, or a folder next to
the page — so it keeps working whatever happens to a Metalens server, and it can be pinned to
"v3" or follow "latest".

    release.json            what this is: dataset, release number / date / content hash, badge,
                            citation, preset, engine, and per row layout its columns (the contract
                            a dashboard declares its needs against)
    tables/<unit>.json      one typed table per row layout: columns, papers, records, rows
    evidence.json           the quote, page and source behind every cell that has one
    README.md               what the files are and how to cite them

Only what a public reader of a published dashboard could see: never file names, document ids,
geometry, page images or PDFs. Computed columns (Hedges' g …) carry no evidence of their own:
they name their inputs and formula, and the inputs' evidence is in the file.
"""
from __future__ import annotations

import io
import json
import re
import zipfile

from . import analysis_table, records

FORMAT = "metalens-release"
FORMAT_VERSION = 1


def _unit_file(unit_id: str) -> str:
    return "tables/" + re.sub(r"[^A-Za-z0-9_.-]+", "_", unit_id) + ".json"


def _dumps(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def _evidence(conn, release: dict, table: dict, items: list[dict], item_at: dict) -> dict:
    """{row index: {column: {k: kind, i: [item indices], was?: original value}}} for one table.
    Quotes are stored once (``items``) however many cells they support."""
    cols = [c for c in table["columns"] if c.get("scope") not in ("system", "derived")]
    idx = {c["name"]: k for k, c in enumerate(table["columns"])}
    ask = [{"record_id": table["records"][row["r"]]["id"], "path": row["p"], "column": c["name"], "_row": n}
           for n, row in enumerate(table["rows"]) for c in cols if row["v"][idx[c["name"]]] not in (None, "")]
    found = analysis_table.cell_evidence(conn, release["dataset_id"], table["unit"]["id"], ask, release=release,
                                         max_cells=None, read_layout=False)
    by_key = {(e["record_id"], e["path"], e["column"]): e for e in found}
    out: dict[str, dict] = {}
    for a in ask:
        e = by_key.get((a["record_id"], a["path"], a["column"]))
        if not e or (not e.get("items") and not e.get("corrected")):
            continue
        refs = []
        for it in e.get("items") or []:
            key = json.dumps(it, sort_keys=True, ensure_ascii=False, default=str)
            if key not in item_at:
                item_at[key] = len(items); items.append(it)
            refs.append(item_at[key])
        cell = {"k": e["kind"], "i": refs}
        if e.get("corrected"):
            cell["was"] = e["corrected"].get("original_value")
        out.setdefault(str(a["_row"]), {})[a["column"]] = cell
    return out


def build(conn, release: dict) -> dict[str, bytes]:
    """{relative path: file content} for one release (a releases row WITH its snapshot)."""
    first = analysis_table.build(conn, release["dataset_id"], None, owner=False, release=release)
    tables = {first["unit"]["id"]: first}
    for u in first["units"]:
        if u["id"] not in tables:
            tables[u["id"]] = analysis_table.build(conn, release["dataset_id"], u["id"], owner=False, release=release)
    d = first["dataset"]
    ds = records.get_dataset(conn, release["dataset_id"]) or {}
    files: dict[str, bytes] = {}
    items: list[dict] = []
    item_at: dict[str, int] = {}
    cells: dict[str, dict] = {}
    units = []
    for u in first["units"]:
        t = tables[u["id"]]
        files[_unit_file(u["id"])] = _dumps({"format": FORMAT, "format_version": FORMAT_VERSION, "unit": t["unit"],
                                             "columns": t["columns"], "papers": t["papers"], "records": t["records"], "rows": t["rows"]})
        cells[u["id"]] = _evidence(conn, release, t, items, item_at)
        units.append({"id": u["id"], "label": u["label"], "level": u["level"], "default": u["default"], "n_rows": len(t["rows"]),
                      "file": _unit_file(u["id"]),
                      # the contract: what a dashboard may declare that it needs
                      "columns": [{k: c.get(k) for k in ("name", "label", "type", "scope", "roles", "n", "distinct") if c.get(k) is not None}
                                  | ({"derived": c["derived"]} if c.get("derived") else {}) for c in t["columns"]]})
    files["evidence.json"] = _dumps({"format": FORMAT, "format_version": FORMAT_VERSION,
                                     "kinds": {"exact": "the value itself is quoted", "row": "the table row is quoted",
                                               "table": "the whole table is cited", "entry": "quoted for the entry as a whole"},
                                     "items": items, "cells": cells})
    meta = {"format": FORMAT, "format_version": FORMAT_VERSION,
            "dataset": {"title": d.get("title"), "slug": ds.get("slug"), "author": d.get("author"), "citation": d.get("citation"),
                        "published_url": d.get("published_url"), "description": ds.get("description"),
                        "keywords": [k for k in (ds.get("keywords") or []) if isinstance(k, str)]},
            "release": {"number": release["number"], "created_at": release["created_at"], "content_sha": release["content_sha"],
                        "notes": release.get("notes"), "changes": release.get("changes"),
                        "doi": release.get("doi"), "concept_doi": ds.get("zenodo_concept_doi")},
            "credibility": release.get("credibility"), "engine": release.get("engine"), "preset": d.get("preset"),
            "schema_id": release.get("schema_id"), "n_papers": d.get("n_papers"), "left_out": d.get("left_out"),
            "units": units, "files": sorted([*files, "release.json", "README.md"])}
    files["release.json"] = json.dumps(meta, ensure_ascii=False, indent=1, default=str).encode("utf-8")
    files["README.md"] = _readme(meta).encode("utf-8")
    return files


def _readme(meta: dict) -> str:
    rel, ds = meta["release"], meta["dataset"]
    lines = [f"# {ds.get('title') or 'Dataset'} — release v{rel['number']}", "",
             f"Frozen on {str(rel['created_at'])[:10]} · content sha256 `{rel['content_sha']}` · "
             f"{(meta.get('credibility') or {}).get('label') or ''} · {meta.get('n_papers')} papers", ""]
    if rel.get("doi"):
        lines[2] += f" · DOI [{rel['doi']}](https://doi.org/{rel['doi']})"
    if ds.get("citation"):
        lines += ["## How to cite", "", ds["citation"] + (f" https://doi.org/{rel['doi']}" if rel.get("doi") else ""), ""]
    lines += ["## Files", "",
              "- `release.json` — what this release is, and per row layout the columns it offers",
              *[f"- `{u['file']}` — one row per {u['label'].lower()} ({u['n_rows']} rows)" for u in meta["units"]],
              "- `evidence.json` — the quote, page and source behind every cell that has one "
              "(`cells[unit][row index][column]` → `items`)", "",
              "Every value was extracted from the cited paper; columns marked `derived` are computed from "
              "extracted values by the stated formula. Made with Metalens.", ""]
    return "\n".join(lines)


def as_zip(files: dict[str, bytes], folder: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(files):
            info = zipfile.ZipInfo(f"{folder}/{path}", date_time=(2020, 1, 1, 0, 0, 0))   # same bytes for the same release
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, files[path])
    return buf.getvalue()
