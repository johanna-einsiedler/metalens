"""One Publish endpoint, three destinations: 'github+metalens' (PR now, listed after the
merge), 'github' (PR only, never listed here), 'metalens' (listed here only). Plus the
included-papers list in exports and the public paper-coverage lookup. Skips without Postgres."""
from __future__ import annotations

import json
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402
from paperlens import app as appmod, exporter, github_publish, records  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402


def _setup(conn, sess):
    ds = records.create_dataset(conn, title=f"Targets {uuid.uuid4().hex[:4]}", schema_id=None, session_id=sess)
    doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None, source_job_id="t", session_id=sess)
    records.assign_document_to_dataset(conn, ds["id"], doc); conn.commit()
    return ds


def test_publish_targets_and_paper_coverage(monkeypatch) -> None:
    try:
        conn = records.connect(); records.init_db(conn)
    except Exception:
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app); sess = f"tgt-{uuid.uuid4().hex[:6]}"; h = {"X-Session-Id": sess}
    monkeypatch.setattr(github_publish, "token", lambda: "t")
    def fake_publish(conn_, dataset_id, **kw):
        github_publish.write_pr_url(conn_, dataset_id, "https://github.com/o/metalens-datasets/pull/3")
        return {"pr_url": "https://github.com/o/metalens-datasets/pull/3", "branch": "b"}
    monkeypatch.setattr(github_publish, "publish_dataset", fake_publish)
    monkeypatch.setattr(appmod.worker, "enqueue", lambda *a, **k: None)

    # GitHub only: PR, private, never in the catalogue even after the merge
    gh = _setup(conn, sess)
    r = c.post(f"/api/datasets/{gh['id']}/publish", json={"target": "github"}, headers=h)
    assert r.status_code == 200 and r.json()["pr_url"].endswith("/pull/3")
    d = records.get_dataset(conn, gh["id"])
    assert d["publish_status"] == "pending" and d["visibility"] == "private" and d["catalogue"] is False
    records.mark_published(conn, gh["id"], file_sha="s1", meta={})
    d = records.get_dataset(conn, gh["id"])
    assert d["publish_status"] == "published" and d["visibility"] == "private" and d["published_url"]
    assert gh["id"] not in {x["id"] for x in c.get("/api/datasets/public").json()["datasets"]}

    # GitHub and the catalogue: viewable now, listed after the merge
    both = _setup(conn, sess)
    r = c.post(f"/api/datasets/{both['id']}/publish", json={"target": "github+metalens"}, headers=h)
    assert r.status_code == 200
    d = records.get_dataset(conn, both["id"])
    assert d["publish_status"] == "pending" and d["visibility"] == "public" and d["catalogue"] is True
    assert both["id"] not in {x["id"] for x in c.get("/api/datasets/public").json()["datasets"]}
    records.mark_published(conn, both["id"], file_sha="s2", meta={})
    assert both["id"] in {x["id"] for x in c.get("/api/datasets/public").json()["datasets"]}

    # Metalens only (no GitHub): listed here, no PR
    monkeypatch.setattr(github_publish, "token", lambda: None)
    here = _setup(conn, sess)
    r = c.post(f"/api/datasets/{here['id']}/publish", json={"target": "metalens"}, headers=h)
    assert r.status_code == 200 and r.json()["publish_status"] == "published"
    assert here["id"] in {x["id"] for x in c.get("/api/datasets/public").json()["datasets"]}
    assert c.post(f"/api/datasets/{here['id']}/publish", json={"target": "github"}, headers=h).status_code == 400
    assert c.post(f"/api/datasets/{here['id']}/publish", json={"target": "nowhere"}, headers=h).status_code == 422

    # the included papers travel with the export and the README; the coverage lookup finds them
    mat = exporter.materialize_dataset(conn, both["id"])
    papers = mat["metadata"]["papers"]
    assert len(papers) == 1 and papers[0]["doi"] == "10.1037/abc.0000123" and papers[0]["n_records"] >= 1
    assert "## Included papers" in github_publish.readme_markdown(mat) and "https://doi.org/10.1037/abc.0000123" in github_publish.readme_markdown(mat)
    cov = c.get("/api/papers/coverage", params={"q": "10.1037/abc.0000123"}).json()["papers"]
    assert cov and {x["id"] for x in cov[0]["datasets"]} >= {both["id"], here["id"]}
    assert gh["id"] not in {x["id"] for x in cov[0]["datasets"]}                     # GitHub-only stays out
    by_title = c.get("/api/papers/coverage", params={"q": "remote-work productivity"}).json()["papers"]
    assert any(p["doi"] == "10.1037/abc.0000123" for p in by_title)
    assert c.get("/api/papers/coverage", params={"q": "no such paper zzz"}).json()["papers"] == []
    for d_ in (gh, both, here):
        records.clear_dataset_documents(conn, d_["id"]); records.delete_dataset(conn, d_["id"])
    conn.commit(); conn.close()


