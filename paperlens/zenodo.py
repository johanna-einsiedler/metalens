"""A DOI for a dataset release, minted on Zenodo.

One Zenodo record per dataset; every release deposited becomes a new *version* of it, so a release
has its own DOI (citable, frozen) and the dataset a concept DOI that always resolves to the newest
version. The files deposited are the release's static export (release.json, the tables, the
evidence, a README) — the same files the datasets repository holds — and release.json names the
DOI it was given, because Zenodo reserves the DOI before the files are uploaded.

Configuration (environment)
  PAPERLENS_ZENODO_TOKEN     personal access token with ``deposit:write`` and ``deposit:actions``
  PAPERLENS_ZENODO_SANDBOX   truthy → sandbox.zenodo.org (test DOIs that resolve nowhere)

Minting is opt-in per release (a DOI is permanent) and owner-only. Nothing here is queued: the
files are small and the whole exchange takes a few seconds.
"""
from __future__ import annotations

import html
import os

import httpx

from . import records, release_export, releases

_PROD = "https://zenodo.org/api"
_SANDBOX = "https://sandbox.zenodo.org/api"


def token() -> str | None:
    return os.environ.get("PAPERLENS_ZENODO_TOKEN") or None


def sandbox() -> bool:
    return (os.environ.get("PAPERLENS_ZENODO_SANDBOX") or "").strip().lower() in ("1", "true", "yes", "on")


def configured() -> bool:
    return bool(token())


def status() -> dict:
    """What the dataset page shows: whether a DOI can be minted here, and whether it would be a test one."""
    return {"configured": configured(), "sandbox": sandbox()}


def base_url() -> str:
    return _SANDBOX if sandbox() else _PROD


def is_test_doi(doi: str | None) -> bool:
    """Sandbox DOIs carry the 10.5072 prefix; they resolve nowhere and never count as real."""
    return bool(doi) and doi.startswith("10.5072/")


def counts_here(doi: str | None) -> bool:
    """Does this DOI belong to the Zenodo this server talks to? A sandbox DOI is ignored once the
    server is on the real Zenodo (and vice versa), so a release can be minted again after the switch."""
    return bool(doi) and is_test_doi(doi) == sandbox()


def _headers() -> dict:
    return {"Authorization": f"Bearer {token()}", "Accept": "application/json"}


def _ok(r: httpx.Response, step: str) -> httpx.Response:
    """Zenodo explains a refusal in its JSON body; surface that instead of the bare status."""
    if r.is_success:
        return r
    try:
        body = r.json()
        msg = body.get("message") or "; ".join(f"{e.get('field')}: {e.get('message')}" for e in body.get("errors") or []) or str(body)[:300]
    except ValueError:
        msg = (r.text or "")[:300]
    raise RuntimeError(f"Zenodo {step}: HTTP {r.status_code} — {msg}")


def metadata(ds: dict, rel: dict, *, github_url: str | None) -> dict:
    """The record's metadata, from the publishing details the owner already fills in."""
    anonymous = ds.get("attribution") == "anonymous"
    name = (ds.get("owner_citation_name") or "").strip()
    changes = rel.get("changes") or {}
    what = []
    if changes.get("first"):
        what.append("First release.")
    else:
        for key, word in (("papers_added", "added"), ("papers_removed", "removed")):
            n = len(changes.get(key) or [])
            if n:
                what.append(f"{n} paper{'s' if n != 1 else ''} {word}.")
    desc = [html.escape(ds.get("description") or ""), html.escape(rel.get("notes") or ""), " ".join(what),
            f"Release v{rel['number']} of a Metalens dataset: {changes.get('n_papers') or (rel.get('stats') or {}).get('n_papers') or '?'} papers, "
            f"{(rel.get('credibility') or {}).get('label') or 'unverified'}. Every value is tied to the passage of the paper it was "
            f"read from (evidence.json); release.json describes the tables. Content sha256 {rel['content_sha']}."]
    related = []
    if github_url:
        related.append({"identifier": f"{github_url}/releases/v{rel['number']}", "relation": "isIdenticalTo", "scheme": "url"})
    meta = {"title": f"{ds.get('title') or ds.get('slug') or 'Dataset'} — release v{rel['number']}",
            "upload_type": "dataset",
            "description": "<p>" + "</p><p>".join(p for p in desc if p.strip()) + "</p>",
            "creators": [{"name": "Anonymous"}] if anonymous or not name else [{"name": name}],
            "version": f"v{rel['number']}",
            "publication_date": str(rel.get("created_at") or "")[:10] or None,
            "access_right": "open", "license": "cc-by-4.0",
            "keywords": [k for k in (ds.get("keywords") or []) if isinstance(k, str)] + ["Metalens"]}
    if related:
        meta["related_identifiers"] = related
    return {"metadata": {k: v for k, v in meta.items() if v is not None}}


