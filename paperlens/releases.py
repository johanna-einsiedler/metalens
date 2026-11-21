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
from . import validation

FORMAT = 1
_CACHE: dict[str, dict] = {}          # release id → parsed snapshot (releases are immutable)
_CACHE_MAX = 8


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def fingerprint(conn: psycopg.Connection, dataset_id: str) -> str:
    """A hash of everything a dashboard reads from the LIVE dataset (record values, statuses,
    extraction provenance, paper metadata, which records belong to it). Any add, delete, edit
    or verification changes it; layout caches (rects, table context) do not."""
    data = conn.execute(
        """SELECT md5(coalesce(string_agg(md5(concat_ws('|', r.id::text, r.entry_index::text,
                      COALESCE(r.screened_empty, false)::text, r.verification_status, r.field_values::text,
                      r.extraction->>'resolved_model', r.extraction->>'model', r.extraction->>'date',
                      p.title, p.doi, p.year::text, p.journal, p.authors::text)), '' ORDER BY r.id), ''))
           FROM record r LEFT JOIN paper p ON p.id = r.paper_id WHERE r.dataset_id = %s::uuid""",
        (dataset_id,)).fetchone()[0]
    # what the dataset carries beyond its records: committed vocabularies and pages, the newest
    # finished workflow run — a release must be able to freeze a change in any of them
    extra = conn.execute(
        """SELECT md5(coalesce(string_agg(x, ',' ORDER BY x), '')) FROM (
             SELECT 'v:' || unit || ':' || "column" || ':' || version::text AS x FROM dataset_vocabulary
               WHERE dataset_id = %s::uuid AND status = 'committed'
             UNION ALL
             SELECT 'p:' || "group" || ':' || key || ':' || version::text FROM dataset_page
               WHERE dataset_id = %s::uuid AND status = 'committed'
             UNION ALL
             SELECT 'd:' || step || ':' || version::text FROM dataset_derived
               WHERE dataset_id = %s::uuid AND status = 'committed'
             UNION ALL
             SELECT 'r:' || id::text FROM workflow_run WHERE (dataset_id = %s::uuid OR group_id = (SELECT group_id FROM dataset WHERE id = %s::uuid)) AND status = 'done'
             UNION ALL
             SELECT 'c:' || rc.record_id::text || ':' || rc.coder_user_id::text || ':' || rc.updated_at::text      -- the coders' codings
               FROM record_coding rc JOIN record r ON r.id = rc.record_id WHERE r.dataset_id = %s::uuid
             UNION ALL
             SELECT DISTINCT 'pc:' || dc.document_id::text || ':' || dc.coder_user_id::text || ':' || dc.updated_at::text
               FROM document_coding dc JOIN record r ON r.document_id = dc.document_id WHERE r.dataset_id = %s::uuid
             UNION ALL
             SELECT DISTINCT 'pm:' || ed.id::text || ':' || md5(coalesce(ed.paper_metadata_raw::text, ''))                -- the paper-level values
               FROM extraction_document ed JOIN record r ON r.document_id = ed.id WHERE r.dataset_id = %s::uuid AND ed.paper_metadata_raw IS NOT NULL) s""",
        (dataset_id, dataset_id, dataset_id, dataset_id, dataset_id, dataset_id, dataset_id, dataset_id)).fetchone()[0]
    return data if extra == "d41d8cd98f00b204e9800998ecf8427e" else f"{data}:{extra[:16]}"


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
    from . import checks, coders, crosscheck, derived, pages, vocabulary, workflows
    fz = crosscheck.frozen(conn, dataset_id)
    # the declared paper-level values per document (a coder's answers in a stages preset live
    # there), and every coder's coding — frozen with the records they belong to
    pm = {did: raw for did, raw in conn.execute(
        """SELECT DISTINCT ed.id::text, ed.paper_metadata_raw FROM extraction_document ed JOIN record r ON r.document_id = ed.id
           WHERE r.dataset_id = %s::uuid AND ed.paper_metadata_raw IS NOT NULL""", (dataset_id,)).fetchall() if isinstance(raw, dict)}
    cb = coders.export_block(conn, dataset_id)
    vocabs = vocabulary.snapshot(conn, dataset_id)
    pgs = pages.snapshot(conn, dataset_id)
    wf = workflow_block(conn, dataset_id)
    findings = checks.frozen(conn, dataset_id)
    wspec = workflows.resolve(conn, src["dataset"])[0] if src["dataset"].get("workflow") else None
    drv = derived.snapshot(conn, dataset_id, wspec) if wspec else []
    return {"format": FORMAT, "schema_id": src["schema_id"], "spec": src["spec"], "records": recs,
            "corrected": src["corrected"], "evidence": ev, **({"paper_metadata": pm} if pm else {}),
            **({"codings": {"coders": cb["coders"], "records": cb["by_record"], "papers": cb["by_document"]}} if cb["by_record"] or cb["by_document"] else {}),
            **({"crosscheck": fz} if fz else {}),
            **({"vocabularies": vocabs} if vocabs else {}), **({"pages": pgs} if pgs else {}),
            **({"workflow": wf} if wf else {}), **({"findings": findings} if findings else {}),
            **({"derived": drv} if drv else {})}