def test_unlisting_keeps_the_github_state_and_survives_the_sync() -> None:
    try:
        conn = records.connect(); records.init_db(conn)
    except Exception:
        import pytest; pytest.skip("no Postgres")
    sess = f"unl-{uuid.uuid4().hex[:6]}"
    ds = _setup(conn, sess)
    records.set_publish_target(conn, ds["id"], catalogue=True); records.set_dataset_visibility(conn, ds["id"], "public")
    records.mark_published(conn, ds["id"], file_sha="s", meta={})
    records.set_dataset_visibility(conn, ds["id"], "private")               # "Unlist here"
    d = records.get_dataset(conn, ds["id"])
    assert d["visibility"] == "private" and d["publish_status"] == "published" and d["catalogue"] is False
    assert d["published_url"]                                             # the GitHub copy is still acknowledged
    records.mark_published(conn, ds["id"], file_sha="s", meta={})           # what the hourly sync does
    assert records.get_dataset(conn, ds["id"])["visibility"] == "private"  # …and it stays unlisted
    records.clear_dataset_documents(conn, ds["id"]); records.delete_dataset(conn, ds["id"]); conn.commit(); conn.close()


def test_republish_is_a_new_version_and_a_suggested_looking_citation_stays_live(monkeypatch) -> None:
    try:
        conn = records.connect(); records.init_db(conn)
    except Exception:
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import auth
    c = TestClient(appmod.app)
    user = auth.create_user(conn, f"rep-{uuid.uuid4().hex[:6]}@example.org", "pw-12345678")
    auth.update_profile(conn, user["id"], citation_name="Haus, Martin"); tok = auth.create_session(conn, user["id"]); conn.commit()
    c.cookies.set("pl_session", tok)
    ds = c.post("/api/datasets", json={"title": "Republish set", "visibility": "private"}).json()
    doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None, source_job_id="t", owner_user_id=user["id"])
    records.assign_document_to_dataset(conn, ds["id"], doc); conn.commit()
    # a "custom" citation that is just the suggested named text: not custom, follows the publication
    sugg = c.get(f"/api/datasets/{ds['id']}/overview").json()["citation_suggested"]
    c.patch(f"/api/datasets/{ds['id']}", json={"citation": sugg, "attribution": "anonymous"})
    ov = c.get(f"/api/datasets/{ds['id']}/overview").json()
    assert ov["citation_custom"] is False and ov["citation"].startswith("Anonymous (")
    # publish → merge → the citation moves to GitHub by itself
    monkeypatch.setattr(github_publish, "token", lambda: "t")
    prs = []
    def fake_publish(conn_, dataset_id, **kw):
        prs.append(dataset_id); github_publish.write_pr_url(conn_, dataset_id, f"https://github.com/o/m/pull/{len(prs)}")
        return {"pr_url": f"https://github.com/o/m/pull/{len(prs)}", "branch": "b"}
    monkeypatch.setattr(github_publish, "publish_dataset", fake_publish); monkeypatch.setattr(appmod.worker, "enqueue", lambda *a, **k: None)
    assert c.post(f"/api/datasets/{ds['id']}/publish", json={"target": "github+metalens"}).json().get("update") is False
    records.mark_published(conn, ds["id"], file_sha="s1", meta={})
    ov = c.get(f"/api/datasets/{ds['id']}/overview").json()
    assert ov["citation"].endswith(ov["published_url"]) and ov["changed_since_publish"] is False
    # a review action after publication → an update is due; publishing it bumps the version, stays published
    rid = str(conn.execute("SELECT id FROM record WHERE dataset_id = %s::uuid LIMIT 1", (ds["id"],)).fetchone()[0])
    c.post(f"/api/records/{rid}/verify", json={"status": "verified"})
    ov = c.get(f"/api/datasets/{ds['id']}/overview").json()
    assert ov["changed_since_publish"] is True
    r = c.post(f"/api/datasets/{ds['id']}/publish", json={"target": "github+metalens"}).json()
    assert r["update"] is True and r["version"] == 2 and len(prs) == 2
    ov = c.get(f"/api/datasets/{ds['id']}/overview").json()
    assert ov["publish_status"] == "published" and ov["version"] == 2 and "(version 2)" in ov["citation"]
    records.clear_dataset_documents(conn, ds["id"]); records.delete_dataset(conn, ds["id"]); conn.commit(); conn.close()


def test_published_files_cite_their_github_location_before_the_merge() -> None:
    try:
        conn = records.connect(); records.init_db(conn)
    except Exception:
        import pytest; pytest.skip("no Postgres")
    sess = f"cite-{uuid.uuid4().hex[:6]}"
    ds = _setup(conn, sess)
    url = f"https://github.com/o/metalens-datasets/tree/main/datasets/{ds['slug']}"
    mat = exporter.materialize_dataset(conn, ds["id"], published_url=url)
    assert mat["metadata"]["citation"].endswith(url) and "(version 1)" in mat["metadata"]["citation"]
    assert url in github_publish.readme_markdown(mat)
    plain = exporter.materialize_dataset(conn, ds["id"])                 # a plain export keeps the page URL
    assert "/dataset?id=" in plain["metadata"]["citation"]
    records.update_dataset_meta(conn, ds["id"], citation="My own wording.")   # a hand-edited citation is never overwritten
    assert exporter.materialize_dataset(conn, ds["id"], published_url=url)["metadata"]["citation"] == "My own wording."
    records.clear_dataset_documents(conn, ds["id"]); records.delete_dataset(conn, ds["id"]); conn.commit(); conn.close()
