"""The datasets repository is the source of truth for published datasets.

``sync`` reads ``datasets/<slug>/`` on the repo's default branch and reconciles the
catalogue with it:

* a local dataset whose slug is on the branch (its pull request was merged) is marked
  ``published`` — from then on its citation and catalogue entry point at the GitHub copy;
* a folder with no local dataset (a PR made by hand, or files edited on GitHub) is IMPORTED
  as a public, read-only dataset (``github_source``): metadata, preset spec, records and
  evidence quotes, but no PDFs or page images since the repo holds none. Its badge is the
  one recorded in ``metadata.json`` (the verification happened before publication);
* an imported dataset whose ``results.json`` changed on GitHub is re-imported.

Runs hourly from the worker, on demand from the dataset page, and never needs a token for a
public repository (one is used when present, for the rate limit). HTTP is injected so the
flow is testable with a MockTransport, like ``github_publish``.
"""
from __future__ import annotations

import base64
import json

import httpx

from . import preset_spec, records
from .github_publish import _API, _headers, repo, token
from .ingest import ingest


def _hdrs() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
         "User-Agent": "metalens-sync"}
    if token():
        h.update(_headers())
    return h


def _get(client: httpx.Client, url: str, **kw) -> httpx.Response:
    r = client.get(url, headers=_hdrs(), timeout=30.0, **kw)
    if r.status_code >= 300:
        raise RuntimeError(f"GitHub sync: GET {url.split('/repos/')[-1]} → {r.status_code}: {r.text[:200]}")
    return r


def _read_file(client: httpx.Client, gh_repo: str, path: str, ref: str) -> tuple[bytes, str]:
    """(bytes, blob sha). The contents API inlines files up to 1 MB; larger ones come through
    the blob API by sha."""
    meta = _get(client, f"{_API}/repos/{gh_repo}/contents/{path}", params={"ref": ref}).json()
    sha = meta.get("sha", "")
    if meta.get("content"):
        return base64.b64decode(meta["content"]), sha
    blob = _get(client, f"{_API}/repos/{gh_repo}/git/blobs/{sha}").json()
    return base64.b64decode(blob.get("content", "")), sha


def list_published(client: httpx.Client, gh_repo: str | None = None) -> list[dict]:
    """Every dataset folder on the default branch: [{slug, metadata, results_sha}]."""
    gh_repo = gh_repo or repo()
    info = _get(client, f"{_API}/repos/{gh_repo}").json()
    ref = info.get("default_branch", "main")
    r = client.get(f"{_API}/repos/{gh_repo}/contents/datasets", headers=_hdrs(), params={"ref": ref}, timeout=30.0)
    if r.status_code == 404:
        return []
    if r.status_code >= 300:
        raise RuntimeError(f"GitHub sync: listing datasets → {r.status_code}")
    out = []
    for entry in r.json():
        if entry.get("type") != "dir":
            continue
        slug = entry["name"]
        try:
            meta_bytes, _ = _read_file(client, gh_repo, f"datasets/{slug}/metadata.json", ref)
            metadata = json.loads(meta_bytes.decode("utf-8"))
        except Exception:                                   # noqa: BLE001 - not a dataset folder
            continue
        rmeta = client.get(f"{_API}/repos/{gh_repo}/contents/datasets/{slug}/results.json",
                           headers=_hdrs(), params={"ref": ref}, timeout=30.0)
        results_sha = rmeta.json().get("sha") if rmeta.status_code == 200 else None
        out.append({"slug": slug, "metadata": metadata, "results_sha": results_sha, "ref": ref})
    return out


def _import_records(conn, client: httpx.Client, gh_repo: str, entry: dict, dataset_id: str,
                    schema_id: str | None, entries_key: str | None) -> int:
    raw, _ = _read_file(client, gh_repo, f"datasets/{entry['slug']}/results.json", entry["ref"])
    results = json.loads(raw.decode("utf-8"))
    n = 0
    for paper in results.get("papers") or []:
        result = paper.get("result") if isinstance(paper, dict) else None
        if not isinstance(result, dict):
            continue
        try:
            res = ingest(result, entries_key=entries_key)
        except Exception:                                   # noqa: BLE001 - skip an unreadable paper
            continue
        doc = records.persist(conn, res, schema_id=schema_id, source_job_id=f"github:{entry['slug']}",
                              filename=paper.get("filename"))
        records.assign_document_to_dataset(conn, dataset_id, doc)
        n += 1
    return n


def _schema_for(conn, metadata: dict) -> tuple[str | None, str | None]:
    """The schema row the imported records belong to: the preset spec that travelled with
    the dataset (registered here if unknown), else the bare id. Returns (schema_id, entries_key)."""
    preset = metadata.get("preset") or {}
    schema_id = metadata.get("schema_id") or preset.get("schema_id")
    spec = preset.get("spec")
    if spec:
        try:
            spec = preset_spec.normalize(spec)
            with conn.transaction():
                records.upsert_schema(conn, schema_id or preset_spec.schema_id(spec), preset_spec.field_defs_for(spec))
            return schema_id or preset_spec.schema_id(spec), preset_spec.entries_key(spec)
        except Exception:                                   # noqa: BLE001 - fall through to the bare id
            pass
    if schema_id:
        with conn.transaction():
            records.upsert_schema(conn, schema_id)
    return schema_id, None


def sync(conn, *, client: httpx.Client | None = None, gh_repo: str | None = None) -> dict:
    """Reconcile the catalogue with the datasets repository. Returns counts."""
    gh_repo = gh_repo or repo()
    close = client is None
    client = client or httpx.Client()
    out = {"seen": 0, "published": 0, "imported": 0, "reimported": 0, "unchanged": 0}
    try:
        for entry in list_published(client, gh_repo):
            out["seen"] += 1
            local = records.dataset_by_slug(conn, entry["slug"])
            if local and not local.get("github_source"):
                if local.get("publish_status") != "published" or local.get("published_file_sha") != entry["results_sha"]:
                    records.mark_published(conn, local["id"], file_sha=entry["results_sha"], meta=entry["metadata"])
                    out["published"] += 1
                else:
                    out["unchanged"] += 1
                continue
            if local and local.get("published_file_sha") == entry["results_sha"]:
                out["unchanged"] += 1
                continue
            md = entry["metadata"]
            schema_id, entries_key = _schema_for(conn, md)
            if local:                                       # GitHub-only dataset whose files changed
                records.clear_dataset_documents(conn, local["id"])
                ds_id = local["id"]
                out["reimported"] += 1
            else:
                ds = records.create_dataset(conn, title=md.get("title") or entry["slug"], description=md.get("description"),
                                            schema_id=schema_id, visibility="public", prompt=(md.get("recipe") or {}).get("prompt"),
                                            model=(md.get("recipe") or {}).get("model"), slug=entry["slug"], github_source=True)
                ds_id = ds["id"]
                out["imported"] += 1
            records.update_dataset_meta(conn, ds_id, readme=md.get("readme"), keywords=md.get("keywords") or [],
                                        attribution=md.get("attribution") or "named", citation=md.get("citation"))
            _import_records(conn, client, gh_repo, entry, ds_id, schema_id, entries_key)
            records.mark_published(conn, ds_id, file_sha=entry["results_sha"], meta=md)
    finally:
        if close:
            client.close()
    return out
