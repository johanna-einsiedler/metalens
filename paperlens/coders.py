"""Coders: accounts the owner of a dataset lets code its papers, each with a coding of their own.

A dataset has one owner. The owner may add coders by email (``dataset_coder``; a grant on a
workflow dataset covers its linked datasets). A coder reads the owner's papers — the pages, the
quotes, the records as imported — and codes them independently: their values for the editable
fields, their verdict, their note, their paper-level values and their marks live in a CODING
(``record_coding``, ``document_coding``, ``coding_event``), never in the record. Two coders never
see each other's coding; the owner sees every coding. Nothing here writes ``record.field_values``,
``record.verification_status`` or ``verification_event``, so the badge's event readers, the
history and the activity feed are untouched by a coder's work; the badge counts codings on its
own (``records.dataset_credibility``).

Who is a coder decides where a write goes: a listed coder's verdicts and edits always land in
their coding — the owner's too, once the owner lists themselves. An owner who is not a coder
edits the stored record as before. Coders may change values and table rows and give verdicts
and notes; the shape of the data (entries added or removed, the paper's identity, a field set
across records, a document deleted) is the owner's.
"""
from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Json

from . import contract, records

VERDICTS = ("verified", "flagged")
_VALID_STATUS = ("verified", "flagged", "unverified", "corrected", "noted")

# the datasets the user codes: granted on the dataset itself, or on its workflow dataset
_CODED_DATASETS_SQL = """(SELECT c.dataset_id FROM dataset_coder c WHERE c.user_id = %s::uuid
                          UNION SELECT d2.id FROM dataset d2 JOIN dataset_coder c2 ON c2.dataset_id = d2.group_id WHERE c2.user_id = %s::uuid)"""


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


def _label(i: int) -> str:
    return f"coder_{i + 1}"


# ── grants ────────────────────────────────────────────────────────────────────

def coders_of(conn: psycopg.Connection, dataset_id: str) -> list[dict]:
    """The coders of a dataset in join order — its own grants and its workflow dataset's — each
    ``{user_id, email, label, added_at, dataset_id}``; the label (``coder_1`` …) is what public
    files call them."""
    if not records._is_uuid(dataset_id):
        return []
    d = records.get_dataset(conn, dataset_id) or {}
    gid = d.get("group_id")
    rows = conn.execute(
        """SELECT c.user_id::text, u.email, c.added_at, c.dataset_id::text
           FROM dataset_coder c JOIN users u ON u.id = c.user_id
           WHERE c.dataset_id = %s::uuid OR (%s::text IS NOT NULL AND c.dataset_id = %s::uuid)
           ORDER BY c.added_at, u.email""", (dataset_id, gid, gid or dataset_id)).fetchall()
    out, seen = [], set()
    for uid, email, added, on in rows:
        if uid in seen:
            continue
        seen.add(uid)
        out.append({"user_id": uid, "email": email, "label": _label(len(out)), "added_at": added.isoformat() if added else None,
                    "dataset_id": on, "scope": "group" if gid and on == gid and on != dataset_id else "dataset"})
    return out


def labels_for(conn: psycopg.Connection, dataset_id: str) -> dict[str, dict]:
    """{user id: {label, email}} for the dataset's coders; a coder removed since keeps a label of
    their own (``former_1`` …) when their codings come up."""
    return {c["user_id"]: {"label": c["label"], "email": c["email"]} for c in coders_of(conn, dataset_id)}


def is_coder(conn: psycopg.Connection, dataset_id: str, who) -> bool:
    uid = getattr(who, "user_id", None)
    if not uid or not records._is_uuid(dataset_id):
        return False
    return conn.execute(
        f"SELECT 1 FROM dataset d WHERE d.id = %s::uuid AND (d.id IN {_CODED_DATASETS_SQL} OR d.group_id IN {_CODED_DATASETS_SQL})",
        (dataset_id, uid, uid, uid, uid)).fetchone() is not None


