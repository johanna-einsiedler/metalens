"""Duplicate papers in a dataset: GET /api/datasets/{id}/duplicates lists them (same DOI, else
title, else filename; newest first) and POST /api/datasets/{id}/dedupe deletes every copy but
the newest. Re-importing a corrected JSON into its dataset is how duplicates arise.

Skips without Postgres.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402
from paperlens import app as appmod, records  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _add(conn, ds_id: str, sess: str, result: dict, filename: str) -> str:
    doc = records.persist(conn, ingest(result), schema_id=None, source_job_id="dedupe", session_id=sess)
    conn.execute("UPDATE extraction_document SET filename = %s WHERE id = %s::uuid", (filename, doc))
    conn.execute("UPDATE record SET dataset_id = %s::uuid WHERE document_id = %s::uuid", (ds_id, doc))
    conn.commit()
    return doc


def test_duplicates_are_grouped_and_dedupe_keeps_the_newest() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = "sess-dedupe-a"
    ds = records.create_dataset(conn, title="Dedupe A", schema_id=None, session_id=sess)
    old = _add(conn, ds["id"], sess, fixtures.FORESTPLOT_JSON, "goh.pdf")
    newer = _add(conn, ds["id"], sess, fixtures.FORESTPLOT_JSON, "goh.pdf")       # the re-import
    other = _add(conn, ds["id"], sess, fixtures.MASEM_V2_JSON, "other.pdf")       # a different paper
    c = TestClient(appmod.app)
    h = {"X-Session-Id": sess}

    r = c.get(f"/api/datasets/{ds['id']}/duplicates", headers=h)
    assert r.status_code == 200, r.text
    groups = r.json()["groups"]
    assert len(groups) == 1
    ids = [d["document_id"] for d in groups[0]["documents"]]
    assert ids == [newer, old]                                    # newest first
    assert groups[0]["key"].startswith("doi:")                    # the DOI wins when present

    r = c.post(f"/api/datasets/{ds['id']}/dedupe", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == [old] and r.json()["kept"] == [newer]
    left = {str(x[0]) for x in conn.execute(
        "SELECT DISTINCT document_id FROM record WHERE dataset_id = %s::uuid", (ds["id"],)).fetchall()}
    assert left == {newer, other}
    assert conn.execute("SELECT count(*) FROM extraction_document WHERE id = %s::uuid", (old,)).fetchone()[0] == 0
    assert c.get(f"/api/datasets/{ds['id']}/duplicates", headers=h).json()["groups"] == []
    conn.close()


def test_duplicates_fall_back_to_title_and_filename() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    # title match when the DOI is missing on one side; filename match when both are missing
    assert records._paper_key(None, "Human–AI collectives most accurately diagnose!", "x.pdf") == \
        records._paper_key("", "human ai collectives most accurately diagnose", "y.pdf")
    assert records._paper_key("https://doi.org/10.1/ABC", None, None) == records._paper_key("10.1/abc", "t", "f")
    assert records._paper_key(None, None, "Goh_2025.pdf") == records._paper_key(None, "", "goh_2025.json")
    assert records._paper_key(None, None, None) is None


def test_dedupe_is_owner_only() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    ds = records.create_dataset(conn, title="Dedupe B", schema_id=None, session_id="sess-dedupe-owner")
    c = TestClient(appmod.app)
    assert c.get(f"/api/datasets/{ds['id']}/duplicates", headers={"X-Session-Id": "someone-else"}).status_code == 404
    assert c.post(f"/api/datasets/{ds['id']}/dedupe", headers={"X-Session-Id": "someone-else"}).status_code == 404
    conn.close()
