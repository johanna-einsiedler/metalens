"""Materialize a dataset into the two JSON files the metalens-datasets repo expects.

Datasets are live queries over records (never stored as a blob), so publishing needs
to reconstruct the exact publishable canonical JSON per document. This produces:

    metadata.json  {title, description, schema_id, recipe, stats, credibility, …}
    results.json   {"papers": [{filename, model, result: <publishable canonical JSON>}]}

`result` is `reconstruct.reconstruct_publishable(records.load(doc))` — the same shape
`scripts/import_published_datasets.py` re-ingests. No PDFs are exported (records only).
"""
from __future__ import annotations

from . import reconstruct, records


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
    }

    doc_rows = conn.execute(
        """SELECT DISTINCT ed.id, ed.filename
             FROM extraction_document ed JOIN record r ON r.document_id = ed.id
            WHERE r.dataset_id = %s::uuid
            ORDER BY ed.id""",
        (dataset_id,),
    ).fetchall()

    default_model = (ov.get("recipe") or {}).get("model")
    papers = []
    for (doc_id, filename) in doc_rows:
        pub = reconstruct.reconstruct_publishable(records.load(conn, str(doc_id)))
        mrow = conn.execute(
            "SELECT extraction ->> 'model' FROM record WHERE document_id = %s LIMIT 1", (doc_id,)
        ).fetchone()
        model = (mrow[0] if mrow and mrow[0] else default_model)
        name = filename or (pub.get("paper_metadata") or {}).get("title") or str(doc_id)
        papers.append({"filename": name, "model": model, "result": pub})

    return {"metadata": metadata, "results": {"papers": papers}}
