"""Releases of a curated workflow: frozen, numbered, citable versions of the declaration.

A workflow's CONTRACT is content-addressed (``econ-regressions@1508e540``: the steps, their
pins and their review declarations); its wording, its card and a derive prompt's text are not,
so a card page needs the other half — a version a person can cite, with a date, release notes
and a DOI. A curated workflow (one with a card) gets releases of its own, independent of any
dataset built with it. A release freezes the whole normalised document and, per extract step,
the preset release its pinned contract matched; ``workflow_bundle`` turns it into the files a
deposit holds and ``zenodo.deposit_workflow`` mints the DOI. Cutting a release is a moderator's
act, as for presets: the workflows are bundled files, so the person who ships the commit says
when a version is worth a number.
"""
from __future__ import annotations

import datetime as _dt
import uuid

import psycopg
from psycopg.types.json import Json

from . import preset_spec as ps
from . import records
from . import workflow_spec as ws

_COLS = ("id::text, workflow_id, number, created_at, created_by::text, notes, contract, spec, spec_sha, presets, engine, "
         "doi, zenodo_record_id, zenodo_url, zenodo_concept_doi, doi_minted_at, doi_minted_by::text")


def _row(r) -> dict:
    return {"id": r[0], "workflow_id": r[1], "number": r[2], "created_at": r[3].isoformat(timespec="seconds") if r[3] else None,
            "created_by": r[4], "notes": r[5], "contract": r[6], "spec": r[7], "spec_sha": r[8], "presets": r[9] or [], "engine": r[10],
            "doi": r[11], "zenodo_record_id": r[12], "zenodo_url": r[13], "zenodo_concept_doi": r[14],
            "doi_minted_at": r[15].isoformat(timespec="seconds") if r[15] else None, "doi_minted_by": r[16]}


spec_sha = ws.spec_sha


def live(workflow_id: str) -> dict | None:
    """The bundled workflow as currently shipped — only for curated ones (those with a card)."""
    from . import workflows
    spec = workflows.get_workflow(workflow_id)
    return spec if spec is not None and ws.has_card(spec) else None


def get(conn: psycopg.Connection, release_id: str) -> dict | None:
    if not records._is_uuid(release_id):   # noqa: SLF001
        return None
    r = conn.execute(f"SELECT {_COLS} FROM workflow_release WHERE id = %s::uuid", (release_id,)).fetchone()
    return _row(r) if r else None


def list_for_workflow(conn: psycopg.Connection, workflow_id: str) -> list[dict]:
    return [_row(r) for r in conn.execute(f"SELECT {_COLS} FROM workflow_release WHERE workflow_id = %s ORDER BY number DESC",
                                          (workflow_id.partition("@")[0],)).fetchall()]


def get_by_number(conn: psycopg.Connection, workflow_id: str, number: int) -> dict | None:
    r = conn.execute(f"SELECT {_COLS} FROM workflow_release WHERE workflow_id = %s AND number = %s",
                     (workflow_id.partition("@")[0], number)).fetchone()
    return _row(r) if r else None


def latest(conn: psycopg.Connection, workflow_id: str) -> dict | None:
    r = conn.execute(f"SELECT {_COLS} FROM workflow_release WHERE workflow_id = %s ORDER BY number DESC LIMIT 1",
                     (workflow_id.partition("@")[0],)).fetchone()
    return _row(r) if r else None


def by_contract(conn: psycopg.Connection, contract: str) -> list[dict]:
    """Every release whose frozen contract is ``contract`` (oldest first)."""
    return [_row(r) for r in conn.execute(f"SELECT {_COLS} FROM workflow_release WHERE contract = %s ORDER BY number", (contract,)).fetchall()]


def changed_since(spec: dict, rel: dict | None) -> bool:
    return rel is None or spec_sha(spec) != rel["spec_sha"]


