"""Postgres persistence for the record spine (psycopg3).

``persist(conn, res)`` writes an ``IngestResult`` (paper + document + records +
evidence + confidence) and returns the document id. ``load(conn, doc_id)`` reads
it back into an ``IngestResult`` so ``reconstruct_publishable(load(...))`` equals
the original publishable form — the DB-level round-trip proof.

Hand-written SQL in the thin-function style of the archive's db.py. UUIDs are
minted here (uuid4); no DB extension required.
"""
from __future__ import annotations

import math
import os
import re
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Json

from .ingest import EvidenceSpan, FieldConfidence, IngestResult, Record

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def dsn() -> str:
    return os.environ.get("PAPERLENS_DATABASE_URL", "dbname=paperlens")


def connect() -> psycopg.Connection:
    # Autocommit + explicit `with conn.transaction()` for writes. This avoids the
    # footgun where an earlier SELECT (e.g. the principal dependency resolving a
    # session) opens a read transaction, causing a later `with conn.transaction()`
    # to nest as a savepoint that's rolled back when the connection closes. In
    # autocommit mode, reads commit individually and transaction() issues a real
    # BEGIN/COMMIT around each write block.
    return psycopg.connect(dsn(), autocommit=True)


def init_db(conn: psycopg.Connection) -> None:
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        conn.execute(fh.read())
    conn.commit()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── write ─────────────────────────────────────────────────────────────────────

def upsert_paper(conn: psycopg.Connection, res: IngestResult) -> str:
    """Insert or DOI-dedupe the universal paper record. Returns paper id."""
    t = res.paper_typed
    doi = t.get("doi")
    raw = res.paper_metadata_raw
    if doi:
        row = conn.execute(
            """
            INSERT INTO paper (id, doi, title, authors, year, journal, raw_metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (doi) DO UPDATE SET
                title        = COALESCE(paper.title, EXCLUDED.title),
                authors      = COALESCE(paper.authors, EXCLUDED.authors),
                year         = COALESCE(paper.year, EXCLUDED.year),
                journal      = COALESCE(paper.journal, EXCLUDED.journal),
                raw_metadata = COALESCE(paper.raw_metadata, EXCLUDED.raw_metadata)
            RETURNING id
            """,
            (_new_id(), doi, t.get("title"),
             Json(t["authors"]) if t.get("authors") is not None else None,
             t.get("year"), t.get("journal"),
             Json(raw) if raw is not None else None),
        ).fetchone()
        return str(row[0])
    # No DOI -> cannot dedupe yet (fuzzy-match deferred); always a fresh paper.
    pid = _new_id()
    conn.execute(
        """INSERT INTO paper (id, doi, title, authors, year, journal, raw_metadata)
           VALUES (%s, NULL, %s, %s, %s, %s, %s)""",
        (pid, t.get("title"),
         Json(t["authors"]) if t.get("authors") is not None else None,
         t.get("year"), t.get("journal"),
         Json(raw) if raw is not None else None),
    )
    return pid


def upsert_schema(conn: psycopg.Connection, schema_id: str,
                  field_defs: dict | None = None) -> None:
    """Ensure a schema row exists (plan §3: presets emit schema rows on ingest).

    Resolves ``field_defs`` from the preset's view-grammar via
    presets.emit_schema_row() when not supplied. Schemas are immutable once
    referenced, so ON CONFLICT never overwrites an existing row's field_defs;
    a preset change mints a new "<preset>@<version>" id instead.
    """
    from . import presets
    preset_id, _, schema_version = schema_id.partition("@")
    if field_defs is None:
        # pass conn so a DB-backed personal preset resolves its grammar here too
        field_defs = presets.emit_schema_row(preset_id, conn=conn)
    source = "preset" if field_defs is not None else "auto"
    # File presets are immutable (frozen grammar protects published data's provenance).
    # Personal (DB) presets are still being developed, so refresh their grammar on each
    # ingest — edits to the preset's sub_views take effect on the next extraction.
    on_conflict = ("DO UPDATE SET field_defs = EXCLUDED.field_defs"
                   if preset_id and get_personal_preset(conn, preset_id) is not None
                   else "DO NOTHING")
    conn.execute(
        f"""INSERT INTO schema (id, preset_id, schema_version, field_defs, source)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) {on_conflict}""",
        (schema_id, preset_id or None, schema_version or None,
         Json(field_defs) if field_defs is not None else None, source),
    )


def persist(conn: psycopg.Connection, res: IngestResult, *,
            schema_id: str | None = None, source_job_id: str | None = None,
            session_id: str | None = None, owner_user_id: str | None = None,
            extraction: dict | None = None, filename: str | None = None,
            pdf_sha256: str | None = None) -> str:
    """Persist a full ingest result in one transaction. Returns document id."""
    with conn.transaction():
        if schema_id:
            upsert_schema(conn, schema_id)
        paper_id = upsert_paper(conn, res)

        doc_id = _new_id()
        conn.execute(
            """INSERT INTO extraction_document
                 (id, paper_id, schema_id, source_job_id, core_key, core_shape,
                  had_top_evidence, top_extras, paper_metadata_raw, owner_user_id,
                  session_id, filename, pdf_sha256)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, %s, %s, %s)""",
            (doc_id, paper_id, schema_id, source_job_id, res.core_key, res.core_shape,
             res.had_top_evidence, Json(res.top_extras),
             Json(res.paper_metadata_raw) if res.paper_metadata_raw is not None else None,
             owner_user_id, session_id, filename, pdf_sha256),
        )

        # records, keeping entry_index -> record_id
        rec_ids: dict[int, str] = {}
        for r in res.records:
            rid = _new_id()
            rec_ids[r.entry_index] = rid
            conn.execute(
                """INSERT INTO record
                     (id, document_id, paper_id, schema_id, entry_index, field_values,
                      extraction, session_id, owner_user_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::uuid)""",
                (rid, doc_id, paper_id, schema_id, r.entry_index, Json(r.field_values),
                 Json(extraction) if extraction is not None else None, session_id, owner_user_id),
            )

        for s in res.evidence:
            rid = rec_ids.get(s.entry_index) if s.entry_index is not None else None
            conn.execute(
                """INSERT INTO evidence_span
                     (id, document_id, record_id, ord, placement, entry_index,
                      field_path, snippet, page, source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (_new_id(), doc_id, rid, s.ord, s.placement, s.entry_index,
                 s.field_path, s.snippet, s.page, s.source),
            )

        for c in res.confidence:
            conn.execute(
                """INSERT INTO field_confidence (id, document_id, record_id, block, level, notes)
                   VALUES (%s, %s, NULL, %s, %s, %s)""",
                (_new_id(), doc_id, c.block, c.level, c.notes),
            )

    return doc_id


# ── read ──────────────────────────────────────────────────────────────────────

def load(conn: psycopg.Connection, doc_id: str) -> IngestResult:
    """Reload a persisted document into an IngestResult (for round-trip)."""
    doc = conn.execute(
        """SELECT core_key, core_shape, had_top_evidence, top_extras, paper_metadata_raw
           FROM extraction_document WHERE id = %s""",
        (doc_id,),
    ).fetchone()
    if doc is None:
        raise KeyError(doc_id)
    core_key, core_shape, had_top_evidence, top_extras, paper_metadata_raw = doc

    rec_rows = conn.execute(
        "SELECT entry_index, field_values FROM record "
        "WHERE document_id = %s AND NOT screened_empty ORDER BY entry_index",
        (doc_id,),
    ).fetchall()
    records = [Record(entry_index=ei, field_values=fv) for ei, fv in rec_rows]

    ev_rows = conn.execute(
        """SELECT ord, placement, entry_index, field_path, snippet, page, source
           FROM evidence_span WHERE document_id = %s ORDER BY placement, ord""",
        (doc_id,),
    ).fetchall()
    evidence = [
        EvidenceSpan(ord=o, placement=pl, entry_index=ei, field_path=fp,
                     snippet=sn, page=pg, source=src)
        for (o, pl, ei, fp, sn, pg, src) in ev_rows
    ]

    cf_rows = conn.execute(
        "SELECT block, level, notes FROM field_confidence WHERE document_id = %s",
        (doc_id,),
    ).fetchall()
    confidence = [FieldConfidence(block=b, level=lv, notes=nt) for (b, lv, nt) in cf_rows]

    return IngestResult(
        core_key=core_key,
        core_shape=core_shape,
        records=records,
        evidence=evidence,
        confidence=confidence,
        paper_metadata_raw=paper_metadata_raw,
        paper_typed=_typed_from_raw(paper_metadata_raw),
        top_extras=top_extras or {},
        schema_version=(top_extras or {}).get("schema_version"),
        had_top_evidence=had_top_evidence,
    )


def _typed_from_raw(raw: Any) -> dict[str, Any]:
    # load() doesn't need a typed projection for reconstruct; keep it minimal.
    return {} if not isinstance(raw, dict) else {
        k: raw[k] for k in ("title", "doi", "year", "journal") if raw.get(k) is not None
    }


# ── enrichment writers (plan §3.5) ────────────────────────────────────────────

_JSONB_COLS = frozenset({"authors", "openalex_topics", "mesh", "sdg",
                         "supplementary_links", "code_links", "data_links", "funders"})
_DATE_COLS = frozenset({"publication_date"})
_SCALAR_COLS = frozenset({"title", "journal", "publisher", "work_type", "license",
                          "is_oa", "oa_status", "oa_pdf_url", "primary_topic", "openalex_id",
                          "year", "issn", "author_keywords", "jel_codes", "referenced_works"})
_ENRICH_COLS = _JSONB_COLS | _DATE_COLS | _SCALAR_COLS


def get_or_create_paper_by_doi(conn: psycopg.Connection, doi: str) -> str:
    row = conn.execute("SELECT id FROM paper WHERE doi = %s", (doi,)).fetchone()
    if row:
        return str(row[0])
    pid = _new_id()
    conn.execute("INSERT INTO paper (id, doi) VALUES (%s, %s)", (pid, doi))
    return pid


def update_paper_enrichment(conn: psycopg.Connection, paper_id: str,
                            fields: dict[str, Any],
                            provenance: list[tuple]) -> None:
    """Write enriched columns + one paper_field_provenance row per field."""
    with conn.transaction():
        sets, params = [], []
        for col, val in fields.items():
            if col not in _ENRICH_COLS:
                continue
            if col in _JSONB_COLS:
                sets.append(f"{col} = %s")
                params.append(Json(val))
            elif col in _DATE_COLS:
                sets.append(f"{col} = %s::date")
                params.append(val)
            else:
                sets.append(f"{col} = %s")
                params.append(val)
        sets.append("last_enriched_at = now()")
        conn.execute(f"UPDATE paper SET {', '.join(sets)} WHERE id = %s",
                     (*params, paper_id))
        for (field, source, method, confidence) in provenance:
            conn.execute(
                """INSERT INTO paper_field_provenance
                     (id, paper_id, field, source, method, confidence)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (_new_id(), paper_id, field, source, method, confidence),
            )


