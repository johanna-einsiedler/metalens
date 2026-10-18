"""Releases of a curated preset: frozen, numbered, citable versions of the declaration.

A preset's DATA CONTRACT is content-addressed (``masem-direct@eb5f0287``): the hash moves when
the fields move and stays put when only wording, prompt or documentation change. A card page
needs the other half — a version a person can cite, with a date, release notes and a DOI —
so a curated preset (one with a card, ``preset_spec.has_card``) gets releases of its own,
independent of any dataset built with it. A release freezes the whole normalised spec, prompt
and card text inlined, next to the schema row it hashes to; ``preset_bundle`` turns it into
the files a deposit holds and ``zenodo.deposit_preset`` mints the DOI.

Cutting a release is a moderator's act (``admins``): the presets are bundled files, so
"the preset changed" means "a commit changed it", and the person who ships the commit is
the one who says when a version is worth a number.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import uuid

import psycopg
from psycopg.types.json import Json

from . import preset_spec as ps, presets, records

_COLS = ("id::text, preset_id, number, created_at, created_by::text, notes, schema_id, spec, spec_sha, prompt_sha256, "
         "engine, doi, zenodo_record_id, zenodo_url, zenodo_concept_doi, doi_minted_at, doi_minted_by::text")


def _row(r) -> dict:
    return {"id": r[0], "preset_id": r[1], "number": r[2],
            "created_at": r[3].isoformat(timespec="seconds") if r[3] else None, "created_by": r[4], "notes": r[5],
            "schema_id": r[6], "spec": r[7], "spec_sha": r[8], "prompt_sha256": r[9], "engine": r[10],
            "doi": r[11], "zenodo_record_id": r[12], "zenodo_url": r[13], "zenodo_concept_doi": r[14],
            "doi_minted_at": r[15].isoformat(timespec="seconds") if r[15] else None, "doi_minted_by": r[16]}


def spec_sha(spec: dict) -> str:
    """One hash over the whole normalised spec (prompt and card inlined): has anything changed?"""
    return hashlib.sha256(ps.canonical_json(spec).encode("utf-8")).hexdigest()


def live(preset_id: str) -> dict | None:
    """The bundled spec as currently shipped — only for curated presets (those with a card)."""
    spec = presets.load_all().get(presets.resolve_id(preset_id))
    return spec if spec is not None and ps.has_card(spec) else None


def get(conn: psycopg.Connection, release_id: str) -> dict | None:
    if not records._is_uuid(release_id):   # noqa: SLF001
        return None
    r = conn.execute(f"SELECT {_COLS} FROM preset_release WHERE id = %s::uuid", (release_id,)).fetchone()
    return _row(r) if r else None


def list_for_preset(conn: psycopg.Connection, preset_id: str) -> list[dict]:
    return [_row(r) for r in conn.execute(
        f"SELECT {_COLS} FROM preset_release WHERE preset_id = %s ORDER BY number DESC", (presets.resolve_id(preset_id),)).fetchall()]


def get_by_number(conn: psycopg.Connection, preset_id: str, number: int) -> dict | None:
    r = conn.execute(f"SELECT {_COLS} FROM preset_release WHERE preset_id = %s AND number = %s",
                     (presets.resolve_id(preset_id), number)).fetchone()
    return _row(r) if r else None


def latest(conn: psycopg.Connection, preset_id: str) -> dict | None:
    r = conn.execute(f"SELECT {_COLS} FROM preset_release WHERE preset_id = %s ORDER BY number DESC LIMIT 1",
                     (presets.resolve_id(preset_id),)).fetchone()
    return _row(r) if r else None


def by_schema(conn: psycopg.Connection, preset_id: str, schema_id: str) -> list[dict]:
    """Every release of the preset whose frozen contract is ``schema_id`` (oldest first)."""
    return [_row(r) for r in conn.execute(
        f"SELECT {_COLS} FROM preset_release WHERE preset_id = %s AND schema_id = %s ORDER BY number",
        (presets.resolve_id(preset_id), schema_id)).fetchall()]


def changed_since(spec: dict, rel: dict | None) -> bool:
    """Does the shipped spec differ from this release in any way (fields, wording, prompt, card)?"""
    return rel is None or spec_sha(spec) != rel["spec_sha"]


def create(conn: psycopg.Connection, preset_id: str, *, notes: str | None = None, created_by: str | None = None) -> dict:
    """Cut the next release of a curated preset as it is shipped now. Raises LookupError for a
    preset without a card and ValueError when nothing changed since the latest release."""
    from . import __version__, exporter
    pid = presets.resolve_id(preset_id)
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"preset-release:{pid}",))
        spec = live(pid)
        if spec is None:
            raise LookupError(f"{preset_id!r} is not a curated preset (no card).")
        last = latest(conn, pid)
        if not changed_since(spec, last):
            raise ValueError(f"Nothing changed since v{last['number']}.")
        sid = ps.schema_id(spec)
        records.upsert_schema(conn, sid, ps.field_defs_for(spec))     # the FK target; immutable once there
        prompt_sha = (hashlib.sha256(ps.render_prompt(spec).encode("utf-8")).hexdigest()
                      if (spec.get("meta") or {}).get("mode") != "import" else None)
        rid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO preset_release (id, preset_id, number, created_by, notes, schema_id, spec, spec_sha, prompt_sha256, engine)
               VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (rid, pid, (last["number"] + 1) if last else 1,
             created_by if records._is_uuid(created_by or "") else None,   # noqa: SLF001
             (notes or "").strip()[:4000] or None, sid, Json(spec), spec_sha(spec), prompt_sha,
             Json({"name": "metalens", "version": __version__, "git_sha": exporter._git_sha()})))   # noqa: SLF001
    return get(conn, rid)


def concept_doi(conn: psycopg.Connection, preset_id: str) -> str | None:
    """The lineage's concept DOI: what the newest deposit that counts on this server reported."""
    from . import zenodo
    rows = conn.execute("SELECT doi, zenodo_concept_doi FROM preset_release WHERE preset_id = %s AND zenodo_concept_doi IS NOT NULL ORDER BY number DESC",
                        (presets.resolve_id(preset_id),)).fetchall()
    return next((r[1] for r in rows if zenodo.counts_here(r[0])), None)


