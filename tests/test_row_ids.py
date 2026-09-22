"""Evidence refers to sub-entry and table rows by a permanent row id, not by position: a row
that is deleted or inserted cannot shift a quote onto its neighbour. The ids are internal:
they never appear in the publishable form, and the round-trip invariant holds."""
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

from paperlens import contract, records  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402
from paperlens.reconstruct import reconstruct_publishable  # noqa: E402

RESULT = {
    "paper_metadata": {"title": "Row ids", "doi": None, "year": 2021, "authors": ["A B"], "journal": None},
    "samples": [{"sample_id": "s1", "n": 100,
                 "factor_loadings": [{"item": k + 1, "factor": "F1", "loading": v} for k, v in enumerate([0.7, 0.6, 0.5, 0.4])],
                 "conditions": [{"name": "c0", "measures": [{"m": "acc", "v": 0.8}, {"m": "f1", "v": 0.7}]},
                                {"name": "c1", "measures": [{"m": "acc", "v": 0.9}]}]}],
    "evidence": [{"snippet": "item 3 loads .50", "page": 2, "source": "Table 1", "field": "samples[0].factor_loadings[2]"},
                 {"snippet": "item 4 loads .40", "page": 2, "source": "Table 1", "field": "samples[0].factor_loadings[3]"},
                 {"snippet": "c1 accuracy .90", "page": 3, "source": "Table 2", "field": "samples[0].conditions[1].measures[0]"},
                 {"snippet": "N = 100", "page": 1, "source": None, "field": "samples[0].n"}]}


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def test_ingest_assigns_ids_and_refs_and_nothing_leaks() -> None:
    res = ingest(copy.deepcopy(RESULT), entries_key="samples")
    fv = res.records[0].field_values
    assert [r["_rid"] for r in fv["factor_loadings"]] == ["o0", "o1", "o2", "o3"]
    assert fv["conditions"][1]["_rid"] == "o1" and fv["conditions"][1]["measures"][0]["_rid"] == "o0"
    refs = {s.field_path: (s.child_rid, s.row_rid) for s in res.evidence}
    assert refs["samples[0].factor_loadings[2]"] == ("o2", None)
    assert refs["samples[0].conditions[1].measures[0]"] == ("o1", "o0")
    assert refs["samples[0].n"] == (None, None)
    out = reconstruct_publishable(res)
    assert "_rid" not in json.dumps(out)
    assert out == contract.strip_to_publishable(copy.deepcopy(RESULT), entries_key="samples")   # round trip holds
    assert ingest(copy.deepcopy(RESULT), entries_key="samples").records[0].field_values == fv       # ids are deterministic


def test_live_path_follows_the_row_not_the_position() -> None:
    fv = ingest(copy.deepcopy(RESULT), entries_key="samples").records[0].field_values
    del fv["factor_loadings"][1]                                        # a reviewer removes item 2
    fv["factor_loadings"].insert(0, {"item": 0, "factor": "F1", "loading": 0.1, "_rid": "m-abc"})   # and adds one on top
    assert contract.live_path("samples[0].factor_loadings[2]", "o2", None, fv) == "samples[0].factor_loadings[2]"   # item 3: 2 → 1 → 2
    assert contract.live_path("samples[0].factor_loadings[3]", "o3", None, fv) == "samples[0].factor_loadings[3]"
    assert fv["factor_loadings"][2]["item"] == 3 and fv["factor_loadings"][3]["item"] == 4
    del fv["factor_loadings"][0]
    assert contract.live_path("samples[0].factor_loadings[2]", "o2", None, fv) == "samples[0].factor_loadings[1]"
    assert contract.live_path("samples[0].factor_loadings[1]", "o1", None, fv) == "samples[0].factor_loadings"      # row gone → whole table
    fv["conditions"].pop(0)
    assert contract.live_path("samples[0].conditions[1].measures[0]", "o1", "o0", fv) == "samples[0].conditions[0].measures[0]"
    assert contract.live_path("samples[0].n", None, None, fv) == "samples[0].n"
    assert contract.live_path("samples[0].factor_loadings[2]", None, None, fv) == "samples[0].factor_loadings[2]"  # old data: as stored


def test_document_view_serves_current_positions() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    doc = records.persist(conn, ingest(copy.deepcopy(RESULT), entries_key="samples"), schema_id=None,
                          source_job_id="rowids", session_id=f"rid-{uuid.uuid4().hex[:6]}")
    view = records.document_view(conn, doc)
    rec = view["records"][0]
    fv = copy.deepcopy(rec["field_values"]); del fv["factor_loadings"][0]          # item 1 removed
    records.verify_record(conn, rec["id"], status="corrected", field_values=fv)
    paths = {e["snippet"]: e["field_path"] for e in records.document_view(conn, doc)["evidence"]}
    assert paths["item 3 loads .50"] == "samples[0].factor_loadings[1]"            # followed the row
    assert paths["item 4 loads .40"] == "samples[0].factor_loadings[2]"
    assert paths["N = 100"] == "samples[0].n"
    records.delete_document(conn, doc) if hasattr(records, "delete_document") else None
    conn.commit(); conn.close()
