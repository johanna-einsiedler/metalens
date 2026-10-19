"""The 200-character rule end to end: what leaves for a public reader is cut around the value,
what the owner reads is whole — in cell evidence, in the release files, and in the three public
APIs (analysis evidence, record detail, provenance). Skips without Postgres."""
from __future__ import annotations

import copy
import json
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

from paperlens import analysis_table as at, exporter, quotes, records, release_export, releases  # noqa: E402
from test_analysis_table import HAC, _db_ok, _seed  # noqa: E402

LONG_ROW = ("The accuracy of the three conditions, measured on the held-out items of the second experiment and "
            "averaged over the four task families, was accuracy 0.70 0.80 0.78 for the human-alone, AI-alone and "
            "human–AI team conditions respectively; the team's advantage over the human alone held in every task "
            "family but the smallest, where the two were indistinguishable.")
LONG_ENTRY = ("Participants in the first experiment used the LLM through a chat interface during the second half of "
              "the session, after a calibration block without it; the order of the blocks was fixed, and the same "
              "items were shown to every participant in the same order.")


def _long_hac() -> dict:
    r = copy.deepcopy(HAC)
    for e in r["evidence"]:
        if e["snippet"].startswith("accuracy"):
            e["snippet"] = LONG_ROW
        elif e["snippet"].startswith("participants"):
            e["snippet"] = LONG_ENTRY
    assert len(LONG_ROW) > 200 and len(LONG_ENTRY) > 200
    return r


def _cell(conn, ds):
    t = at.build(conn, ds, "conditions.measures", owner=False)
    row = t["rows"][0]; rid = t["records"][row["r"]]["id"]
    return rid, {"record_id": rid, "path": row["p"], "column": "Avg_Perf_Human"}


def test_cell_evidence_and_release_files_cut_quotes_around_the_value() -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    ds, _doc = _seed(conn, _long_hac(), "human-ai-collab", f"q-{uuid.uuid4().hex[:6]}")
    rid, cell = _cell(conn, ds)
    pub = at.cell_evidence(conn, ds, "conditions.measures", [cell])[0]["items"][0]
    assert pub["truncated"] is True and len(pub["snippet"]) <= 200 and "0.70" in pub["snippet"] and pub["page"] == 5
    assert pub["snippet"].startswith("…") and pub["snippet"].endswith("…") and pub["source"] == "Table 3"
    own = at.cell_evidence(conn, ds, "conditions.measures", [cell], full=True)[0]["items"][0]
    assert own["snippet"] == LONG_ROW and "truncated" not in own
    # the release files: every quote within the limit, the rule stated, the page named
    rel = releases.get(conn, releases.create(conn, ds, notes="q")["id"], with_snapshot=True)
    files = release_export.build(conn, rel)
    ev = json.loads(files["evidence.json"]); meta = json.loads(files["release.json"])
    assert ev["items"] and all(len(i["snippet"]) <= 200 for i in ev["items"]) and any(i.get("truncated") for i in ev["items"])
    assert ev["policy"]["max_chars"] == 200 and meta["evidence_policy"]["full_quotes_url"].endswith(f"/dataset?id={ds}")
    assert meta["dataset"]["id"] == ds and meta["dataset"]["url"] == meta["evidence_policy"]["full_quotes_url"]
    assert "200 characters" in files["README.md"].decode("utf-8")
    # the release's evidence API answers the same cut for a public reader
    assert at.cell_evidence(conn, ds, "conditions.measures", [cell], release=rel)[0]["items"][0]["snippet"] == pub["snippet"]
    conn.close()


def test_public_apis_cut_quotes_for_everyone_but_the_owner() -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens.app import app
    conn = records.connect(); records.init_db(conn)
    sess = f"q-{uuid.uuid4().hex[:6]}"
    ds, _doc = _seed(conn, _long_hac(), "human-ai-collab", sess)
    records.set_dataset_visibility(conn, ds, "public"); conn.commit()
    rid, cell = _cell(conn, ds)
    c = TestClient(app); OWNER = {"X-Session-Id": sess}; OTHER = {"X-Session-Id": f"o-{uuid.uuid4().hex[:6]}"}
    body = {"unit": "conditions.measures", "cells": [cell]}
    mine = c.post(f"/api/datasets/{ds}/analysis/evidence", json=body, headers=OWNER).json()["cells"][0]["items"][0]
    theirs = c.post(f"/api/datasets/{ds}/analysis/evidence", json=body, headers=OTHER).json()["cells"][0]["items"][0]
    anon = c.post(f"/api/datasets/{ds}/analysis/evidence", json=body).json()["cells"][0]["items"][0]
    assert mine["snippet"] == LONG_ROW and "truncated" not in mine
    assert theirs["truncated"] is True and len(theirs["snippet"]) <= 200 and "0.70" in theirs["snippet"]
    assert anon["snippet"] == theirs["snippet"]
    mine = c.get(f"/api/records/{rid}", headers=OWNER).json()["evidence"]
    theirs = c.get(f"/api/records/{rid}", headers=OTHER).json()["evidence"]
    assert any(len(e["snippet"]) > 200 for e in mine) and not any(e.get("truncated") for e in mine)
    assert theirs and all(len(e["snippet"]) <= 200 for e in theirs) and all(e.get("truncated") for e in theirs)
    mine = c.post("/api/records/provenance", json={"ids": [rid]}, headers=OWNER).json()["records"][0]
    theirs = c.post("/api/records/provenance", json={"ids": [rid]}, headers=OTHER).json()["records"][0]
    assert len(mine["snippet"]) > 200 and "truncated" not in mine
    assert len(theirs["snippet"]) <= 200 and theirs["truncated"] is True and theirs["page"] == mine["page"]
    conn.close()


def _snippets(x):
    if isinstance(x, dict):
        if isinstance(x.get("snippet"), str):
            yield x
        for v in x.values():
            yield from _snippets(v)
    elif isinstance(x, list):
        for v in x:
            yield from _snippets(v)


def test_the_published_dataset_file_is_cut_too() -> None:
    """results.json in the datasets repository is a public file: the same rule, centred on the
    value each item's field path names; the document in the database is untouched."""
    doc = {"paper_metadata": {"title": "T"}, "experiments": [{"conditions": [{"measures": [{"mean": 0.7}]}]}],
           "evidence": [{"field": "experiments[0].conditions[0].measures[0].mean", "snippet": LONG_ROW, "page": 5, "source": "Table 3"},
                        {"field": "experiments[0]", "snippet": "short", "page": 1, "source": None}]}
    capped = quotes.cap_document(doc)
    assert len(doc["evidence"][0]["snippet"]) > 200                                   # the input is untouched
    assert capped["evidence"][0]["truncated"] and "0.70" in capped["evidence"][0]["snippet"] and len(capped["evidence"][0]["snippet"]) <= 200
    assert capped["evidence"][1] == doc["evidence"][1]
    if not _db_ok():
        pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    ds, _doc = _seed(conn, _long_hac(), "human-ai-collab", f"q-{uuid.uuid4().hex[:6]}")
    material = exporter.materialize_dataset(conn, ds)
    items = list(_snippets(material["results"]))
    assert items and all(len(i["snippet"]) <= 200 for i in items) and any(i.get("truncated") for i in items)
    conn.close()