def attach_rects(conn: psycopg.Connection, document_id: str, highlights: list[dict]) -> int:
    """Set evidence_span.rect from computed highlights, matched by (page, snippet).

    Highlights come from the same evidence the spans were built from, so a
    (document, page, snippet) match is exact. Recovered-orphan highlights with no
    matching span are simply skipped. Returns the number of spans updated.
    """
    if not highlights:
        return 0
    n = 0
    with conn.transaction():
        for h in highlights:
            cur = conn.execute(
                """UPDATE evidence_span SET rect = %s
                   WHERE document_id = %s AND page = %s AND snippet = %s""",
                (Json(h.get("rects")), document_id, h.get("page"), h.get("snippet")),
            )
            n += cur.rowcount
    return n


def get_schema(conn: psycopg.Connection, schema_id: str) -> dict | None:
    row = conn.execute(
        """SELECT id, preset_id, schema_version, field_defs, source
           FROM schema WHERE id = %s""",
        (schema_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "preset_id": row[1], "schema_version": row[2],
            "field_defs": row[3], "source": row[4]}


def paper_with_provenance(conn: psycopg.Connection, doi: str) -> dict | None:
    """A paper row + its per-field provenance — the §3.5 provenance footer."""
    from .ingest import _normalize_doi
    p = conn.execute(
        """SELECT id, doi, title, journal, year, license, is_oa, oa_status, oa_pdf_url,
                  primary_topic, openalex_topics, jel_codes, author_keywords,
                  code_links, data_links, supplementary_links, last_enriched_at
           FROM paper WHERE doi = %s""",
        (_normalize_doi(doi),),
    ).fetchone()
    if p is None:
        return None
    cols = ["id", "doi", "title", "journal", "year", "license", "is_oa", "oa_status",
            "oa_pdf_url", "primary_topic", "openalex_topics", "jel_codes",
            "author_keywords", "code_links", "data_links", "supplementary_links",
            "last_enriched_at"]
    paper = {c: (str(v) if c == "id" else v) for c, v in zip(cols, p)}
    prov = conn.execute(
        "SELECT field, source, method, confidence FROM paper_field_provenance WHERE paper_id = %s",
        (p[0],),
    ).fetchall()
    paper["provenance"] = [
        {"field": f, "source": s, "method": me, "confidence": c} for (f, s, me, c) in prov
    ]
    return paper


# ── coverage / passport (plan §3.5) ───────────────────────────────────────────

def paper_coverage(conn: psycopg.Connection, doi: str) -> dict | None:
    """Search a paper by DOI -> universal metadata + how it has been processed."""
    from .ingest import _normalize_doi
    norm = _normalize_doi(doi)
    paper = conn.execute(
        """SELECT id, doi, title, year, journal, authors
           FROM paper WHERE doi = %s""",
        (norm,),
    ).fetchone()
    if paper is None:
        return None
    pid, pdoi, title, year, journal, authors = paper
    coverage = conn.execute(
        """SELECT schema_id, dataset_id, count(*) AS n_records,
                  count(*) FILTER (WHERE verification_status = 'verified') AS n_verified
           FROM record WHERE paper_id = %s
           GROUP BY schema_id, dataset_id
           ORDER BY schema_id""",
        (pid,),
    ).fetchall()
    return {
        "paper": {"id": str(pid), "doi": pdoi, "title": title, "year": year,
                  "journal": journal, "authors": authors},
        "coverage": [
            {"schema_id": sid, "dataset_id": str(did) if did else None,
             "records": n, "verified": nv}
            for (sid, did, n, nv) in coverage
        ],
    }


def list_documents(conn: psycopg.Connection, limit: int = 50, *,
                   owner_user_id: str | None = None, session_id: str | None = None,
                   dataset_id: str | None = None) -> list[dict]:
    """Recent extraction documents with their paper title + record count (viewer index).

    Scoped to the principal: a document belongs to whoever owns the DOCUMENT or any
    of its records (the logged-in user, or the anonymous session that extracted it).
    Matching on the document's own owner — not only its records' — means a successful
    extraction that yielded ZERO records is still visible to (and deletable by) its
    owner, instead of becoming an invisible orphan. With no principal given (both
    None) it returns all documents — back-compat for tests/admin. ``dataset_id``
    further restricts to documents with records in that project."""
    rows = conn.execute(
        """SELECT d.id, d.paper_id, d.schema_id, d.created_at, d.filename,
                  p.title, p.doi,
                  count(r.id) FILTER (WHERE NOT COALESCE(r.screened_empty, false)) AS n_records,
                  count(*) FILTER (WHERE r.verification_status = 'verified'
                                     AND NOT COALESCE(r.screened_empty, false)) AS n_verified,
                  bool_or(COALESCE(r.screened_empty, false)) AS screened
           FROM extraction_document d
           LEFT JOIN paper p ON p.id = d.paper_id
           LEFT JOIN record r ON r.document_id = d.id
           GROUP BY d.id, p.title, p.doi
           HAVING ((%s::text IS NULL AND %s::text IS NULL)
                   OR (%s::text IS NOT NULL
                       AND (d.owner_user_id::text = %s OR bool_or(r.owner_user_id::text = %s)))
                   OR (%s::text IS NOT NULL
                       AND (d.session_id = %s OR bool_or(r.session_id = %s))))
              AND (%s::text IS NULL OR bool_or(r.dataset_id::text = %s::text))
           ORDER BY d.created_at DESC
           LIMIT %s""",
        (owner_user_id, session_id,
         owner_user_id, owner_user_id, owner_user_id,
         session_id, session_id, session_id,
         dataset_id, dataset_id, limit),
    ).fetchall()
    return [
        {"document_id": str(did), "paper_id": str(pid) if pid else None,
         "schema_id": sid, "created_at": ca.isoformat() if ca else None,
         "filename": fn, "title": title, "doi": doi,
         "n_records": n, "n_verified": nv, "screened": bool(sc)}
        for (did, pid, sid, ca, fn, title, doi, n, nv, sc) in rows
    ]


def list_papers(conn: psycopg.Connection, *, owner_user_id: str | None = None,
                session_id: str | None = None, limit: int = 200) -> list[dict]:
    """The principal's distinct cached PDFs — one row per content hash — regardless of
    dataset membership: the "All my papers" library. A single PDF may have several
    extractions (one per preset/run) and belong to zero or more LIVE datasets; deleting a
    dataset detaches the paper but keeps it here (still re-extractable). ``document_id`` is
    a representative surviving extraction (newest) used to fetch the cached PDF and to
    re-extract without re-upload. Scoped to whoever owns any of the paper's documents or
    records (logged-in user or anonymous session)."""
    if owner_user_id is None and session_id is None:
        return []
    rows = conn.execute(
        """SELECT d.pdf_sha256,
                  (array_agg(p.title    ORDER BY d.created_at DESC) FILTER (WHERE p.title IS NOT NULL))[1]    AS title,
                  (array_agg(d.filename ORDER BY d.created_at DESC) FILTER (WHERE d.filename IS NOT NULL))[1] AS filename,
                  (array_agg(d.id::text ORDER BY d.created_at DESC))[1] AS document_id,
                  max(pd.n_pages)  AS n_pages,
                  min(d.created_at) AS created_at,
                  count(DISTINCT d.id) FILTER (WHERE r.id IS NOT NULL
                      AND NOT COALESCE(r.screened_empty, false)) AS n_extractions,
                  count(DISTINCT r.id) FILTER (WHERE NOT COALESCE(r.screened_empty, false)) AS n_records,
                  jsonb_agg(DISTINCT jsonb_build_object('id', ds.id::text, 'title', ds.title))
                      FILTER (WHERE ds.id IS NOT NULL) AS datasets
           FROM extraction_document d
           LEFT JOIN paper           p  ON p.id = d.paper_id
           LEFT JOIN parsed_document pd ON pd.pdf_sha256 = d.pdf_sha256
           LEFT JOIN record          r  ON r.document_id = d.id
           LEFT JOIN dataset         ds ON ds.id = r.dataset_id
           WHERE d.pdf_sha256 IS NOT NULL
           GROUP BY d.pdf_sha256
           HAVING (%s::text IS NOT NULL AND bool_or(d.owner_user_id::text = %s OR r.owner_user_id::text = %s))
               OR (%s::text IS NOT NULL AND bool_or(d.session_id = %s OR r.session_id = %s))
           ORDER BY max(d.created_at) DESC
           LIMIT %s""",
        (owner_user_id, owner_user_id, owner_user_id,
         session_id, session_id, session_id, limit),
    ).fetchall()
    return [
        {"pdf_sha256": sha, "title": title, "filename": fn, "document_id": did,
         "n_pages": npg, "created_at": ca.isoformat() if ca else None,
         "n_extractions": nx, "n_records": nrec, "datasets": dss or []}
        for (sha, title, fn, did, npg, ca, nx, nrec, dss) in rows
    ]


def delete_paper(conn: psycopg.Connection, pdf_sha256: str, *,
                 owner_user_id: str | None = None, session_id: str | None = None) -> int:
    """Remove a cached PDF from the library entirely: hard-delete every extraction_document
    the principal owns that shares this content hash (each cascades its records/evidence and
    deletes its stored PDF + page images via ``delete_document``). Returns the number of
    extractions removed. Owner-scoped so a shared content hash never lets one principal
    delete another's extraction."""
    if owner_user_id is None and session_id is None:
        return 0
    rows = conn.execute(
        """SELECT DISTINCT d.id::text FROM extraction_document d
           LEFT JOIN record r ON r.document_id = d.id
           WHERE d.pdf_sha256 = %s
           GROUP BY d.id
           HAVING (%s::text IS NOT NULL AND bool_or(d.owner_user_id::text = %s OR r.owner_user_id::text = %s))
               OR (%s::text IS NOT NULL AND bool_or(d.session_id = %s OR r.session_id = %s))""",
        (pdf_sha256, owner_user_id, owner_user_id, owner_user_id,
         session_id, session_id, session_id),
    ).fetchall()
    n = 0
    for (did,) in rows:
        n += delete_document(conn, did).get("deleted", 0)
    return n


def document_view(conn: psycopg.Connection, document_id: str) -> dict | None:
    """Everything the viewer needs for one document: paper metadata, the resolved
    schema grammar, page-image urls, records, and evidence spans (with rects)."""
    from . import storage
    doc = conn.execute(
        "SELECT paper_id, schema_id, filename, paper_metadata_raw "
        "FROM extraction_document WHERE id = %s",
        (document_id,),
    ).fetchone()
    if doc is None:
        return None
    paper_id, schema_id, filename, paper_metadata_raw = doc

    paper = None
    if paper_id:
        prow = conn.execute(
            "SELECT title, doi, year, journal, authors FROM paper WHERE id = %s",
            (paper_id,),
        ).fetchone()
        if prow:
            paper = {"title": prow[0], "doi": prow[1], "year": prow[2],
                     "journal": prow[3], "authors": prow[4]}

    field_defs = None
    if schema_id:
        srow = conn.execute("SELECT field_defs FROM schema WHERE id = %s", (schema_id,)).fetchone()
        field_defs = srow[0] if srow else None

    rec_rows = conn.execute(
        """SELECT id, entry_index, field_values, verification_status, extraction, screened_empty
           FROM record WHERE document_id = %s ORDER BY entry_index""",
        (document_id,),
    ).fetchall()
    # Per-record human corrections (value-changing verification diffs) for provenance in
    # the review UI + the JSON/CSV export — original→final, who, when.
    corrections_by_rec: dict[str, list[dict]] = {}
    rec_ids = [str(rid) for (rid, *_r) in rec_rows]
    if rec_ids:
        for (rid, diff, email, kind, created) in conn.execute(
            """SELECT e.record_id, e.diff, u.email, e.verifier_kind, e.created_at
               FROM verification_event e LEFT JOIN users u ON u.id = e.verifier_user_id
               WHERE e.record_id = ANY(%s::uuid[]) AND e.diff IS NOT NULL
               ORDER BY e.created_at""",
            (rec_ids,),
        ).fetchall():
            for d in (diff if isinstance(diff, list) else []):
                if not isinstance(d, dict) or d.get("original_value") == d.get("final_value"):
                    continue
                corrections_by_rec.setdefault(str(rid), []).append({
                    "field_path": d.get("field_path"),
                    "original_value": d.get("original_value"),
                    "final_value": d.get("final_value"),
                    "editor": email or kind, "at": created.isoformat() if created else None,
                })
    records_out = [
        {"id": str(rid), "entry_index": ei, "field_values": fv,
         "verification_status": vs, "extraction": _ex, "screened_empty": bool(_se),
         "corrections": corrections_by_rec.get(str(rid), [])}
        for (rid, ei, fv, vs, _ex, _se) in rec_rows
    ]
    n_pages = 0
    for row in rec_rows:
        ex = row[4]                                   # extraction column (not the trailing flag)
        if isinstance(ex, dict) and ex.get("n_pages"):
            n_pages = int(ex["n_pages"]); break

    ev_rows = conn.execute(
        """SELECT record_id, entry_index, page, field_path, snippet, source, rect
           FROM evidence_span WHERE document_id = %s ORDER BY page, ord""",
        (document_id,),
    ).fetchall()
    evidence_out = [
        {"record_id": str(rid) if rid else None, "entry_index": ei, "page": pg,
         "field_path": fp, "snippet": sn, "source": src, "rect": rect}
        for (rid, ei, pg, fp, sn, src, rect) in ev_rows
    ]
    if not n_pages and evidence_out:
        n_pages = max((e["page"] or 0) for e in evidence_out)

    store = storage.get_store()
    pages = [{"page": i, "url": store.url(storage.page_image_key(document_id, i))}
             for i in range(1, n_pages + 1)]

    return {"document_id": document_id, "paper_id": str(paper_id) if paper_id else None,
            "schema_id": schema_id, "filename": filename, "paper": paper,
            "paper_metadata": paper_metadata_raw if isinstance(paper_metadata_raw, dict) else None,
            "field_defs": field_defs, "pages": pages, "records": records_out,
            "evidence": evidence_out}


# ── verification / credibility (Phase 3) ──────────────────────────────────────

_VALID_STATUS = ("verified", "flagged", "unverified")


def get_record(conn: psycopg.Connection, record_id: str) -> dict | None:
    r = conn.execute(
        """SELECT id, document_id, paper_id, dataset_id, entry_index, schema_id,
                  field_values, verification_status FROM record WHERE id = %s::uuid""",
        (record_id,),
    ).fetchone()
    if r is None:
        return None
    return {"id": str(r[0]), "document_id": str(r[1]) if r[1] else None,
            "paper_id": str(r[2]) if r[2] else None,
            "dataset_id": str(r[3]) if r[3] else None, "entry_index": r[4],
            "schema_id": r[5], "field_values": r[6], "verification_status": r[7]}


def verify_record(conn: psycopg.Connection, record_id: str, *, status: str,
                  diff: list | None = None, notes: str | None = None,
                  verifier_user_id: str | None = None, verifier_kind: str = "maintainer",
                  field_values: dict | None = None) -> dict:
    """Record a verification event + project it onto record.verification_status.

    Optionally apply a correction (full ``field_values`` replacement) — the
    "editing values routes through the verification layer" rule. One transaction.
    """
    if status not in _VALID_STATUS:
        raise ValueError(f"status must be one of {_VALID_STATUS}")
    eid = _new_id()
    with conn.transaction():
        conn.execute(
            """INSERT INTO verification_event
                 (id, record_id, verifier_user_id, verifier_kind, status, diff, notes)
               VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s)""",
            (eid, record_id, verifier_user_id, verifier_kind, status,
             Json(diff) if diff is not None else None, notes),
        )
        conn.execute("UPDATE record SET verification_status = %s WHERE id = %s::uuid",
                     (status, record_id))
        if field_values is not None:
            conn.execute("UPDATE record SET field_values = %s WHERE id = %s::uuid",
                         (Json(field_values), record_id))
    return {"id": eid, "record_id": record_id, "status": status,
            "verifier_kind": verifier_kind}


def record_events(conn: psycopg.Connection, record_id: str) -> list[dict]:
    """The record's change history (verification + corrections), newest first,
    with the editor's email when it was a logged-in user."""
    rows = conn.execute(
        """SELECT e.id, e.verifier_user_id, u.email, e.verifier_kind, e.status,
                  e.diff, e.notes, e.created_at
           FROM verification_event e
           LEFT JOIN users u ON u.id = e.verifier_user_id
           WHERE e.record_id = %s::uuid ORDER BY e.created_at DESC""",
        (record_id,),
    ).fetchall()
    return [
        {"id": str(i), "verifier_user_id": str(u) if u else None, "verifier_email": em,
         "verifier_kind": k, "status": s, "diff": d, "notes": n,
         "created_at": c.isoformat() if c else None}
        for (i, u, em, k, s, d, n, c) in rows
    ]


def _diff_changes_value(diff: Any) -> bool:
    if not isinstance(diff, list):
        return False
    return any(isinstance(e, dict) and e.get("original_value") != e.get("final_value")
               for e in diff)


def _wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """Wilson score interval for a proportion k/n (agreement rate CI)."""
    if n == 0:
        return None
    phat = k / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n) / denom
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


