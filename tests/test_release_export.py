"""A release as static files: what a dashboard written outside Metalens reads. The files hold the
same tables and the same evidence the API serves for that release, and nothing a public reader
must not see. Skips without Postgres."""
from __future__ import annotations

import io
import json
import os
import sys
import uuid
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import analysis_table as at, records, release_export, releases  # noqa: E402
from test_analysis_table import HAC, INDIRECT, _db_ok, _seed  # noqa: E402


def test_files_hold_the_release_tables_and_evidence() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    ds, doc = _seed(conn, HAC, "human-ai-collab", f"rx-{uuid.uuid4().hex[:6]}")
    rel = releases.get(conn, releases.create(conn, ds, notes="first")["id"], with_snapshot=True)
    files = release_export.build(conn, rel)
    assert set(files) == {"release.json", "README.md", "tile-prompt.md", "evidence.json", "tables/entries.json", "tables/conditions.json", "tables/conditions.measures.json"}
    assert b"1200x630" in files["tile-prompt.md"] and b"metalens.json" in files["tile-prompt.md"]   # the house style travels with the release
    meta = json.loads(files["release.json"])
    assert meta["format"] == "metalens-release" and meta["release"]["number"] == 1 and meta["release"]["content_sha"] == rel["content_sha"]
    assert meta["release"]["notes"] == "first" and meta["preset"]["id"] == "human-ai-collab" and sorted(meta["files"]) == sorted(files)
    assert meta["dataset"]["keywords"] == [] and "description" in meta["dataset"]                # what a dashboard's manifest can carry over
    unit = next(u for u in meta["units"] if u["default"])
    assert unit["id"] == "conditions.measures" and unit["file"] == "tables/conditions.measures.json" and unit["n_rows"] == 2
    g = next(c for c in unit["columns"] if c["name"] == "g_team_vs_human")
    assert g["derived"]["formula"] == "hedges_g" and "Avg_Perf_HumanAI" in g["derived"]["inputs"]      # the contract names computed columns

    # the table is the one the API serves for that release
    table = json.loads(files[unit["file"]])
    api = at.build(conn, ds, "conditions.measures", owner=False, release=rel)
    assert table["rows"] == api["rows"] and table["columns"] == api["columns"] and table["records"] == api["records"]

    # evidence: row 0 is quoted as a table row; both of its means point at the SAME stored item
    ev = json.loads(files["evidence.json"])
    row0 = ev["cells"]["conditions.measures"]["0"]
    assert row0["Avg_Perf_Human"]["k"] == "row" and row0["Avg_Perf_Human"]["i"] == row0["Avg_Perf_AI"]["i"]
    item = ev["items"][row0["Avg_Perf_Human"]["i"][0]]
    assert item == {"snippet": "accuracy 0.70 0.80 0.78", "page": 5, "source": "Table 3"}
    assert "g_team_vs_human" not in row0 and "_study" not in row0                       # computed and system columns carry none
    assert len(ev["items"]) == len({json.dumps(i, sort_keys=True) for i in ev["items"]})     # every quote once
    # same answer as the evidence API gives for that cell of that release
    cell = {"record_id": table["records"][0]["id"], "path": table["rows"][0]["p"], "column": "Avg_Perf_Human"}
    assert at.cell_evidence(conn, ds, "conditions.measures", [cell], release=rel)[0]["items"][0]["snippet"] == item["snippet"]

    # nothing a public reader must not see, anywhere in the files
    blob = b"".join(files.values()).decode("utf-8")
    assert "secret-name.pdf" not in blob and doc not in blob and '"rect"' not in blob and "document_id" not in blob
    assert "# Analysis set — release v1" in files["README.md"].decode() and rel["content_sha"] in files["README.md"].decode()

    # frozen: the export of v1 does not change when the dataset does
    records.delete_document(conn, doc)
    assert release_export.build(conn, releases.get(conn, rel["id"], with_snapshot=True)) == files
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()


def test_entry_level_tables_and_the_download() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    sess = f"rx-{uuid.uuid4().hex[:6]}"; mine, other = {"X-Session-Id": sess}, {"X-Session-Id": f"o-{uuid.uuid4().hex[:6]}"}
    ds, _ = _seed(conn, INDIRECT, "masem-indirect", sess)
    releases.create(conn, ds)
    c = TestClient(appmod.app)
    assert c.get(f"/api/datasets/{ds}/releases/1/export", headers=other).status_code == 404       # a private dataset
    assert c.get(f"/api/datasets/{ds}/releases/2/export", headers=mine).status_code == 404
    r = c.get(f"/api/datasets/{ds}/releases/1/export", headers=mine)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip" and "-v1.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content)); names = z.namelist(); folder = names[0].split("/")[0]
    assert f"{folder}/release.json" in names and f"{folder}/tables/factor_loadings.json" in names and f"{folder}/evidence.json" in names
    ev = json.loads(z.read(f"{folder}/evidence.json"))
    kinds = {cell["k"] for rows in ev["cells"]["factor_loadings"].values() for cell in rows.values()}
    assert "row" in kinds                                                                # a loading is supported by its table row
    one = c.get(f"/api/datasets/{ds}/releases/1/export?file=release.json", headers=mine)
    assert one.status_code == 200 and one.json()["release"]["number"] == 1
    assert c.get(f"/api/datasets/{ds}/releases/1/export?file=../secrets", headers=mine).status_code == 404
    assert c.get(f"/api/datasets/{ds}/releases/1/export", headers=mine).content == r.content         # same release, same bytes
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()
