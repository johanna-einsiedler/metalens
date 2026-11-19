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


# ── the paper block's tables ──────────────────────────────────────────────────

PAPER_TABLES = {
    "paper_metadata": {"title": "Paper tables", "doi": None, "year": 2022, "authors": ["C D"], "journal": None,
                       "statements": [{"text": "first", "page": 1}, {"text": "second", "page": 2}, {"text": "third", "page": 3}],
                       "scale": "lab"},
    "samples": [{"sample_id": "s1", "n": 10}],
    "evidence": [{"snippet": "the second statement", "page": 2, "source": "text", "field": "paper_metadata.statements[1]"},
                 {"snippet": "the third statement", "page": 3, "source": "text", "field": "paper_metadata.statements[2].text"},
                 {"snippet": "a lab study", "page": 1, "source": "text", "field": "paper_metadata.scale"}]}


def test_paper_table_rows_get_ids_and_paper_level_quotes_refer_to_them() -> None:
    res = ingest(copy.deepcopy(PAPER_TABLES), entries_key="samples")
    pm = res.paper_metadata_raw
    assert [r["_rid"] for r in pm["statements"]] == ["o0", "o1", "o2"] and "_rid" not in pm
    refs = {s.field_path: (s.child_rid, s.row_rid) for s in res.evidence}
    assert refs["paper_metadata.statements[1]"] == ("o1", None) and refs["paper_metadata.statements[2].text"] == ("o2", None)
    assert refs["paper_metadata.scale"] == (None, None)
    out = reconstruct_publishable(res)
    assert "_rid" not in json.dumps(out) and out["paper_metadata"]["statements"][1] == {"text": "second", "page": 2}
    assert out == contract.strip_to_publishable(copy.deepcopy(PAPER_TABLES), entries_key="samples")
    # the path follows the row once a row above it goes, and names the whole table once the row itself is gone
    del pm["statements"][0]
    assert contract.live_path("paper_metadata.statements[1]", "o1", None, pm) == "paper_metadata.statements[0]"
    assert contract.live_path("paper_metadata.statements[2].text", "o2", None, pm) == "paper_metadata.statements[1].text"
    del pm["statements"][0]
    assert contract.live_path("paper_metadata.statements[1]", "o1", None, pm) == "paper_metadata.statements"
    assert contract.row_refs("paper_metadata.statements[0]", pm) == ("o2", None)


def test_a_row_added_without_an_id_gets_one_and_the_others_keep_theirs() -> None:
    fv = {"rows": [{"a": 1, "_rid": "o0"}, {"a": 2}, {"a": 3, "_rid": "c9zz", "inner": [{"b": 1}, {"b": 2, "_rid": "o1"}]}], "scalar": 5, "plain": [1, 2]}
    contract.ensure_row_ids(fv)
    ids = [r["_rid"] for r in fv["rows"]]
    assert ids[0] == "o0" and ids[2] == "c9zz" and ids[1].startswith("n") and len(ids[1]) == 9
    assert fv["rows"][2]["inner"][0]["_rid"].startswith("n") and fv["rows"][2]["inner"][1]["_rid"] == "o1"
    assert fv["plain"] == [1, 2] and fv["scalar"] == 5
    contract.ensure_row_ids(None)                                     # nothing to do, no error


def test_paper_table_edits_keep_the_ids_and_the_view_follows_the_rows() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    doc = records.persist(conn, ingest(copy.deepcopy(PAPER_TABLES), entries_key="samples"), schema_id=None,
                          source_job_id="rowids-pm", session_id=f"rid-{uuid.uuid4().hex[:6]}")
    view = records.document_view(conn, doc)
    pm = copy.deepcopy(view["paper_metadata"])
    assert [r["_rid"] for r in pm["statements"]] == ["o0", "o1", "o2"]            # the view keeps the ids (the client sends them back)
    rows = pm["statements"][1:] + [{"text": "added by hand", "page": 4}]           # the first row removed, one added without an id
    out = records.set_paper_metadata_fields(conn, doc, {"statements": rows})
    kept = out["paper_metadata"]["statements"]
    assert [r["_rid"] for r in kept[:2]] == ["o1", "o2"] and kept[2]["_rid"].startswith("n") and out["changed"] == ["statements"]
    paths = {e["snippet"]: e["field_path"] for e in records.document_view(conn, doc)["evidence"]}
    assert paths["the second statement"] == "paper_metadata.statements[0]" and paths["the third statement"] == "paper_metadata.statements[1].text"
    assert paths["a lab study"] == "paper_metadata.scale"
    # the audit diff carries no internal id
    ev = conn.execute("SELECT diff FROM verification_event WHERE document_id = %s::uuid AND record_id IS NULL ORDER BY created_at DESC LIMIT 1", (doc,)).fetchone()[0]
    assert "_rid" not in json.dumps(ev) and ev[0]["final_value"][2] == {"text": "added by hand", "page": 4}
    # a record edit that adds a row without an id gets one too
    rec = records.document_view(conn, doc)["records"][0]
    fv = copy.deepcopy(rec["field_values"]); fv["extra_rows"] = [{"k": "x"}]
    records.verify_record(conn, rec["id"], status="corrected", field_values=fv)
    stored = records.document_view(conn, doc)["records"][0]["field_values"]
    assert stored["extra_rows"][0]["_rid"].startswith("n")
    records.delete_document(conn, doc) if hasattr(records, "delete_document") else None
    conn.commit(); conn.close()
