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
import hashlib
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
                   filename: str | None = None, params: dict | None = None,
                   entries_key: str | None = None, spec: dict | None = None,
                   prompt_edited: bool = False,
                   complete: CompleteFn | None = None, store=None,
                   run_id: str | None = None, step_id: str | None = None,
                   role: str = "final", group_id: str | None = None, pass_index: int | None = None,
                   chain: dict | None = None, record_chain: dict | None = None,
                   store_artifacts: bool = True) -> dict:
    """``spec`` is the preset declaration the run was made with (gives ingest the declared
    core array and lets the result be validated for triage); ``params`` the parameter
    values it used (stored for provenance); ``prompt_edited`` whether the caller replaced
    the generated prompt with its own text. Without a spec the schema row is consulted.
    ``run_id`` / ``step_id`` name the workflow run and step this extraction belongs to.

    A pipeline (chain.py) persists each of its documents through here: a run has
    ``role="run"`` and ``store_artifacts=False`` (its quotes are located, but the PDF and page
    images are stored once, under the final), the final carries the pipeline's provenance
    (``chain``) and what the engine knows per entry (``record_chain``)."""
    store = store or storage.get_store()
    if spec is None and schema_id:
        try:
            spec = records.schema_spec(conn, schema_id)
        except Exception:                  # never let grammar lookup fail an extraction
            spec = None
    if spec is not None and not entries_key:
        entries_key = (spec.get("entries") or {}).get("key")
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
    page_images, highlights, scanned = pdf_utils.pdf_to_pages_with_rects(pdf_bytes, evidence_items, render_pages=store_artifacts)

    # normalize -> records
    res = ingest(llm.text, entries_key=entries_key)
    # What the model got structurally wrong, for the coder's triage — never a failure.
    issues = None
    if spec is not None and not (spec.get("prompt") or {}).get("generate") == []:
        try:
            from . import preset_spec
            issues = preset_spec.validate_result(parse_result_json(llm.text), spec, params)
        except Exception:
            issues = None
    # the exact prompt is provenance: a preset can change wording without re-versioning
    # its schema, so each document remembers what it was actually asked
    # an import comes through here with no prompt: it ran elsewhere, so it has no hash either
    # (a hash of the empty string would read as "a prompt" to every export and matcher)
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None
    extraction = {"model": model, "resolved_model": llm.resolved_model,
                  "finish_reason": llm.finish_reason, "usage": llm.usage,
                  "n_pages": len(page_images), "prompt_sha256": prompt_sha256,
                  "prompt_edited": bool(prompt_edited),
                  **({"run_id": run_id, "step_id": step_id} if run_id else {})}
    from . import parsed
    sha = parsed.pdf_sha256(pdf_bytes)
    doc_id = records.persist(conn, res, schema_id=schema_id, session_id=session_id,
                             owner_user_id=owner_user_id, source_job_id=source_job_id,
                             extraction=extraction, filename=filename, pdf_sha256=sha,
                             prompt_sha256=prompt_sha256, prompt=prompt or None, params=params, issues=issues,
                             run_id=run_id, step_id=step_id,
                             role=role, group_id=group_id, pass_index=pass_index, chain=chain, record_chain=record_chain)
    paper_id = conn.execute(
        "SELECT paper_id FROM extraction_document WHERE id = %s", (doc_id,)).fetchone()[0]
    # End the implicit read transaction opened by the SELECT above, so attach_rects'
    # transaction is the OUTERMOST one and actually commits (otherwise it nests as a
    # savepoint and the rect UPDATEs are rolled back when the worker closes the conn).
    conn.commit()

    # durable artifacts: source PDF (enables re-highlight without re-calling the LLM)
    # + rendered page images (survive restarts; power click-to-source highlights)
    if store_artifacts:
        store.put(storage.pdf_key(doc_id), pdf_bytes, "application/pdf")
    # + the model's verbatim response: everything downstream is derived from it, and a
    # coder debugging "why is this value / evidence missing" needs the original, not the
    # reconstruction from stored rows
    store.put(storage.raw_key(doc_id), (llm.text or "").encode("utf-8"), "text/plain; charset=utf-8")
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

    return {"document_id": doc_id, "paper_id": str(paper_id), "schema_id": schema_id,
            "n_records": len(res.records), "n_pages": len(page_images),
            "n_highlights": len(highlights), "scanned_pages": scanned,
            "page_image_keys": page_keys, "doi": res.doi,
            "n_issues": len(issues) if issues else 0}


def run(conn, pdf_bytes: bytes, prompt: str, *, pipeline: list[dict] | None = None, generate=None, progress=None, **kw) -> dict:
    """Extract one paper the way its workflow step says: a plain single call (``run_extraction``,
    byte for byte what it always was), or — when the step runs its preset k times and/or a later
    step reads those runs — ``chain.run_pipeline``. ``pipeline`` lists the stages
    (``workflows.pipeline_for``); ``generate`` and ``progress`` are the pipeline's hooks."""
    if not pipeline:
        return run_extraction(conn, pdf_bytes, prompt, **kw)
    from . import chain
    return chain.run_pipeline(conn, pdf_bytes, stages=pipeline, generate=generate, progress=progress, **kw)


def with_feedback(prompt: str, findings: list[dict]) -> str:
    """The prompt of a loop's second attempt: the same task, plus what the workflow's
    deterministic check found in the earlier output of this paper, to be fixed this time."""
    if not findings:
        return prompt
    lines = ["# PREVIOUS ATTEMPT (generated)", "",
             ("An earlier extraction of this paper was checked deterministically and these problems were found. "
              "Read the paper again and return a corrected extraction; do not repeat them:"), ""]
    for f in findings[:60]:
        where = f.get("path") or ""
        lines.append(f"- {where + ': ' if where else ''}{f.get('message') or f.get('code')}" + (f" [{f['code']}]" if f.get("code") else ""))
    if len(findings) > 60:
        lines.append(f"- … and {len(findings) - 60} more")
    return prompt.rstrip() + "\n\n---\n\n" + "\n".join(lines) + "\n"
