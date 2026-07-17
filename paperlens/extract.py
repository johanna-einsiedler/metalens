"""PDF extraction orchestrator — the clean-rebuild of the archive's
jobs.py ``_run_extraction``, writing RECORDS + object-storage instead of a blob.

Flow:  PDF bytes
         -> render pages (vision) or text layer (text path)
         -> LLM call (injectable; default wires to providers.py, browser-side key)
         -> parse canonical JSON
         -> evidence-rect highlighting (real PyMuPDF text search, vendored pdf_utils)
         -> ingest into normalized records (paper / record / evidence_span / confidence)
         -> store page images in object storage + attach highlight rects to spans

The LLM step is an injectable ``complete`` callable so the whole chain is testable
offline with a fake provider + a generated PDF — no API key, no network. Real
extraction supplies a browser-side key per request (never persisted server-side).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Callable

from . import pdf_utils, records, storage
from .contract import parse_result_json
from .ingest import ingest

_VISION_PAGE_INSTRUCTION = (
    "\n\nThis document has been split into {n} page image{s}. They are provided "
    "below in order, each labelled with its sequential PDF page number (1 = first "
    "page of the PDF). IMPORTANT: when citing evidence, always use this sequential "
    "PDF page number — NOT any journal or book page number printed in the page."
)
_TEXT_PAGE_INSTRUCTION = (
    "\n\nThe document text has been extracted and split into {n} labelled page "
    "sections below. IMPORTANT: when citing evidence, use the PDF page number shown "
    "in the section headers (e.g. '--- PDF page 4 of 12 ---')."
)


@dataclass
class LLMResult:
    text: str
    finish_reason: str | None = None
    usage: dict | None = None
    resolved_model: str | None = None


CompleteFn = Callable[..., LLMResult]


def _default_complete(pdf_bytes: bytes, prompt: str, *, model: str, api_key: str,
                      base_url: str | None = None, use_text: bool = False) -> LLMResult:
    """Real LLM step via providers.py (vision by default, text path on request /
    for text-only providers). Imports providers lazily so the rest of the module
    (and its tests) don't require the openai SDK."""
    from . import providers
    provider = providers.get_provider(model, base_url)
    force_text = use_text or provider == "deepseek"

    if force_text:
        markdown_text, n = pdf_utils.pdf_to_markdown(pdf_bytes)
        if not markdown_text.strip():
            raise ValueError("No text layer found in this PDF (scanned/image-only "
                             "PDFs need the vision path).")
        instr = _TEXT_PAGE_INSTRUCTION.format(n=n)
        result, finish, usage, resolved = providers.extract_with_text(
            model=model, api_key=api_key, markdown_text=markdown_text,
            prompt=prompt, page_instruction=instr, base_url=base_url)
        return LLMResult(result, finish, usage, resolved)

    images = pdf_utils.pdf_to_images(pdf_bytes, dpi=pdf_utils.EXTRACTION_DPI, fmt="png")
    if not images:
        raise ValueError("The PDF appears to be empty.")
    n = len(images)
    instr = _VISION_PAGE_INSTRUCTION.format(n=n, s="s" if n != 1 else "")
    content: list[Any] = [{"type": "text", "text": prompt + instr}]
    for i, b64 in enumerate(images):
        content.append({"type": "text", "text": f"PDF page {i + 1} of {n}:"})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}})
    result, finish, usage, resolved = providers.extract_with_images(
        model=model, api_key=api_key, content_blocks=content, extraction_images=images,
        prompt=prompt, page_instruction=instr, n=n, base_url=base_url)
    return LLMResult(result, finish, usage, resolved)


def run_extraction(conn, pdf_bytes: bytes, prompt: str, *, model: str = "",
                   api_key: str = "", base_url: str | None = None, use_text: bool = False,
                   schema_id: str | None = None, session_id: str | None = None,
                   owner_user_id: str | None = None, source_job_id: str | None = None,
                   filename: str | None = None,
                   complete: CompleteFn | None = None, store=None) -> dict:
    store = store or storage.get_store()
    complete = complete or _default_complete

    llm = complete(pdf_bytes, prompt, model=model, api_key=api_key,
                   base_url=base_url, use_text=use_text)

    # Fail early + legibly if the model didn't return the canonical JSON (usually
    # a too-thin prompt, a safety block, or truncation) — surface the raw output.
    if not isinstance(parse_result_json(llm.text), dict):
        snippet = (llm.text or "").strip()[:500] or "<empty response>"
        raise ValueError(
            "The model did not return a JSON object. The prompt must instruct it to "
            "emit the canonical record JSON (with an `evidence` array). First 500 "
            f"chars of the model response:\n{snippet}")

    # evidence + highlight geometry (real PyMuPDF text search, vendored pdf_utils)
    evidence_items = pdf_utils.evidence_items_from_result(llm.text)
    for page, snippets in pdf_utils.recover_orphan_pages(llm.text, pdf_bytes).items():
        for s in snippets:
            evidence_items.append({"page": page, "snippet": s, "field": None, "source": None})
    page_images, highlights, scanned = pdf_utils.pdf_to_pages_with_rects(pdf_bytes, evidence_items)

    # normalize -> records
    res = ingest(llm.text)
    extraction = {"model": model, "resolved_model": llm.resolved_model,
                  "finish_reason": llm.finish_reason, "usage": llm.usage,
                  "n_pages": len(page_images)}
    from . import parsed
    sha = parsed.pdf_sha256(pdf_bytes)
    doc_id = records.persist(conn, res, schema_id=schema_id, session_id=session_id,
                             owner_user_id=owner_user_id, source_job_id=source_job_id,
                             extraction=extraction, filename=filename, pdf_sha256=sha)
    paper_id = conn.execute(
        "SELECT paper_id FROM extraction_document WHERE id = %s", (doc_id,)).fetchone()[0]
    # End the implicit read transaction opened by the SELECT above, so attach_rects'
    # transaction is the OUTERMOST one and actually commits (otherwise it nests as a
    # savepoint and the rect UPDATEs are rolled back when the worker closes the conn).
    conn.commit()

    # durable artifacts: source PDF (enables re-highlight without re-calling the LLM)
    # + rendered page images (survive restarts; power click-to-source highlights)
    store.put(storage.pdf_key(doc_id), pdf_bytes, "application/pdf")
    page_keys: list[str] = []
    for n, b64 in enumerate(page_images, start=1):
        key = storage.page_image_key(doc_id, n)
        store.put(key, base64.b64decode(b64), "image/jpeg")
        page_keys.append(key)
    records.attach_rects(conn, doc_id, highlights)

    # Persist the parsed text layer (deduped by PDF hash) for reuse: a "parsed text"
    # preview, faster locate, and re-extraction under a different preset. Best-effort —
    # never let a parse/store hiccup (e.g. a scanned PDF) fail an otherwise-good run.
    try:
        parsed.get_or_parse(conn, store, pdf_bytes)
    except Exception:
        pass

    return {"document_id": doc_id, "paper_id": str(paper_id),
            "n_records": len(res.records), "n_pages": len(page_images),
            "n_highlights": len(highlights), "scanned_pages": scanned,
            "page_image_keys": page_keys, "doi": res.doi}
