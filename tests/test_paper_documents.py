"""The documents of ONE paper across presets — the tables document and the claims document of an
article — listed for the workspace's paper strip, with the companion pairs and the record links
the cross-check gives both sides. Skips without Postgres."""
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
from test_analysis_table import _db_ok, _seed  # noqa: E402
from test_register_presets import CLAIMS, TABLES  # noqa: E402
from test_regression_tables_preset import RESULT as ECON  # noqa: E402


def _rec_ids(conn, doc):
    return [str(r[0]) for r in conn.execute("SELECT id FROM record WHERE document_id = %s::uuid ORDER BY entry_index", (doc,)).fetchall()]


def test_the_documents_of_one_paper_and_the_links_between_them() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    sess, osess = f"pd-{uuid.uuid4().hex[:6]}", f"po-{uuid.uuid4().hex[:6]}"
    mine, other = {"X-Session-Id": sess}, {"X-Session-Id": osess}
    claims_ds, cdoc = _seed(conn, CLAIMS, "register-claims", sess)      # the same DOI → one paper row for both
    tables_ds, tdoc = _seed(conn, TABLES, "register-tables", sess)
    base = records.documents_for_paper(conn, cdoc, session_id=sess)["n_hidden"]   # other tests' documents of this DOI
    _, hidden_doc = _seed(conn, TABLES, "register-tables", osess)        # someone else's document of the same paper
    c = TestClient(appmod.app)

    # the paper row ties the two together even without a PDF hash
    out = records.documents_for_paper(conn, cdoc, session_id=sess)
    assert out["paper"]["doi"] == "10.1/t" and out["pdf_sha256"] is None and out["n_hidden"] == base + 1
    assert [d["document_id"] for d in out["documents"]] == [cdoc, tdoc]
    assert [d["preset_id"] for d in out["documents"]] == ["register-claims", "register-tables"]
    assert [(d["n_records"], d["n_verified"], d["n_flagged"]) for d in out["documents"]] == [(2, 0, 0), (1, 0, 0)]
    assert [x["id"] for x in out["documents"][0]["datasets"]] == [claims_ds] and out["documents"][0]["datasets"][0]["title"] == "Analysis set"
    assert out["companions"] == []                                      # no companion set yet
    assert records.documents_for_paper(conn, tdoc, session_id=sess)["documents"][1]["document_id"] == tdoc   # the same strip from the other side
    assert records.documents_for_paper(conn, str(uuid.uuid4()), session_id=sess) is None
    assert records.documents_for_paper(conn, "not-a-uuid", session_id=sess) is None

    # the same PDF hash ties documents together too, whatever their paper rows
    sha = "a" * 64
    conn.execute("UPDATE extraction_document SET pdf_sha256 = %s WHERE id = ANY(%s::uuid[])", (sha, [cdoc, tdoc])); conn.commit()
    out = records.documents_for_paper(conn, cdoc, session_id=sess)
    assert out["pdf_sha256"] == sha and [d["document_id"] for d in out["documents"]] == [cdoc, tdoc]
    assert [d["pdf_sha256"] for d in out["documents"]] == [sha, sha]

    # the API: owner-only (the bright wall), preset titles filled in, the strangers' documents counted only
    r = c.get(f"/api/documents/{cdoc}/paper-documents", headers=mine)
    assert r.status_code == 200
    body = r.json()
    assert [d["preset_title"] for d in body["documents"]] == ["Claims in empirical economics papers", "Register-data tables"]
    assert body["n_hidden"] == base + 1 and all(d["mine"] for d in body["documents"])
    assert c.get(f"/api/documents/{cdoc}/paper-documents", headers=other).status_code == 404
    assert c.get(f"/api/documents/{uuid.uuid4()}/paper-documents", headers=mine).status_code == 404
    # the other account sees its own document of the paper, and ours only as hidden
    theirs = c.get(f"/api/documents/{hidden_doc}/paper-documents", headers=other).json()
    assert [d["document_id"] for d in theirs["documents"]] == [hidden_doc] and theirs["n_hidden"] == base + 2

    # once the claims dataset names the tables dataset as its companion, the strip knows the pair …
    assert c.patch(f"/api/datasets/{claims_ds}", json={"companion_dataset_id": tables_ds}, headers=mine).status_code == 200
    body = c.get(f"/api/documents/{tdoc}/paper-documents", headers=mine).json()
    assert body["companions"] == [{"dataset_id": claims_ds, "companion_dataset_id": tables_ds}]
    assert next(d for d in body["documents"] if d["document_id"] == cdoc)["datasets"][0]["companion_dataset_id"] == tables_ds
    # … and every verdict that resolves a column carries that column's record: the link from a
    # claim's result row to the regression column, and from the column back to the claims citing it
    (treg,) = _rec_ids(conn, tdoc)
    rows = c.get(f"/api/datasets/{claims_ds}/crosscheck", headers=mine).json()["rows"]
    assert [(r["result_id"], r["status"], r["companion_record_id"]) for r in rows] == [("R1", "exact", treg), ("R2", "unlinked", None)]
    assert rows[0]["record_id"] == _rec_ids(conn, cdoc)[0]

    # the economics regression tables are a registered companion of the claims preset as well
    econ_ds, _ = _seed(conn, ECON, "econ-regression-tables", sess)
    assert c.patch(f"/api/datasets/{claims_ds}", json={"companion_dataset_id": econ_ds}, headers=mine).status_code == 200
    assert (records.get_dataset(conn, claims_ds) or {})["companion_dataset_id"] == econ_ds
    assert c.get(f"/api/datasets/{claims_ds}/crosscheck", headers=mine).json()["companion_presets"] == ["register-tables", "econ-regression-tables"]
    for ds in (claims_ds, tables_ds, econ_ds):
        records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds)
    conn.close()