def workflow_block(conn: psycopg.Connection, dataset_id: str) -> dict | None:
    """The workflow the dataset is built with and the newest finished run: what a release and an
    export record as the process behind the data."""
    from . import workflow_spec as ws, workflows
    d = records.get_dataset(conn, dataset_id) or {}
    spec, implicit = workflows.resolve(conn, d)
    run = workflows.last_done_run(conn, dataset_id)
    steps = []
    if run:
        for sr in workflows.step_runs(conn, run["id"]):
            rv = sr.get("review") or {}
            steps.append({"id": sr["step_id"], "kind": sr["kind"], "status": sr["status"], "preset_schema_id": sr.get("preset_schema_id"),
                          "preset_release": sr.get("preset_release"), "prompt_sha256": sr.get("prompt_sha256"), "n_calls": sr.get("n_calls"),
                          "review": {k: rv.get(k) for k in ("mode", "unit", "blocks", "satisfied_at", "overridden_by") if k in rv},
                          "outputs": {k: v for k, v in (sr.get("outputs") or {}).items() if k != "document_ids"}})
    if implicit and run is None:
        return None
    return {"id": spec["id"], "contract": ws.workflow_id(spec), "version": spec.get("version", 1), "implicit": implicit,
            "release": run.get("workflow_release") if run else None, "run_id": run["id"] if run else None,
            "run_at": run.get("finished_at") if run else None, "engine": run.get("engine") if run else None, "steps": steps}


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
        **({"codings": _codings_diff(prev, new)} if (new.get("codings") or (prev or {}).get("codings")) else {}),
    }


def _codings_diff(prev: dict | None, new: dict) -> dict:
    """How the coders' codings moved between two snapshots: codings added or changed, per record."""
    a = ((prev or {}).get("codings") or {}).get("records") or {}
    b = (new.get("codings") or {}).get("records") or {}
    key = lambda cs: {c.get("coder"): (c.get("status"), _canonical(c.get("values"))) for c in cs}   # noqa: E731
    added = changed = 0
    for rid, cs in b.items():
        kb, ka = key(cs), key(a.get(rid) or [])
        added += sum(1 for c in kb if c not in ka)
        changed += sum(1 for c in kb if c in ka and ka[c] != kb[c])
    return {"added": added, "changed": changed, "n_coders": len((new.get("codings") or {}).get("coders") or [])}


# ── rows ─────────────────────────────────────────────────────────────────────
_COLS = ("id::text, dataset_id::text, number, created_at, created_by::text, reason, notes, changes, fingerprint, "
         "content_sha, spec_sha, schema_id, engine, stats, credibility, published_at, doi, zenodo_record_id, zenodo_url, "
         "workflow_run_id::text, gates")


