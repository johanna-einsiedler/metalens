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
            return httpx.Response(200, json={"release": "genai-human-in-the-loop-1636f30f-v1", "source": {}, "preview": "img/card.png",
                                             "description": "  Forest plots\n and subgroups. ", "authors": "A. Author, B. Author", "keywords": ["human–AI", " teams "], "ignored": "x"})
        return httpx.Response(404)
    ext = xd.check(conn, xd.get(conn, made["id"]), client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert ext["release_shown"] == 1 and ext["check_note"] is None and ext["checked_at"]
    assert ext["preview_url"] == "https://x.github.io/dash/data/img/card.png" and ext["authors"] == "A. Author, B. Author"   # tile fields; the image path is relative to the manifest
    assert ext["description"] == "Forest plots and subgroups." and ext["keywords"] == ["human–AI", "teams"]
    listed = c.get(f"/api/datasets/{ds}/external-dashboards", headers=mine).json()
    assert listed["latest_release"] == 1 and listed["dashboards"][0]["release_shown"] == 1
    assert stranger.delete(f"/api/external-dashboards/{made['id']}", headers=other).status_code == 404
    # the public Dashboards page lists it once the dataset is public AND a moderator has approved it
    assert not any(x["id"] == made["id"] for x in c.get("/api/dashboards/public").json()["external"])
    records.set_dataset_visibility(conn, ds, "public"); conn.commit()
    assert not any(x["id"] == made["id"] for x in c.get("/api/dashboards/public").json()["external"])   # public, but unapproved
    xd.set_approved(conn, made["id"], by_user_id=None, approved=True); conn.commit()
    pub = c.get("/api/dashboards/public").json()
    hit = next(x for x in pub["external"] if x["id"] == made["id"])
    assert hit["dataset_title"] == "Analysis set" and hit["latest_release"] == 1 and hit["release_shown"] == 1
    assert hit["n_papers"] == 1 and hit["keywords"] == ["human–AI", "teams"]                    # the release's paper count; the page's own keywords
    assert c.get("/dashboards").status_code == 200
    assert c.delete(f"/api/external-dashboards/{made['id']}", headers=mine).json() == {"deleted": 1}
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()


def test_approval_gates_the_public_page_and_the_owner_supplies_the_tile(monkeypatch) -> None:
    """Nothing reaches /dashboards until a moderator lists it; the owner's image beats the manifest's."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import admins, app as appmod
    conn = records.connect(); records.init_db(conn)
    sess = f"xd-{uuid.uuid4().hex[:6]}"; mine = {"X-Session-Id": sess}
    ds, _ = _seed(conn, HAC, "human-ai-collab", sess)
    releases.create(conn, ds)
    records.set_dataset_visibility(conn, ds, "public"); conn.commit()
    c = TestClient(appmod.app)
    email = f"xd-{uuid.uuid4().hex[:8]}@example.org"
    c.post("/api/auth/register", json={"email": email, "password": "xd-test-pass-1"}, headers=mine)
    made = c.post(f"/api/datasets/{ds}/external-dashboards",
                  json={"title": "Own code", "url": "https://x.github.io/own/"}, headers=mine).json()
    assert made["approved"] is False and made["tile_url"] is None

    # a stranger who is not named in PAPERLENS_ADMINS can neither see the queue nor approve
    monkeypatch.setenv("PAPERLENS_ADMINS", "someone-else@example.org")
    assert not admins.is_admin({"email": email})
    assert c.get("/api/external-dashboards/pending", headers=mine).status_code == 403
    assert c.post(f"/api/external-dashboards/{made['id']}/approve", headers=mine).status_code == 403
    assert not any(x["id"] == made["id"] for x in c.get("/api/dashboards/public").json()["external"])

    # the owner's own image wins over whatever the manifest names, and is kept by a later check
    xd.check(conn, xd.get(conn, made["id"]),
             client=httpx.Client(transport=httpx.MockTransport(
                 lambda req: httpx.Response(200, json={"release": "v1", "preview": "from-manifest.png"})
                 if req.url.path.endswith("/metalens.json") else httpx.Response(404))))
    assert c.patch(f"/api/external-dashboards/{made['id']}", json={"preview_url": "not a url"}, headers=mine).status_code == 422
    got = c.patch(f"/api/external-dashboards/{made['id']}", json={"preview_url": "https://cdn.example.org/tile.png"}, headers=mine).json()
    assert got["preview_override"] == "https://cdn.example.org/tile.png"
    assert got["preview_url"] == "https://x.github.io/own/from-manifest.png"      # the manifest's is still remembered
    assert got["tile_url"] == "https://cdn.example.org/tile.png"                  # but the tile shows the owner's

    # a moderator lists it, and only then is it public
    monkeypatch.setenv("PAPERLENS_ADMINS", f" {email.upper()} ")                  # spacing and case do not matter
    assert admins.is_admin({"email": email})
    assert [x["id"] for x in c.get("/api/external-dashboards/pending", headers=mine).json()["dashboards"]] == [made["id"]]
    assert c.get("/api/auth/me", headers=mine).json()["is_admin"] is True
    ok = c.post(f"/api/external-dashboards/{made['id']}/approve", headers=mine).json()
    assert ok["approved"] is True and ok["approved_at"]
    hit = next(x for x in c.get("/api/dashboards/public").json()["external"] if x["id"] == made["id"])
    assert hit["tile_url"] == "https://cdn.example.org/tile.png"
    assert c.get("/api/external-dashboards/pending", headers=mine).json()["dashboards"] == []

    # clearing the override falls back to the manifest's image; unapproving takes it off the page
    back = c.patch(f"/api/external-dashboards/{made['id']}", json={"preview_url": None}, headers=mine).json()
    assert back["tile_url"] == "https://x.github.io/own/from-manifest.png"
    c.post(f"/api/external-dashboards/{made['id']}/approve?approved=false", headers=mine)
    assert not any(x["id"] == made["id"] for x in c.get("/api/dashboards/public").json()["external"])
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()


def test_tile_prompt_is_house_style_and_ships_with_a_release() -> None:
    from paperlens import tile_prompt
    p = tile_prompt.build("Humans & GenAI in Decision Tasks", "When do human-AI teams beat either alone?", ["decision tasks", "meta-analysis"])
    assert "1200x630" in p and "Humans & GenAI in Decision Tasks" in p and "decision tasks, meta-analysis" in p
    assert "#eb6834" in p and "no text, letters, numbers" in p.lower()
    assert "no faces" in p and "circuit boards" in p                   # the AI-illustration cliches are ruled out
    long = tile_prompt.build("T", "x" * 400)
    assert len(long) < 3000 and "…" in long                            # a long description is cut, not pasted whole
    assert "metalens.json" in tile_prompt.doc("T") and "```" in tile_prompt.doc("T")


def test_unpublishing_takes_a_dataset_out_of_the_catalogue() -> None:
    """The owner can withdraw a published dataset; nothing is deleted and it can go back."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    sess = f"xd-{uuid.uuid4().hex[:6]}"; mine = {"X-Session-Id": sess}
    ds, _ = _seed(conn, HAC, "human-ai-collab", sess)
    records.set_dataset_visibility(conn, ds, "public"); conn.commit()
    c = TestClient(appmod.app)
    listed = lambda: any(d["id"] == ds for d in c.get("/api/datasets/public").json()["datasets"])
    assert listed()
    stranger = TestClient(appmod.app)
    assert stranger.patch(f"/api/datasets/{ds}", json={"visibility": "private"},
                          headers={"X-Session-Id": "o-" + uuid.uuid4().hex[:6]}).status_code == 403
    assert listed()                                              # a stranger cannot withdraw it
    assert c.patch(f"/api/datasets/{ds}", json={"visibility": "private"}, headers=mine).status_code == 200
    assert not listed()
    assert records.get_dataset(conn, ds) is not None             # withdrawn, not deleted
    # withdrawing clears the `catalogue` flag, so the hourly sync cannot quietly relist it:
    # getting back into the catalogue means publishing again, not just flipping visibility
    records.set_dataset_visibility(conn, ds, "public"); conn.commit()
    assert (records.get_dataset(conn, ds) or {}).get("visibility") == "public" and not listed()
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()


def test_only_a_moderator_can_remove_an_imported_dataset(monkeypatch) -> None:
    """github_sync imports a dataset with no owner, so _owns() is False for everyone; without
    the moderator path such a dataset could never be removed by anybody."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    sess = f"xd-{uuid.uuid4().hex[:6]}"; mine = {"X-Session-Id": sess}
    ds, _ = _seed(conn, HAC, "human-ai-collab", sess)
    conn.execute("UPDATE dataset SET owner_user_id = NULL, session_id = NULL, github_source = true, visibility = 'public' WHERE id = %s::uuid", (ds,))
    conn.commit()
    assert records.dataset_is_ownerless(conn, ds)

    c = TestClient(appmod.app)
    monkeypatch.setenv("PAPERLENS_ADMINS", "nobody@example.org")
    assert c.delete(f"/api/datasets/{ds}", headers=mine).status_code == 403        # not signed in: no moderator either
    email = f"xd-{uuid.uuid4().hex[:8]}@example.org"
    c.post("/api/auth/register", json={"email": email, "password": "xd-test-pass-1"}, headers=mine)
    assert c.delete(f"/api/datasets/{ds}", headers=mine).status_code == 403        # signed in, but not a moderator
    assert records.get_dataset(conn, ds) is not None
    assert c.get(f"/api/datasets/{ds}/overview", headers=mine).json()["ownerless"] is True

    monkeypatch.setenv("PAPERLENS_ADMINS", email)
    assert c.delete(f"/api/datasets/{ds}", headers=mine).json()["deleted"] == 1
    assert records.get_dataset(conn, ds) is None
    conn.close()


def test_a_moderator_cannot_delete_someone_elses_owned_dataset(monkeypatch) -> None:
    """The moderator path is ONLY for ownerless datasets; an owned one stays the owner's."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    owner_sess = f"xd-{uuid.uuid4().hex[:6]}"
    ds, _ = _seed(conn, HAC, "human-ai-collab", owner_sess)
    records.set_dataset_visibility(conn, ds, "public"); conn.commit()
    assert not records.dataset_is_ownerless(conn, ds)
    admin = TestClient(appmod.app); a_sess = {"X-Session-Id": "adm-" + uuid.uuid4().hex[:6]}
    email = f"adm-{uuid.uuid4().hex[:8]}@example.org"
    admin.post("/api/auth/register", json={"email": email, "password": "xd-test-pass-1"}, headers=a_sess)
    monkeypatch.setenv("PAPERLENS_ADMINS", email)
    assert admin.delete(f"/api/datasets/{ds}", headers=a_sess).status_code == 403
    assert records.get_dataset(conn, ds) is not None
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()
