"""Dashboards built outside Metalens (the author's own code, hosted on their own site) over a
dataset's releases. Metalens only lists them and checks which release they show.

The check reads a manifest the page publishes: ``<page url>/metalens.json`` or, for pages built
from the dev kit, ``<page url>/data/config.json``. Accepted keys: ``release`` (a folder name or
number) or ``release_number``; the number is read from the trailing "-vN" / "vN" of a folder name.
Only http(s) URLs are fetched, with a short timeout and a size cap; nothing is stored but the number.

A registration is not public the moment it is made: it is listed on ``/dashboards`` once a
moderator approves it (``paperlens/admins.py``), because the page it points at is someone else's
and Metalens vouches for it by listing it. Its owner may also supply the tile image directly
(``preview_override``), which wins over whatever the manifest names.
"""
from __future__ import annotations

import json
import re
import uuid
from urllib.parse import urlparse

import httpx
import psycopg

from . import records

_COLS = ("id::text, dataset_id::text, owner_user_id::text, title, url, repo_url, manifest_url, release_shown, checked_at, "
         "check_note, created_at, preview_url, description, authors, keywords, preview_override, approved_at")


def _cols(alias: str) -> str:
    """The same columns, qualified — _COLS is unqualified, and `id` is ambiguous inside a join."""
    return ", ".join(f"{alias}.{c.strip()}" for c in _COLS.split(","))


def _row(r) -> dict:
    return {"id": r[0], "dataset_id": r[1], "owner_user_id": r[2], "title": r[3], "url": r[4], "repo_url": r[5], "manifest_url": r[6],
            "release_shown": r[7], "checked_at": r[8].isoformat(timespec="seconds") if r[8] else None, "check_note": r[9],
            "created_at": r[10].isoformat(timespec="seconds") if r[10] else None,
            "preview_url": r[11], "description": r[12], "authors": r[13], "keywords": list(r[14] or []),
            "preview_override": r[15],
            "tile_url": r[15] or r[11],                      # what the tile actually shows: the owner's image, else the manifest's
            "approved": r[16] is not None,
            "approved_at": r[16].isoformat(timespec="seconds") if r[16] else None}


def valid_url(u: str | None) -> bool:
    try:
        p = urlparse(u or "")
        return p.scheme in ("http", "https") and bool(p.netloc)
    except ValueError:
        return False


def list_for_dataset(conn: psycopg.Connection, dataset_id: str) -> list[dict]:
    return [_row(r) for r in conn.execute(f"SELECT {_COLS} FROM external_dashboard WHERE dataset_id = %s::uuid ORDER BY created_at", (dataset_id,)).fetchall()]


def get(conn: psycopg.Connection, ext_id: str) -> dict | None:
    if not records._is_uuid(ext_id):   # noqa: SLF001
        return None
    r = conn.execute(f"SELECT {_COLS} FROM external_dashboard WHERE id = %s::uuid", (ext_id,)).fetchone()
    return _row(r) if r else None


def create(conn: psycopg.Connection, *, dataset_id: str, owner_user_id: str | None, title: str, url: str,
           repo_url: str | None, manifest_url: str | None) -> dict:
    eid = str(uuid.uuid4())
    with conn.transaction():
        conn.execute("""INSERT INTO external_dashboard (id, dataset_id, owner_user_id, title, url, repo_url, manifest_url)
                        VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s)""",
                     (eid, dataset_id, owner_user_id, title.strip()[:200], url.strip(), (repo_url or "").strip() or None, (manifest_url or "").strip() or None))
    return get(conn, eid)


def delete(conn: psycopg.Connection, ext_id: str) -> int:
    with conn.transaction():
        return conn.execute("DELETE FROM external_dashboard WHERE id = %s::uuid", (ext_id,)).rowcount


def set_preview(conn: psycopg.Connection, ext_id: str, url: str | None) -> dict | None:
    """The owner's own tile image (None clears it and the manifest's is used again)."""
    u = (url or "").strip() or None
    if u is not None and not valid_url(u):
        raise ValueError("A preview image needs an http(s) URL.")
    with conn.transaction():
        conn.execute("UPDATE external_dashboard SET preview_override = %s WHERE id = %s::uuid", (u[:500] if u else None, ext_id))
    return get(conn, ext_id)


def set_approved(conn: psycopg.Connection, ext_id: str, *, by_user_id: str | None, approved: bool) -> dict | None:
    """List this registration publicly, or take it off the page again."""
    with conn.transaction():
        if approved:
            conn.execute("UPDATE external_dashboard SET approved_at = now(), approved_by = %s::uuid WHERE id = %s::uuid", (by_user_id, ext_id))
        else:
            conn.execute("UPDATE external_dashboard SET approved_at = NULL, approved_by = NULL WHERE id = %s::uuid", (ext_id,))
    return get(conn, ext_id)


