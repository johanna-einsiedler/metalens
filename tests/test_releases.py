"""Dataset releases: a release is a frozen, self-contained copy of what dashboards read. Adding,
editing, verifying or deleting afterwards changes the live dataset and its fingerprint, never
the release. Skips without Postgres."""
from __future__ import annotations

import copy
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import analysis_table as at, records, releases  # noqa: E402
from test_analysis_table import HAC, _db_ok, _seed  # noqa: E402


def _second_paper() -> dict:
    doc = copy.deepcopy(HAC)
    doc["paper_metadata"] = {"title": "Another teams paper", "doi": "10.1/y", "year": 2025, "authors": ["Qazi A"], "journal": "K"}
    doc["experiments"][0]["conditions"][0]["measures"][0]["Avg_Perf_HumanAI"] = 0.91
    return doc


def _values(table: dict, column: str) -> list:
    k = [c["name"] for c in table["columns"]].index(column)
    return [row["v"][k] for row in table["rows"]]


def test_release_is_frozen_while_the_dataset_moves_on() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    sess = f"rel-{uuid.uuid4().hex[:6]}"
    ds, doc1 = _seed(conn, HAC, "human-ai-collab", sess)
    fp0 = releases.fingerprint(conn, ds)
    assert releases.pending(conn, ds)["changed"] is True and releases.latest(conn, ds) is None

    r1 = releases.create(conn, ds, notes="first")
    assert r1["number"] == 1 and r1["changes"]["first"] and r1["changes"]["n_papers"] == 1 and len(r1["content_sha"]) == 64
    assert releases.changed_since(conn, ds, r1) is False and releases.fingerprint(conn, ds) == fp0
    live = at.build(conn, ds, "conditions.measures", owner=True)
    frozen = at.build(conn, ds, "conditions.measures", owner=True, release=releases.get(conn, r1["id"], with_snapshot=True))
    assert frozen["rows"] == live["rows"] and frozen["columns"] == live["columns"]          # same table, two sources
    assert frozen["dataset"]["release"]["number"] == 1 and live["dataset"]["release"] is None

    # the dataset moves on: a paper is added, a row verified, a value corrected
    from test_analysis_table import _seed_into
    _seed_into(conn, _second_paper(), "human-ai-collab", sess, ds)
    assert releases.fingerprint(conn, ds) != fp0 and releases.changed_since(conn, ds, r1) is True
    rec = conn.execute("SELECT id::text FROM record WHERE dataset_id = %s::uuid AND document_id = %s::uuid LIMIT 1", (ds, doc1)).fetchone()[0]
    fp1 = releases.fingerprint(conn, ds)
    records.verify_record(conn, rec, status="verified", verifier_kind="maintainer")
    assert releases.fingerprint(conn, ds) != fp1                                           # a verification is a change too

    pend = releases.pending(conn, ds)
    assert pend["next_number"] == 2 and [p["title"] for p in pend["changes"]["papers_added"]] == ["Another teams paper"]
    assert pend["changes"]["status"]["newly_verified"] == 1 and pend["warnings"]["unverified_in_new_papers"] >= 1

    again = at.build(conn, ds, "conditions.measures", owner=False, release=releases.get(conn, r1["id"], with_snapshot=True))
    assert again["rows"] == frozen["rows"] and len(at.build(conn, ds, "conditions.measures", owner=True)["rows"]) > len(frozen["rows"])
    assert all("filename" not in r and "document_id" not in r for r in again["records"])    # the bright wall holds for releases

    r2 = releases.create(conn, ds)
    assert r2["number"] == 2 and r2["changes"]["papers_added"] and (records.get_dataset(conn, ds) or {}).get("version") == 2
    assert releases.ensure_current(conn, ds, reason="github_publish")["number"] == 2          # nothing changed: no new release

    # deleting the first paper does not touch either release: rows AND evidence survive
    cell = {"record_id": rec, "path": "conditions[0].measures[0]", "column": "Avg_Perf_Human"}
    before = at.cell_evidence(conn, ds, "conditions.measures", [cell], release=releases.get(conn, r1["id"], with_snapshot=True))
    assert before and before[0]["items"][0]["snippet"] == "accuracy 0.70 0.80 0.78"
    records.delete_document(conn, doc1)
    assert releases.changed_since(conn, ds, r2) is True
    after = at.cell_evidence(conn, ds, "conditions.measures", [cell], release=releases.get(conn, r1["id"], with_snapshot=True))
    assert after[0]["items"][0]["snippet"] == "accuracy 0.70 0.80 0.78" and after[0]["kind"] == before[0]["kind"]
    assert _values(at.build(conn, ds, "conditions.measures", owner=False, release=releases.get(conn, r2["id"], with_snapshot=True)), "Avg_Perf_HumanAI") \
        == _values(at.build(conn, ds, "conditions.measures", owner=False, release=releases.get_by_number(conn, ds, 2, with_snapshot=True)), "Avg_Perf_HumanAI")
    assert releases.pending(conn, ds)["changes"]["papers_removed"][0]["title"] == "Teams paper"

    # releases go with their dataset
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds)
    assert conn.execute("SELECT count(*) FROM dataset_release WHERE dataset_id = %s::uuid", (ds,)).fetchone()[0] == 0
    conn.close()