def dataset_credibility(conn: psycopg.Connection, dataset_id: str) -> dict:
    """COMPUTED dataset badge (never hand-assigned): AI-only -> sample-verified
    (X% audited · Y% agree, Wilson CI) -> human-verified."""
    total = conn.execute(
        "SELECT count(*) FROM record WHERE dataset_id = %s::uuid AND NOT screened_empty",
        (dataset_id,),
    ).fetchone()[0]
    rows = conn.execute(
        """SELECT r.id::text, ve.status, ve.diff
           FROM record r JOIN verification_event ve ON ve.record_id = r.id
           WHERE r.dataset_id = %s::uuid""",
        (dataset_id,),
    ).fetchall()

    by_record: dict[str, list] = {}
    for rid, status, diff in rows:
        by_record.setdefault(rid, []).append((status, diff))
    audited = len(by_record)

    agreed = 0
    for evs in by_record.values():
        disagreed = any(s == "flagged" for s, _ in evs) or \
            any(_diff_changes_value(d) for _, d in evs)
        if not disagreed:
            agreed += 1

    return {"dataset_id": dataset_id, **_badge_from_counts(total, audited, agreed)}


def _badge_from_counts(total: int, audited: int, agreed: int) -> dict:
    """The single source of badge truth — used by the per-dataset credibility call
    AND the batched public-datasets query, so the two can never drift."""
    if audited == 0:
        tier = "ai_only"
    elif total > 0 and audited >= total:
        tier = "human_verified"
    else:
        tier = "sample_verified"
    return {
        "tier": tier, "n_records": total, "audited": audited,
        "audited_pct": round(100 * audited / total, 1) if total else 0.0,
        "agreement": round(agreed / audited, 4) if audited else None,
        "agreement_ci": _wilson(agreed, audited),
        "label": _badge_label(tier, total, audited, agreed),
    }