def _row(r, snap: dict | None = None) -> dict:
    out = {"id": r[0], "dataset_id": r[1], "number": r[2], "created_at": r[3].isoformat(timespec="seconds") if r[3] else None,
           "created_by": r[4], "reason": r[5], "notes": r[6], "changes": r[7], "fingerprint": r[8], "content_sha": r[9],
           "spec_sha": r[10], "schema_id": r[11], "engine": r[12], "stats": r[13], "credibility": r[14],
           "published_at": r[15].isoformat(timespec="seconds") if r[15] else None,
           "doi": r[16], "zenodo_record_id": r[17], "zenodo_url": r[18], "workflow_run_id": r[19], "gates": r[20]}
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
    return {k: rel.get(k) for k in ("number", "created_at", "reason", "notes", "changes", "content_sha", "stats", "credibility", "published_at",
                                    "doi", "zenodo_url", "workflow_run_id", "gates")}


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
    cred["validations"] = validation.for_dataset(conn, dataset_id)
    from . import workflows
    gate = workflows.gate_report(conn, dataset_id)
    return {"changed": changed_since(conn, dataset_id, last), "next_number": _next_number(conn, dataset_id, last),
            "latest": public_row(last) if last else None, "changes": changes, "gate": gate,
            "warnings": {"unverified": sum(1 for r in unverified if r.get("status") != "flagged"),
                         "flagged": sum(1 for r in unverified if r.get("status") == "flagged"),
                         "unverified_in_new_papers": sum(1 for r in unverified if r["doc"] not in old_docs) if last else 0,
                         "empty": not snap["records"],
                         **_crosscheck_warnings(snap.get("crosscheck"), (last or {}).get("snapshot", {}).get("crosscheck") if last else None),
                         **_vocabulary_warnings(conn, dataset_id, snap.get("vocabularies")),
                         **({"check_findings": snap["findings"]["n"]} if snap.get("findings") else {}),
                         **({"derived_units": len(snap["derived"])} if snap.get("derived") else {})},
            "credibility": {k: cred.get(k) for k in ("tier", "label", "label_long", "audited", "agreed", "n_records", "sample", "checks", "validations") if cred.get(k) is not None}}


def _crosscheck_warnings(now: dict | None, before: dict | None) -> dict:
    """The next release's cross-check: mismatches to look at, and whether the companion moved since the last release."""
    if not now:
        return {}
    from . import crosscheck
    s = crosscheck.summary(now["rows"])
    out = {"crosscheck_mismatch": s.get("mismatch", 0), "crosscheck_unlinked": s.get("unlinked", 0)}
    if before and before.get("companion_content_sha") != now.get("companion_content_sha"):
        out["companion_moved"] = True
    return out


def _vocabulary_warnings(conn, dataset_id: str, vocabs: list | None) -> dict:
    """Values of a harmonised column the committed vocabulary does not cover yet."""
    if not vocabs:
        return {}
    from . import vocabulary
    n = 0
    for v in vocabs:
        n += len(vocabulary.unresolved(vocabulary.values(conn, dataset_id, v["unit"], v["column"], examples=False), v.get("assignments") or {}))
    return {"vocabulary_unresolved": n} if n else {}


def _next_number(conn, dataset_id: str, last: dict | None) -> int:
    if last:
        return last["number"] + 1
    return int((records.get_dataset(conn, dataset_id) or {}).get("version") or 1)   # continue the old counter


class GateClosed(Exception):
    """The workflow's release gate holds: a gate step is not fresh, or a review gate wants verdicts."""

    def __init__(self, gate: dict):
        self.gate = gate
        parts = [f"{b['step']} is {b['state']}" for b in gate.get("blocking") or []]
        parts += [f"{g} awaits review" for g in gate.get("gates_blocking") or []]
        super().__init__("The release gate holds: " + "; ".join(parts))


