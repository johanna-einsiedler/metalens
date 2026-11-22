"""User-made workflows: a workflow document someone wrote in the editor, kept in the database
beside the bundled files. Resolved by id like a bundled one (``workflows.get_workflow`` reads
the row when it has a connection; a bundled id always wins, so a row's id is minted
``<slug>-<uuid8>``), owned by an account or an anonymous session (the personal presets' rule),
private unless made public, validated on every save under the same grammar as the files. No
releases or DOIs of their own yet: a project pins the contract (``id@sha8``) as it does for a
bundled workflow, and a run freezes the spec it ran."""
from __future__ import annotations

import uuid

import psycopg
from psycopg.types.json import Json

from . import records
from . import workflow_spec as ws

_COLS = "id, owner_user_id::text, session_id, visibility, title, spec, contract, spec_sha, version, created_at, updated_at"


def _row(r) -> dict | None:
    if r is None:
        return None
    keys = ("id", "owner_user_id", "session_id", "visibility", "title", "spec", "contract", "spec_sha", "version", "created_at", "updated_at")
    out = dict(zip(keys, r))
    for k in ("created_at", "updated_at"):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()
    return out


def mint_id(conn: psycopg.Connection, spec: dict) -> str:
    """``<slug>-<uuid8>`` from the document's id or title: never a bundled id, never a row's."""
    from . import workflows
    base = str(spec.get("id") or ((spec.get("meta") or {}).get("title")) or "workflow")
    slug = records._slugify(base)[:40].strip("-") or "workflow"   # noqa: SLF001
    taken = set(workflows.load_all())
    while True:
        wid = f"{slug}-{uuid.uuid4().hex[:8]}"
        if wid not in taken and get(conn, wid) is None:
            return wid


def get(conn: psycopg.Connection, wid: str) -> dict | None:
    if not isinstance(wid, str) or not wid:
        return None
    return _row(conn.execute(f"SELECT {_COLS} FROM user_workflow WHERE id = %s", (wid.partition("@")[0],)).fetchone())


def spec_of(conn: psycopg.Connection, wid: str) -> dict | None:
    row = get(conn, wid)
    return row["spec"] if row else None


def readable(row: dict | None, principal) -> bool:
    """The owner, or anyone when public."""
    return bool(row) and (row["visibility"] == "public" or records._owns(principal, row["owner_user_id"], row["session_id"]))   # noqa: SLF001


def is_owner(conn: psycopg.Connection, wid: str, principal) -> bool:
    row = get(conn, wid)
    return bool(row) and records._owns(principal, row["owner_user_id"], row["session_id"])   # noqa: SLF001


def create(conn: psycopg.Connection, spec: dict, *, owner_user_id: str | None, session_id: str | None, visibility: str = "private") -> dict:
    """Store a normalised, validated document under a minted id (the document's ``id`` becomes
    that id; the contract does not hash the id, so the contract is the document's)."""
    wid = mint_id(conn, spec)
    doc = dict(spec); doc["id"] = wid
    title = ((doc.get("meta") or {}).get("title")) or wid
    with conn.transaction():
        conn.execute(
            """INSERT INTO user_workflow (id, owner_user_id, session_id, visibility, title, spec, contract, spec_sha, version)
               VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, 1)""",
            (wid, owner_user_id if records._is_uuid(owner_user_id or "") else None, session_id, visibility, title,   # noqa: SLF001
             Json(doc), ws.workflow_id(doc), ws.spec_sha(doc)))
    return get(conn, wid)


def update(conn: psycopg.Connection, wid: str, *, spec: dict | None = None, visibility: str | None = None) -> dict | None:
    """A new document (the id kept) bumps the version and recomputes the contract; the
    visibility alone changes nothing else."""
    row = get(conn, wid)
    if row is None:
        return None
    with conn.transaction():
        if spec is not None:
            doc = dict(spec); doc["id"] = wid
            conn.execute("""UPDATE user_workflow SET spec = %s, contract = %s, spec_sha = %s, title = %s, version = version + 1, updated_at = now() WHERE id = %s""",
                         (Json(doc), ws.workflow_id(doc), ws.spec_sha(doc), ((doc.get("meta") or {}).get("title")) or wid, wid))
        if visibility is not None:
            conn.execute("UPDATE user_workflow SET visibility = %s, updated_at = now() WHERE id = %s", (visibility, wid))
    return get(conn, wid)


def declared_by(conn: psycopg.Connection, wid: str) -> list[str]:
    """The datasets (and projects) that run this workflow — what keeps it from being deleted."""
    return [r[0] for r in conn.execute("SELECT id::text FROM dataset WHERE split_part(workflow, '@', 1) = %s ORDER BY created_at", (wid,)).fetchall()]


def delete(conn: psycopg.Connection, wid: str) -> int:
    with conn.transaction():
        cur = conn.execute("DELETE FROM user_workflow WHERE id = %s", (wid,))
    return cur.rowcount


def list_for(conn: psycopg.Connection, principal, *, owned_only: bool = False) -> list[dict]:
    """The caller's own workflows (an account's, or an anonymous session's) and, unless
    ``owned_only``, everyone's public ones."""
    uid = getattr(principal, "user_id", None); sid = getattr(principal, "session_id", None)
    uid = uid if records._is_uuid(uid or "") else None   # noqa: SLF001
    rows = conn.execute(
        f"""SELECT {_COLS} FROM user_workflow
            WHERE ((%s::uuid IS NOT NULL AND owner_user_id = %s::uuid) OR (%s::text IS NOT NULL AND owner_user_id IS NULL AND session_id = %s))
               OR (NOT %s AND visibility = 'public')
            ORDER BY updated_at DESC""", (uid, uid, sid, sid, owned_only)).fetchall()
    out = []
    for r in rows:
        row = _row(r)
        row["owned"] = records._owns(principal, row["owner_user_id"], row["session_id"])   # noqa: SLF001
        out.append(row)
    return out


def summary(row: dict, principal=None) -> dict:
    """What a listing says about a user workflow: like a bundled one's entry, plus whose it is."""
    spec = row["spec"]; meta = spec.get("meta") or {}
    return {"id": row["id"], "contract": row["contract"], "version": row["version"], "title": meta.get("title") or row["title"],
            "tagline": meta.get("tagline"), "has_card": False, "personal": True, "visibility": row["visibility"],
            "owned": records._owns(principal, row["owner_user_id"], row["session_id"]) if principal is not None else bool(row.get("owned")),   # noqa: SLF001
            "steps": [{"id": st["id"], "kind": st["kind"], "label": st.get("label"), "preset": st.get("preset"), "source": st.get("source"),
                       "review": ws.review_of(st) if st["kind"] == "review" else None, "over": st.get("over") if st["kind"] == "review" else None,
                       "check": st.get("check"), "dataset": st.get("dataset")} for st in spec.get("steps") or []],
            "presets": [p for _s, p in ws.pins(spec)]}
