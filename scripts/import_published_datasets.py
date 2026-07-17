"""Import published metalens-datasets into the local DB as public example datasets.

Reads a local clone of github.com/johanna-einsiedler/metalens-datasets and ingests
the curated datasets (records only — these are pre-extracted, so no PDFs / page
images). Replaces any existing PUBLIC example datasets, so the catalogue starts off
with just these two.

    git clone https://github.com/johanna-einsiedler/metalens-datasets.git
    uv run python scripts/import_published_datasets.py --repo ./metalens-datasets
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paperlens import records          # noqa: E402
from paperlens.ingest import ingest    # noqa: E402


def adapt_ai_findings(paper: dict) -> dict:
    """ai-at-work shape: {filename, model, result:{paper_metadata, findings, …}}.
    Map the core array `findings` -> `samples` and fold the paper-level extras into
    paper_metadata so detect_core picks the right array."""
    r = paper.get("result") or {}
    pm = dict(r.get("paper_metadata") or {})
    for k in ("subtopics", "one_line", "qual_notes"):
        if r.get(k) is not None:
            pm.setdefault(k, r[k])
    pm.setdefault("title", paper.get("filename"))
    return {"samples": r.get("findings") or [],
            "paper_metadata": pm,
            "evidence": r.get("evidence"),
            "extraction_confidence": r.get("extraction_confidence")}


def adapt_masem_factors(paper: dict) -> dict:
    """tas20 shape: {filename, …, entries:[{sample_id, factor_loadings, …}], …}.
    `entries` are the per-sample factor records (our masem-ncs18 schema)."""
    return {"samples": paper.get("entries") or [],
            "paper_metadata": {"title": paper.get("filename")}}


DATASETS = [
    ("ai-at-work-2026-06",      "ai-findings@v1",  adapt_ai_findings),
    ("tas20-corrected-2026-06", "masem-ncs18@v1",  adapt_masem_factors),
]


def remove_public_examples(conn) -> None:
    """Delete existing public datasets + their records (cascades evidence) so the
    catalogue starts clean. Private/owned data is untouched."""
    for did, title in conn.execute(
            "SELECT id, title FROM dataset WHERE visibility = 'public'").fetchall():
        docs = [r[0] for r in conn.execute(
            "SELECT DISTINCT document_id FROM record "
            "WHERE dataset_id = %s AND document_id IS NOT NULL", (did,)).fetchall()]
        for doc in docs:
            conn.execute("DELETE FROM extraction_document WHERE id = %s", (doc,))
        conn.execute("DELETE FROM record WHERE dataset_id = %s", (did,))
        conn.execute("DELETE FROM dataset WHERE id = %s::uuid", (did,))
        print(f"  removed existing public dataset: {title}")


def import_one(conn, repo: str, dirname: str, schema_id: str, adapt) -> None:
    base = os.path.join(repo, "datasets", dirname)
    with open(os.path.join(base, "metadata.json")) as fh:
        meta = json.load(fh)
    with open(os.path.join(base, "results.json")) as fh:
        res = json.load(fh)
    records.upsert_schema(conn, schema_id)   # the dataset FK needs the schema row to exist
    ds = records.create_dataset(conn, title=meta["title"], description=meta.get("description"),
                                schema_id=schema_id, visibility="public")
    n_ok = n_rec = n_skip = 0
    for paper in res.get("papers", []):
        if paper.get("extraction_failed"):
            n_skip += 1
            continue
        norm = adapt(paper)
        if not norm.get("samples"):
            n_skip += 1
            continue
        try:
            r = ingest(norm)
        except Exception as exc:                      # noqa: BLE001
            print(f"    skip {paper.get('filename')}: {exc}")
            n_skip += 1
            continue
        doc_id = records.persist(conn, r, schema_id=schema_id)
        records.assign_document_to_dataset(conn, ds["id"], doc_id)
        n_ok += 1
        n_rec += len(r.records)
    print(f"  imported {dirname}: {n_ok} papers, {n_rec} records "
          f"({n_skip} skipped) -> dataset {ds['id']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="path to a clone of metalens-datasets")
    args = ap.parse_args()
    conn = records.connect()
    try:
        print("Removing existing public example datasets…")
        remove_public_examples(conn)
        print("Importing curated datasets…")
        for dirname, schema_id, adapt in DATASETS:
            import_one(conn, args.repo, dirname, schema_id, adapt)
    finally:
        conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