def _badge_label(tier: str, total: int, audited: int, agreed: int) -> str:
    if tier == "ai_only":
        return "AI-only"
    pct = round(100 * audited / total) if total else 0
    agree = round(100 * agreed / audited) if audited else 0
    if tier == "human_verified":
        return f"Human-verified · {agree}% agree"
    return f"{pct}% audited · {agree}% agree"


# ── views (Phase 4: the observatory is a saved view over records) ─────────────

def create_view(conn: psycopg.Connection, *, title: str, view_type: str = "aggregate",
                dataset_ids: list | None = None, query: dict | None = None,
                viz_config: dict | None = None, owner_user_id: str | None = None,
                session_id: str | None = None, visibility: str = "public",
                theme_id: str | None = None) -> dict:
    vid = _new_id()
    with conn.transaction():
        conn.execute(
            """INSERT INTO saved_view
                 (id, owner_user_id, session_id, title, view_type, dataset_ids,
                  query, viz_config, theme_id, visibility)
               VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s::uuid, %s)""",
            (vid, owner_user_id, session_id, title, view_type,
             Json(dataset_ids or []), Json(query or {}), Json(viz_config or {}),
             theme_id, visibility),
        )
    return get_view(conn, vid)


def get_view(conn: psycopg.Connection, view_id: str) -> dict | None:
    r = conn.execute(
        """SELECT id, owner_user_id, session_id, title, view_type, dataset_ids,
                  query, viz_config, theme_id, visibility, created_at
           FROM saved_view WHERE id = %s::uuid""",
        (view_id,),
    ).fetchone()
    if r is None:
        return None
    return {"id": str(r[0]), "owner_user_id": str(r[1]) if r[1] else None,
            "session_id": r[2], "title": r[3], "view_type": r[4],
            "dataset_ids": r[5] or [], "query": r[6] or {}, "viz_config": r[7] or {},
            "theme_id": str(r[8]) if r[8] else None, "visibility": r[9],
            "created_at": r[10].isoformat() if r[10] else None}


def list_views(conn: psycopg.Connection, *, owner_user_id: str | None = None,
               session_id: str | None = None, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """SELECT id, title, view_type, dataset_ids, visibility, created_at, owner_user_id
           FROM saved_view
           WHERE visibility = 'public'
              OR (%s::text IS NOT NULL AND owner_user_id::text = %s::text)
              OR (%s::text IS NOT NULL AND session_id = %s::text)
           ORDER BY created_at DESC LIMIT %s""",
        (owner_user_id, owner_user_id, session_id, session_id, limit),
    ).fetchall()
    return [{"id": str(i), "title": t, "view_type": vt, "dataset_ids": d or [],
             "visibility": v, "created_at": c.isoformat() if c else None,
             "owner_user_id": str(o) if o else None}
            for (i, t, vt, d, v, c, o) in rows]


def _path(obj: Any, path: str) -> Any:
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def aggregate(conn: psycopg.Connection, *, filters: dict, group_by: str | None,
              measure: str = "count", value_field: str | None = None,
              public_only: bool | None = True) -> dict:
    """Group the records matching ``filters`` by ``group_by`` into a count/mean —
    the OWID-style cross-dataset aggregation. ``group_by`` is either a paper column
    (``paper.year`` / ``paper.primary_topic``) grouped in SQL, or a dotted path into
    ``field_values`` bucketed in Python. Reuses the shared _record_query builder.
    The ad-hoc catalogue endpoint keeps ``public_only=True``; ``run_view`` passes
    ``None`` (the view's dataset_ids are its scope, gated at the view level)."""
    where, params = _record_query(filters, public_only=public_only)
    rows = conn.execute(
        f"""SELECT r.field_values, p.year, p.primary_topic
            FROM record r JOIN paper p ON p.id = r.paper_id
            LEFT JOIN dataset d ON d.id = r.dataset_id WHERE {where}""",
        params,
    ).fetchall()
    total = len(rows)
    base = {"group_by": group_by, "measure": measure, "value_field": value_field,
            "total_records": total}
    if not group_by:
        return {**base, "series": []}

    buckets: dict[str, dict] = {}
    for fv, year, primary_topic in rows:
        if group_by == "paper.year":
            key = year
        elif group_by == "paper.primary_topic":
            key = primary_topic
        else:
            key = _path(fv, group_by)
        key = "(none)" if key is None else str(key)
        b = buckets.setdefault(key, {"group": key, "n": 0, "sum": 0.0, "num": 0})
        b["n"] += 1
        if measure == "mean" and value_field:
            val = _num(_path(fv, value_field))
            if val is not None:
                b["sum"] += val
                b["num"] += 1

    series = []
    for b in buckets.values():
        value = (round(b["sum"] / b["num"], 4) if b["num"] else None) if measure == "mean" else b["n"]
        series.append({"group": b["group"], "value": value, "n": b["n"]})
    if measure == "mean":
        series.sort(key=lambda s: s["group"])
    else:
        series.sort(key=lambda s: -(s["value"] or 0))
    return {**base, "series": series}


def run_view(conn: psycopg.Connection, view_id: str) -> dict | None:
    """Execute a saved view against the CURRENT records (recomputes on every call).
    The observatory is now one saved instance of ``aggregate``."""
    v = get_view(conn, view_id)
    if v is None:
        return None
    q, cfg = v["query"], v["viz_config"]
    filters = {"dataset": v["dataset_ids"] or None,
               "schema": q.get("schema_id"),
               "status": q.get("verification_status")}
    agg = aggregate(conn, filters=filters, group_by=cfg.get("group_by"),
                    measure=cfg.get("measure", "count"), value_field=cfg.get("value_field"),
                    public_only=None)   # a view's dataset_ids ARE its scope; access gated at the view level
    return {"view_id": v["id"], "title": v["title"], "kind": cfg.get("kind", "bar"), **agg}


# ── catalog query layer (cross-dataset search / facets / paper search) ─────────

def _visibility_clause(principal, *, public_only: bool) -> tuple[str, list]:
    """The authorization predicate ANDed into every record query. Assumes the
    query LEFT JOINs ``dataset AS d``. A null principal (or public_only) sees only
    records in a PUBLIC dataset; otherwise the principal's own records are added —
    the same NULL-guard idiom proven in ``list_documents``."""
    if public_only or principal is None:
        return "(d.visibility = 'public')", []
    uid, sid = principal.user_id, principal.session_id
    return (
        "(d.visibility = 'public'"
        " OR (%s::text IS NOT NULL AND r.owner_user_id::text = %s::text)"
        " OR (%s::text IS NOT NULL AND r.session_id = %s::text))",
        [uid, uid, sid, sid],
    )


def _owns(principal, owner_user_id, session_id) -> bool:
    """Does the principal own a row with these owner/session columns?"""
    if principal is None:
        return False
    return ((principal.user_id is not None and owner_user_id == principal.user_id)
            or (principal.session_id is not None and session_id == principal.session_id))


def is_document_owner(conn: psycopg.Connection, document_id: str, principal) -> bool:
    r = conn.execute(
        "SELECT owner_user_id::text, session_id FROM extraction_document WHERE id = %s::uuid",
        (document_id,)).fetchone()
    return r is not None and _owns(principal, r[0], r[1])


def is_dataset_owner(conn: psycopg.Connection, dataset_id: str, principal) -> bool:
    r = conn.execute(
        "SELECT owner_user_id::text, session_id FROM dataset WHERE id = %s::uuid",
        (dataset_id,)).fetchone()
    return r is not None and _owns(principal, r[0], r[1])


def is_view_owner(conn: psycopg.Connection, view_id: str, principal) -> bool:
    r = conn.execute(
        "SELECT owner_user_id::text, session_id FROM saved_view WHERE id = %s::uuid",
        (view_id,)).fetchone()
    return r is not None and _owns(principal, r[0], r[1])


def record_is_visible(conn: psycopg.Connection, record_id: str, principal) -> bool:
    """A record is visible iff it's in a public dataset OR owned by the principal."""
    r = conn.execute(
        """SELECT d.visibility, rec.owner_user_id::text, rec.session_id
           FROM record rec LEFT JOIN dataset d ON d.id = rec.dataset_id
           WHERE rec.id = %s::uuid""", (record_id,)).fetchone()
    if r is None:
        return False
    return r[0] == "public" or _owns(principal, r[1], r[2])


def records_all_owned(conn: psycopg.Connection, record_ids: list[str], principal) -> bool:
    """True iff every given record exists and is owned by the principal."""
    ids = [str(x) for x in (record_ids or [])]
    if not ids:
        return False
    rows = conn.execute(
        "SELECT owner_user_id::text, session_id FROM record WHERE id::text = ANY(%s)",
        (ids,)).fetchall()
    return len(rows) == len(ids) and all(_owns(principal, o, s) for (o, s) in rows)


