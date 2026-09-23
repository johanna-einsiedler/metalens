"""A dataset checked against its companion (claims' point estimates against the tables' cells):
the pure matching rules, the _crosscheck column, the frozen verdicts of a release, the export,
and the API. Skips the database parts without Postgres."""
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

from paperlens import analysis_table as at, crosscheck as cc, records, release_export, releases  # noqa: E402
from test_analysis_table import _db_ok, _seed  # noqa: E402
from test_register_presets import CLAIMS, TABLES  # noqa: E402


def _row(**kw) -> dict:
    base = {"record_id": "r", "path": "results[0]", "claim_id": "S1", "result_id": "R1", "doi": "10.1/t", "title": "T", "exhibit": "table",
            "source_table": "Table 3", "panel": None, "column": "(1)", "point_estimate": -1.292}
    return {**base, **kw}


def _reg(**kw) -> dict:
    base = {"record_id": "x", "paper": "10.1/t", "regression_id": "T3::_::C1", "table": "3", "panel": "", "column": "1", "coefficients": [-1.292, 0.4]}
    return {**base, **kw}


def test_matching_rules() -> None:
    assert cc.table_token("Table II") == "2" and cc.table_token("Table A.I") == "A1" and cc.table_token("(3)") == "3" and cc.table_token("Figure 2") is None
    assert cc.panel_token("Panel A.i - Completed upper secondary") == "A.I" and cc.panel_token("A. Vocational") == "A" and cc.panel_token(None) == ""
    assert cc.panels_compatible("A", "A1") and cc.panels_compatible("", "B") and not cc.panels_compatible("A", "B")
    assert cc.num_match(-1.292, -1.292) and cc.num_match(0.2140, 0.214) and cc.num_match(100.4, 100.9) and not cc.num_match(0.046, 0.047)
    regs = [_reg(), _reg(regression_id="T4::A::C1", table="4", panel="A", coefficients=[-1.991]), _reg(regression_id="T4::B::C1", table="4", panel="B", coefficients=[0.5])]
    out = cc.statuses([_row(), _row(result_id="R2", source_table="Table IV", panel="A", point_estimate=-1.991),      # roman table, panel letter
                       _row(result_id="R3", source_table="Table 4", panel=None, point_estimate=-1.99),               # no panel: both panels are candidates; tolerance
                       _row(result_id="R4", source_table="Table 3", point_estimate=-1.3),                             # resolves, no cell equals it
                       _row(result_id="R5", source_table="Table 9"), _row(result_id="R6", point_estimate=None),
                       _row(result_id="R7", exhibit="figure", source_table="Figure 2"),
                       _row(result_id="R7b", exhibit="figure", source_table="Figure 3", value_from="text"),   # the analysis is a figure, the number is in the prose
                       _row(result_id="R8", doi="10.9/other", title="Other")], regs)
    assert [r["status"] for r in out] == ["exact", "exact", "exact", "mismatch", "unlinked", "no_estimate", "figure", "stated", "unlinked"]
    assert "states this number in its prose" in out[7]["detail"] and "Figure 3" in out[7]["detail"]
    assert out[0]["regression_id"] == "T3::_::C1" and out[3]["table_coefficients"] == [-1.292, 0.4]
    assert cc.summary(out) == {"exact": 3, "mismatch": 1, "unlinked": 2, "no_estimate": 1, "figure": 1, "stated": 1}
    assert cc.companion_preset("register-claims@abc") == "register-tables" and cc.companion_preset("human-ai-collab@x") is None
    assert cc.check_for("register-claims@1", "register-tables@2")["unit"] == "results" and cc.check_for("register-tables@2", "register-claims@1") is None


