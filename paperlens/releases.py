"""Dataset releases: frozen, self-contained copies of what dashboards read from a dataset.

A dataset is a moving thing (papers are added, rows verified, corrected, deleted). A published
dashboard must not move with it: it PINS a release, and its owner updates it deliberately. A
release therefore holds everything a dashboard needs — the records with values, statuses and
corrections, paper metadata, the evidence quotes, and the preset as it was (including its
``display.analysis`` settings, so editing a preset cannot change a public page). It never holds
geometry, page images or PDFs.

Whether a dataset changed since a release is answered by a FINGERPRINT computed from the data,
not by a counter some write path might forget to bump.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Json

from . import analysis_table, records

FORMAT = 1
_CACHE: dict[str, dict] = {}          # release id → parsed snapshot (releases are immutable)
_CACHE_MAX = 8


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def fingerprint(conn: psycopg.Connection, dataset_id: str) -> str:
    """A hash of everything a dashboard reads from the LIVE dataset (record values, statuses,
    extraction provenance, paper metadata, which records belong to it). Any add, delete, edit
    or verification changes it; layout caches (rects, table context) do not."""
    return conn.execute(
        """SELECT md5(coalesce(string_agg(md5(concat_ws('|', r.id::text, r.entry_index::text,
                      COALESCE(r.screened_empty, false)::text, r.verification_status, r.field_values::text,
                      r.extraction->>'resolved_model', r.extraction->>'model', r.extraction->>'date',
                      p.title, p.doi, p.year::text, p.journal, p.authors::text)), '' ORDER BY r.id), ''))
           FROM record r LEFT JOIN paper p ON p.id = r.paper_id WHERE r.dataset_id = %s::uuid""",
        (dataset_id,)).fetchone()[0]


def _spec_sha(spec: dict | None) -> str:
    return hashlib.sha256(_canonical(spec or {})).hexdigest()


def snapshot(conn: psycopg.Connection, dataset_id: str) -> dict | None:
    """The live dataset as a release snapshot (format 1): exactly what analysis_table reads."""
    src = analysis_table.live_source(conn, dataset_id)
    if src is None:
        return None
    recs = [{"id": rid, "doc": doc, "entry_index": ei, "field_values": fv, "status": status,
             "extraction": extraction if isinstance(extraction, dict) else None,
             "title": title, "doi": doi, "year": year, "journal": journal, "authors": authors,
             "filename": filename, "created": created.isoformat() if created else None}
            for (rid, doc, ei, fv, status, extraction, _sid, title, doi, year, journal, authors, filename, created) in src["raw"]]
    ev = [{"id": sid, "rec": rid, "doc": doc, "entry_index": ei, "field_path": fp, "snippet": snippet, "page": page,
           "source": source, "child_rid": crid, "row_rid": rrid, "context": context}
          for sid, rid, doc, ei, fp, snippet, page, source, crid, rrid, context
          in analysis_table.evidence_rows(conn, [r["doc"] for r in recs])]
    return {"format": FORMAT, "schema_id": src["schema_id"], "spec": src["spec"], "records": recs,
            "corrected": src["corrected"], "evidence": ev}


def content_sha(snap: dict) -> str:
    """sha256 of the snapshot WITHOUT the table-layout cache of evidence items (read from the PDF
    on demand, so it may be filled in later without the content having changed)."""
    bare = {**snap, "evidence": [{k: v for k, v in e.items() if k != "context"} for e in snap.get("evidence") or []]}
    return hashlib.sha256(_canonical(bare)).hexdigest()


# ── what changed between two snapshots (the automatic release notes) ─────────
def _papers(snap: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in snap.get("records") or []:
        key = str(r.get("doi") or r.get("title") or r.get("doc"))
        p = out.setdefault(key, {"title": r.get("title"), "doi": r.get("doi"), "year": r.get("year"), "records": 0,
                                 "study": analysis_table._study_label(r.get("authors"), r.get("year"), r.get("title"))})   # noqa: SLF001
        p["records"] += 1
    return out


def diff_snapshots(prev: dict | None, new: dict) -> dict:
    a, b = _papers(prev or {}), _papers(new)
    ra = {r["id"]: r for r in (prev or {}).get("records") or []}
    rb = {r["id"]: r for r in new.get("records") or []}
    both = set(ra) & set(rb)
    brief = lambda p: {k: p[k] for k in ("study", "title", "doi", "records")}   # noqa: E731
    return {
        "first": prev is None,
        "papers_added": [brief(b[k]) for k in b if k not in a], "papers_removed": [brief(a[k]) for k in a if k not in b],
        "records": {"added": len(set(rb) - set(ra)), "removed": len(set(ra) - set(rb)),
                    "changed": sum(1 for k in both if ra[k].get("field_values") != rb[k].get("field_values"))},
        "status": {"newly_verified": sum(1 for k in both if ra[k].get("status") != "verified" and rb[k].get("status") == "verified")
                                     + sum(1 for k in set(rb) - set(ra) if rb[k].get("status") == "verified"),
                   "newly_flagged": sum(1 for k in both if ra[k].get("status") != "flagged" and rb[k].get("status") == "flagged")},
        "preset_changed": prev is not None and _spec_sha(prev.get("spec")) != _spec_sha(new.get("spec")),
        "n_papers": len(b), "n_records": len(rb),
    }


# ── rows ─────────────────────────────────────────────────────────────────────
_COLS = ("id::text, dataset_id::text, number, created_at, created_by::text, reason, notes, changes, fingerprint, "
         "content_sha, spec_sha, schema_id, engine, stats, credibility")


def _row(r, snap: dict | None = None) -> dict:
    out = {"id": r[0], "dataset_id": r[1], "number": r[2], "created_at": r[3].isoformat(timespec="seconds") if r[3] else None,
           "created_by": r[4], "reason": r[5], "notes": r[6], "changes": r[7], "fingerprint": r[8], "content_sha": r[9],
           "spec_sha": r[10], "schema_id": r[11], "engine": r[12], "stats": r[13], "credibility": r[14]}
    if snap is not None:
        out["snapshot"] = snap
    return out


def _load_snapshot(conn, release_id: str) -> dict:
    if release_id not in _CACHE:
        blob = conn.execute("SELECT snapshot FROM dataset_release WHERE id = %s::uuid", (release_id,)).fetchone()[0]
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[release_id] = json.loads(gzip.decompress(bytes(blob)).decode("utf-8"))
    return _CACHE[release_id]


def get(conn: psycopg.Connection, release_id: str, *, with_snapshot: bool = False) -> dict | None:
    if not records._is_uuid(release_id):   # noqa: SLF001
        return None
    r = conn.execute(f"SELECT {_COLS} FROM dataset_release WHERE id = %s::uuid", (release_id,)).fetchone()
    return _row(r, _load_snapshot(conn, r[0]) if with_snapshot else None) if r else None


def get_by_number(conn: psycopg.Connection, dataset_id: str, number: int, *, with_snapshot: bool = False) -> dict | None:
    r = conn.execute(f"SELECT {_COLS} FROM dataset_release WHERE dataset_id = %s::uuid AND number = %s", (dataset_id, number)).fetchone()
    return _row(r, _load_snapshot(conn, r[0]) if with_snapshot else None) if r else None


def latest(conn: psycopg.Connection, dataset_id: str, *, with_snapshot: bool = False) -> dict | None:
    r = conn.execute(f"SELECT {_COLS} FROM dataset_release WHERE dataset_id = %s::uuid ORDER BY number DESC LIMIT 1", (dataset_id,)).fetchone()
    return _row(r, _load_snapshot(conn, r[0]) if with_snapshot else None) if r else None


def public_row(rel: dict) -> dict:
    """What anyone who may see the dataset may know about a release."""
    return {k: rel.get(k) for k in ("number", "created_at", "reason", "notes", "changes", "content_sha", "stats", "credibility")}


def list_for_dataset(conn: psycopg.Connection, dataset_id: str) -> list[dict]:
    return [_row(r) for r in conn.execute(
        f"SELECT {_COLS} FROM dataset_release WHERE dataset_id = %s::uuid ORDER BY number DESC", (dataset_id,)).fetchall()]


def changed_since(conn: psycopg.Connection, dataset_id: str, rel: dict | None) -> bool:
    """Does the live dataset differ from this release (data, or the preset it is read with)?"""
    if rel is None:
        return True
    if fingerprint(conn, dataset_id) != rel["fingerprint"]:
        return True
    d = records.get_dataset(conn, dataset_id) or {}
    schema_id = rel.get("schema_id") or d.get("schema_id")
    return _spec_sha(records.schema_spec(conn, schema_id) if schema_id else None) != rel["spec_sha"]


def pending(conn: psycopg.Connection, dataset_id: str) -> dict:
    """What the NEXT release would contain, for the "Create release" dialog: the changes since
    the latest one, what is not yet reviewed (and how much of that sits in new papers), and the
    badge that would be frozen. Warnings, never blockers."""
    last = latest(conn, dataset_id, with_snapshot=True)
    snap = snapshot(conn, dataset_id) or {"records": [], "evidence": []}
    changes = diff_snapshots(last["snapshot"] if last else None, snap)
    old_docs = {r["doc"] for r in (last["snapshot"]["records"] if last else [])}
    unverified = [r for r in snap["records"] if r.get("status") not in ("verified",)]
    cred = records.dataset_credibility(conn, dataset_id)
    return {"changed": changed_since(conn, dataset_id, last), "next_number": _next_number(conn, dataset_id, last),
            "latest": public_row(last) if last else None, "changes": changes,
            "warnings": {"unverified": sum(1 for r in unverified if r.get("status") != "flagged"),
                         "flagged": sum(1 for r in unverified if r.get("status") == "flagged"),
                         "unverified_in_new_papers": sum(1 for r in unverified if r["doc"] not in old_docs) if last else 0,
                         "empty": not snap["records"]},
            "credibility": {"tier": cred.get("tier"), "label": cred.get("label")}}


def _next_number(conn, dataset_id: str, last: dict | None) -> int:
    if last:
        return last["number"] + 1
    return int((records.get_dataset(conn, dataset_id) or {}).get("version") or 1)   # continue the old counter


def create(conn: psycopg.Connection, dataset_id: str, *, reason: str = "manual", notes: str | None = None,
           created_by: str | None = None) -> dict | None:
    """Cut a release of the dataset as it is now. Returns None for an unknown dataset."""
    from . import __version__, exporter
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"release:{dataset_id}",))
        snap = snapshot(conn, dataset_id)
        if snap is None:
            return None
        last = latest(conn, dataset_id, with_snapshot=True)
        number = _next_number(conn, dataset_id, last)
        cred = records.dataset_credibility(conn, dataset_id)
        changes = diff_snapshots(last["snapshot"] if last else None, snap)
        rid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO dataset_release (id, dataset_id, number, created_by, reason, notes, changes, fingerprint,
                                            content_sha, spec_sha, schema_id, engine, stats, credibility, snapshot)
               VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (rid, dataset_id, number, created_by if records._is_uuid(created_by or "") else None, reason,   # noqa: SLF001
             (notes or "").strip()[:4000] or None, Json(changes), fingerprint(conn, dataset_id), content_sha(snap),
             _spec_sha(snap.get("spec")), snap.get("schema_id"),
             Json({"name": "metalens", "version": __version__, "git_sha": exporter._git_sha()}),   # noqa: SLF001
             Json({"n_papers": changes["n_papers"], "n_records": changes["n_records"], "n_evidence": len(snap["evidence"])}),
             Json({"tier": cred.get("tier"), "label": cred.get("label"), "audited": cred.get("audited"), "n_records": cred.get("n_records")}),
             # stored in its own key order (the order of a preset's fields and inputs is meaningful to
             # readers); only the hashes use the canonical, sorted form
             gzip.compress(json.dumps(snap, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"), mtime=0)))
        conn.execute("UPDATE dataset SET version = %s, updated_at = now() WHERE id = %s::uuid", (number, dataset_id))
    return get(conn, rid)


def ensure_current(conn: psycopg.Connection, dataset_id: str, *, reason: str, created_by: str | None = None) -> dict | None:
    """The latest release if the dataset has not changed since, else a new one."""
    last = latest(conn, dataset_id)
    if last and not changed_since(conn, dataset_id, last):
        return last
    return create(conn, dataset_id, reason=reason, created_by=created_by)