def create(conn: psycopg.Connection, dataset_id: str, *, reason: str = "manual", notes: str | None = None,
           created_by: str | None = None, allow_stale: bool = False, override_gates: bool = False) -> dict | None:
    """Cut a release of the dataset as it is now. Returns None for an unknown dataset. Raises
    ``GateClosed`` when the workflow's gate holds, unless the caller overrides it — recorded on
    the release (``gates``), so an override is never silent."""
    from . import __version__, exporter, workflows
    gate = workflows.gate_report(conn, dataset_id)
    # a review step awaiting its verdicts is the review gate itself: overriding the gate covers it
    blocking = [b for b in gate.get("blocking") or [] if not (b.get("state") == "awaiting_review" and override_gates)]
    stale_over = bool(blocking) and allow_stale
    gates_over = bool(gate.get("gates_blocking")) and override_gates
    if (blocking and not allow_stale) or (gate.get("gates_blocking") and not override_gates):
        raise GateClosed({**gate, "blocking": blocking})
    run = workflows.last_done_run(conn, dataset_id)
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"release:{dataset_id}",))
        snap = snapshot(conn, dataset_id)
        if snap is None:
            return None
        last = latest(conn, dataset_id, with_snapshot=True)
        number = _next_number(conn, dataset_id, last)
        cred = records.dataset_credibility(conn, dataset_id)
        cred["validations"] = validation.for_dataset(conn, dataset_id)      # the stamps' records, frozen with the badge
        changes = diff_snapshots(last["snapshot"] if last else None, snap)
        rid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO dataset_release (id, dataset_id, number, created_by, reason, notes, changes, fingerprint,
                                            content_sha, spec_sha, schema_id, engine, stats, credibility, snapshot, workflow_run_id, gates)
               VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, %s)""",
            (rid, dataset_id, number, created_by if records._is_uuid(created_by or "") else None, reason,   # noqa: SLF001
             (notes or "").strip()[:4000] or None, Json(changes), fingerprint(conn, dataset_id), content_sha(snap),
             _spec_sha(snap.get("spec")), snap.get("schema_id"),
             Json({"name": "metalens", "version": __version__, "git_sha": exporter._git_sha()}),   # noqa: SLF001
             Json({"n_papers": changes["n_papers"], "n_records": changes["n_records"], "n_evidence": len(snap["evidence"])}),
             Json({k: cred.get(k) for k in ("tier", "label", "label_long", "audited", "agreed", "n_records", "sample", "checks") if cred.get(k) is not None}),
             # stored in its own key order (the order of a preset's fields and inputs is meaningful to
             # readers); only the hashes use the canonical, sorted form
             gzip.compress(json.dumps(snap, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"), mtime=0),
             run["id"] if run else None,
             Json({"blocking": gate.get("blocking") or [], "gates_blocking": gate.get("gates_blocking") or [],
                   "stale_overridden": stale_over, "gates_overridden": gates_over})))
        conn.execute("UPDATE dataset SET version = %s, updated_at = now() WHERE id = %s::uuid", (number, dataset_id))
    return get(conn, rid)


def ensure_current(conn: psycopg.Connection, dataset_id: str, *, reason: str, created_by: str | None = None) -> dict | None:
    """The latest release if the dataset has not changed since, else a new one."""
    last = latest(conn, dataset_id)
    if last and not changed_since(conn, dataset_id, last):
        return last
    # nobody is asked here (a dashboard publish, a GitHub sync): the gate is passed and the release says so
    return create(conn, dataset_id, reason=reason, created_by=created_by, allow_stale=True, override_gates=True)


def mark_published(conn: psycopg.Connection, release_id: str) -> None:
    """This release was sent to the datasets repository."""
    with conn.transaction():
        conn.execute("UPDATE dataset_release SET published_at = COALESCE(published_at, now()) WHERE id = %s::uuid", (release_id,))


# ── project releases: every member under its own number, the workflow, the review statistics ──

_PCOLS = ("id::text, project_id::text, number, created_at, created_by::text, notes, workflow_contract, workflow_spec_sha, "
          "workflow_run_id::text, gates, members, content_sha, stats, credibility, review, engine, doi, zenodo_record_id, zenodo_url, "
          "doi_minted_at, published_at")


def _prow(r) -> dict:
    return {"id": r[0], "project_id": r[1], "number": r[2], "created_at": r[3].isoformat(timespec="seconds") if r[3] else None,
            "created_by": r[4], "notes": r[5], "workflow_contract": r[6], "workflow_spec_sha": r[7], "workflow_run_id": r[8], "gates": r[9],
            "members": r[10], "content_sha": r[11], "stats": r[12], "credibility": r[13], "review": r[14], "engine": r[15],
            "doi": r[16], "zenodo_record_id": r[17], "zenodo_url": r[18],
            "doi_minted_at": r[19].isoformat(timespec="seconds") if r[19] else None,
            "published_at": r[20].isoformat(timespec="seconds") if r[20] else None}


def public_project_row(rel: dict) -> dict:
    return {k: rel.get(k) for k in ("number", "created_at", "notes", "workflow_contract", "workflow_run_id", "gates", "members", "content_sha",
                                    "stats", "credibility", "review", "doi", "zenodo_url", "published_at")}


def list_project(conn: psycopg.Connection, project_id: str) -> list[dict]:
    return [_prow(r) for r in conn.execute(f"SELECT {_PCOLS} FROM project_release WHERE project_id = %s::uuid ORDER BY number DESC", (project_id,)).fetchall()]


def get_project(conn: psycopg.Connection, project_id: str, number: int) -> dict | None:
    r = conn.execute(f"SELECT {_PCOLS} FROM project_release WHERE project_id = %s::uuid AND number = %s", (project_id, number)).fetchone()
    return _prow(r) if r else None


def latest_project(conn: psycopg.Connection, project_id: str) -> dict | None:
    r = conn.execute(f"SELECT {_PCOLS} FROM project_release WHERE project_id = %s::uuid ORDER BY number DESC LIMIT 1", (project_id,)).fetchone()
    return _prow(r) if r else None


def changed_since_project(conn: psycopg.Connection, project: dict, rel: dict | None) -> bool:
    """Whether the project moved since a project release: a member whose live data moved since
    its own latest release, a member released since, a member linked since, or a newer finished run."""
    from . import workflows
    if rel is None:
        return True
    grp = workflows.group_of(conn, project) or {"roles": {}}
    frozen = {m["role"]: m for m in rel.get("members") or []}
    for role, info in grp["roles"].items():
        last = latest(conn, info["dataset_id"])
        m = frozen.get(role)
        if m is None or last is None or last["number"] != m.get("number") or changed_since(conn, info["dataset_id"], last):
            return True
    run = workflows.last_done_run(conn, project["id"])
    return (run["id"] if run else None) != rel.get("workflow_run_id")


def release_members(conn: psycopg.Connection, grp: dict, *, notes: str | None, created_by: str | None, allow_stale: bool,
                    override_gates: bool, who=None, skip: str | None = None) -> tuple[list[dict], list[str]]:
    """Freeze every linked dataset of a group (but ``skip``), each under its own number and gate.
    Returns ``(members, problems)``; a member's own problem never undoes the others."""
    done, problems = [], []
    for role, info in grp["roles"].items():
        if info["dataset_id"] == skip:
            continue
        try:
            if who is not None and not records.dataset_records_all_owned(conn, info["dataset_id"], who):
                problems.append(f"{role}: not released — it holds records you don't own")
                continue
            last = latest(conn, info["dataset_id"])
            if last and not changed_since(conn, info["dataset_id"], last):
                done.append({"role": role, "dataset_id": info["dataset_id"], "release_id": last["id"], "number": last["number"],
                             "content_sha": last["content_sha"], "unchanged": True})
                continue
            rel = create(conn, info["dataset_id"], reason="group", notes=notes, created_by=created_by,
                         allow_stale=allow_stale, override_gates=override_gates)
            done.append({"role": role, "dataset_id": info["dataset_id"], "release_id": rel["id"], "number": rel["number"],
                         "content_sha": rel["content_sha"], "unchanged": False})
        except GateClosed as exc:
            problems.append(f"{role}: not released — {exc}")
        except Exception as exc:  # noqa: BLE001 - the member's own problem never undoes this release
            problems.append(f"{role}: not released — {exc}")
    return done, problems


