"""Reusable parsed-document cache.

The PDF text layer used to be re-extracted from the source PDF on every extraction
and highlight call, then discarded. This module persists a PDF's parsed markdown +
per-page text ONCE, keyed by the PDF's content hash, in object storage — so the same
parse can be re-used for a "parsed text" preview, faster locate/highlight, and (later)
re-extraction under a different preset without re-parsing.

Storage layout (object store, NOT the DB — text can be large):
    text/<sha>.md          full page-labelled markdown
    text/<sha>.pages.json  list[str], one entry per page
The DB `parsed_document` row is only the hash → object-key map. The `text/` prefix is
deliberately NOT served by the owner-gated /artifacts route (which matches pdf/ and
pages/ only), so parsed text is reachable solely through owner-gated API endpoints.
"""
from __future__ import annotations

import hashlib
import json

from . import pdf_utils, storage


def pdf_sha256(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def text_md_key(sha: str) -> str:
    return f"text/{sha}.md"


def text_pages_key(sha: str) -> str:
    return f"text/{sha}.pages.json"


def _split_pages(markdown_text: str) -> list[str]:
    """Split page-labelled markdown into per-page strings (reuses the providers splitter)."""
    try:
        from .providers import _split_markdown_pages
        pages = _split_markdown_pages(markdown_text)
    except Exception:
        pages = [markdown_text]
    # re.split can yield a leading empty/preamble chunk before the first page marker
    return [p for p in pages if p.strip()] or ([markdown_text] if markdown_text.strip() else [])


def get_or_parse(conn, store, pdf_bytes: bytes) -> dict:
    """Return {sha, markdown, pages, n_pages, cached}. Persists the parse on first sight.

    Best-effort and side-effecting: safe to call around the extraction transaction; the
    row insert is its own ON CONFLICT DO NOTHING transaction so a concurrent parse of the
    same PDF can't collide. Scanned/image-only PDFs yield empty text (stored as empty),
    never an error.
    """
    sha = pdf_sha256(pdf_bytes)
    store = store or storage.get_store()

    row = conn.execute(
        "SELECT md_key, pages_key, n_pages FROM parsed_document WHERE pdf_sha256 = %s", (sha,)
    ).fetchone()
    if row:
        md_key, pages_key, n_pages = row
        try:
            markdown = store.get(md_key).decode("utf-8")
            pages = json.loads(store.get(pages_key).decode("utf-8"))
            return {"sha": sha, "markdown": markdown, "pages": pages,
                    "n_pages": n_pages, "cached": True}
        except Exception:
            pass  # object vanished / unreadable → fall through and re-parse

    markdown, n = pdf_utils.pdf_to_markdown(pdf_bytes)
    pages = _split_pages(markdown)
    md_key, pages_key = text_md_key(sha), text_pages_key(sha)
    store.put(md_key, markdown.encode("utf-8"), "text/markdown")
    store.put(pages_key, json.dumps(pages).encode("utf-8"), "application/json")
    with conn.transaction():
        conn.execute(
            "INSERT INTO parsed_document (pdf_sha256, n_pages, md_key, pages_key, char_len) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (pdf_sha256) DO NOTHING",
            (sha, n, md_key, pages_key, len(markdown)),
        )
    return {"sha": sha, "markdown": markdown, "pages": pages, "n_pages": n, "cached": False}


def get_by_sha(conn, store, sha: str) -> dict | None:
    """Load a cached parse by hash, or None if absent/unreadable."""
    row = conn.execute(
        "SELECT md_key, pages_key, n_pages FROM parsed_document WHERE pdf_sha256 = %s", (sha,)
    ).fetchone()
    if not row:
        return None
    md_key, pages_key, n_pages = row
    store = store or storage.get_store()
    try:
        markdown = store.get(md_key).decode("utf-8")
        pages = json.loads(store.get(pages_key).decode("utf-8"))
    except Exception:
        return None
    return {"sha": sha, "markdown": markdown, "pages": pages, "n_pages": n_pages, "cached": True}


def for_document(conn, store, doc_id: str) -> dict | None:
    """Cached parse for a document via its stored pdf_sha256 (None if not cached)."""
    row = conn.execute(
        "SELECT pdf_sha256 FROM extraction_document WHERE id = %s", (doc_id,)
    ).fetchone()
    if not row or not row[0]:
        return None
    return get_by_sha(conn, store, row[0])


def page_text(conn, store, doc_id: str, page: int) -> str | None:
    """Cached text of one 1-indexed page for a document (None if not cached)."""
    data = for_document(conn, store, doc_id)
    if not data:
        return None
    pages = data["pages"]
    idx = page - 1
    return pages[idx] if 0 <= idx < len(pages) else None