def coder_of_document(conn: psycopg.Connection, document_id: str, who) -> str | None:
    """The dataset this user codes the document in, else None (the document's records sit in one
    dataset; a document in no dataset has no coders)."""
    uid = getattr(who, "user_id", None)
    if not uid or not records._is_uuid(document_id):
        return None
    row = conn.execute("SELECT dataset_id::text FROM record WHERE document_id = %s::uuid AND dataset_id IS NOT NULL LIMIT 1",
                       (document_id,)).fetchone()
    if not row:
        return None
    return row[0] if is_coder(conn, row[0], who) else None


def coder_of_run(conn: psycopg.Connection, run: dict, who) -> bool:
    return is_coder(conn, run.get("dataset_id") or "", who)


def add(conn: psycopg.Connection, dataset_id: str, email: str, *, by: str | None) -> dict:
    """Grant an account the right to code this dataset. KeyError when no account has that email."""
    row = conn.execute("SELECT id::text FROM users WHERE email = %s", ((email or "").strip().lower(),)).fetchone()
    if row is None:
        raise KeyError(email)
    with conn.transaction():
        conn.execute("INSERT INTO dataset_coder (dataset_id, user_id, added_by) VALUES (%s::uuid, %s::uuid, %s::uuid) ON CONFLICT DO NOTHING",
                     (dataset_id, row[0], by))
    return next(c for c in coders_of(conn, dataset_id) if c["user_id"] == row[0])


def remove(conn: psycopg.Connection, dataset_id: str, user_id: str) -> dict:
    """Withdraw the grant; the coder's codings stay (they are the record of what was done)."""
    with conn.transaction():
        cur = conn.execute("DELETE FROM dataset_coder WHERE dataset_id = %s::uuid AND user_id = %s::uuid", (dataset_id, user_id))
    return {"removed": cur.rowcount}


def datasets_for(conn: psycopg.Connection, user_id: str) -> list[dict]:
    """The datasets this account codes — a member of a workflow dataset under its parent's title."""
    if not user_id:
        return []
    rows = conn.execute(
        f"""SELECT d.id::text, d.title, d.schema_id, d.group_id::text, d.group_role, p.title,
                   (SELECT count(*) FROM record r WHERE r.dataset_id = d.id AND NOT COALESCE(r.screened_empty, false))
            FROM dataset d LEFT JOIN dataset p ON p.id = d.group_id
            WHERE d.id IN {_CODED_DATASETS_SQL} AND (d.group_id IS NULL OR d.group_id <> d.id)
            ORDER BY p.title NULLS LAST, d.created_at""", (user_id, user_id)).fetchall()
    return [{"id": did, "title": title, "schema_id": sid, "workflow_dataset_id": gid, "group_role": role,
             "workflow_title": ptitle, "n_records": int(n)} for did, title, sid, gid, role, ptitle, n in rows]


# ── the coding as the coder sees it ───────────────────────────────────────────