def test_column_release_export_and_api() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    sess = f"cc-{uuid.uuid4().hex[:6]}"; mine, other = {"X-Session-Id": sess}, {"X-Session-Id": f"o-{uuid.uuid4().hex[:6]}"}
    claims_ds, _ = _seed(conn, CLAIMS, "register-claims", sess)
    tables_ds, tdoc = _seed(conn, TABLES, "register-tables", sess)
    c, stranger = TestClient(appmod.app), TestClient(appmod.app)
    # nothing shows without a companion; the check only says it applies
    assert c.get(f"/api/datasets/{claims_ds}/crosscheck", headers=mine).json() == {"applies": True, "companion_preset": "register-tables", "companion": None, "frozen": False, "summary": None, "rows": []}
    assert c.get(f"/api/datasets/{tables_ds}/crosscheck", headers=mine).json()["applies"] is False
    assert "_crosscheck" not in {col["name"] for col in at.build(conn, claims_ds, "results", owner=True)["columns"]}
    # setting the companion: mine, of the registered preset pair
    assert c.patch(f"/api/datasets/{claims_ds}", json={"companion_dataset_id": claims_ds}, headers=mine).status_code == 422
    assert c.patch(f"/api/datasets/{tables_ds}", json={"companion_dataset_id": claims_ds}, headers=mine).status_code == 422      # wrong direction
    assert stranger.patch(f"/api/datasets/{claims_ds}", json={"companion_dataset_id": tables_ds}, headers=other).status_code == 403
    assert c.patch(f"/api/datasets/{claims_ds}", json={"companion_dataset_id": tables_ds}, headers=mine).status_code == 200
    assert (records.get_dataset(conn, claims_ds) or {})["companion_dataset_id"] == tables_ds
    # live: R1 (Table 3 column (1), -1.292) is exact; R2 (Table 4 A) has no column in the companion
    t = at.build(conn, claims_ds, "results", owner=True)
    col = next(x for x in t["columns"] if x["name"] == "_crosscheck")
    k = t["columns"].index(col)
    assert col["scope"] == "check" and [r["v"][k] for r in t["rows"]] == ["exact", "unlinked", None]     # the claim without results has no verdict
    live = c.get(f"/api/datasets/{claims_ds}/crosscheck", headers=mine).json()
    assert live["companion"]["id"] == tables_ds and live["companion_unreleased"] is True and live["summary"] == {"exact": 1, "mismatch": 0, "unlinked": 1, "no_estimate": 0, "figure": 0, "stated": 0}
    assert live["rows"][0]["regression_id"] == "T3::_::C1" and live["rows"][0]["result_id"] == "R1"
    # a release freezes the verdicts (against the companion's release, once it has one) and exports them
    assert c.post(f"/api/datasets/{tables_ds}/releases", json={}, headers=mine).json()["number"] == 1
    pend = c.get(f"/api/datasets/{claims_ds}/releases/pending", headers=mine).json()
    assert pend["warnings"]["crosscheck_mismatch"] == 0 and pend["warnings"]["crosscheck_unlinked"] == 1
    rel = c.post(f"/api/datasets/{claims_ds}/releases", json={}, headers=mine).json()
    full = releases.get_by_number(conn, claims_ds, rel["number"], with_snapshot=True)
    fz = full["snapshot"]["crosscheck"]
    assert fz["companion_dataset_id"] == tables_ds and fz["companion_release"] == 1 and [r["status"] for r in fz["rows"]] == ["exact", "unlinked"]
    files = release_export.build(conn, full)
    meta = json.loads(files["release.json"])
    assert meta["companion"]["release"] == 1 and meta["companion"]["summary"]["exact"] == 1 and "crosscheck.json" in meta["files"]
    assert json.loads(files["crosscheck.json"])["rows"][0]["status"] == "exact"
    frozen = c.get(f"/api/datasets/{claims_ds}/crosscheck?release=1", headers=mine).json()
    assert frozen["frozen"] is True and frozen["summary"]["exact"] == 1
    # the companion loses its paper: live still reads the companion's RELEASE (frozen there), so nothing moves
    records.delete_document(conn, tdoc)
    assert [r["v"][k] for r in at.build(conn, claims_ds, "results", owner=True)["rows"]][:2] == ["exact", "unlinked"]
    # the companion dataset disappears altogether: the live column goes, the release keeps its verdicts
    records.clear_dataset_documents(conn, tables_ds); records.delete_dataset(conn, tables_ds)
    assert (records.get_dataset(conn, claims_ds) or {})["companion_dataset_id"] is None
    assert "_crosscheck" not in {col["name"] for col in at.build(conn, claims_ds, "results", owner=True)["columns"]}
    t_rel = at.build(conn, claims_ds, "results", owner=False, release=full)
    kk = [x["name"] for x in t_rel["columns"]].index("_crosscheck")
    assert [r["v"][kk] for r in t_rel["rows"]][:2] == ["exact", "unlinked"]
    gone = c.get(f"/api/datasets/{claims_ds}/crosscheck?release=1", headers=mine).json()
    assert gone["frozen"] is True and gone["summary"]["exact"] == 1 and gone["companion"]["title"] is None
    records.clear_dataset_documents(conn, claims_ds); records.delete_dataset(conn, claims_ds)
    conn.close()