def test_content_sha_ignores_the_layout_cache_and_the_preset_is_frozen() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    ds, _ = _seed(conn, HAC, "human-ai-collab", f"rel-{uuid.uuid4().hex[:6]}")
    snap = releases.snapshot(conn, ds)
    sha = releases.content_sha(snap)
    snap2 = copy.deepcopy(snap); snap2["evidence"][0]["context"] = {"caption": "Table 3", "cells": []}
    assert releases.content_sha(snap2) == sha                                              # a PDF-derived cache is not content
    snap3 = copy.deepcopy(snap); snap3["records"][0]["status"] = "flagged"
    assert releases.content_sha(snap3) != sha

    rel = releases.create(conn, ds)
    full = releases.get(conn, rel["id"], with_snapshot=True)
    assert (full["snapshot"]["spec"]["display"]["analysis"]["complete"]["columns"]) == ["Avg_Perf_Human", "Avg_Perf_AI", "Avg_Perf_HumanAI"]
    # a later change of the preset's analysis settings does not reach the release …
    frozen_spec = copy.deepcopy(full["snapshot"]["spec"])
    full["snapshot"]["spec"]["display"]["analysis"].pop("complete")                         # simulate "the preset changed" on the cached copy
    assert at.build(conn, ds, "conditions.measures", owner=False, release=full)["dataset"]["left_out"] is None
    full["snapshot"]["spec"] = frozen_spec
    assert at.build(conn, ds, "conditions.measures", owner=False, release=full)["dataset"]["left_out"] is not None
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()


def test_release_api_is_owner_only_and_refuses_an_unchanged_dataset() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    sess = f"rel-api-{uuid.uuid4().hex[:6]}"; mine, other = {"X-Session-Id": sess}, {"X-Session-Id": f"o-{uuid.uuid4().hex[:6]}"}
    ds, _ = _seed(conn, HAC, "human-ai-collab", sess)
    c = TestClient(appmod.app)
    assert c.get(f"/api/datasets/{ds}/releases", headers=other).status_code == 404          # private dataset
    assert c.get(f"/api/datasets/{ds}/releases", headers=mine).json() == {"releases": [], "head": {"changed": True}}
    assert c.get(f"/api/datasets/{ds}/releases/pending", headers=mine).json()["next_number"] == 1
    made = c.post(f"/api/datasets/{ds}/releases", json={"notes": "first cut"}, headers=mine).json()
    assert made["number"] == 1 and made["notes"] == "first cut" and "snapshot" not in made and "fingerprint" not in made
    assert c.post(f"/api/datasets/{ds}/releases", json={}, headers=mine).status_code == 409
    assert c.get(f"/api/datasets/{ds}/releases", headers=mine).json()["head"] == {"changed": False}
    t = c.get(f"/api/datasets/{ds}/analysis?release=1", headers=mine).json()
    assert t["dataset"]["release"]["number"] == 1 and t["rows"]
    assert c.get(f"/api/datasets/{ds}/analysis?release=7", headers=mine).status_code == 404
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()