def _record_query(filters: dict, principal=None, *, public_only: bool | None = True) -> tuple[str, list]:
    """Shared WHERE builder for search / facets / aggregate. Assumes the query
    aliases record as ``r``, paper as ``p``, and LEFT JOINs dataset as ``d``.
    Appends the visibility predicate (default public-only, so the catalogue never
    returns private/not-yet-donated records). ``public_only=None`` skips the
    predicate entirely — for view-scoped aggregation where the explicit
    ``dataset`` filter is the scope and access is gated at the view level.
    Returns (where_sql, params)."""
    clauses, params = [], []
    if filters.get("q"):
        clauses.append("r.field_values_tsv @@ websearch_to_tsquery('english', %s)")
        params.append(filters["q"])
    if filters.get("schema"):
        clauses.append("r.schema_id = %s")
        params.append(filters["schema"])
    if filters.get("dataset"):
        clauses.append("r.dataset_id::text = ANY(%s)")
        params.append([str(x) for x in filters["dataset"]])
    if filters.get("status"):
        clauses.append("r.verification_status = %s")
        params.append(filters["status"])
    if filters.get("year") not in (None, ""):
        clauses.append("p.year = %s")
        params.append(int(filters["year"]))
    if filters.get("jel"):
        clauses.append("p.jel_codes && %s")
        params.append([filters["jel"]])
    if filters.get("topic"):
        clauses.append("p.primary_topic = %s")
        params.append(filters["topic"])
    if public_only is not None:
        vis_sql, vis_params = _visibility_clause(principal, public_only=public_only)
        clauses.append(vis_sql)
        params.extend(vis_params)
    return (" AND ".join(clauses) or "TRUE"), params


def search_records(conn: psycopg.Connection, filters: dict,
                   limit: int = 50, offset: int = 0) -> dict:
    where, params = _record_query(filters)
    has_q = bool(filters.get("q"))
    rank = ("ts_rank(r.field_values_tsv, websearch_to_tsquery('english', %s))"
            if has_q else "0")
    order = "rank DESC, p.year DESC NULLS LAST" if has_q else "r.created_at DESC"
    sql = f"""
      SELECT r.id, r.entry_index, r.schema_id, r.verification_status, r.field_values,
             r.dataset_id, p.id, p.doi, p.title, p.year, p.journal, p.primary_topic,
             p.jel_codes, d.slug, d.title, {rank} AS rank, count(*) OVER() AS total
      FROM record r JOIN paper p ON p.id = r.paper_id
      LEFT JOIN dataset d ON d.id = r.dataset_id
      WHERE {where}
      ORDER BY {order} LIMIT %s OFFSET %s"""
    rank_params = [filters["q"]] if has_q else []
    rows = conn.execute(sql, [*rank_params, *params, limit, offset]).fetchall()
    total = rows[0][16] if rows else 0
    results = [{
        "id": str(r[0]), "entry_index": r[1], "schema_id": r[2],
        "verification_status": r[3], "field_values": r[4],
        "paper": {"id": str(r[6]) if r[6] else None, "doi": r[7], "title": r[8],
                  "year": r[9], "journal": r[10], "primary_topic": r[11], "jel_codes": r[12]},
        "dataset": ({"id": str(r[5]), "slug": r[13], "title": r[14]} if r[5] else None),
    } for r in rows]
    return {"total": total, "limit": limit, "offset": offset, "results": results}


def facets(conn: psycopg.Connection, filters: dict) -> dict:
    """Value+count drill-downs for the current query (v1: no self-exclusion —
    all active filters apply to all facets)."""
    where, params = _record_query(filters)
    base = (f"FROM record r JOIN paper p ON p.id = r.paper_id "
            f"LEFT JOIN dataset d ON d.id = r.dataset_id WHERE {where}")
    out: dict = {}
    for key, col in [("schema", "r.schema_id"),
                     ("verification_status", "r.verification_status"),
                     ("primary_topic", "p.primary_topic"), ("year", "p.year")]:
        rows = conn.execute(
            f"SELECT {col} AS v, count(*) AS c {base} GROUP BY {col} "
            f"ORDER BY c DESC NULLS LAST LIMIT 40", params).fetchall()
        out[key] = [{"value": v, "count": c} for (v, c) in rows if v is not None]
    rows = conn.execute(
        f"SELECT j AS v, count(*) AS c FROM record r JOIN paper p ON p.id = r.paper_id "
        f"LEFT JOIN dataset d ON d.id = r.dataset_id, "
        f"LATERAL unnest(p.jel_codes) j WHERE {where} GROUP BY j "
        f"ORDER BY c DESC LIMIT 40", params).fetchall()
    out["jel"] = [{"value": v, "count": c} for (v, c) in rows]
    return out


def public_datasets_with_badges(conn: psycopg.Connection,
                                limit: int = 50, offset: int = 0,
                                q: str | None = None) -> list[dict]:
    """Public datasets each with a COMPUTED credibility badge, in ONE query (no N+1).
    When ``q`` is given, full-text search the dataset's title/description/prompt/schema
    (the recipe) and rank by relevance; otherwise most-recent first."""
    q = (q or "").strip() or None
    rows = conn.execute(
        """
        WITH ev AS (
          SELECT record_id,
                 bool_or(status = 'flagged' OR EXISTS (
                   SELECT 1 FROM jsonb_array_elements(coalesce(diff, '[]'::jsonb)) e
                   WHERE e->>'original_value' IS DISTINCT FROM e->>'final_value')) AS disagreed
          FROM verification_event GROUP BY record_id
        ),
        rec AS (
          SELECT r.dataset_id,
                 count(*) AS n_records,
                 count(*) FILTER (WHERE ev.record_id IS NOT NULL) AS audited,
                 count(*) FILTER (WHERE ev.record_id IS NOT NULL AND NOT ev.disagreed) AS agreed
          FROM record r LEFT JOIN ev ON ev.record_id = r.id
          WHERE r.dataset_id IS NOT NULL
          GROUP BY r.dataset_id
        )
        SELECT d.id, d.slug, d.title, d.description, d.schema_id,
               coalesce(rec.n_records, 0), coalesce(rec.audited, 0), coalesce(rec.agreed, 0),
               u.citation_name
        FROM dataset d LEFT JOIN rec ON rec.dataset_id = d.id
                       LEFT JOIN users u ON u.id = d.owner_user_id
        WHERE d.visibility = 'public'
          AND (%s::text IS NULL OR d.search_tsv @@ websearch_to_tsquery('english', %s))
        ORDER BY
          CASE WHEN %s::text IS NULL THEN 0
               ELSE ts_rank(d.search_tsv, websearch_to_tsquery('english', %s)) END DESC,
          d.created_at DESC
        LIMIT %s OFFSET %s""",
        (q, q, q, q, limit, offset),
    ).fetchall()
    return [{
        "id": str(did), "slug": slug, "title": title, "description": desc,
        "schema_id": schema_id, "cite_as": cite,
        "credibility": _badge_from_counts(int(n), int(audited), int(agreed)),
    } for (did, slug, title, desc, schema_id, n, audited, agreed, cite) in rows]


def papers_search(conn: psycopg.Connection, *, q: str | None = None, jel: str | None = None,
                  topic: str | None = None, year=None, limit: int = 25, offset: int = 0) -> dict:
    """Fuzzy paper search (title/abstract/keywords + jel/topic/year) — the front
    door complementing exact-DOI ``paper_coverage``."""
    clauses, params = [], []
    if q:
        like = f"%{q}%"
        clauses.append("(p.title ILIKE %s OR coalesce(p.raw_metadata->>'abstract','') ILIKE %s "
                       "OR array_to_string(coalesce(p.author_keywords, '{}'), ' ') ILIKE %s)")
        params += [like, like, like]
    if jel:
        clauses.append("p.jel_codes && %s"); params.append([jel])
    if topic:
        clauses.append("p.primary_topic = %s"); params.append(topic)
    if year not in (None, ""):
        clauses.append("p.year = %s"); params.append(int(year))
    where = " AND ".join(clauses) or "TRUE"
    rows = conn.execute(
        f"""SELECT p.id, p.doi, p.title, p.year, p.journal, p.primary_topic, p.jel_codes, p.is_oa,
                   (SELECT count(*) FROM record r WHERE r.paper_id = p.id) AS n_records,
                   count(*) OVER() AS total
            FROM paper p WHERE {where}
            ORDER BY p.year DESC NULLS LAST LIMIT %s OFFSET %s""",
        [*params, limit, offset],
    ).fetchall()
    total = rows[0][9] if rows else 0
    papers = [{"id": str(r[0]), "doi": r[1], "title": r[2], "year": r[3], "journal": r[4],
               "primary_topic": r[5], "jel_codes": r[6], "is_oa": r[7], "n_records": r[8]}
              for r in rows]
    return {"total": total, "limit": limit, "offset": offset, "papers": papers}


