"""Publishing details on a dataset: description, README, keywords, attribution (named or
anonymous) and a citation that is suggested from the owner's citation name and can be
hand-edited. They reach the overview, the public catalogue and the export. Skips without
Postgres."""
from __future__ import annotations

import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402
from paperlens import app as appmod, auth, exporter, github_publish, records  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402


def test_publishing_details_round_trip_and_reach_catalogue_and_export() -> None:
    try:
        conn = records.connect(); records.init_db(conn)
    except Exception:
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    email = f"meta-{uuid.uuid4().hex[:6]}@example.org"
    user = auth.create_user(conn, email, "pw-12345678"); auth.update_profile(conn, user["id"], citation_name="Doe, J.")
    tok = auth.create_session(conn, user["id"]); conn.commit()
    c.cookies.set("pl_session", tok)
    ds = c.post("/api/datasets", json={"title": "Meta test set", "visibility": "private"}).json()
    doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None, source_job_id="meta", session_id=None, owner_user_id=user["id"])
    records.assign_document_to_dataset(conn, ds["id"], doc); conn.commit()

    ov = c.get(f"/api/datasets/{ds['id']}/overview").json()
    assert ov["attribution"] == "named" and ov["cite_as"] == "Doe, J." and ov["citation"].startswith("Doe, J. (")
    assert "Meta test set (version 1) [Data set]. Metalens." in ov["citation"] and ov["citation_custom"] is False

    r = c.patch(f"/api/datasets/{ds['id']}", json={"description": "Two toy records.", "readme": "## About\nHand-made.",
                                                   "keywords": ["toy", " test ", ""], "attribution": "anonymous"})
    assert r.status_code == 200, r.text
    ov = c.get(f"/api/datasets/{ds['id']}/overview").json()
    assert ov["keywords"] == ["toy", "test"] and ov["readme"].startswith("## About") and ov["description"] == "Two toy records."
    assert ov["attribution"] == "anonymous" and ov["cite_as"] is None and ov["citation"].startswith("Anonymous (")

    # a hand-edited citation sticks; "" goes back to the suggested one
    c.patch(f"/api/datasets/{ds['id']}", json={"citation": "Doe J (2026) My own wording."})
    ov = c.get(f"/api/datasets/{ds['id']}/overview").json()
    assert ov["citation"] == "Doe J (2026) My own wording." and ov["citation_custom"] is True
    c.patch(f"/api/datasets/{ds['id']}", json={"citation": ""})
    assert c.get(f"/api/datasets/{ds['id']}/overview").json()["citation_custom"] is False
    assert c.patch(f"/api/datasets/{ds['id']}", json={"attribution": "pseudonymous"}).status_code == 422

    # public listing carries keywords and hides the name when anonymous
    c.patch(f"/api/datasets/{ds['id']}", json={"visibility": "public"})
    pub = next(d for d in c.get("/api/datasets/public").json()["datasets"] if d["id"] == ds["id"])
    assert pub["keywords"] == ["toy", "test"] and pub["cite_as"] is None
    # export + GitHub README carry them too
    mat = exporter.materialize_dataset(conn, ds["id"])
    md = mat["metadata"]
    assert md["keywords"] == ["toy", "test"] and md["attribution"] == "anonymous" and md["readme"].startswith("## About")
    readme = github_publish.readme_markdown(mat)
    assert readme.startswith("# Meta test set") and "Keywords: toy, test" in readme and "## How to cite" in readme \
        and "published anonymously" in readme and "Hand-made." in readme
    records.delete_dataset(conn, ds["id"]); conn.commit(); conn.close()


def test_owner_name_is_not_exposed_on_an_anonymous_dataset() -> None:
    try:
        conn = records.connect(); records.init_db(conn)
    except Exception:
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    user = auth.create_user(conn, f"anon-{uuid.uuid4().hex[:6]}@example.org", "pw-12345678")
    auth.update_profile(conn, user["id"], citation_name="Hidden, H."); tok = auth.create_session(conn, user["id"]); conn.commit()
    c.cookies.set("pl_session", tok)
    ds = c.post("/api/datasets", json={"title": "Anon set", "visibility": "private"}).json()
    c.patch(f"/api/datasets/{ds['id']}", json={"attribution": "anonymous", "visibility": "public"})
    mine = c.get(f"/api/datasets/{ds['id']}/overview").json()
    assert mine["owner_citation_name"] == "Hidden, H." and mine["cite_as"] is None      # the owner's form sees the name
    other = TestClient(appmod.app)
    for path in (f"/api/datasets/{ds['id']}/overview", f"/api/datasets/{ds['id']}"):
        body = other.get(path, headers={"X-Session-Id": "visitor"}).json()
        assert "owner_citation_name" not in body and body["cite_as"] is None and "Hidden" not in str(body)
    records.delete_dataset(conn, ds["id"]); conn.commit(); conn.close()
