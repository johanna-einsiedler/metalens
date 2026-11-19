"""Coders: the owner grants accounts the right to code a dataset; each codes independently (own
values, verdicts, notes, paper-level fields, marks), the owner sees every coding, the record as
stored and its verification events never move; exports, snapshots and the badge carry the codings.
Skips without Postgres."""
from __future__ import annotations

import json
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_workflows import _pdf, _result, _skip

from paperlens import app as appmod
from paperlens import coders, presets, records, releases

PW = "pw123456"


def _account(tag: str):
    """A signed-in TestClient of a fresh account (the cookie carries the session) and its user id."""
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.headers.update({"X-Session-Id": f"sess-{tag}-{uuid.uuid4().hex[:6]}"})
    email = f"{tag}-{uuid.uuid4().hex[:8]}@coders.test"
    r = c.post("/api/auth/register", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return c, r.json()["user"]["id"], email


def _dataset_with_papers(owner, n: int = 2) -> tuple[str, list[str]]:
    ds = owner.post("/api/datasets", json={"title": "Coded", "schema_id": "econ-regression-tables@67780825", "visibility": "private"}).json()["id"]
    docs = []
    for i in range(n):
        pdf = _pdf(f"cd{i}-{uuid.uuid4().hex[:4]}")
        r = owner.post("/api/ingest-pdf", files={"pdf": (f"cd{i}.pdf", pdf, "application/pdf")},
                       data={"result": json.dumps(_result(f"cd{i}", n=2)), "schema_id": "econ-regression-tables@67780825", "dataset_id": ds})
        assert r.status_code == 200, r.text
        docs.append(r.json()["document_id"])
    return ds, docs


def _paper_field() -> str:
    """A declared scalar paper-level field of the bundled preset (what a coder may set)."""
    spec = presets.load_all()["econ-regression-tables"]
    f = next(f for f in (spec.get("paper") or {}).get("fields") or [] if f.get("type") in ("string", "text"))
    return f["name"]


def test_coders_code_independently_and_the_owner_sees_every_coding() -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    owner, owner_id, _ = _account("owner")
    a, a_id, a_email = _account("coder-a")
    b, b_id, b_email = _account("coder-b")
    ds, docs = _dataset_with_papers(owner)
    doc0 = docs[0]
    # before the grant: a coder is a stranger (404 everywhere)
    assert a.get(f"/api/documents/{doc0}/view").status_code == 404
    assert a.post(f"/api/datasets/{ds}/coders", json={"email": b_email}).status_code == 404
    assert owner.post(f"/api/datasets/{ds}/coders", json={"email": "nobody@coders.test"}).status_code == 404
    assert owner.post(f"/api/datasets/{ds}/coders", json={"email": a_email}).json()["coder"]["label"] == "coder_1"
    assert a.post(f"/api/datasets/{ds}/coders", json={"email": b_email}).status_code == 403      # a coder manages nobody
    assert owner.post(f"/api/datasets/{ds}/coders", json={"email": b_email.upper()}).json()["coder"]["label"] == "coder_2"
    lst = owner.get(f"/api/datasets/{ds}/coders").json()["coders"]
    assert [c["label"] for c in lst] == ["coder_1", "coder_2"] and lst[0]["user_id"] == a_id and lst[1]["email"] == b_email
    assert owner.post(f"/api/datasets/{ds}/coders", json={"email": a_email}).json()["coder"]["label"] == "coder_1"   # idempotent
    # the coders see the owner's papers: the view, the sidebar, the dataset
    va = a.get(f"/api/documents/{doc0}/view").json()
    assert va["coding"] == {"as": a_id, "label": "coder_1", "read_only": False} and "codings" not in va
    assert all(r["verification_status"] == "unverified" for r in va["records"]) and va["pages"]
    side = a.get(f"/api/documents?dataset={ds}").json()["documents"]
    assert {d["document_id"] for d in side} == set(docs) and all(d["n_verified"] == 0 for d in side)
    assert a.get(f"/api/datasets/{ds}").status_code == 200
    ov = a.get(f"/api/datasets/{ds}/overview").json()
    assert ov["viewer_is_coder"] is True and ov["viewer_is_owner"] is False and "coders" not in ov
    assert owner.get(f"/api/datasets/{ds}/overview").json()["coders"][1]["label"] == "coder_2"
    assert [d["id"] for d in a.get("/api/datasets?coder=1").json()["datasets"]] == [ds]
    rid = va["records"][0]["id"]
    orig = va["records"][0]["field_values"]["column"]
    # A corrects a cell and verifies; B flags with a note — two codings, nothing on the record
    r = a.post(f"/api/records/{rid}/verify", json={"status": "verified", "patch": [{"field_path": "column", "value": "(9)"}],
                                                  "diff": [{"field_path": "column", "original_value": orig, "final_value": "(9)"}]})
    assert r.status_code == 200 and r.json()["coding"] is True, r.text
    assert b.post(f"/api/records/{rid}/verify", json={"status": "flagged", "notes": "unsure about the column"}).status_code == 200
    va = a.get(f"/api/documents/{doc0}/view").json()["records"][0]
    vb = b.get(f"/api/documents/{doc0}/view").json()["records"][0]
    vo = owner.get(f"/api/documents/{doc0}/view").json()
    assert va["verification_status"] == "verified" and va["field_values"]["column"] == "(9)" and va["corrections"][0]["editor"] == "coder_1"
    assert vb["verification_status"] == "flagged" and vb["field_values"]["column"] == orig and vb["note"]["text"] == "unsure about the column" and not vb["corrections"]
    assert vo["records"][0]["verification_status"] == "unverified" and vo["records"][0]["field_values"]["column"] == orig and not vo["records"][0]["corrections"]
    chips = {c["coder"]: c for c in vo["codings"]["records"][rid]}
    assert chips["coder_1"]["status"] == "verified" and chips["coder_1"]["changed"] is True
    assert chips["coder_2"]["status"] == "flagged" and chips["coder_2"]["changed"] is False and chips["coder_2"]["note"]
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM verification_event WHERE record_id = %s::uuid", (rid,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM record_coding WHERE record_id = %s::uuid", (rid,)).fetchone()[0] == 2
    assert [e["verifier_kind"] for e in a.get(f"/api/records/{rid}/events").json()["events"]] == ["coder"]
    assert len(b.get(f"/api/records/{rid}/events").json()["events"]) == 1
    assert a.get(f"/api/documents?dataset={ds}").json()["documents"][-1]["n_verified"] in (0, 1)
    assert sum(d["n_verified"] for d in a.get(f"/api/documents?dataset={ds}").json()["documents"]) == 1
    # A's paper-level answer lands in A's coding; the identity stays the owner's
    pf = _paper_field()
    assert a.patch(f"/api/documents/{doc0}/paper", json={"fields": {pf: "coder A's note"}}).json()["coding"] is True
    assert a.patch(f"/api/documents/{doc0}/paper", json={"title": "Renamed"}).status_code == 403
    assert a.get(f"/api/documents/{doc0}/view").json()["paper_metadata"][pf] == "coder A's note"
    assert (b.get(f"/api/documents/{doc0}/view").json()["paper_metadata"] or {}).get(pf) != "coder A's note"
    vo = owner.get(f"/api/documents/{doc0}/view").json()
    assert (vo["paper_metadata"] or {}).get(pf) != "coder A's note" and vo["codings"]["paper"][0]["coder"] == "coder_1"
    # the owner may look at one coding, read-only
    as_a = owner.get(f"/api/documents/{doc0}/view?coder={a_id}").json()
    assert as_a["coding"]["read_only"] is True and as_a["records"][0]["field_values"]["column"] == "(9)" and as_a["paper_metadata"][pf] == "coder A's note"
    assert owner.get(f"/api/documents/{doc0}/view?coder={owner_id}").status_code == 404
    # the shape of the data is the owner's
    assert a.post(f"/api/documents/{doc0}/records", json={"field_values": {}}).status_code == 403
    assert a.delete(f"/api/records/{rid}").status_code == 403
    assert a.delete(f"/api/documents/{doc0}").status_code in (403, 404)
    assert a.post(f"/api/documents/{doc0}/set-field", json={"key": "column", "value": "x"}).status_code == 404
    assert a.post(f"/api/datasets/{ds}/workflow/runs", json={}).status_code in (403, 404)
    # marks: each coder's own; the owner sees all; a coder removes only their own
    conn.rollback()
    ha = records.add_highlight(conn, doc0, page=1, text="a sentence", rects=[[1, 1, 10, 10]], created_by=a_id); conn.commit()
    assert [h["id"] for h in a.get(f"/api/documents/{doc0}/view").json()["highlights"]] == [ha["id"]]
    assert b.get(f"/api/documents/{doc0}/view").json()["highlights"] == []
    assert len(owner.get(f"/api/documents/{doc0}/view").json()["highlights"]) == 1
    assert b.delete(f"/api/highlights/{ha['id']}").status_code == 403
    assert a.delete(f"/api/highlights/{ha['id']}").json()["deleted"] == 1
    # the export: every coding by label, emails for the owner alone
    ex = owner.get(f"/api/datasets/{ds}/export").json()
    assert [c["label"] for c in ex["coders"]] == ["coder_1", "coder_2"] and ex["coders"][0]["email"] == a_email
    p0 = next(p for p in ex["papers"] if p["document_id"] == doc0)
    r0 = next(r for r in p0["records"] if r["record_id"] == rid)
    assert {c["coder"]: c["status"] for c in r0["codings"]} == {"coder_1": "verified", "coder_2": "flagged"}
    assert next(c for c in r0["codings"] if c["coder"] == "coder_1")["values"]["column"] == "(9)" and r0["values"]["column"] == orig
    assert p0["paper_codings"][0]["paper_metadata"][pf] == "coder A's note"
    conn.rollback()
    assert "email" not in json.dumps(coders.export_block(conn, ds)["coders"])
    # the snapshot and the fingerprint
    snap = releases.snapshot(conn, ds)
    assert snap["codings"]["records"][rid][0]["coder"] == "coder_1" and snap["paper_metadata"][doc0][pf] != "coder A's note"
    assert snap["codings"]["papers"][doc0][0]["paper_metadata"][pf] == "coder A's note"
    fp1 = releases.fingerprint(conn, ds)
    assert b.post(f"/api/records/{rid}/verify", json={"status": "verified"}).status_code == 200
    conn.rollback()
    assert releases.fingerprint(conn, ds) != fp1
    d = releases.diff_snapshots(snap, releases.snapshot(conn, ds))
    assert d["codings"]["changed"] == 1 and d["codings"]["n_coders"] == 2
    # the badge counts the codings: one record audited (A verified it, B now too), not agreed (A changed a value)
    cred = records.dataset_credibility(conn, ds)
    assert cred["n_records"] == 4 and cred["audited"] == 1 and cred["agreed"] == 0 and cred["tier"] == "sample_verified"
    rid2 = va and owner.get(f"/api/documents/{docs[1]}/view").json()["records"][0]["id"]
    assert b.post(f"/api/records/{rid2}/verify", json={"status": "verified"}).status_code == 200
    conn.rollback()
    cred = records.dataset_credibility(conn, ds)
    assert cred["audited"] == 2 and cred["agreed"] == 1
    # …and the catalogue's SQL counts exactly the same
    conn.execute("UPDATE dataset SET visibility = 'public', publish_status = 'published', catalogue = true WHERE id = %s::uuid", (ds,)); conn.commit()
    row = next(r for r in records.public_datasets_with_badges(conn, limit=200) if r["id"] == ds)
    assert {k: row["credibility"][k] for k in ("n_records", "audited", "agreement", "tier")} == {k: cred[k] for k in ("n_records", "audited", "agreement", "tier")}
    # withdrawing a grant: the coder is a stranger again, the codings stay
    assert owner.delete(f"/api/datasets/{ds}/coders/{a_id}").json()["removed"] == 1
    conn.execute("UPDATE dataset SET visibility = 'private' WHERE id = %s::uuid", (ds,)); conn.commit()
    assert a.get(f"/api/documents/{doc0}/view").status_code == 404
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM record_coding WHERE coder_user_id = %s::uuid", (a_id,)).fetchone()[0] >= 1
    assert owner.get(f"/api/documents/{doc0}/view").json()["codings"]["records"][rid][0]["coder"] in ("former_1", "coder_1")
    conn.close()