def project_review_stats(conn: psycopg.Connection, project: dict, run: dict | None) -> dict:
    """The review statistics of a run of the project, per task and coder: what each gate asked,
    what was checked, what disagreed, who did how much."""
    from . import projects
    if run is None:
        return {"run_id": None, "tasks": []}
    out = []
    for t in projects.tasks(conn, project, run=run):
        g = t.get("gate") or {}
        out.append({"step_id": t["step_id"], "label": t["label"], "template": t.get("template"), "template_id": t.get("template_id"),
                    "unit": t.get("unit"), "mode": t.get("mode"), "coders": t.get("coders"), "datasets": t.get("datasets"),
                    "total": g.get("total", 0), "checked": g.get("checked", 0), "pending": g.get("pending", 0), "disagreed": g.get("disagreed", 0),
                    "satisfied": g.get("satisfied"), "overridden_by": g.get("overridden_by"),
                    **({k: g.get(k) for k in ("frame_n", "n_target", "drawn", "exhaustive", "table", "per_paper") if g.get(k) is not None}),
                    "by_coder": [{k: b.get(k) for k in ("label", "total", "checked", "pending", "disagreed", "papers")} for b in g.get("by_coder") or []]})
    return {"run_id": run["id"], "tasks": out}


def project_credibility(conn: psycopg.Connection, members: list[dict]) -> dict:
    """One badge over the project's datasets: the badge rule over the summed counts of their
    frozen releases, with how many datasets it spans."""
    total = audited = agreed = 0
    for m in members:
        rel = get(conn, m["release_id"]) if m.get("release_id") else None
        c = (rel or {}).get("credibility") or {}
        total += int(c.get("n_records") or 0); audited += int(c.get("audited") or 0); agreed += int(c.get("agreed") or 0)
    out = records._badge_from_counts(total, audited, agreed)   # noqa: SLF001 - the one badge rule
    out["n_datasets"] = len(members)
    out["label_long"] = f"{out['label_long']} Across {len(members)} dataset{'s' if len(members) != 1 else ''}."
    return out


