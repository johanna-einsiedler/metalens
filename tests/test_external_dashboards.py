"""Dashboards built outside Metalens: registered on the dataset, checked for the release they show."""
from __future__ import annotations

import os
import sys
import uuid

import httpx

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import external_dashboards as xd, records, releases  # noqa: E402
from test_analysis_table import HAC, _db_ok, _seed  # noqa: E402


def test_release_number_from_manifests() -> None:
    assert xd.release_number({"release": "genai-human-in-the-loop-1636f30f-v3"}) == 3
    assert xd.release_number({"release": "v2"}) == 2 and xd.release_number({"release_number": 5}) == 5 and xd.release_number({"release": "7"}) == 7
    assert xd.release_number({"release": "no-number-here"}) is None and xd.release_number({}) is None
    assert xd.candidates({"url": "https://x.github.io/dash/index.html", "manifest_url": None}) == ["https://x.github.io/dash/metalens.json", "https://x.github.io/dash/data/config.json"]
    assert xd.candidates({"url": "https://x.github.io/dash", "manifest_url": None})[0] == "https://x.github.io/dash/metalens.json"
    assert xd.valid_url("https://a.b/c") and not xd.valid_url("javascript:alert(1)") and not xd.valid_url("file:///etc/passwd")


def test_register_check_and_delete() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    sess = f"xd-{uuid.uuid4().hex[:6]}"; mine, other = {"X-Session-Id": sess}, {"X-Session-Id": "o-" + uuid.uuid4().hex[:6]}
    ds, _ = _seed(conn, HAC, "human-ai-collab", sess)
    releases.create(conn, ds)
    c = TestClient(appmod.app); stranger = TestClient(appmod.app)              # the login cookie must not leak onto the stranger
    body = {"title": "Living meta-analysis", "url": "https://x.github.io/dash/", "repo_url": "https://github.com/x/dash"}
    assert stranger.post(f"/api/datasets/{ds}/external-dashboards", json=body, headers=other).status_code == 404   # private dataset
    assert c.post(f"/api/datasets/{ds}/external-dashboards", json=body, headers=mine).status_code == 401        # needs an account
    c.post("/api/auth/register", json={"email": f"xd-{uuid.uuid4().hex[:8]}@example.org", "password": "xd-test-pass-1"}, headers=mine)
    assert c.post(f"/api/datasets/{ds}/external-dashboards", json={**body, "url": "javascript:alert(1)"}, headers=mine).status_code == 422
    made = c.post(f"/api/datasets/{ds}/external-dashboards", json=body, headers=mine).json()      # the check ran (and failed to reach the page)
    assert made["title"] == "Living meta-analysis" and made["release_shown"] is None and made["check_note"]
    # a check against a page that publishes its config
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/metalens.json"):
            return httpx.Response(404)
        if req.url.path.endswith("/data/config.json"):
            return httpx.Response(200, json={"release": "genai-human-in-the-loop-1636f30f-v1", "source": {}})
        return httpx.Response(404)
    ext = xd.check(conn, xd.get(conn, made["id"]), client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert ext["release_shown"] == 1 and ext["check_note"] is None and ext["checked_at"]
    listed = c.get(f"/api/datasets/{ds}/external-dashboards", headers=mine).json()
    assert listed["latest_release"] == 1 and listed["dashboards"][0]["release_shown"] == 1
    assert stranger.delete(f"/api/external-dashboards/{made['id']}", headers=other).status_code == 404
    assert c.delete(f"/api/external-dashboards/{made['id']}", headers=mine).json() == {"deleted": 1}
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()