def pinned_presets(conn: psycopg.Connection, spec: dict) -> list[dict]:
    """Per extract step: the pinned contract, the preset release it matches (the newest whose
    frozen contract is the pin) and the preset's concept DOI."""
    from . import preset_releases
    out = []
    for step_id, pin in ws.pins(spec):
        base = ws.preset_base(pin)
        try:
            rels = preset_releases.by_schema(conn, base, pin)
            concept = preset_releases.concept_doi(conn, base)
        except Exception:  # noqa: BLE001 - a preset without releases is fine
            rels, concept = [], None
        out.append({"step": step_id, "preset_id": base, "schema_id": pin, "preset_release": rels[-1]["number"] if rels else None,
                    "concept_doi": concept})
    return out


def create(conn: psycopg.Connection, workflow_id: str, *, notes: str | None = None, created_by: str | None = None) -> dict:
    """Cut the next release of a curated workflow as shipped now. LookupError for a workflow
    without a card, ValueError when nothing changed since the latest release."""
    from . import __version__, exporter, workflows
    wid = workflow_id.partition("@")[0]
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"workflow-release:{wid}",))
        spec = live(wid)
        if spec is None:
            raise LookupError(f"{workflow_id!r} is not a curated workflow (no card).")
        errs, _w = workflows.validate_for(conn, spec)
        if errs:
            raise ValueError("The workflow does not validate here: " + "; ".join(errs[:3]))
        last = latest(conn, wid)
        if not changed_since(spec, last):
            raise ValueError(f"Nothing changed since v{last['number']}.")
        for _step, pin in ws.pins(spec):                       # the pinned contracts must exist as rows
            pspec = workflows.preset_for_pin(conn, pin)
            if pspec is not None:
                records.upsert_schema(conn, pin, ps.field_defs_for(pspec))
        rid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO workflow_release (id, workflow_id, number, created_by, notes, contract, spec, spec_sha, presets, engine)
               VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (rid, wid, (last["number"] + 1) if last else 1, created_by if records._is_uuid(created_by or "") else None,   # noqa: SLF001
             (notes or "").strip()[:4000] or None, ws.workflow_id(spec), Json(spec), spec_sha(spec), Json(pinned_presets(conn, spec)),
             Json({"name": "metalens", "version": __version__, "git_sha": exporter._git_sha()})))   # noqa: SLF001
    return get(conn, rid)


def concept_doi(conn: psycopg.Connection, workflow_id: str) -> str | None:
    from . import zenodo
    rows = conn.execute("SELECT doi, zenodo_concept_doi FROM workflow_release WHERE workflow_id = %s AND zenodo_concept_doi IS NOT NULL ORDER BY number DESC",
                        (workflow_id.partition("@")[0],)).fetchall()
    return next((r[1] for r in rows if zenodo.counts_here(r[0])), None)


def public_row(rel: dict) -> dict:
    from . import zenodo
    counts = zenodo.counts_here(rel.get("doi"))
    return {"number": rel["number"], "created_at": rel["created_at"], "notes": rel.get("notes"), "contract": rel["contract"],
            "spec_sha": rel["spec_sha"], "presets": rel.get("presets") or [], "engine": rel.get("engine"),
            "doi": rel.get("doi") if counts else None, "zenodo_url": rel.get("zenodo_url") if counts else None}


def citation(spec: dict, rel: dict | None, *, concept_doi: str | None, url: str) -> str:
    """How to cite the workflow: the concept DOI when one exists, else the card page; with a
    release the version is named, without one only the year."""
    who = ps.authors_apa((spec.get("meta") or {}).get("authors")) or "[authors]"
    year = (rel or {}).get("created_at", "")[:4] if rel else ""
    year = year or str(_dt.date.today().year)
    title = (spec.get("meta") or {}).get("title") or spec["id"]
    version = f" (version {rel['number']})" if rel else ""
    where = f"https://doi.org/{concept_doi}" if concept_doi else url
    return f"{who} ({year}). {title}: Metalens workflow{version} [Software]. Metalens. {where}"


def citation_for_version(spec: dict, rel: dict, *, url: str) -> str:
    from . import zenodo
    doi = rel.get("doi") if zenodo.counts_here(rel.get("doi")) else None
    return citation(spec, rel, concept_doi=doi, url=url)