def create_project(conn: psycopg.Connection, project: dict, *, notes: str | None = None, created_by: str | None = None,
                   allow_stale: bool = False, override_gates: bool = False, who=None) -> dict:
    """Cut a project release: the project's gate (a step not fresh, a review wanting verdicts)
    holds unless overridden — recorded; every linked dataset is released under its own number
    (or kept at its latest when unchanged); the workflow and its newest finished run, the review
    statistics per task and coder, and one badge over all are frozen with them. Raises
    ``GateClosed``; ``RuntimeError`` when no dataset could be released."""
    from . import __version__, exporter, workflows
    pid = project["id"]
    gate = workflows.gate_report(conn, pid)
    # a review step awaiting its verdicts is the review gate itself: overriding the gate covers it
    blocking = [b for b in gate.get("blocking") or [] if not (b.get("state") == "awaiting_review" and override_gates)]
    stale_over = bool(blocking) and allow_stale
    gates_over = bool(gate.get("gates_blocking")) and override_gates
    if (blocking and not allow_stale) or (gate.get("gates_blocking") and not override_gates):
        raise GateClosed({**gate, "blocking": blocking})
    grp = workflows.group_of(conn, project) or {"roles": {}}
    if not grp["roles"]:
        raise RuntimeError("No dataset is linked to this project yet.")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"project-release:{pid}",))
        members, problems = release_members(conn, grp, notes=notes, created_by=created_by, allow_stale=allow_stale,
                                            override_gates=override_gates, who=who)
        if not members:
            raise RuntimeError("Nothing was released: " + "; ".join(problems))
        spec, _implicit = workflows.resolve(conn, project)
        run = workflows.last_done_run(conn, pid)
        wf = workflow_block(conn, pid)
        review = project_review_stats(conn, project, run)
        cred = project_credibility(conn, members)
        last = latest_project(conn, pid)
        number = (last["number"] + 1) if last else 1
        stats = {"n_datasets": len(members), "n_papers": sum(int(((get(conn, m["release_id"]) or {}).get("stats") or {}).get("n_papers") or 0) for m in members),
                 "n_records": sum(int(((get(conn, m["release_id"]) or {}).get("stats") or {}).get("n_records") or 0) for m in members),
                 "problems": problems}
        from . import workflow_spec as ws
        sha = hashlib.sha256(_canonical({"workflow": {"contract": ws.workflow_id(spec), "run_id": run["id"] if run else None},
                                         "members": [{"role": m["role"], "number": m["number"], "content_sha": m["content_sha"]} for m in members],
                                         "review": review})).hexdigest()
        rid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO project_release (id, project_id, number, created_by, notes, workflow_contract, workflow_spec_sha, workflow_run_id, gates,
                                            members, content_sha, stats, credibility, review, engine)
               VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s)""",
            (rid, pid, number, created_by if records._is_uuid(created_by or "") else None, (notes or "").strip()[:4000] or None,   # noqa: SLF001
             ws.workflow_id(spec), ws.spec_sha(spec), run["id"] if run else None,
             Json({"blocking": gate.get("blocking") or [], "gates_blocking": gate.get("gates_blocking") or [],
                   "stale_overridden": stale_over, "gates_overridden": gates_over}),
             Json(members), sha, Json(stats), Json(cred), Json(review),
             Json({"name": "metalens", "version": __version__, "git_sha": exporter._git_sha(), **((wf or {}).get("engine") or {})})))   # noqa: SLF001
    return get_project(conn, pid, number)