def _prior(conn, dataset_id: str) -> dict | None:
    rows = conn.execute("SELECT zenodo_record_id, doi FROM dataset_release WHERE dataset_id = %s::uuid AND zenodo_record_id IS NOT NULL "
                        "ORDER BY number DESC", (dataset_id,)).fetchall()
    return next(({"record_id": r[0]} for r in rows if counts_here(r[1])), None)


def deposit(conn, release: dict, *, client: httpx.Client | None = None) -> dict:
    """Mint the DOI for one release (a releases row; the snapshot is loaded here). Returns the
    release row with ``doi``, ``zenodo_record_id`` and ``zenodo_url`` filled in. Raises when Zenodo
    is not configured, the release already has a DOI, or Zenodo refuses a step."""
    if not configured():
        raise RuntimeError("Zenodo is not configured on this server (PAPERLENS_ZENODO_TOKEN).")
    if counts_here(release.get("doi")):
        raise RuntimeError(f"Release v{release['number']} already has a DOI: {release['doi']}.")
    ds = records.get_dataset(conn, release["dataset_id"]) or {}
    base = base_url()
    close = client is None
    client = client or httpx.Client(timeout=60.0)
    try:
        prior = _prior(conn, release["dataset_id"])
        if prior:                                     # the next version of the dataset's record
            r = _ok(client.post(f"{base}/deposit/depositions/{prior['record_id']}/actions/newversion", headers=_headers()), "new version")
            draft_url = (r.json().get("links") or {}).get("latest_draft")
            if not draft_url:
                raise RuntimeError("Zenodo new version: no draft link in the reply.")
            draft = _ok(client.get(draft_url, headers=_headers()), "read draft").json()
            for f in draft.get("files") or []:        # the previous version's files come along; this release brings its own
                _ok(client.delete(f"{base}/deposit/depositions/{draft['id']}/files/{f['id']}", headers=_headers()), "drop copied file")
        else:
            draft = _ok(client.post(f"{base}/deposit/depositions", headers=_headers(), json={}), "create deposition").json()
        doi = ((draft.get("metadata") or {}).get("prereserve_doi") or {}).get("doi")
        bucket = (draft.get("links") or {}).get("bucket")
        if not doi or not bucket:
            raise RuntimeError("Zenodo deposition: no reserved DOI or file bucket in the reply.")
        full = releases.get(conn, release["id"], with_snapshot=True)
        full["doi"] = doi
        files = release_export.build(conn, full)
        for path, content in sorted(files.items()):
            _ok(client.put(f"{bucket}/{uploaded_name(path)}", headers={**_headers(), "Content-Type": "application/octet-stream"}, content=content),
                f"upload {path}")
        _ok(client.put(f"{base}/deposit/depositions/{draft['id']}", headers=_headers(), json=metadata(ds, full, github_url=ds.get("published_url"))), "metadata")
        rec = _ok(client.post(f"{base}/deposit/depositions/{draft['id']}/actions/publish", headers=_headers()), "publish").json()
    finally:
        if close:
            client.close()
    doi = rec.get("doi") or doi
    links = rec.get("links") or {}
    url = links.get("record_html") or links.get("html") or f"https://doi.org/{doi}"     # the public record page, not the deposit form
    with conn.transaction():
        conn.execute("UPDATE dataset_release SET doi = %s, zenodo_record_id = %s, zenodo_url = %s WHERE id = %s::uuid",
                     (doi, rec.get("id") or draft["id"], url, release["id"]))
        if rec.get("conceptdoi"):
            conn.execute("UPDATE dataset SET zenodo_concept_doi = %s WHERE id = %s::uuid", (rec["conceptdoi"], release["dataset_id"]))
    return releases.get(conn, release["id"])


def uploaded_name(path: str) -> str:
    """Zenodo buckets are flat: tables/entries.json is stored as tables__entries.json."""
    return path.replace("/", "__")