def apply(conn: psycopg.Connection, view: dict, coder_user_id: str, *, read_only: bool = False, label: str | None = None) -> dict:
    """Lay one coder's coding over a document view (``records.document_view``): their values,
    verdicts, notes and corrections on each record, their paper-level values, their own marks
    only. The view then carries ``coding {as, label, read_only}``."""
    rec_ids = [r["id"] for r in view.get("records") or []]
    by_rec: dict[str, tuple] = {}
    if rec_ids:
        for rid, fv, status, notes, updated in conn.execute(
                """SELECT record_id::text, field_values, verification_status, notes, updated_at FROM record_coding
                   WHERE coder_user_id = %s::uuid AND record_id = ANY(%s::uuid[])""", (coder_user_id, rec_ids)).fetchall():
            by_rec[rid] = (fv, status, notes, updated)
    corrections: dict[str, list[dict]] = {}
    if rec_ids:
        for rid, diff, created in conn.execute(
                """SELECT record_id::text, diff, created_at FROM coding_event
                   WHERE coder_user_id = %s::uuid AND record_id = ANY(%s::uuid[]) AND diff IS NOT NULL ORDER BY created_at""",
                (coder_user_id, rec_ids)).fetchall():
            for d in (diff if isinstance(diff, list) else []):
                if isinstance(d, dict) and d.get("original_value") != d.get("final_value"):
                    corrections.setdefault(rid, []).append({"field_path": d.get("field_path"), "original_value": d.get("original_value"),
                                                            "final_value": d.get("final_value"), "editor": label or "coder",
                                                            "at": created.isoformat() if created else None})
    for r in view.get("records") or []:
        c = by_rec.get(r["id"])
        r["verification_status"] = c[1] if c else "unverified"
        if c and c[0] is not None:
            r["field_values"] = c[0]
        r["corrections"] = corrections.get(r["id"], [])
        r["note"] = {"text": c[2], "status": c[1], "by": label or "coder", "at": c[3].isoformat() if c[3] else None} if c and c[2] else None
    pm = conn.execute("SELECT paper_metadata FROM document_coding WHERE document_id = %s::uuid AND coder_user_id = %s::uuid",
                      (view.get("document_id"), coder_user_id)).fetchone()
    if pm and isinstance(pm[0], dict):
        view["paper_metadata"] = pm[0]
    view["highlights"] = [h for h in view.get("highlights") or [] if h.get("created_by") == coder_user_id]
    view["coding"] = {"as": coder_user_id, "label": label or "coder", "read_only": read_only}
    return view


def summary(conn: psycopg.Connection, document_id: str, dataset_id: str | None) -> dict:
    """What the owner sees of the codings on a document: per record one chip per coder (status,
    whether the coder changed a value), and per coder whether they edited the paper-level fields."""
    labels = labels_for(conn, dataset_id) if dataset_id else {}
    rows = conn.execute(
        """SELECT rc.record_id::text, rc.coder_user_id::text, rc.verification_status, rc.notes,
                  (rc.field_values IS NOT NULL AND rc.field_values <> r.field_values), rc.updated_at
           FROM record_coding rc JOIN record r ON r.id = rc.record_id WHERE r.document_id = %s::uuid
           ORDER BY rc.updated_at""", (document_id,)).fetchall()
    by_rec: dict[str, list[dict]] = {}
    former: dict[str, str] = {}
    def name(uid: str) -> str:
        if uid in labels:
            return labels[uid]["label"]
        former.setdefault(uid, f"former_{len(former) + 1}")
        return former[uid]
    for rid, uid, status, notes, changed, updated in rows:
        by_rec.setdefault(rid, []).append({"coder": name(uid), "user_id": uid, "status": status, "changed": bool(changed),
                                           "note": notes, "at": updated.isoformat() if updated else None})
    paper = [{"coder": name(uid), "user_id": uid, "at": updated.isoformat() if updated else None}
             for uid, updated in conn.execute("SELECT coder_user_id::text, updated_at FROM document_coding WHERE document_id = %s::uuid AND paper_metadata IS NOT NULL",
                                              (document_id,)).fetchall()]
    for lst in by_rec.values():
        lst.sort(key=lambda c: c["coder"])
    return {"records": by_rec, "paper": paper, "coders": [{"user_id": uid, **info} for uid, info in labels.items()]}


# ── a coder's writes ──────────────────────────────────────────────────────────

