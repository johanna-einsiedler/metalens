"""A cell edit is applied on the record as stored (``patch``), not by the page sending the whole
record back: two edits that cross on the wire both land, a stale page cannot revert what it
does not know about, and a structural edit (a row added or removed) still replaces the record.

Skips the database parts without Postgres.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402
from paperlens import app as appmod, contract, records  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _skip():
    if not _db_ok():
        pytest.skip("no Postgres")


# ── the path grammar, the page's and the server's alike ───────────────────────────────────

def test_path_tokens_match_the_review_screen() -> None:
    assert contract.path_tokens("n") == ["n"]
    assert contract.path_tokens("loadings._table[2].loading") == ["loadings", "_table", 2, "loading"]
    assert contract.path_tokens("records[14].es") == ["records", 14, "es"]
    assert contract.path_tokens("") == [] and contract.path_tokens(None) == []


def test_set_path_sets_one_cell_and_leaves_the_rest() -> None:
    fv = {"n": 100, "rows": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], "t": {"_table": [{"v": 0.1}]}}
    contract.set_path(fv, "rows[1].a", 9)
    contract.set_path(fv, "t._table[0].v", None)
    contract.set_path(fv, "n", None)
    contract.set_path(fv, "notes.extra", "new")                 # objects on the way are created
    assert fv == {"n": None, "rows": [{"a": 1, "b": "x"}, {"a": 9, "b": "y"}], "t": {"_table": [{"v": None}]}, "notes": {"extra": "new"}}
    with pytest.raises(ValueError):
        contract.set_path(fv, "rows[5].a", 1)                   # a row the record does not have is not invented
    with pytest.raises(ValueError):
        contract.set_path(fv, "", 1)
    contract.set_path(fv, "n.deeper", 1)                        # a scalar in the way becomes an object, as on the page
    assert fv["n"] == {"deeper": 1}


# ── on the record ──────────────────────────────────────────────────────────────────────────

def _record(conn, sess: str) -> dict:
    doc = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None, source_job_id="patch", session_id=sess)
    rid = str(conn.execute("SELECT id FROM record WHERE document_id = %s::uuid ORDER BY entry_index LIMIT 1", (doc,)).fetchone()[0])
    # a nested shape, as a sample with its correlation rows has
    records.verify_record(conn, rid, status="corrected",
                          field_values={"n": 312, "design": "RCT", "rows": [{"es": 0.1, "k": "a"}, {"es": 0.2, "k": "b"}, {"es": 0.3, "k": "c"}]})
    conn.commit()
    return records.get_record(conn, rid)


def test_two_edits_that_cross_on_the_wire_both_land() -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    sess = f"patch-{uuid.uuid4().hex[:6]}"
    rec = _record(conn, sess)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app); H = {"X-Session-Id": sess}
    # the page edits rows[0].es, then rows[2].es before the first save has answered — each save
    # names ITS cell only, so the order they reach the server in does not matter
    r1 = c.post(f"/api/records/{rec['id']}/verify", headers=H, json={"status": "corrected",
                "diff": [{"field_path": "rows[0].es", "original_value": 0.1, "final_value": None}],
                "patch": [{"field_path": "rows[0].es", "value": None}]})
    r2 = c.post(f"/api/records/{rec['id']}/verify", headers=H, json={"status": "corrected",
                "diff": [{"field_path": "rows[2].es", "original_value": 0.3, "final_value": 0.33}],
                "patch": [{"field_path": "rows[2].es", "value": 0.33}]})
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    fv = records.get_record(conn, rec["id"])["field_values"]
    assert [r["es"] for r in fv["rows"]] == [None, 0.2, 0.33] and fv["n"] == 312 and fv["design"] == "RCT"
    # a correction is logged, the record's status is untouched
    assert records.get_record(conn, rec["id"])["verification_status"] == "unverified"
    ev = c.get(f"/api/records/{rec['id']}/events", headers=H).json()["events"]
    assert ev[0]["status"] == "corrected" and ev[0]["diff"][0]["field_path"] == "rows[2].es"
    # a top-level cell, and a cleared one
    assert c.post(f"/api/records/{rec['id']}/verify", headers=H, json={"status": "corrected", "patch": [{"field_path": "n", "value": None}]}).status_code == 200
    assert records.get_record(conn, rec["id"])["field_values"]["n"] is None
    # a stale page cannot invent a row; a patch without a path is refused
    assert c.post(f"/api/records/{rec['id']}/verify", headers=H, json={"status": "corrected", "patch": [{"field_path": "rows[7].es", "value": 1}]}).status_code == 422
    assert c.post(f"/api/records/{rec['id']}/verify", headers=H, json={"status": "corrected", "patch": [{"value": 1}]}).status_code == 422
    assert [r["es"] for r in records.get_record(conn, rec["id"])["field_values"]["rows"]] == [None, 0.2, 0.33]
    # a structural edit still replaces the record as a whole
    fv2 = dict(fv); fv2["rows"] = fv["rows"][:2]
    assert c.post(f"/api/records/{rec['id']}/verify", headers=H, json={"status": "corrected", "field_values": fv2,
                  "diff": [{"field_path": "rows[2]", "original_value": "c", "final_value": "(row removed)"}]}).status_code == 200
    assert len(records.get_record(conn, rec["id"])["field_values"]["rows"]) == 2
    conn.close()


def test_only_the_owner_patches() -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    sess = f"patch-{uuid.uuid4().hex[:6]}"
    rec = _record(conn, sess)
    ds = records.create_dataset(conn, title="public", schema_id=None, session_id=sess, visibility="public")
    records.assign_document_to_dataset(conn, ds["id"], rec["document_id"]); conn.commit()
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    r = c.post(f"/api/records/{rec['id']}/verify", headers={"X-Session-Id": "reader"},
               json={"status": "flagged", "patch": [{"field_path": "n", "value": 1}]})
    assert r.status_code == 403
    assert records.get_record(conn, rec["id"])["field_values"]["n"] == 312
    conn.close()


def test_concurrent_cell_edits_on_one_record_queue_instead_of_deadlocking() -> None:
    """Every save inserts a verification event (a key-share lock on the record, through its
    foreign key) and then takes the row lock to apply the patch. Two saves arriving together
    used to wait on each other — Postgres killed one with a deadlock — which an "all true"
    click (four cells at once) or two quick edits hit. The row is locked first now, so the
    saves queue: all of them land, none fails."""
    _skip()
    from concurrent.futures import ThreadPoolExecutor
    conn = records.connect(); records.init_db(conn)
    sess = "patch-race-" + uuid.uuid4().hex[:6]
    rid = _record(conn, sess)["id"]
    conn.close()

    def edit(i: int) -> None:
        c = records.connect()
        try:
            records.verify_record(c, rid, status="corrected",
                                  diff=[{"field_path": f"rows[{i % 3}].es", "original_value": None, "final_value": i}],
                                  patch=[{"field_path": f"rows[{i % 3}].es", "value": i}])
            c.commit()
        finally:
            c.close()

    n = 24
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(edit, range(n)))                       # raises if any save failed
    conn = records.connect()
    try:
        events = conn.execute("SELECT count(*) FROM verification_event WHERE record_id = %s::uuid AND status = 'corrected'",
                              (rid,)).fetchone()[0]
        fv = conn.execute("SELECT field_values FROM record WHERE id = %s::uuid", (rid,)).fetchone()[0]
    finally:
        conn.close()
    assert events >= n and all(isinstance(r["es"], int) for r in fv["rows"])   # every save recorded, every cell holds one of the writes
