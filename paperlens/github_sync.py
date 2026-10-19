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

Runs daily from the worker (and when it starts), on demand from the dataset page, and never
needs a token for a public repository (one is used when present, for the rate limit). A run
costs ONE request when the repository has no new commit since the last run; otherwise one more
request lists every file with its version, and only the folders that changed are read. HTTP is
injected so the flow is testable with a MockTransport, like ``github_publish``.
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


def head_commit(client: httpx.Client, gh_repo: str) -> str:
    """The newest commit on the repository's default branch: one request."""
    commits = _get(client, f"{_API}/repos/{gh_repo}/commits", params={"per_page": 1}).json()
    if not commits:
        raise RuntimeError("GitHub sync: the datasets repository has no commits")
    return commits[0]["sha"]


def list_published(client: httpx.Client, gh_repo: str | None = None, ref: str | None = None) -> list[dict]:
    """Every dataset folder at ``ref`` (default: the newest commit): [{slug, results_sha,
    metadata_sha, ref}], from ONE request that lists the whole repository with each file's
    version. ``metadata`` is read later, and only for the folders that need it."""
    gh_repo = gh_repo or repo()
    ref = ref or head_commit(client, gh_repo)
    tree = _get(client, f"{_API}/repos/{gh_repo}/git/trees/{ref}", params={"recursive": "1"}).json()
    if tree.get("truncated"):                              # >100,000 files: the listing is incomplete
        raise RuntimeError("GitHub sync: the repository listing was truncated")
    folders: dict[str, dict] = {}
    for item in tree.get("tree") or []:
        parts = (item.get("path") or "").split("/")
        if item.get("type") == "blob" and len(parts) == 3 and parts[0] == "datasets" and parts[2] in ("metadata.json", "results.json"):
            folders.setdefault(parts[1], {})[parts[2]] = item.get("sha")
    return [{"slug": slug, "results_sha": f.get("results.json"), "metadata_sha": f["metadata.json"], "ref": ref}
            for slug, f in sorted(folders.items()) if f.get("metadata.json")]     # no metadata.json: not a dataset folder


def _metadata(client: httpx.Client, gh_repo: str, entry: dict) -> dict | None:
    try:
        raw, _ = _read_file(client, gh_repo, f"datasets/{entry['slug']}/metadata.json", entry["ref"])
        return json.loads(raw.decode("utf-8"))
    except Exception:                                       # noqa: BLE001 - an unreadable folder is skipped
        return None


_STATE_KEY = "github_sync:head"


def _last_head(conn) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = %s", (_STATE_KEY,)).fetchone()
    return row[0] if row else None


def _remember_head(conn, value: str) -> None:
    with conn.transaction():
        conn.execute("""INSERT INTO sync_state (key, value) VALUES (%s, %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (_STATE_KEY, value))


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


def sync(conn, *, client: httpx.Client | None = None, gh_repo: str | None = None, force: bool = False) -> dict:
    """Reconcile the catalogue with the datasets repository. Returns counts. Unless ``force``,
    a repository whose newest commit is the one reconciled last time is not read any further."""
    gh_repo = gh_repo or repo()
    close = client is None
    client = client or httpx.Client()
    out = {"seen": 0, "published": 0, "imported": 0, "reimported": 0, "unchanged": 0}
    try:
        head = head_commit(client, gh_repo)
        state = f"{gh_repo}@{head}"
        if not force and _last_head(conn) == state:
            return {**out, "skipped": "no new commit", "head": head}
        for entry in list_published(client, gh_repo, head):
            out["seen"] += 1
            local = records.dataset_by_slug(conn, entry["slug"])
            if local and not local.get("github_source"):
                if local.get("publish_status") != "published" or local.get("published_file_sha") != entry["results_sha"]:
                    md = _metadata(client, gh_repo, entry)
                    if md is None:
                        continue
                    records.mark_published(conn, local["id"], file_sha=entry["results_sha"], meta=md)
                    out["published"] += 1
                else:
                    out["unchanged"] += 1
                continue
            if local and local.get("published_file_sha") == entry["results_sha"]:
                out["unchanged"] += 1
                continue
            md = _metadata(client, gh_repo, entry)
            if md is None:
                continue
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
        _remember_head(conn, state)
        out["head"] = head
    finally:
        if close:
            client.close()
    return out