def list_pending(conn: psycopg.Connection) -> list[dict]:
    """Registrations over a public dataset that are waiting for a moderator."""
    rows = conn.execute(f"""SELECT {_cols("x")} FROM external_dashboard x JOIN dataset d ON d.id = x.dataset_id
                            WHERE x.approved_at IS NULL AND d.visibility = 'public' ORDER BY x.created_at""").fetchall()
    return [_row(r) for r in rows]


_NUM = re.compile(r"[-_/]?v(\d+)$")


def release_number(manifest: dict) -> int | None:
    for k in ("release_number", "release"):
        v = manifest.get(k)
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            m = _NUM.search(v.strip())
            if m:
                return int(m.group(1))
            if v.strip().isdigit():
                return int(v)
    return None


def candidates(ext: dict) -> list[str]:
    if ext.get("manifest_url"):
        return [ext["manifest_url"]]
    base = ext["url"].split("#")[0].split("?")[0]
    base = base[: base.rfind("/") + 1] if not base.endswith("/") and "." in base.rsplit("/", 1)[-1] else base.rstrip("/") + "/"
    return [base + "metalens.json", base + "data/config.json"]


def check(conn: psycopg.Connection, ext: dict, *, client: httpx.Client | None = None) -> dict:
    """Ask the page which release it shows; record the answer (or why there is none)."""
    close = client is None
    client = client or httpx.Client(timeout=10.0, follow_redirects=True)
    shown, note, about = None, "the page publishes no manifest (metalens.json or data/config.json)", {}
    try:
        for u in candidates(ext):
            if not valid_url(u):
                continue
            try:
                r = client.get(u, headers={"Accept": "application/json"})
            except httpx.HTTPError as exc:
                note = f"could not reach the page: {exc.__class__.__name__}"; continue
            if r.status_code != 200 or len(r.content) > 200_000:
                continue
            try:
                m = json.loads(r.content)
            except ValueError:
                note = "the manifest is not valid JSON"; continue
            shown = release_number(m) if isinstance(m, dict) else None
            note = "" if shown is not None else "the manifest names no release"
            if shown is not None:
                about = _about(m, u); break
    finally:
        if close:
            client.close()
    with conn.transaction():
        conn.execute("""UPDATE external_dashboard SET release_shown = %s, checked_at = now(), check_note = %s,
                               preview_url = COALESCE(%s, preview_url), description = COALESCE(%s, description), authors = COALESCE(%s, authors),
                               keywords = COALESCE(%s::jsonb, keywords)
                        WHERE id = %s::uuid""",
                     (shown, note or None, about.get("preview_url"), about.get("description"), about.get("authors"),
                      json.dumps(about["keywords"]) if about.get("keywords") else None, ext["id"]))
    return get(conn, ext["id"])


def _about(m: dict, manifest_url: str) -> dict:
    """What the manifest says about the page, for its tile: preview image (resolved against the
    manifest's location, http(s) only), one line of description, authors, keywords."""
    from urllib.parse import urljoin
    out = {}
    prev = m.get("preview")
    if isinstance(prev, str) and prev.strip():
        u = urljoin(manifest_url, prev.strip())
        if valid_url(u) and re.search(r"\.(png|jpe?g|webp|svg)(\?|$)", u, re.I):
            out["preview_url"] = u[:500]
    for k in ("description", "authors"):
        v = m.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = re.sub(r"\s+", " ", v).strip()[:300]
    kw = m.get("keywords")
    if isinstance(kw, str):
        kw = kw.split(",")
    if isinstance(kw, list):
        kw = [re.sub(r"\s+", " ", k).strip()[:40] for k in kw if isinstance(k, str) and k.strip()][:8]
        if kw:
            out["keywords"] = kw
    return out


def list_public(conn: psycopg.Connection) -> list[dict]:
    """Every registered dashboard over a public dataset, with the dataset it belongs to."""
    rows = conn.execute(
        """SELECT x.id::text, x.dataset_id::text, x.owner_user_id::text, x.title, x.url, x.repo_url, x.manifest_url, x.release_shown,
                  x.checked_at, x.check_note, x.created_at, x.preview_url, x.description, x.authors, x.keywords,
                  x.preview_override, x.approved_at, d.title, d.slug,
                  (SELECT max(number) FROM dataset_release r WHERE r.dataset_id = d.id), d.keywords,
                  (SELECT (r.stats->>'n_papers')::int FROM dataset_release r WHERE r.dataset_id = d.id AND r.number = x.release_shown)
           FROM external_dashboard x JOIN dataset d ON d.id = x.dataset_id
           WHERE d.visibility = 'public' AND x.approved_at IS NOT NULL ORDER BY x.created_at DESC""").fetchall()
    out = []
    for r in rows:
        item = _row(r[:17])
        item.update({"dataset_title": r[17], "dataset_slug": r[18], "latest_release": r[19], "n_papers": r[21],
                     "keywords": item["keywords"] or [k for k in (r[20] or []) if isinstance(k, str)]})   # the dataset's unless the page names its own
        out.append(item)
    return out
