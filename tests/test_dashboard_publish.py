"""Publishing a dashboard freezes its spec over ONE dataset release. Readers get that page
through the dashboard's own data endpoints; the owner keeps a live draft; adding a paper or
editing the draft never changes the public page until the owner publishes again. Skips
without Postgres."""
from __future__ import annotations

import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import records  # noqa: E402
from test_analysis_table import HAC, _db_ok, _seed, _seed_into  # noqa: E402
from test_releases import _second_paper  # noqa: E402


def test_publish_pins_a_release_and_the_public_page_stays_put() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    c = TestClient(appmod.app)
    email = f"pub-{uuid.uuid4().hex[:8]}@example.org"
    uid = c.post("/api/auth/register", json={"email": email, "password": "publish-test-pass-1"}).json()["user"]["id"]
    sess = f"pub-{uuid.uuid4().hex[:6]}"
    ds, _ = _seed(conn, HAC, "human-ai-collab", sess)
    conn.execute("UPDATE dataset SET owner_user_id = %s::uuid, session_id = NULL WHERE id = %s::uuid", (uid, ds))
    conn.execute("UPDATE record SET owner_user_id = %s::uuid WHERE dataset_id = %s::uuid", (uid, ds)); conn.commit()
    reader = TestClient(appmod.app)                                     # someone else, not signed in

    spec = c.post("/api/dashboards/validate", json={"dataset_id": ds}).json()["spec"]
    did = c.post("/api/dashboards", json={"dataset_id": ds, "title": "Teams", "spec": spec}).json()["id"]
    assert reader.get(f"/api/dashboards/{did}").status_code == 404      # a draft is the owner's
    mine = c.get(f"/api/dashboards/{did}").json()
    assert mine["view"] == "draft" and mine["published"] is False and mine["update"] is None

    # first publication cuts release v1 (the dataset had none) and freezes the spec
    pub = c.post(f"/api/dashboards/{did}/publish", json={"rev": mine["rev"], "release": "latest"}).json()
    assert pub["release"] == 1 and pub["hidden_blocks"] == []
    page = reader.get(f"/api/dashboards/{did}").json()
    assert page["view"] == "published" and page["release"]["number"] == 1 and page["title"] == "Teams"
    assert "rev" not in page and "proposal" not in page and "update" not in page and page["can_edit"] is False
    # the dataset is PRIVATE: its own endpoints stay closed, the dashboard's data endpoints serve the release
    assert reader.get(f"/api/datasets/{ds}/analysis").status_code == 404
    table = reader.get(f"/api/dashboards/{did}/table?unit=conditions.measures").json()
    n_rows = len(table["rows"])
    assert n_rows == 2 and table["dataset"]["release"]["number"] == 1 and table["viewer"]["owner"] is False
    assert all("filename" not in r and "document_id" not in r for r in table["records"])
    cell = {"record_id": table["records"][0]["id"], "path": table["rows"][0]["p"], "column": "Avg_Perf_Human"}
    ev = reader.post(f"/api/dashboards/{did}/evidence", json={"unit": "conditions.measures", "cells": [cell]}).json()["cells"]
    assert ev and ev[0]["items"][0]["snippet"].startswith("accuracy")
    assert reader.get(f"/api/dashboards/{did}?view=draft").status_code == 404
    assert reader.get(f"/api/dashboards/{did}/table?view=draft").status_code == 404

    # the owner adds a paper and edits the draft: the public page does not move
    _seed_into(conn, _second_paper(), "human-ai-collab", sess, ds)
    conn.execute("UPDATE record SET owner_user_id = %s::uuid WHERE dataset_id = %s::uuid", (uid, ds)); conn.commit()
    mine = c.get(f"/api/dashboards/{did}").json()
    assert mine["update"] == {"pinned": 1, "latest": 1, "available": False, "head_changed": True} and mine["draft_differs"] is False
    draft = {**mine["spec"], "blocks": mine["spec"]["blocks"][:2]}
    assert c.patch(f"/api/dashboards/{did}", json={"rev": mine["rev"], "title": "Teams (draft)", "spec": draft}).status_code == 200
    assert len(c.get(f"/api/dashboards/{did}/table?unit=conditions.measures").json()["rows"]) == 2 * n_rows       # the draft is live
    page = reader.get(f"/api/dashboards/{did}").json()
    assert page["title"] == "Teams" and len(page["spec"]["blocks"]) == len(spec["blocks"])
    assert len(reader.get(f"/api/dashboards/{did}/table?unit=conditions.measures").json()["rows"]) == n_rows
    assert c.get(f"/api/dashboards/{did}?view=published").json()["title"] == "Teams"                                # the owner can look at the public page
    tile = next(x for x in reader.get("/api/dashboards/public").json()["metalens"] if x["id"] == did)                # the Dashboards page tile
    assert tile["title"] == "Teams" and tile["release"] == 1 and tile["n_blocks"] == len(spec["blocks"]) and tile["preview"]["icon"]

    # a new release → "update available" → ONE call moves the page, keeping the published spec
    assert c.post(f"/api/datasets/{ds}/releases", json={"notes": "second paper"}).json()["number"] == 2
    mine = c.get(f"/api/dashboards/{did}").json()
    assert mine["update"]["available"] is True and mine["update"]["latest"] == 2 and mine["draft_differs"] is True
    listed = c.get(f"/api/dashboards?dataset={ds}").json()["dashboards"][0]
    assert listed["published"] is True and listed["release"] == 1 and listed["update_available"] == 2
    assert reader.get(f"/api/dashboards/{did}/update-preview").status_code == 404                                # the owner's business
    prev = c.get(f"/api/dashboards/{did}/update-preview?release=latest").json()
    assert (prev["from"], prev["to"], prev["papers"]) == (1, 2, [1, 2]) and prev["summary"]["broken"] == 0
    assert [p["title"] for p in prev["changes"]["papers_added"]] == ["Another teams paper"]
    table_block = next(b for b in prev["blocks"] if b["template"] == "rows_table")
    assert table_block["status"] == "changed" and table_block["after"]["rows"] == 2 * table_block["before"]["rows"]
    assert c.get(f"/api/dashboards/{did}/update-preview?release=head").json()["to"] is None
    up = c.post(f"/api/dashboards/{did}/publish", json={"rev": mine["rev"], "release": 2, "source": "published"}).json()
    assert up["release"] == 2
    page = reader.get(f"/api/dashboards/{did}").json()
    assert page["release"]["number"] == 2 and page["title"] == "Teams" and len(page["spec"]["blocks"]) == len(spec["blocks"])
    assert len(reader.get(f"/api/dashboards/{did}/table?unit=conditions.measures").json()["rows"]) == 2 * n_rows
    assert c.get(f"/api/dashboards/{did}").json()["title"] == "Teams (draft)"                                        # the draft is untouched

    # publishing the draft is the other move; unpublishing closes the page
    mine = c.get(f"/api/dashboards/{did}").json()
    assert c.post(f"/api/dashboards/{did}/publish", json={"rev": mine["rev"], "source": "draft"}).json()["release"] == 2
    assert reader.get(f"/api/dashboards/{did}").json()["title"] == "Teams (draft)"
    assert c.post(f"/api/dashboards/{did}/publish", json={"rev": 1}).status_code == 409                               # stale revision
    assert c.post(f"/api/dashboards/{did}/publish", json={"rev": mine["rev"] + 1, "release": 9}).status_code == 404
    assert c.post(f"/api/dashboards/{did}/unpublish").json()["visibility"] == "private"
    assert reader.get(f"/api/dashboards/{did}").status_code == 404

    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.commit()
    assert conn.execute("SELECT count(*) FROM dashboard WHERE id = %s::uuid", (did,)).fetchone()[0] == 0              # dashboard + releases go together
    conn.execute("DELETE FROM users WHERE id = %s::uuid", (uid,)); conn.commit(); conn.close()