def record_detail(conn: psycopg.Connection, record_id: str) -> dict | None:
    """Full record detail for the catalog detail panel / deep-link."""
    rec = get_record(conn, record_id)
    if rec is None:
        return None
    paper = None
    if rec["paper_id"]:
        prow = conn.execute(
            """SELECT id, doi, title, year, journal, primary_topic, jel_codes, oa_pdf_url
               FROM paper WHERE id = %s::uuid""", (rec["paper_id"],)).fetchone()
        if prow:
            paper = {"id": str(prow[0]), "doi": prow[1], "title": prow[2], "year": prow[3],
                     "journal": prow[4], "primary_topic": prow[5], "jel_codes": prow[6],
                     "oa_pdf_url": prow[7]}
            if prow[1]:
                pwp = paper_with_provenance(conn, prow[1])
                paper["provenance"] = pwp.get("provenance", []) if pwp else []
    ev = conn.execute(
        """SELECT field_path, snippet, page, source, rect
           FROM evidence_span WHERE record_id = %s::uuid ORDER BY ord""",
        (record_id,)).fetchall()
    evidence = [{"field_path": f, "snippet": s, "page": pg, "source": src, "rect": rect}
                for (f, s, pg, src, rect) in ev]
    dataset = None
    if rec["dataset_id"]:
        d = get_dataset(conn, rec["dataset_id"])
        dataset = {"id": d["id"], "slug": d["slug"], "title": d["title"]} if d else None
    return {"record": rec, "paper": paper, "evidence": evidence,
            "events": record_events(conn, record_id), "dataset": dataset}


# ── datasets (Phase 2: persistent owned collections) ──────────────────────────

def _slugify(s: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40]
    return slug or "dataset"


def create_dataset(conn: psycopg.Connection, *, title: str, description: str | None = None,
                   schema_id: str | None = None, owner_user_id: str | None = None,
                   session_id: str | None = None, visibility: str = "private",
                   prompt: str | None = None, model: str | None = None) -> dict:
    did = _new_id()
    slug = f"{_slugify(title)}-{did[:8]}"
    with conn.transaction():
        if schema_id:
            upsert_schema(conn, schema_id)     # ensure the FK target exists (avoids a 500)
        conn.execute(
            """INSERT INTO dataset
                 (id, slug, schema_id, title, description, owner_user_id, session_id,
                  visibility, prompt, model, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())""",
            (did, slug, schema_id, title, description, owner_user_id, session_id,
             visibility, prompt, model),
        )
    return {"id": did, "slug": slug, "title": title, "description": description,
            "schema_id": schema_id, "visibility": visibility,
            "owner_user_id": owner_user_id, "session_id": session_id,
            "prompt": prompt, "model": model, "n_records": 0}


def list_datasets(conn: psycopg.Connection, *, owner_user_id: str | None = None,
                  session_id: str | None = None, limit: int = 50) -> list[dict]:
    """Datasets visible to the principal: public + owned (by user, or by anon session)."""
    rows = conn.execute(
        """SELECT d.id, d.slug, d.title, d.description, d.schema_id, d.visibility,
                  d.owner_user_id, d.session_id, d.created_at, count(r.id) AS n_records
           FROM dataset d
           LEFT JOIN record r ON r.dataset_id = d.id
           WHERE d.visibility = 'public'
              OR (%s::text IS NOT NULL AND d.owner_user_id::text = %s::text)
              OR (%s::text IS NOT NULL AND d.session_id = %s::text)
           GROUP BY d.id
           ORDER BY d.created_at DESC
           LIMIT %s""",
        (owner_user_id, owner_user_id, session_id, session_id, limit),
    ).fetchall()
    return [
        {"id": str(r[0]), "slug": r[1], "title": r[2], "description": r[3],
         "schema_id": r[4], "visibility": r[5],
         "owner_user_id": str(r[6]) if r[6] else None, "session_id": r[7],
         "created_at": r[8].isoformat() if r[8] else None, "n_records": r[9]}
        for r in rows
    ]


def get_dataset(conn: psycopg.Connection, dataset_id: str) -> dict | None:
    r = conn.execute(
        """SELECT d.id, d.slug, d.title, d.description, d.schema_id, d.visibility,
                  d.owner_user_id, d.session_id, d.created_at, u.citation_name,
                  d.prompt, d.model, d.updated_at, d.git_pr_url
           FROM dataset d LEFT JOIN users u ON u.id = d.owner_user_id
           WHERE d.id = %s::uuid""",
        (dataset_id,),
    ).fetchone()
    if r is None:
        return None
    return {"id": str(r[0]), "slug": r[1], "title": r[2], "description": r[3],
            "schema_id": r[4], "visibility": r[5],
            "owner_user_id": str(r[6]) if r[6] else None, "session_id": r[7],
            "created_at": r[8].isoformat() if r[8] else None,
            "cite_as": r[9], "prompt": r[10], "model": r[11],
            "updated_at": r[12].isoformat() if r[12] else None,
            "git_pr_url": r[13]}


def dataset_overview(conn: psycopg.Connection, dataset_id: str) -> dict | None:
    """One computed payload for the dataset overview page: recipe (model/prompt/schema),
    on-the-fly stats, the credibility badge, and the papers (documents) list. Reuses
    ``dataset_credibility`` + ``list_documents`` so nothing is denormalized/stored."""
    d = get_dataset(conn, dataset_id)
    if d is None:
        return None

    row = conn.execute(
        """SELECT count(DISTINCT r.paper_id)                                     AS n_papers,
                  count(*) FILTER (WHERE NOT r.screened_empty)                   AS n_records,
                  count(*) FILTER (WHERE r.verification_status = 'verified'
                                     AND NOT r.screened_empty)                   AS n_verified,
                  count(DISTINCT r.schema_id) FILTER (WHERE NOT r.screened_empty) AS n_schemas,
                  count(*) FILTER (WHERE r.screened_empty)                       AS n_screened,
                  min(ed.created_at)                                             AS first_extracted,
                  max(ed.created_at)                                             AS last_extracted,
                  coalesce(sum(CASE
                      WHEN r.extraction -> 'usage' ->> 'total_tokens' ~ '^[0-9]+$'
                      THEN (r.extraction -> 'usage' ->> 'total_tokens')::bigint
                      ELSE 0 END), 0)                                            AS total_tokens
           FROM record r
           LEFT JOIN extraction_document ed ON ed.id = r.document_id
           WHERE r.dataset_id = %s::uuid""",
        (dataset_id,),
    ).fetchone()
    (n_papers, n_records, n_verified, n_schemas, n_screened,
     first_extracted, last_extracted, total_tokens) = row

    last_change = conn.execute(
        """SELECT max(ve.created_at)
           FROM verification_event ve JOIN record r ON r.id = ve.record_id
           WHERE r.dataset_id = %s::uuid""",
        (dataset_id,),
    ).fetchone()[0]

    cred = dataset_credibility(conn, dataset_id)
    docs = list_documents(conn, limit=500, dataset_id=dataset_id)

    return {
        **d,
        "recipe": {"model": d["model"], "prompt": d["prompt"], "schema_id": d["schema_id"]},
        "stats": {
            "n_papers": n_papers, "n_records": n_records, "n_verified": n_verified,
            "n_screened": n_screened,          # papers attempted that yielded 0 records
            "verified_pct": round(100 * n_verified / n_records, 1) if n_records else 0.0,
            "n_schemas": n_schemas, "total_tokens": int(total_tokens or 0),
            "first_extracted": first_extracted.isoformat() if first_extracted else None,
            "last_extracted": last_extracted.isoformat() if last_extracted else None,
            "last_change": last_change.isoformat() if last_change else None,
        },
        "credibility": cred,
        "documents": docs,
    }