def public_row(rel: dict) -> dict:
    """What anyone may know about a release; a DOI from the other Zenodo (sandbox vs real) is hidden."""
    from . import zenodo
    counts = zenodo.counts_here(rel.get("doi"))
    return {"number": rel["number"], "created_at": rel["created_at"], "notes": rel.get("notes"), "schema_id": rel["schema_id"],
            "spec_sha": rel["spec_sha"], "prompt_sha256": rel.get("prompt_sha256"), "engine": rel.get("engine"),
            "doi": rel.get("doi") if counts else None, "zenodo_url": rel.get("zenodo_url") if counts else None}


def _what(spec: dict) -> str:
    return "import schema" if (spec.get("meta") or {}).get("mode") == "import" else "extraction preset"


def citation(spec: dict, rel: dict | None, *, concept_doi: str | None, url: str) -> str:
    """How to cite the preset: the concept DOI when one exists (it follows the newest version),
    else the card page; with a release the version is named, without one only the year."""
    who = ps.authors_apa((spec.get("meta") or {}).get("authors")) or "[authors]"
    year = (rel or {}).get("created_at", "")[:4] if rel else ""
    year = year or str(_dt.date.today().year)
    title = (spec.get("meta") or {}).get("title") or spec["id"]
    version = f" (version {rel['number']})" if rel else ""
    where = f"https://doi.org/{concept_doi}" if concept_doi else url
    return f"{who} ({year}). {title}: {_what(spec)}{version} [Software]. Metalens. {where}"


def citation_for_version(spec: dict, rel: dict, *, url: str) -> str:
    """The same line pinned to one version: its own DOI when it has one that counts here."""
    from . import zenodo
    doi = rel.get("doi") if zenodo.counts_here(rel.get("doi")) else None
    return citation(spec, rel, concept_doi=doi, url=url)
