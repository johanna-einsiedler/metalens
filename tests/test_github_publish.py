"""WS7 — GitHub publishing (github_publish.py), offline via httpx.MockTransport.

Asserts the API call sequence (get repo → get ref → create branch → PUT both files →
open PR), that the PR url is written to dataset.git_pr_url, and that a missing token
fails cleanly. No real network. Skips without Postgres.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx  # noqa: E402

from paperlens import extract, github_publish, records, storage  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _fake(pdf, prompt, *, model="", api_key="", base_url=None, use_text=False):
    return extract.LLMResult(text=json.dumps({
        "paper_metadata": {"title": "GH Test"},
        "records": [{"Paper_Name": "G_2026", "Avg_Perf_HumanAI": 0.6}],
        "evidence": [],
    }), finish_reason="stop", usage={"total": 5}, resolved_model="fake")


def _seed_dataset(conn):
    import fitz
    d = fitz.open(); d.new_page().insert_text((72, 90), "x"); pdf = d.tobytes(); d.close()
    with tempfile.TemporaryDirectory() as tmp:
        out = extract.run_extraction(conn, pdf, prompt="x", model="gpt-4o", api_key="",
                                     schema_id="human-ai-collab@v1", session_id="sess-gh",
                                     complete=_fake, store=storage.LocalObjectStore(root=tmp))
        conn.commit()
    ds = records.create_dataset(conn, title="GH Publish DS", schema_id="human-ai-collab@v1",
                                session_id="sess-gh", visibility="private", model="gpt-4o")
    records.assign_document_to_dataset(conn, ds["id"], out["document_id"])
    conn.commit()
    return ds["id"]


def test_token_missing_raises() -> None:
    os.environ.pop("PAPERLENS_GITHUB_TOKEN", None)
    try:
        github_publish.publish_dataset(None, "x", client=httpx.Client())
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "GITHUB_TOKEN" in str(e)


def test_publish_flow() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    conn = records.connect(); records.init_db(conn)
    ds_id = _seed_dataset(conn)

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        p = request.url.path
        if request.method == "GET" and p.endswith("/metalens-datasets"):
            return httpx.Response(200, json={"default_branch": "main"})
        if request.method == "GET" and "/git/ref/heads/" in p:
            return httpx.Response(200, json={"object": {"sha": "base-sha-123"}})
        if request.method == "POST" and p.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if request.method == "GET" and "/contents/" in p:
            return httpx.Response(404, json={})           # file not present yet
        if request.method == "PUT" and "/contents/" in p:
            return httpx.Response(201, json={"content": {"sha": "blob1"}})
        if request.method == "POST" and p.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "https://github.com/o/metalens-datasets/pull/7"})
        return httpx.Response(500, json={"path": p})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    os.environ["PAPERLENS_GITHUB_TOKEN"] = "ghp_test"
    try:
        res = github_publish.publish_dataset(conn, ds_id, client=client, branch="metalens/test-branch")
    finally:
        os.environ.pop("PAPERLENS_GITHUB_TOKEN", None)

    assert res["pr_url"].endswith("/pull/7")
    # call sequence
    assert any(c.endswith("/metalens-datasets") and c.startswith("GET") for c in calls)
    assert any("/git/ref/heads/main" in c for c in calls)
    assert any(c.startswith("POST") and c.endswith("/git/refs") for c in calls)
    assert sum(1 for c in calls if c.startswith("PUT") and "/contents/" in c) == 2   # metadata + results
    assert any(c.startswith("POST") and c.endswith("/pulls") for c in calls)
    # PR url written back onto the dataset
    got = records.get_dataset(conn, ds_id)
    assert got["git_pr_url"] == "https://github.com/o/metalens-datasets/pull/7"
    conn.close()


def _main() -> int:
    failures = 0
    for label, fn in [
        ("github:token-missing", test_token_missing_raises),
        ("github:publish-flow", test_publish_flow),
    ]:
        try:
            fn(); print(f"  PASS  {label}")
        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ == "Skipped":
                print(f"  SKIP  {label}: {exc}"); continue
            failures += 1; print(f"  FAIL  {label}: {exc!r}")
    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
