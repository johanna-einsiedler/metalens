"""Recompute the evidence highlight rects (and, with --issues, the structural issues) of
existing documents from the stored PDF and evidence rows — no model call, no re-extraction.
Run it after an improvement to the snippet matcher so older documents benefit too.

    uv run python scripts/rehighlight.py <document_id> [<document_id> …] [--issues]

Reads PAPERLENS_DATABASE_URL / PAPERLENS_STORAGE like the app (source .env first).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from psycopg.types.json import Json  # noqa: E402

from paperlens import pdf_utils, preset_spec, records, storage  # noqa: E402
from paperlens.reconstruct import reconstruct_publishable  # noqa: E402


def rehighlight(conn, store, doc_id: str, *, issues: bool) -> str:
    rows = conn.execute(
        "SELECT field_path, snippet, page, source FROM evidence_span WHERE document_id = %s::uuid",
        (doc_id,)).fetchall()
    conn.commit()                                   # end the implicit read transaction
    key = storage.pdf_key(doc_id)
    if not store.exists(key):
        return f"{doc_id}: no PDF in the store — skipped"
    items = [{"field": r[0], "snippet": r[1], "page": r[2], "source": r[3]} for r in rows if r[1]]
    _pages, highlights, _scanned = pdf_utils.pdf_to_pages_with_rects(store.get(key), items, render_pages=False)
    with conn.transaction():                        # stale rects go; unmatched spans stay rect-less
        conn.execute("UPDATE evidence_span SET rect = NULL WHERE document_id = %s::uuid", (doc_id,))
    n = records.attach_rects(conn, doc_id, highlights)
    msg = f"{doc_id}: {len(items)} evidence rows → {len(highlights)} located, {n} rows updated"
    if issues:
        row = conn.execute(
            """SELECT s.field_defs, d.params FROM extraction_document d
               JOIN schema s ON s.id = d.schema_id WHERE d.id = %s::uuid""", (doc_id,)).fetchone()
        res = records.load(conn, doc_id)
        conn.commit()
        spec = (row[0] or {}).get("spec") if row else None
        if spec and (spec.get("prompt") or {}).get("generate") != []:
            found = preset_spec.validate_result(reconstruct_publishable(res), spec, row[1])
            with conn.transaction():
                conn.execute("UPDATE extraction_document SET issues = %s WHERE id = %s::uuid",
                             (Json(found), doc_id))
            msg += f", {len(found)} issues"
        else:
            msg += ", issues unchanged (no declared spec)"
    return msg


def main(argv: list[str]) -> int:
    flags = {a for a in argv if a.startswith("--")}
    ids = [a for a in argv if not a.startswith("--")]
    if not ids:
        print(__doc__)
        return 2
    conn = records.connect()
    store = storage.get_store()
    try:
        for doc_id in ids:
            print(rehighlight(conn, store, doc_id, issues="--issues" in flags))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
