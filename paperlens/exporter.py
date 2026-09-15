"""Materialize a dataset into the two JSON files the metalens-datasets repo expects.

Datasets are live queries over records (never stored as a blob), so publishing needs
to reconstruct the exact publishable canonical JSON per document. This produces:

    metadata.json  {title, description, schema_id, recipe, stats, credibility, …}
    results.json   {"papers": [{filename, model, result: <publishable canonical JSON>}]}

`result` is `reconstruct.reconstruct_publishable(records.load(doc))` — the same shape
`scripts/import_published_datasets.py` re-ingests. No PDFs are exported (records only).
"""
from __future__ import annotations

import os

from . import reconstruct, records


def _preset_block(conn, schema_id: str | None) -> dict | None:
    if not schema_id:
        return None
    row = records.get_schema(conn, schema_id)
    if row is None:
        return None
    fd = row.get("field_defs") or {}
    return {"schema_id": schema_id, "preset_id": row.get("preset_id"),
            "label": fd.get("label") if isinstance(fd, dict) else None,
            "format": fd.get("format") if isinstance(fd, dict) else None,
            "spec": records.schema_spec(conn, schema_id)}


def materialize_dataset(conn, dataset_id: str) -> dict:
    ov = records.dataset_overview(conn, dataset_id)
    if ov is None:
        raise ValueError("Dataset not found.")

    metadata = {
        "title": ov.get("title"),
        "description": ov.get("description"),
        "schema_id": ov.get("schema_id"),
        "slug": ov.get("slug"),
        "cite_as": ov.get("cite_as"),
        "visibility": ov.get("visibility"),
        "recipe": ov.get("recipe"),
        "stats": ov.get("stats"),
        "credibility": ov.get("credibility"),
        # The preset travels WITH the data: a consumer can rebuild the review grammar
        # (fields, tabs, evidence + confidence policy) without this server. Inferred for
        # datasets extracted before the declarative format existed.
        "preset": _preset_block(conn, ov.get("schema_id")),
    }

    doc_rows = conn.execute(
        """SELECT DISTINCT ed.id, ed.filename
             FROM extraction_document ed JOIN record r ON r.document_id = ed.id
            WHERE r.dataset_id = %s::uuid
            ORDER BY ed.id""",
        (dataset_id,),
    ).fetchall()

    # What to cite: the engine that produced the file, alongside the preset (above).
    from . import __version__
    metadata["engine"] = {"name": "metalens", "version": __version__, "git_sha": _git_sha()}

    default_model = (ov.get("recipe") or {}).get("model")
    papers = []
    for (doc_id, filename) in doc_rows:
        pub = reconstruct.reconstruct_publishable(records.load(conn, str(doc_id)))
        mrow = conn.execute(
            "SELECT extraction FROM record WHERE document_id = %s LIMIT 1", (doc_id,)
        ).fetchone()
        ex = (mrow[0] if mrow and isinstance(mrow[0], dict) else {}) or {}
        model = ex.get("model") or default_model
        drow = conn.execute(
            "SELECT prompt_sha256, params, created_at, schema_id FROM extraction_document WHERE id = %s", (doc_id,)
        ).fetchone()
        name = filename or (pub.get("paper_metadata") or {}).get("title") or str(doc_id)
        papers.append({"filename": name, "model": model, "result": pub,
                       # per-paper provenance: enough to re-run the same extraction later
                       "provenance": {"resolved_model": ex.get("resolved_model"),
                                      "prompt_sha256": drow[0] if drow else None,
                                      "params": drow[1] if drow else None,
                                      "extracted_at": drow[2].isoformat() if drow and drow[2] else None,
                                      "schema_id": drow[3] if drow else None}})

    return {"metadata": metadata, "results": {"papers": papers}}


def _git_sha() -> str | None:
    sha = os.environ.get("PAPERLENS_GIT_SHA")
    if sha:
        return sha
    head = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".git", "HEAD")
    try:
        ref = open(head).read().strip()
        if ref.startswith("ref: "):
            return open(os.path.join(os.path.dirname(head), ref[5:])).read().strip()[:12]
        return ref[:12]
    except OSError:
        return None
