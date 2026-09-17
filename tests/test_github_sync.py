"""The datasets repository is the source of truth: `github_sync.sync` marks a local dataset
published once its folder is on the default branch, imports GitHub-only datasets as
read-only public datasets with their records and as-published badge, and re-imports one
whose results.json changed. GitHub is a MockTransport; skips without Postgres.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx  # noqa: E402

import fixtures  # noqa: E402
from paperlens import app as appmod, github_sync, presets, records  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _b64(obj) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def _repo(files: dict) -> httpx.Client:
    """A fake datasets repo: files = {"datasets/<slug>/metadata.json": (obj, sha), …}."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if request.method == "GET" and p.endswith("/metalens-datasets"):
            return httpx.Response(200, json={"default_branch": "main"})
        if request.method == "GET" and p.endswith("/contents/datasets"):
            slugs = sorted({k.split("/")[1] for k in files})
            return httpx.Response(200, json=[{"type": "dir", "name": s} for s in slugs])
        if request.method == "GET" and "/contents/" in p:
            key = p.split("/contents/")[1]
            if key not in files:
                return httpx.Response(404, json={})
            obj, sha = files[key]
            return httpx.Response(200, json={"sha": sha, "content": _b64(obj), "encoding": "base64"})
        return httpx.Response(500, json={"path": p})
    return httpx.Client(transport=httpx.MockTransport(handler))


def _material(slug: str, title: str, n_papers: int = 1, badge: str = "human_verified") -> tuple[dict, dict]:
    spec = presets.load_all()["masem-direct"]
    metadata = {"title": title, "slug": slug, "description": "from the repo", "schema_id": "masem-direct@x",
                "keywords": ["repo", "test"], "attribution": "anonymous", "citation": "Anonymous (2026). " + title,
                "readme": "## Hi", "recipe": {"model": "gemini-x", "prompt": None, "schema_id": "masem-direct@x"},
                "credibility": {"tier": badge, "n_records": 1, "audited": 1, "audited_pct": 100.0,
                                "agreement": 1.0, "agreement_ci": [0.2, 1.0], "label": "Human-verified · 100% agree"},
                "preset": {"schema_id": "masem-direct@x", "preset_id": "masem-direct", "spec": spec}}
    results = {"papers": [{"filename": f"p{i}.pdf", "model": "gemini-x", "result": json.loads(fixtures.MASEM_V2_JSON)}
                          for i in range(n_papers)]}
    return metadata, results


def test_sync_publishes_merged_and_imports_github_only_datasets() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    sess = f"sync-{uuid.uuid4().hex[:6]}"
    # a local dataset whose PR was merged (its slug is on main)
    local = records.create_dataset(conn, title="Local set", schema_id=None, session_id=sess)
    records.set_dataset_visibility(conn, local["id"], "public"); records.set_publish_status(conn, local["id"], "pending")
    gh_slug = f"github-only-{uuid.uuid4().hex[:6]}"
    lmeta, lres = _material(local["slug"], "Local set")
    gmeta, gres = _material(gh_slug, "GitHub only set", n_papers=2)
    files = {f"datasets/{local['slug']}/metadata.json": (lmeta, "m1"), f"datasets/{local['slug']}/results.json": (lres, "r1"),
             f"datasets/{gh_slug}/metadata.json": (gmeta, "m2"), f"datasets/{gh_slug}/results.json": (gres, "r2")}
    out = github_sync.sync(conn, client=_repo(files), gh_repo="o/metalens-datasets")
    assert out["published"] == 1 and out["imported"] == 1
    mine = records.get_dataset(conn, local["id"])
    assert mine["publish_status"] == "published" and mine["published_url"].endswith(f"/datasets/{local['slug']}")
    assert mine["citation"].endswith(mine["published_url"])
    imp = records.dataset_by_slug(conn, gh_slug)
    assert imp and imp["github_source"] and imp["visibility"] == "public" and imp["publish_status"] == "published"
    assert imp["keywords"] == ["repo", "test"] and imp["attribution"] == "anonymous" and imp["readme"] == "## Hi"
    n_docs = conn.execute("SELECT count(DISTINCT document_id) FROM record WHERE dataset_id = %s::uuid", (imp["id"],)).fetchone()[0]
    assert n_docs == 2                                              # both papers' records
    # catalogue: both listed; the imported one carries the as-published badge and the GitHub link
    pub = {d["id"]: d for d in records.public_datasets_with_badges(conn)}
    assert pub[local["id"]]["published_url"] == mine["published_url"]
    assert pub[imp["id"]]["credibility"]["tier"] == "human_verified" and pub[imp["id"]]["github_source"]
    # second run: nothing changes
    out = github_sync.sync(conn, client=_repo(files), gh_repo="o/metalens-datasets")
    assert out["unchanged"] == 2 and out["imported"] == 0 and out["published"] == 0
    # results.json changed on GitHub → the GitHub-only dataset is re-imported, no duplicates
    gmeta2, gres2 = _material(gh_slug, "GitHub only set", n_papers=1)
    files[f"datasets/{gh_slug}/results.json"] = (gres2, "r3")
    out = github_sync.sync(conn, client=_repo(files), gh_repo="o/metalens-datasets")
    assert out["reimported"] == 1
    n_docs = conn.execute("SELECT count(DISTINCT document_id) FROM record WHERE dataset_id = %s::uuid", (imp["id"],)).fetchone()[0]
    assert n_docs == 1 and records.dataset_by_slug(conn, gh_slug)["id"] == imp["id"]
    for d in (local["id"], imp["id"]):
        records.clear_dataset_documents(conn, d); records.delete_dataset(conn, d)
    conn.commit(); conn.close()


def test_pending_dataset_is_not_listed_until_merged() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = f"pend-{uuid.uuid4().hex[:6]}"
    ds = records.create_dataset(conn, title="Pending set", schema_id=None, session_id=sess)
    records.set_dataset_visibility(conn, ds["id"], "public"); records.set_publish_status(conn, ds["id"], "pending"); conn.commit()
    c = TestClient(appmod.app)
    listed = {d["id"] for d in c.get("/api/datasets/public").json()["datasets"]}
    assert ds["id"] not in listed
    records.mark_published(conn, ds["id"], file_sha="r9", meta={})
    listed = {d["id"] for d in c.get("/api/datasets/public").json()["datasets"]}
    assert ds["id"] in listed
    assert c.post("/api/github/sync").status_code == 401             # sign in to trigger a sync
    records.delete_dataset(conn, ds["id"]); conn.commit(); conn.close()