def dataset_records(conn: psycopg.Connection, dataset_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, paper_id, entry_index, schema_id, field_values, verification_status
           FROM record WHERE dataset_id = %s::uuid AND NOT screened_empty
           ORDER BY paper_id, entry_index""",
        (dataset_id,),
    ).fetchall()
    return [
        {"id": str(rid), "paper_id": str(pid) if pid else None, "entry_index": ei,
         "schema_id": sid, "field_values": fv, "verification_status": vs}
        for (rid, pid, ei, sid, fv, vs) in rows
    ]


def dataset_rows(conn: psycopg.Connection, dataset_ids, *, principal=None,
                 public_only: bool = False, limit: int = 5000) -> list[dict]:
    """Tidy rows for D3 dashboards: one row per record across the given datasets,
    authz-scoped (a row is visible iff its dataset is public OR the principal owns
    it). ``field_values`` is kept nested; the client flattens what each figure
    needs. Paper ``year``/``primary_topic``/``title`` are surfaced as extra columns
    (the two paper-level vars aggregation already special-cases)."""
    ids = [str(x) for x in (dataset_ids or []) if x]
    if not ids:
        return []
    where, params = _record_query({"dataset": ids}, principal, public_only=public_only)
    rows = conn.execute(
        f"""SELECT r.id, r.document_id, r.paper_id, r.dataset_id, r.schema_id,
                   r.verification_status, r.field_values, p.year, p.primary_topic, p.title
            FROM record r JOIN paper p ON p.id = r.paper_id
            LEFT JOIN dataset d ON d.id = r.dataset_id
            WHERE {where} AND NOT r.screened_empty
            ORDER BY r.dataset_id, r.paper_id, r.entry_index
            LIMIT %s""",
        (*params, limit),
    ).fetchall()
    return [
        {"record_id": str(rid), "document_id": str(doc) if doc else None,
         "paper_id": str(pid) if pid else None,
         "dataset_id": str(dsid) if dsid else None, "schema_id": sid,
         "verification_status": vs, "year": yr, "primary_topic": pt,
         "paper_title": title, "field_values": fv}
        for (rid, doc, pid, dsid, sid, vs, fv, yr, pt, title) in rows
    ]


def records_provenance(conn: psycopg.Connection, record_ids, *, principal=None,
                       public_only: bool = False) -> list[dict]:
    """Compact provenance for a set of records (powers the figure's data table +
    chart tooltips): document_id, paper, verification, and the record's TOP evidence
    (page + snippet). Authz-scoped exactly like ``dataset_rows`` — records the
    principal can't see are simply omitted."""
    ids = [str(x) for x in (record_ids or []) if x]
    if not ids:
        return []
    vis_sql, vis_params = _visibility_clause(principal, public_only=public_only)
    rows = conn.execute(
        f"""SELECT r.id, r.document_id, r.verification_status, p.title, p.doi, p.year,
                   e.page, e.snippet, e.source
            FROM record r JOIN paper p ON p.id = r.paper_id
            LEFT JOIN dataset d ON d.id = r.dataset_id
            LEFT JOIN LATERAL (
                SELECT page, snippet, source FROM evidence_span ev
                WHERE ev.record_id = r.id ORDER BY ord LIMIT 1) e ON true
            WHERE r.id::text = ANY(%s) AND {vis_sql}""",
        (ids, *vis_params),
    ).fetchall()
    return [
        {"record_id": str(rid), "document_id": str(doc) if doc else None,
         "verification_status": vs, "paper_title": title, "doi": doi, "year": yr,
         "page": page, "snippet": snippet, "source": src}
        for (rid, doc, vs, title, doi, yr, page, snippet, src) in rows
    ]


def assign_records_to_dataset(conn: psycopg.Connection, dataset_id: str,
                              record_ids: list[str]) -> int:
    if not record_ids:
        return 0
    with conn.transaction():
        cur = conn.execute(
            "UPDATE record SET dataset_id = %s::uuid WHERE id::text = ANY(%s)",
            (dataset_id, list(record_ids)),
        )
    return cur.rowcount


def assign_document_to_dataset(conn: psycopg.Connection, dataset_id: str,
                               document_id: str) -> int:
    with conn.transaction():
        # Idempotency: a dataset must never hold the SAME PDF twice (a double-clicked Run,
        # a re-upload while the first extraction is still in flight, or two concurrent
        # workers can each mint a fresh document for the same content hash). Serialize
        # same-(dataset, sha) assigns with a txn advisory lock, then skip if another
        # document with this hash is already in the dataset — non-destructive: the newcomer
        # simply stays private in "All my papers" rather than duplicating the paper.
        sha_row = conn.execute(
            "SELECT pdf_sha256 FROM extraction_document WHERE id = %s::uuid", (document_id,)
        ).fetchone()
        sha = sha_row[0] if sha_row else None
        if sha:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                         (str(dataset_id), sha))
            dup = conn.execute(
                """SELECT 1 FROM extraction_document d2
                   JOIN record r ON r.document_id = d2.id AND r.dataset_id = %s::uuid
                   WHERE d2.pdf_sha256 = %s AND d2.id <> %s::uuid LIMIT 1""",
                (dataset_id, sha, document_id),
            ).fetchone()
            if dup is not None:
                return 0     # this paper is already in the dataset → skip the duplicate
        cur = conn.execute(
            "UPDATE record SET dataset_id = %s::uuid WHERE document_id = %s::uuid",
            (dataset_id, document_id),
        )
        n = cur.rowcount
        if n == 0:
            # 0-record document → record a "screened, no records" sentinel so the paper is
            # still part of the dataset (coverage/provenance). Skip if one already exists.
            doc = conn.execute(
                "SELECT paper_id, schema_id, owner_user_id, session_id "
                "FROM extraction_document WHERE id = %s::uuid", (document_id,),
            ).fetchone()
            has_sentinel = conn.execute(
                "SELECT 1 FROM record WHERE document_id = %s::uuid AND screened_empty LIMIT 1",
                (document_id,),
            ).fetchone()
            if doc is not None and has_sentinel is None:
                conn.execute(
                    """INSERT INTO record (id, document_id, paper_id, dataset_id, schema_id,
                            entry_index, field_values, verification_status, screened_empty,
                            owner_user_id, session_id)
                       VALUES (%s, %s::uuid, %s, %s::uuid, %s, 0, %s, 'unverified', true, %s, %s)""",
                    (_new_id(), document_id, doc[0], dataset_id, doc[1], Json({}), doc[2], doc[3]),
                )
                n = 1
    return n