def test_a_grant_on_a_project_is_the_pool_the_tasks_draw_from() -> None:
    """A person added to a project is in its pool (a label, the project's page) — the papers of a
    linked dataset follow the tasks that assign them (tests/test_task_access.py)."""
    _skip()
    conn = records.connect(); records.init_db(conn)
    owner, owner_id, _ = _account("wd-owner")
    a, a_id, a_email = _account("wd-coder")
    wd = owner.post("/api/workflow-datasets", json={"workflow": "econ", "title": "Grouped"}).json()
    members = {}
    for role in ("tables", "claims"):
        g = owner.post(f"/api/datasets/{wd['id']}/group/link", json={"role": role, "create": True}).json()
        members[role] = g["roles"][role]["dataset_id"]
    assert owner.post(f"/api/datasets/{wd['id']}/coders", json={"email": a_email}).status_code == 200
    conn.rollback()
    from paperlens.principal import Principal
    who = Principal(user_id=a_id, session_id=None)
    assert coders.is_coder(conn, wd["id"], who) and not coders.is_coder(conn, members["tables"], who) and not coders.is_coder(conn, members["claims"], who)
    assert a.get("/api/datasets?coder=1").json()["datasets"] == []
    assert a.get(f"/api/datasets/{wd['id']}/overview").json()["viewer_is_coder"] is True
    assert a.get(f"/api/datasets/{members['tables']}/workflow").status_code == 404
    assert [c["scope"] for c in coders.coders_of(conn, members["tables"])] == ["group"]        # the pool, seen from a member
    assert coders.labels_for(conn, members["claims"])[a_id]["label"] == "coder_1"
    conn.close()