def verify(conn: psycopg.Connection, record_id: str, coder_user_id: str, *, status: str, diff: list | None = None,
           notes: str | None = None, field_values: dict | None = None, patch: list | None = None) -> dict:
    """A coder's verdict, note or edit on one record, in their coding: ``patch`` sets cells on the
    coding's values (started from the record as stored), ``field_values`` replaces them; a
    ``corrected`` or ``noted`` status leaves the verdict as it was. One transaction, one event."""
    if status not in _VALID_STATUS:
        raise ValueError(f"status must be one of {_VALID_STATUS}")
    rec = records.get_record(conn, record_id)
    if rec is None:
        raise ValueError("Record not found.")
    eid = _new_id()
    with conn.transaction():
        conn.execute("INSERT INTO record_coding (record_id, coder_user_id) VALUES (%s::uuid, %s::uuid) ON CONFLICT DO NOTHING",
                     (record_id, coder_user_id))
        row = conn.execute("SELECT field_values, verification_status FROM record_coding WHERE record_id = %s::uuid AND coder_user_id = %s::uuid FOR NO KEY UPDATE",
                           (record_id, coder_user_id)).fetchone()
        fv_new = None
        if field_values is not None:
            fv_new = field_values
        elif patch:
            fv = copy.deepcopy(row[0] if isinstance(row[0], dict) else (rec.get("field_values") or {}))
            for p in patch:
                if not isinstance(p, dict) or not p.get("field_path"):
                    raise ValueError("a patch names a field_path and a value")
                contract.set_path(fv, p["field_path"], p.get("value"))
            fv_new = fv
        if fv_new is not None:
            contract.ensure_row_ids(fv_new)                        # a row added by hand gets its permanent id
        sets, vals = ["updated_at = %s"], [_now()]
        if status not in ("corrected", "noted"):
            sets.append("verification_status = %s"); vals.append(status)
        if fv_new is not None:
            sets.append("field_values = %s"); vals.append(Json(fv_new))
        if notes is not None:
            sets.append("notes = %s"); vals.append(notes)
        conn.execute(f"UPDATE record_coding SET {', '.join(sets)} WHERE record_id = %s::uuid AND coder_user_id = %s::uuid",
                     (*vals, record_id, coder_user_id))
        conn.execute("""INSERT INTO coding_event (id, coder_user_id, record_id, document_id, status, diff, notes)
                        VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s, %s)""",
                     (eid, coder_user_id, record_id, rec.get("document_id"), status, Json(diff) if diff is not None else None, notes))
    return {"id": eid, "record_id": record_id, "status": status, "verifier_kind": "coder", "coding": True}


def set_paper_fields(conn: psycopg.Connection, document_id: str, coder_user_id: str, fields: dict) -> dict:
    """A coder's values for declared paper-level fields, in their coding (started from the
    document's paper metadata as stored); logs a document-level coding event with the diff."""
    row = conn.execute("SELECT paper_metadata_raw FROM extraction_document WHERE id = %s::uuid", (document_id,)).fetchone()
    if row is None:
        raise KeyError(document_id)
    with conn.transaction():
        conn.execute("INSERT INTO document_coding (document_id, coder_user_id) VALUES (%s::uuid, %s::uuid) ON CONFLICT DO NOTHING",
                     (document_id, coder_user_id))
        own = conn.execute("SELECT paper_metadata FROM document_coding WHERE document_id = %s::uuid AND coder_user_id = %s::uuid FOR NO KEY UPDATE",
                           (document_id, coder_user_id)).fetchone()
        raw = copy.deepcopy(own[0] if own and isinstance(own[0], dict) else (row[0] if isinstance(row[0], dict) else {}))
        diff = []
        fields = copy.deepcopy(fields or {})
        contract.ensure_row_ids(fields)
        for name, value in fields.items():
            if raw.get(name) == value:
                continue
            diff.append({"field_path": f"paper_metadata.{name}", "original_value": contract.strip_row_ids(raw.get(name)),
                         "final_value": contract.strip_row_ids(value)})
            raw[name] = value
        if diff:
            conn.execute("UPDATE document_coding SET paper_metadata = %s, updated_at = %s WHERE document_id = %s::uuid AND coder_user_id = %s::uuid",
                         (Json(raw), _now(), document_id, coder_user_id))
            conn.execute("""INSERT INTO coding_event (id, coder_user_id, record_id, document_id, status, diff)
                            VALUES (%s::uuid, %s::uuid, NULL, %s::uuid, 'corrected', %s)""",
                         (_new_id(), coder_user_id, document_id, Json(diff)))
    return {"paper_metadata": raw, "changed": [d["field_path"].split(".", 1)[1] for d in diff], "coding": True}


