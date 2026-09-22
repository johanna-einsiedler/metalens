"""Stored dashboards: a validated block spec over ONE dataset.

The owner's DRAFT (spec, title, rev) is live: only the spec is stored, the data is read from the
dataset on every view. PUBLISHING freezes a copy of the spec and pins ONE dataset release
(releases.py): that is what everyone else sees, and it changes only when the owner publishes
again (an edit, or an update to a newer release). A dashboard dies with its dataset (FK cascade).
Others see it only when it is published; a published dashboard may sit over a private dataset —
its readers get that release's rows and quotes through the dashboard, never the dataset itself."""
from __future__ import annotations

import uuid

import psycopg
from psycopg.types.json import Json

from . import analysis_table, dashboard_spec, records

_COLS = ("id::text, dataset_id::text, owner_user_id::text, session_id, title, spec, grammar, rev, proposal, visibility, created_at, updated_at, "
         "published_spec, published_title, published_release_id::text, published_at")


def _row(r) -> dict:
    return {"id": r[0], "dataset_id": r[1], "owner_user_id": r[2], "session_id": r[3], "title": r[4], "spec": r[5],
            "grammar": r[6], "rev": r[7], "proposal": r[8], "visibility": r[9],
            "created_at": r[10].isoformat() if r[10] else None, "updated_at": r[11].isoformat() if r[11] else None,
            "published_spec": r[12], "published_title": r[13], "published_release_id": r[14],
            "published_at": r[15].isoformat(timespec="seconds") if r[15] else None}


def tables_for(conn, dataset_id: str, *, owner: bool, units: list[str] | None = None, release: dict | None = None) -> tuple[dict[str, dict], str]:
    """The analysis tables a dashboard over this dataset can use, and the default unit; read from
    the live dataset, or from ``release`` (a releases row with its snapshot)."""
    first = analysis_table.build(conn, dataset_id, None, owner=owner, release=release)
    default = first["unit"]["id"]
    out = {default: first}
    for u in first["units"]:
        if u["id"] != default and (units is None or u["id"] in units):
            out[u["id"]] = analysis_table.build(conn, dataset_id, u["id"], owner=owner, release=release)
    return out, default


def default_spec(conn, dataset_id: str, *, owner: bool) -> tuple[dict, dict]:
    tables, default = tables_for(conn, dataset_id, owner=owner)
    d = records.get_dataset(conn, dataset_id) or {}
    spec = records.schema_spec(conn, d.get("schema_id")) if d.get("schema_id") else None
    preset_default = ((spec or {}).get("display") or {}).get("analysis", {}).get("default_dashboard") if spec else None
    draft = dashboard_spec.default_dashboard(tables, default, preset_default=preset_default)
    clean, report = dashboard_spec.validate({**draft, "dataset_id": dataset_id}, tables, default_unit=default, origin="default")
    clean["blocks"] = [b for b in clean["blocks"] if b["sufficiency"]["status"] != "insufficient"]   # a default never shows empty cards
    return clean, report


def create(conn: psycopg.Connection, *, dataset_id: str, title: str, spec: dict, proposal: dict | None,
           owner_user_id: str | None, session_id: str | None) -> dict:
    did = str(uuid.uuid4())
    with conn.transaction():
        conn.execute(
            """INSERT INTO dashboard (id, dataset_id, owner_user_id, session_id, title, spec, grammar, proposal)
               VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s)""",
            (did, dataset_id, owner_user_id, session_id, title, Json(spec), dashboard_spec.GRAMMAR, Json(proposal) if proposal else None))
    return get(conn, did)


def get(conn: psycopg.Connection, dashboard_id: str) -> dict | None:
    if not records._is_uuid(dashboard_id):   # noqa: SLF001
        return None
    r = conn.execute(f"SELECT {_COLS} FROM dashboard WHERE id = %s::uuid", (dashboard_id,)).fetchone()
    return _row(r) if r else None


def author_name(conn, d: dict, dataset: dict | None) -> str | None:
    """Who published this dashboard, for its header: the owner's citation name. None for an
    anonymous session, an account without a name, or the owner of a dataset that is published
    anonymously (their name must not leak through the dashboard)."""
    if not d.get("owner_user_id"):
        return None
    if dataset and dataset.get("owner_user_id") == d["owner_user_id"] and dataset.get("attribution") == "anonymous":
        return None
    r = conn.execute("SELECT citation_name FROM users WHERE id = %s::uuid", (d["owner_user_id"],)).fetchone()
    return (r[0] or "").strip() or None if r else None


def owns(principal, d: dict) -> bool:
    return records._owns(principal, d.get("owner_user_id"), d.get("session_id"))   # noqa: SLF001


def is_published(d: dict) -> bool:
    return d.get("visibility") == "public" and bool(d.get("published_release_id")) and bool(d.get("published_spec"))


def visible(conn, d: dict, principal) -> bool:
    if owns(principal, d) or is_published(d):
        return True
    # before releases existed: a public dashboard over a public dataset (still live)
    ds = records.get_dataset(conn, d["dataset_id"])
    return d.get("visibility") == "public" and bool(ds) and ds.get("visibility") == "public"


def publish(conn, dashboard_id: str, *, rev: int, release_id: str, spec: dict, title: str) -> dict | None:
    """Freeze ``spec`` over a release as the public page. None when ``rev`` is stale."""
    with conn.transaction():
        cur = conn.execute(
            """UPDATE dashboard SET published_spec = %s, published_title = %s, published_release_id = %s::uuid,
                      published_at = now(), visibility = 'public', rev = rev + 1, updated_at = now()
               WHERE id = %s::uuid AND rev = %s""", (Json(spec), title, release_id, dashboard_id, rev))
    return get(conn, dashboard_id) if cur.rowcount else None


