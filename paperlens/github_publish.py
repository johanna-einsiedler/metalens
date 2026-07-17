"""Publish a dataset to the metalens-datasets GitHub repo as a pull request.

Materializes the dataset (exporter.py) into datasets/<slug>/metadata.json +
results.json on a fresh branch, opens a PR, and records the PR url on the dataset
(dataset.git_pr_url). The GitHub token lives ONLY in the server/worker env
(PAPERLENS_GITHUB_TOKEN) — never in a job payload or the browser. HTTP is injected
(an httpx.Client) so the flow is testable offline with a MockTransport, mirroring
enrich.py. Zenodo/DOI deposition is deferred — see the seam at the end.
"""
from __future__ import annotations

import base64
import json
import os

import httpx

from . import exporter, records

_API = "https://api.github.com"


def token() -> str | None:
    return os.environ.get("PAPERLENS_GITHUB_TOKEN") or None


def repo() -> str:
    return os.environ.get("PAPERLENS_DATASETS_REPO", "johanna-einsiedler/metalens-datasets")


def _headers() -> dict:
    return {"Authorization": f"Bearer {token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "metalens-publisher"}


def _ok(r: httpx.Response, action: str) -> httpx.Response:
    if r.status_code >= 300:
        raise RuntimeError(f"GitHub {action} failed ({r.status_code}): {r.text[:300]}")
    return r


def write_pr_url(conn, dataset_id: str, url: str) -> None:
    with conn.transaction():
        conn.execute("UPDATE dataset SET git_pr_url = %s WHERE id = %s::uuid", (url, dataset_id))


def _put_file(client, gh_repo, path, obj, branch, message):
    """Create-or-update one file on ``branch`` (needs the existing blob sha to update)."""
    url = f"{_API}/repos/{gh_repo}/contents/{path}"
    existing = client.get(url, params={"ref": branch}, headers=_headers(), timeout=30.0)
    sha = existing.json().get("sha") if existing.status_code == 200 else None
    body = {"message": message, "branch": branch,
            "content": base64.b64encode(json.dumps(obj, indent=2, default=str).encode()).decode()}
    if sha:
        body["sha"] = sha
    _ok(client.put(url, json=body, headers=_headers(), timeout=30.0), f"put {path}")


def publish_dataset(conn, dataset_id: str, *, client: httpx.Client | None = None,
                    branch: str | None = None) -> dict:
    """Publish the dataset as a PR. Returns {pr_url, branch}. Raises if unconfigured."""
    if not token():
        raise RuntimeError("PAPERLENS_GITHUB_TOKEN is not set — GitHub publishing is unavailable.")
    material = exporter.materialize_dataset(conn, dataset_id)
    ds = records.get_dataset(conn, dataset_id) or {}
    slug = ds.get("slug") or records._slugify(material["metadata"].get("title") or dataset_id)
    gh_repo = repo()
    branch = branch or f"metalens/{slug}-{dataset_id[:8]}"

    close = client is None
    client = client or httpx.Client()
    try:
        # 1) default branch + its head sha
        info = _ok(client.get(f"{_API}/repos/{gh_repo}", headers=_headers(), timeout=30.0),
                   "get repo").json()
        base = info.get("default_branch", "main")
        ref = _ok(client.get(f"{_API}/repos/{gh_repo}/git/ref/heads/{base}",
                             headers=_headers(), timeout=30.0), "get ref").json()
        base_sha = ref["object"]["sha"]

        # 2) create the working branch (ignore 422 = already exists → reuse it)
        r = client.post(f"{_API}/repos/{gh_repo}/git/refs", headers=_headers(), timeout=30.0,
                        json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        if r.status_code >= 300 and r.status_code != 422:
            _ok(r, "create branch")

        # 3) write the two files
        d = f"datasets/{slug}"
        _put_file(client, gh_repo, f"{d}/metadata.json", material["metadata"], branch,
                  f"metalens: {slug} metadata")
        _put_file(client, gh_repo, f"{d}/results.json", material["results"], branch,
                  f"metalens: {slug} results")

        # 4) open the PR
        pr = _ok(client.post(f"{_API}/repos/{gh_repo}/pulls", headers=_headers(), timeout=30.0,
                             json={"title": f"Add dataset: {material['metadata'].get('title') or slug}",
                                   "head": branch, "base": base,
                                   "body": "Published from Metalens.\n\n🤖 Generated with Metalens"}),
                 "open PR").json()
        pr_url = pr.get("html_url")
    finally:
        if close:
            client.close()

    if pr_url:
        write_pr_url(conn, dataset_id, pr_url)
    return {"pr_url": pr_url, "branch": branch}


# ── Zenodo seam (deferred) ───────────────────────────────────────────────────────
# A future deposit_to_zenodo(conn, dataset_id, client=None) would create a Zenodo
# deposition, upload results.json, publish → mint a DOI, and write it back onto the
# dataset (a new dataset.zenodo_doi column). Not implemented this round.