def events(conn: psycopg.Connection, record_id: str, coder_user_id: str) -> list[dict]:
    """A coder's own history on a record, newest first, in the shape of ``records.record_events``."""
    rows = conn.execute(
        """SELECT e.id, e.coder_user_id, u.email, e.status, e.diff, e.notes, e.created_at
           FROM coding_event e LEFT JOIN users u ON u.id = e.coder_user_id
           WHERE e.record_id = %s::uuid AND e.coder_user_id = %s::uuid ORDER BY e.created_at DESC""",
        (record_id, coder_user_id)).fetchall()
    return [{"id": str(i), "verifier_user_id": str(u) if u else None, "verifier_email": em, "verifier_kind": "coder",
             "status": s, "diff": d, "notes": n, "created_at": c.isoformat() if c else None} for (i, u, em, s, d, n, c) in rows]


# ── what leaves the app: exports, releases ────────────────────────────────────

def export_block(conn: psycopg.Connection, dataset_id: str, *, with_emails: bool = False) -> dict:
    """Every coding of the dataset's records: ``{coders: [{label, user_id, email?}], by_record:
    {record id: [{coder, status, values, notes, updated_at}]}, by_document: {document id: [{coder,
    paper_metadata, updated_at}]}}``. Coders are named by label; emails only for the owner."""
    labels = labels_for(conn, dataset_id)
    former: dict[str, str] = {}
    def name(uid: str) -> str:
        if uid in labels:
            return labels[uid]["label"]
        former.setdefault(uid, f"former_{len(former) + 1}")
        return former[uid]
    by_record: dict[str, list[dict]] = {}
    for rid, uid, fv, status, notes, updated in conn.execute(
            """SELECT rc.record_id::text, rc.coder_user_id::text, COALESCE(rc.field_values, r.field_values), rc.verification_status, rc.notes, rc.updated_at
               FROM record_coding rc JOIN record r ON r.id = rc.record_id WHERE r.dataset_id = %s::uuid
               ORDER BY r.document_id, r.entry_index, rc.updated_at""", (dataset_id,)).fetchall():
        by_record.setdefault(rid, []).append({"coder": name(uid), "status": status, "values": fv, "notes": notes,
                                              "updated_at": updated.isoformat() if updated else None})
    by_document: dict[str, list[dict]] = {}
    for did, uid, pm, updated in conn.execute(
            """SELECT DISTINCT ON (dc.document_id, dc.coder_user_id) dc.document_id::text, dc.coder_user_id::text, dc.paper_metadata, dc.updated_at
               FROM document_coding dc JOIN record r ON r.document_id = dc.document_id
               WHERE r.dataset_id = %s::uuid AND dc.paper_metadata IS NOT NULL ORDER BY dc.document_id, dc.coder_user_id""", (dataset_id,)).fetchall():
        by_document.setdefault(did, []).append({"coder": name(uid), "paper_metadata": pm, "updated_at": updated.isoformat() if updated else None})
    for lst in list(by_record.values()) + list(by_document.values()):
        lst.sort(key=lambda c: c["coder"])
    coders = [{"label": info["label"], "user_id": uid, **({"email": info["email"]} if with_emails else {})} for uid, info in labels.items()]
    coders += [{"label": lab, "user_id": uid} for uid, lab in former.items()]
    return {"coders": coders, "by_record": by_record, "by_document": by_document}


def badge_counts(conn: psycopg.Connection, dataset_id: str) -> dict[str, dict]:
    """Per record of the dataset: whether any coder gave a verdict, and whether any coder flagged
    it or changed a value — the badge's own rules, over codings (``records.dataset_credibility``)."""
    out: dict[str, dict] = {}
    for rid, audited, disagreed in conn.execute(
            """SELECT rc.record_id::text, bool_or(rc.verification_status IN ('verified', 'flagged')),
                      bool_or(rc.verification_status = 'flagged' OR (rc.field_values IS NOT NULL AND rc.field_values <> r.field_values))
               FROM record_coding rc JOIN record r ON r.id = rc.record_id
               WHERE r.dataset_id = %s::uuid AND NOT COALESCE(r.screened_empty, false) GROUP BY rc.record_id""", (dataset_id,)).fetchall():
        out[rid] = {"audited": bool(audited), "disagreed": bool(disagreed)}
    return out