def documents_by_hashes(conn: psycopg.Connection, hashes: list[str], *,
                        owner_user_id: str | None = None,
                        session_id: str | None = None,
                        schema_id: str | None = None) -> dict[str, list[dict]]:
    """For each pdf_sha256 in ``hashes``, the caller's existing documents with that exact
    content hash that are STILL LIVE IN A DATASET under the given preset — powers the
    "already extracted" warning when adding papers. A duplicate is the SAME PDF, extracted
    with the SAME preset (pass ``schema_id``; None matches any), whose real (non-screened)
    records currently belong to a live dataset. Deliberately does NOT flag papers that are
    merely cached: after ``delete_dataset`` discards a paper's records (leaving only its
    cached PDF), or when re-extracting under a different preset, the paper is no longer a
    duplicate, so a from-scratch rebuild is clean. Scoped to the principal (owner or session);
    returns ``{sha: [{document_id, filename, created_at, n_records}]}`` newest first."""
    hs = [h for h in (hashes or []) if h]
    if not hs or (owner_user_id is None and session_id is None):
        return {}
    # JOIN (not LEFT JOIN) record + dataset so only papers with real records in a still-
    # existing dataset surface. record.dataset_id has no FK, so JOIN dataset (not just
    # dataset_id IS NOT NULL) is required to exclude dangling links to deleted datasets.
    rows = conn.execute(
        """SELECT d.pdf_sha256, d.id, d.filename, d.created_at, count(r.id) AS n_records
           FROM extraction_document d
           JOIN record  r  ON r.document_id = d.id AND NOT COALESCE(r.screened_empty, false)
           JOIN dataset ds ON ds.id = r.dataset_id
           WHERE d.pdf_sha256 = ANY(%s)
             AND (%s::text IS NULL OR d.schema_id = %s)
             AND ((%s::text IS NOT NULL AND d.owner_user_id::text = %s)
               OR (%s::text IS NOT NULL AND d.session_id = %s))
           GROUP BY d.id
           ORDER BY d.created_at DESC""",
        (hs, schema_id, schema_id, owner_user_id, owner_user_id, session_id, session_id),
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for sha, did, fn, created, nrec in rows:
        out.setdefault(sha, []).append({
            "document_id": str(did), "filename": fn,
            "created_at": created.isoformat() if created else None, "n_records": nrec})
    return out


def set_field_across_records(conn: psycopg.Connection, document_id: str, key: str, value,
                             *, verifier_user_id: str | None = None,
                             verifier_kind: str = "maintainer") -> int:
    """Set ``field_values[key] = value`` on EVERY data record of a document — used when a
    coder edits a study-constant field once and it must apply to all entries. Logs a
    verification_event per changed record (so it appears as a correction). Returns #changed."""
    rows = conn.execute(
        "SELECT id, field_values FROM record "
        "WHERE document_id = %s::uuid AND NOT screened_empty", (document_id,),
    ).fetchall()
    n = 0
    with conn.transaction():
        for rid, fv in rows:
            fv = dict(fv or {})
            old = fv.get(key)
            if old == value:
                continue
            fv[key] = value
            conn.execute("UPDATE record SET field_values = %s, verification_status = 'verified' "
                         "WHERE id = %s", (Json(fv), rid))
            conn.execute(
                """INSERT INTO verification_event
                     (id, record_id, verifier_user_id, verifier_kind, status, diff, notes)
                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s, 'verified', %s, NULL)""",
                (_new_id(), str(rid), verifier_user_id, verifier_kind,
                 Json([{"field_path": key, "original_value": old, "final_value": value}])))
            n += 1
    return n


_PAPER_EDIT_COLS = ("title", "year", "journal", "authors")


def update_paper_fields(conn: psycopg.Connection, paper_id: str, fields: dict) -> dict | None:
    """Update an allowlisted subset of the paper record (title/year/journal/authors) — the
    editable Study-info identity fields. Returns the updated paper dict, or None if nothing
    to change. NOTE: the paper row is DOI-deduped, so this corrects title/year for every
    document sharing the paper (intended)."""
    cols = {k: v for k, v in (fields or {}).items() if k in _PAPER_EDIT_COLS}
    if not cols:
        return None
    sets, vals = [], []
    for k, v in cols.items():
        sets.append(f"{k} = %s")
        vals.append(Json(v) if k == "authors" else v)
    vals.append(str(paper_id))
    with conn.transaction():
        conn.execute(f"UPDATE paper SET {', '.join(sets)} WHERE id = %s::uuid", vals)
    row = conn.execute(
        "SELECT id, title, doi, year, journal, authors FROM paper WHERE id = %s::uuid",
        (str(paper_id),),
    ).fetchone()
    if row is None:
        return None
    return {"id": str(row[0]), "title": row[1], "doi": row[2], "year": row[3],
            "journal": row[4], "authors": row[5]}


def delete_document(conn: psycopg.Connection, document_id: str) -> dict:
    """Delete a document — cascades its records/evidence/confidence (FK ON DELETE
    CASCADE) — then its stored PDF + page images. Returns {"deleted": n}."""
    with conn.transaction():
        cur = conn.execute("DELETE FROM extraction_document WHERE id = %s::uuid", (document_id,))
    deleted = cur.rowcount
    if deleted:
        from . import storage
        store = storage.get_store()
        store.delete(storage.pdf_key(document_id))
        store.delete_prefix(f"pages/{document_id}/")
    return {"deleted": deleted}


def delete_record(conn: psycopg.Connection, record_id: str) -> dict:
    """Delete a single record/finding (evidence + confidence + verification events
    cascade via FK ON DELETE CASCADE). Returns {"deleted": n}."""
    with conn.transaction():
        cur = conn.execute("DELETE FROM record WHERE id = %s::uuid", (record_id,))
    return {"deleted": cur.rowcount}


def add_record(conn: psycopg.Connection, document_id: str, *,
               field_values: dict | None = None, session_id: str | None = None,
               owner_user_id: str | None = None) -> dict:
    """Add a manual record/finding to a document. Inherits paper_id + schema_id from
    the document and dataset_id from a sibling record; entry_index = max + 1."""
    doc = conn.execute(
        "SELECT paper_id, schema_id FROM extraction_document WHERE id = %s::uuid",
        (document_id,)).fetchone()
    if doc is None:
        raise KeyError(document_id)
    paper_id, schema_id = doc
    sib = conn.execute(
        "SELECT dataset_id FROM record WHERE document_id = %s::uuid AND dataset_id IS NOT NULL LIMIT 1",
        (document_id,)).fetchone()
    dataset_id = sib[0] if sib else None
    next_ei = conn.execute(
        "SELECT coalesce(max(entry_index), -1) + 1 FROM record WHERE document_id = %s::uuid",
        (document_id,)).fetchone()[0]
    rid = _new_id()
    with conn.transaction():
        conn.execute(
            """INSERT INTO record
                 (id, document_id, paper_id, dataset_id, schema_id, entry_index,
                  field_values, session_id, owner_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::uuid)""",
            (rid, document_id, paper_id, dataset_id, schema_id, next_ei,
             Json(field_values or {}), session_id, owner_user_id),
        )
    return get_record(conn, rid)


def delete_dataset(conn: psycopg.Connection, dataset_id: str) -> dict:
    """Delete a dataset and DISCARD its extraction records — they were this dataset's data
    (deleting them cascades their evidence/confidence/verification via FK). Each paper's
    cached PDF is KEPT: the extraction_document + stored PDF survive, so the paper stays in
    "All my papers" and can be re-extracted into another dataset. A record belongs to at
    most one dataset, so this only removes the records that were in THIS one. Returns the
    dataset + record delete counts."""
    with conn.transaction():
        rec = conn.execute("DELETE FROM record WHERE dataset_id = %s::uuid", (dataset_id,))
        cur = conn.execute("DELETE FROM dataset WHERE id = %s::uuid", (dataset_id,))
    return {"deleted": cur.rowcount, "records_deleted": rec.rowcount}


def delete_user_data(conn: psycopg.Connection, user_id: str) -> dict:
    """Account deletion (GDPR): delete every document the user owns (cascades their
    records/evidence + removes the stored PDFs/page images), their datasets, then the
    user row (cascades sessions). Returns counts."""
    doc_ids = [str(r[0]) for r in conn.execute(
        "SELECT id FROM extraction_document WHERE owner_user_id = %s::uuid", (user_id,)).fetchall()]
    ds_ids = [str(r[0]) for r in conn.execute(
        "SELECT id FROM dataset WHERE owner_user_id = %s::uuid", (user_id,)).fetchall()]
    for did in doc_ids:
        delete_document(conn, did)          # DB cascade + storage blobs
    for dsid in ds_ids:
        delete_dataset(conn, dsid)
    with conn.transaction():
        conn.execute("DELETE FROM personal_preset WHERE owner_user_id = %s::uuid", (user_id,))
        conn.execute("DELETE FROM users WHERE id = %s::uuid", (user_id,))   # cascades sessions
    return {"documents": len(doc_ids), "datasets": len(ds_ids)}


def dataset_records_all_owned(conn: psycopg.Connection, dataset_id: str, principal) -> bool:
    """True iff every record in the dataset is owned by the principal (an empty
    dataset → True). Gate for publishing — never publish another user's records."""
    rows = conn.execute(
        "SELECT owner_user_id::text, session_id FROM record WHERE dataset_id = %s::uuid",
        (dataset_id,)).fetchall()
    return all(_owns(principal, o, s) for (o, s) in rows)


def set_dataset_visibility(conn: psycopg.Connection, dataset_id: str, visibility: str) -> dict:
    with conn.transaction():
        cur = conn.execute("UPDATE dataset SET visibility = %s WHERE id = %s::uuid",
                           (visibility, dataset_id))
    return {"updated": cur.rowcount, "visibility": visibility}


# ── personal presets (user-owned presets, alongside datasets & analyses) ──────────
# Resolved by id GLOBALLY (get_personal_preset) so the worker/persist path can build a
# schema grammar without a principal; the picker (list_personal_presets) is principal
# scoped. Shape mirrors a file preset so presets.get()/emit_schema_row() treat both alike.

def _preset_meta_from_row(r) -> dict:
    return {"id": r[0], "owner_user_id": r[1], "session_id": r[2], "visibility": r[3],
            "title": r[4], "tagline": r[5], "description": r[6], "mode": r[7],
            "prompt": r[8], "sub_views": r[9], "template_params": r[10],
            "accent_color": r[11], "created_at": r[12].isoformat() if r[12] else None,
            "source": "personal"}


_PRESET_COLS = ("id, owner_user_id::text, session_id, visibility, title, tagline, "
                "description, mode, prompt, sub_views, template_params, accent_color, created_at")


def create_personal_preset(conn: psycopg.Connection, *, title: str, prompt: str,
                           tagline: str | None = None, description: str | None = None,
                           mode: str = "extraction", sub_views=None, template_params=None,
                           accent_color: str | None = None, owner_user_id: str | None = None,
                           session_id: str | None = None, visibility: str = "private",
                           preset_id: str | None = None) -> dict:
    pid = preset_id or f"{_slugify(title)}-{_new_id()[:8]}"
    with conn.transaction():
        conn.execute(
            """INSERT INTO personal_preset
                 (id, owner_user_id, session_id, visibility, title, tagline, description,
                  mode, prompt, sub_views, template_params, accent_color, updated_at)
               VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())""",
            (pid, owner_user_id, session_id, visibility, title, tagline, description, mode,
             prompt, Json(sub_views) if sub_views is not None else None,
             Json(template_params) if template_params is not None else None, accent_color),
        )
    return get_personal_preset(conn, pid)


def get_personal_preset(conn: psycopg.Connection, preset_id: str) -> dict | None:
    """Resolve a personal preset by id — GLOBAL (no principal): resolution must work in
    the worker/persist path. Callers gate visibility separately where it matters."""
    r = conn.execute(
        f"SELECT {_PRESET_COLS} FROM personal_preset WHERE id = %s", (preset_id,)).fetchone()
    return _preset_meta_from_row(r) if r else None


def list_personal_presets(conn: psycopg.Connection, *, owner_user_id: str | None = None,
                          session_id: str | None = None, owned_only: bool = False) -> list[dict]:
    """Personal presets visible to the principal. owned_only → just theirs (My Workspace);
    else theirs + everyone's public (the extract picker)."""
    public = "" if owned_only else "visibility = 'public' OR "
    rows = conn.execute(
        f"""SELECT {_PRESET_COLS} FROM personal_preset
            WHERE {public}(%s::text IS NOT NULL AND owner_user_id::text = %s::text)
               OR (%s::text IS NOT NULL AND session_id = %s::text)
            ORDER BY created_at DESC""",
        (owner_user_id, owner_user_id, session_id, session_id),
    ).fetchall()
    return [_preset_meta_from_row(r) for r in rows]


def is_preset_owner(conn: psycopg.Connection, preset_id: str, principal) -> bool:
    r = conn.execute(
        "SELECT owner_user_id::text, session_id FROM personal_preset WHERE id = %s",
        (preset_id,)).fetchone()
    return r is not None and _owns(principal, r[0], r[1])


def update_personal_preset(conn: psycopg.Connection, preset_id: str, **fields) -> dict | None:
    allowed = {"title", "tagline", "description", "mode", "prompt", "sub_views",
               "template_params", "accent_color", "visibility"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        vals.append(Json(v) if k in ("sub_views", "template_params") else v)
        sets.append(f"{k} = %s")
    if sets:
        sets.append("updated_at = now()")
        with conn.transaction():
            conn.execute(f"UPDATE personal_preset SET {', '.join(sets)} WHERE id = %s",
                         (*vals, preset_id))
    return get_personal_preset(conn, preset_id)


def delete_personal_preset(conn: psycopg.Connection, preset_id: str) -> dict:
    with conn.transaction():
        cur = conn.execute("DELETE FROM personal_preset WHERE id = %s", (preset_id,))
    return {"deleted": cur.rowcount}


def set_preset_visibility(conn: psycopg.Connection, preset_id: str, visibility: str) -> dict:
    with conn.transaction():
        cur = conn.execute(
            "UPDATE personal_preset SET visibility = %s, updated_at = now() WHERE id = %s",
            (visibility, preset_id))
    return {"updated": cur.rowcount, "visibility": visibility}


def promote_dataset_preset(conn: psycopg.Connection, dataset_id: str, principal=None) -> str | None:
    """When a dataset is published (made public), flip its personal preset to public too
    so others can use it. Only promotes a DB preset the principal OWNS. Returns the
    promoted preset id, or None if there was nothing to promote (file preset / not owned)."""
    row = conn.execute("SELECT schema_id FROM dataset WHERE id = %s::uuid", (dataset_id,)).fetchone()
    if not row or not row[0]:
        return None
    preset_id = str(row[0]).split("@")[0]
    if get_personal_preset(conn, preset_id) is None:
        return None                                  # a file preset (already global) — nothing to do
    if principal is not None and not is_preset_owner(conn, preset_id, principal):
        return None                                  # don't publish someone else's private preset
    set_preset_visibility(conn, preset_id, "public")
    return preset_id


def records_for_paper(conn: psycopg.Connection, paper_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, entry_index, schema_id, field_values, verification_status
           FROM record WHERE paper_id = %s ORDER BY entry_index""",
        (paper_id,),
    ).fetchall()
    return [
        {"id": str(rid), "entry_index": ei, "schema_id": sid,
         "field_values": fv, "verification_status": vs}
        for (rid, ei, sid, fv, vs) in rows
    ]
