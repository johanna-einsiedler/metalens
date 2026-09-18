"""Audit report: the stored ORIGINAL model response is compared with the reviewed records, so
a changed value, a removed table row, a deleted entry and a manual addition are all counted,
and sensitivity / precision / Jaccard follow (a wrong value penalised twice). Skips without
Postgres; the model is a fake and object storage a temp dir."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import audit, extract, presets, records, storage  # noqa: E402
from paperlens.extract import LLMResult  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _pdf() -> bytes:
    import fitz
    d = fitz.open(); d.new_page().insert_text((72, 90), "Table 1 loadings .70 .60 .50 n = 100"); out = d.tobytes(); d.close()
    return out


def _sample(sid: str, loadings: list[float], n: int) -> dict:
    return {"sample_id": sid, "factor_loadings": [{"item": i + 1, "factor": "F1", "loading": v} for i, v in enumerate(loadings)],
            "factor_correlations": [], "country": "Austria", "lang": None, "n": n, "female": None, "age": None,
            "nfac": 1, "cfa": 0, "met": None, "notes": "model note",
            "extraction_confidence": {"factor_loadings": "high", "factor_correlations": "low", "metadata": "medium"}}


def test_audit_counts_changes_removals_additions_and_indices() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    sess = f"audit-{uuid.uuid4().hex[:6]}"
    result = {"paper_metadata": {"title": "Audit paper", "doi": None, "year": 2020, "authors": ["A B"], "journal": None},
              "samples": [_sample("s1", [0.70, 0.60, 0.50, 0.40], 100), _sample("s2", [0.30, 0.20], 50), _sample("s3", [0.10], 10)],
              "evidence": []}
    run = presets.resolve_run(conn, preset_id="masem-indirect", params={})
    with conn.transaction():
        records.upsert_schema(conn, run.schema_id, run.field_defs)
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        out = extract.run_extraction(conn, _pdf(), run.prompt, model="fake", schema_id=run.schema_id, session_id=sess,
                                     filename="audit.pdf", params=run.params, spec=run.spec, store=store,
                                     complete=lambda *a, **k: LLMResult(text="```json\n" + json.dumps(result) + "\n```", finish_reason="stop"))
        ds = records.create_dataset(conn, title="Audit set", schema_id=run.schema_id, session_id=sess)
        records.assign_document_to_dataset(conn, ds["id"], out["document_id"])
        recs = {r["entry_index"]: r for r in records.document_view(conn, out["document_id"])["records"]}

        # s1 (reviewed): one loading corrected, one row removed, one row added, country filled in → verified
        fv = json.loads(json.dumps(recs[0]["field_values"]))
        fv["factor_loadings"][1]["loading"] = 0.65                       # wrong value
        del fv["factor_loadings"][3]                                     # overextracted row
        fv["factor_loadings"].append({"item": 5, "factor": "F1", "loading": 0.35})   # omission
        fv["lang"] = "German"                                            # omission (scalar)
        records.verify_record(conn, recs[0]["id"], status="verified", field_values=fv)
        # s2: edited but NOT verified → outside the indices
        fv2 = json.loads(json.dumps(recs[1]["field_values"])); fv2["n"] = 55
        records.verify_record(conn, recs[1]["id"], status="corrected", field_values=fv2)
        # s3: deleted as out of scope → its values are overextractions
        records.delete_record(conn, recs[2]["id"])

        rep = audit.dataset_audit(conn, ds["id"], store=store)
        rows = {r["target"]: r for r in rep["rows"]}
        fl = rows["factor_loadings.loading"]
        assert fl["label"] == "Factor loadings" and fl["group"] == "Factor loadings"
        assert fl["extracted"] == 7                                       # 4 + 2 + 1
        assert (fl["reviewed"], fl["unchanged"], fl["changed"], fl["removed"], fl["added"]) == (5, 2, 1, 2, 1)
        # TP 2, FP = changed 1 + removed 2, FN = changed 1 + added 1
        assert fl["pre"] == round(2 / 5, 4) and fl["sen"] == round(2 / 4, 4) and fl["jac"] == round(2 / 7, 4)
        assert rows["lang"]["added"] == 1 and rows["lang"]["sen"] == 0.0 and rows["lang"]["pre"] is None
        n = rows["n"]
        assert n["extracted"] == 3 and n["reviewed"] == 2 and n["unchanged"] == 1 and n["removed"] == 1 and n["changed"] == 0
        assert "notes" not in rows and "factor_loadings.item" not in rows     # free text and row keys are not targets
        assert rep["n_entries"] == 3 and rep["n_entries_reviewed"] == 2 and rep["n_entries_removed"] == 1
        assert rep["edits_in_unreviewed_entries"] == 1 and rep["n_papers_without_original"] == 0

        # a paper whose original response is gone is reported, not guessed at
        store.delete(storage.raw_key(out["document_id"])) if hasattr(store, "delete") else os.remove(os.path.join(d, storage.raw_key(out["document_id"])))
        rep = audit.dataset_audit(conn, ds["id"], store=store)
        assert rep["n_papers_without_original"] == 1 and rep["rows"] == []
    records.clear_dataset_documents(conn, ds["id"]); records.delete_dataset(conn, ds["id"]); conn.commit(); conn.close()


def test_audit_endpoint_follows_the_dataset_gate() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    sess = f"audit-gate-{uuid.uuid4().hex[:6]}"
    ds = records.create_dataset(conn, title="Gate set", schema_id=None, session_id=sess); conn.commit()
    c = TestClient(appmod.app)
    assert c.get(f"/api/datasets/{ds['id']}/audit", headers={"X-Session-Id": "someone-else"}).status_code == 404
    r = c.get(f"/api/datasets/{ds['id']}/audit", headers={"X-Session-Id": sess})
    assert r.status_code == 200 and r.json()["rows"] == [] and r.json()["total"]["extracted"] == 0
    records.delete_dataset(conn, ds["id"]); conn.commit(); conn.close()