def unpublish(conn, dashboard_id: str) -> dict | None:
    with conn.transaction():
        conn.execute("""UPDATE dashboard SET visibility = 'private', published_spec = NULL, published_title = NULL,
                               published_release_id = NULL, published_at = NULL, rev = rev + 1, updated_at = now()
                        WHERE id = %s::uuid""", (dashboard_id,))
    return get(conn, dashboard_id)


def list_for_dataset(conn, dataset_id: str, principal) -> list[dict]:
    from . import releases
    rows = [_row(r) for r in conn.execute(
        f"SELECT {_COLS} FROM dashboard WHERE dataset_id = %s::uuid ORDER BY updated_at DESC", (dataset_id,)).fetchall()]
    latest = releases.latest(conn, dataset_id)
    out = []
    for d in rows:
        if not visible(conn, d, principal):
            continue
        mine = owns(principal, d)
        shown = d["published_spec"] if (is_published(d) and not mine) else d["spec"]
        pinned = releases.get(conn, d["published_release_id"]) if d.get("published_release_id") else None
        item = {"id": d["id"], "title": (d["title"] if mine else d.get("published_title") or d["title"]), "visibility": d["visibility"],
                "rev": d["rev"], "updated_at": d["updated_at"], "mine": mine, "n_blocks": len((shown or {}).get("blocks") or []),
                "published": is_published(d), "published_at": d.get("published_at"),
                "release": pinned["number"] if pinned else None}
        if mine and pinned and latest:                       # only the owner is told that newer data exists
            item["update_available"] = latest["number"] if latest["number"] > pinned["number"] else None
        out.append(item)
    return out


def list_mine(conn, principal) -> list[dict]:
    rows = conn.execute(
        f"""SELECT {_COLS} FROM dashboard
            WHERE (%s::text IS NOT NULL AND owner_user_id::text = %s)
               OR (%s::text IS NOT NULL AND session_id = %s AND owner_user_id IS NULL)
            ORDER BY updated_at DESC LIMIT 200""",
        (principal.user_id, principal.user_id, principal.session_id, principal.session_id)).fetchall()
    return [{k: d[k] for k in ("id", "dataset_id", "title", "visibility", "updated_at")} for d in map(_row, rows)]


def update(conn, dashboard_id: str, *, rev: int, title: str | None = None, spec: dict | None = None,
           visibility: str | None = None, proposal: dict | None = None) -> dict | None:
    """None when ``rev`` is stale (someone saved in between)."""
    sets, vals = ["rev = rev + 1", "updated_at = now()"], []
    if title is not None:
        sets.append("title = %s"); vals.append(title)
    if spec is not None:
        sets.append("spec = %s"); vals.append(Json(spec))
    if visibility in ("private", "public"):
        sets.append("visibility = %s"); vals.append(visibility)
    if proposal is not None:
        sets.append("proposal = %s"); vals.append(Json(proposal))
    with conn.transaction():
        cur = conn.execute(f"UPDATE dashboard SET {', '.join(sets)} WHERE id = %s::uuid AND rev = %s", (*vals, dashboard_id, rev))
    return get(conn, dashboard_id) if cur.rowcount else None


def count_proposal(conn, dashboard_id: str) -> None:
    """One more model proposal was made for this dashboard (the free re-proposal allowance is
    counted here). Does not touch ``rev``: it is bookkeeping, not an edit."""
    with conn.transaction():
        conn.execute(
            """UPDATE dashboard SET proposal = jsonb_set(COALESCE(proposal, '{}'::jsonb), '{attempts_total}',
                      to_jsonb(COALESCE((proposal->>'attempts_total')::int, 0) + 1)) WHERE id = %s::uuid""", (dashboard_id,))


def delete(conn, dashboard_id: str) -> int:
    with conn.transaction():
        return conn.execute("DELETE FROM dashboard WHERE id = %s::uuid", (dashboard_id,)).rowcount


def list_public(conn) -> list[dict]:
    """Published Metalens dashboards, newest first (for the public Dashboards page)."""
    from . import releases
    rows = [_row(r) for r in conn.execute(
        f"SELECT {_COLS} FROM dashboard WHERE visibility = 'public' AND published_release_id IS NOT NULL ORDER BY published_at DESC LIMIT 200").fetchall()]
    out = []
    for d in rows:
        ds = records.get_dataset(conn, d["dataset_id"]) or {}
        rel = releases.get(conn, d["published_release_id"]) if d.get("published_release_id") else None
        spec = d.get("published_spec") or {}
        blocks = spec.get("blocks") or []
        fig = next((b for b in blocks if b.get("type") == "figure"), blocks[0] if blocks else None)
        out.append({"id": d["id"], "title": d.get("published_title") or d["title"], "dataset_id": d["dataset_id"], "dataset_title": ds.get("title"),
                    "published_at": d.get("published_at"), "release": rel["number"] if rel else None, "n_blocks": len(blocks),
                    "n_papers": ((rel or {}).get("stats") or {}).get("n_papers"), "keywords": [k for k in (ds.get("keywords") or []) if isinstance(k, str)],
                    "author": author_name(conn, d, ds), "description": (spec.get("questions") or [{}])[0].get("text"),
                    # for the tile: the first figure's template and its bound column labels
                    "preview": {"icon": (dashboard_spec._BY_ID.get(fig.get("template")) or {}).get("icon", "rows_table"),   # noqa: SLF001
                                "title": fig.get("title")} if fig else None})
    return out
