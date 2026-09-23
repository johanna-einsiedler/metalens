"""Harmonise a column into concepts: the pure validation and resolution, then the whole loop on a
dataset — propose (a fake model), review, commit, the _concept column, growth → residual →
extend, the release freeze and the export. Skips the database parts without Postgres."""
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

from paperlens import analysis_table as at, providers, records, release_export, releases, vocabulary as voc  # noqa: E402
from test_analysis_table import _db_ok, _seed, _seed_into  # noqa: E402
from test_register_presets import CLAIMS  # noqa: E402

VALS = [{"idx": "V001", "value": "import competition", "n": 3, "n_papers": 1, "quotes": [], "spellings": ["import competition"]},
        {"idx": "V002", "value": "trade exposure", "n": 1, "n_papers": 1, "quotes": [], "spellings": ["trade exposure"]},
        {"idx": "V003", "value": "men", "n": 1, "n_papers": 1, "quotes": [], "spellings": ["men"]}]
DRAFT = {"domains": [{"id": "trade", "label": "Trade"}],
         "concepts": [{"id": "trade.import_competition.chinese", "parent": "trade.import_competition", "label": "Chinese import competition", "definition": "…",
                       "direction": "higher_is_less", "aliases": ["China shock"], "member_values": ["V001", "V001", "V009"], "member_polarity": {"V001": "higher_is_less"}},
                      {"id": "Bad Id!", "label": "x", "member_values": ["V002"]}],
         "left_out": [{"value": "V003", "reason": "stratification", "why": "who is in the sample"}]}


def test_validate_and_resolve() -> None:
    clean, repairs = voc.validate_draft(DRAFT, VALS)
    ids = [c["id"] for c in clean["concepts"]]
    assert ids == ["trade.import_competition", "trade.import_competition.chinese"]        # the parent was added; the malformed one dropped
    sub = clean["concepts"][1]
    assert sub["member_values"] == ["V001"] and sub["member_polarity"] == {"V001": "higher_is_less"} and sub["direction"] == "higher_is_less"
    assert [lo["value"] for lo in clean["left_out"]] == ["V003", "V002"]                    # V002 lost its concept → left out with a reason
    assert any("missing parent" in r for r in repairs) and any("malformed" in r for r in repairs) and any("V002" in r for r in repairs)
    top = {"domains": [], "concepts": [{"id": "trade.x", "label": "X", "direction": "higher_is_less", "member_values": []}], "left_out": []}
    assert voc.validate_draft(top, [])[0]["concepts"][0]["direction"] == "higher_is_more"     # a top-level concept is never higher_is_less
    a = voc.resolve(VALS, clean)
    assert a["import competition"]["concept_id"] == "trade.import_competition.chinese" and a["import competition"]["polarity"] == "higher_is_less"
    assert a["men"]["basis"] == "left_out" and a["trade exposure"]["basis"] == "left_out"
    alias_draft = {"domains": [], "concepts": [{"id": "trade.import_competition", "label": "Import competition", "direction": "higher_is_more", "aliases": ["Trade exposure"], "member_values": ["V001"]}], "left_out": []}
    b = voc.resolve(VALS, voc.validate_draft(alias_draft, VALS[:1])[0])
    assert b["trade exposure"] == {"value": "trade exposure", "concept_id": "trade.import_competition", "polarity": "higher_is_more", "basis": "alias"}
    assert [v["idx"] for v in voc.unresolved(VALS, b)] == ["V003"]
    ext, notes = voc.apply_decisions(voc.validate_draft(alias_draft, VALS[:1])[0], VALS, [{"value": "V003", "action": "new", "concept_id": "people.gender", "direction": "not_ordered", "label_text": "Gender"}])
    assert notes == [] and {c["id"] for c in ext["concepts"]} == {"trade.import_competition", "people.gender"}


def _second_paper(cause: str) -> dict:
    doc = copy.deepcopy(CLAIMS)
    doc["paper_metadata"] = {**doc["paper_metadata"], "title": "Another trade paper", "doi": "10.1/u"}
    doc["claims"] = [doc["claims"][0]]
    doc["claims"][0]["cause"] = cause
    doc["evidence"] = [e for e in doc["evidence"] if e["field"][0].startswith("claims[0]") if isinstance(e["field"], list)] or doc["evidence"][:1]
    return doc


def test_the_whole_loop(monkeypatch) -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    sess = f"voc-{uuid.uuid4().hex[:6]}"; mine, other = {"X-Session-Id": sess}, {"X-Session-Id": f"o-{uuid.uuid4().hex[:6]}"}
    ds, _ = _seed(conn, CLAIMS, "register-claims", sess)
    c, stranger = TestClient(appmod.app), TestClient(appmod.app)
    c.post("/api/auth/register", json={"email": f"voc-{uuid.uuid4().hex[:8]}@example.org", "password": "vocab-test-pass-1"}, headers=mine)
    # nothing without a vocabulary
    assert not [x for x in at.build(conn, ds, "results", owner=True)["columns"] if x["scope"] == "vocabulary"]
    assert c.get(f"/api/datasets/{ds}/vocabularies", headers=mine).json() == {"vocabularies": [], "owner": True}
    vals = voc.values(conn, ds, "entries", "cause")
    assert [v["value"] for v in vals] == ["import competition"] and vals[0]["n"] == 1 and vals[0]["quotes"]     # the anchor quote of the claim
    # propose: a fake model answers the structure pass
    canned = {"domains": [{"id": "trade", "label": "Trade"}],
              "concepts": [{"id": "trade.import_competition", "parent": "trade", "label": "Import competition", "definition": "exposure of a worker's firm to import competition",
                            "direction": "higher_is_more", "more_means": "more exposure", "aliases": ["China shock"], "member_values": ["V001"], "member_polarity": {}, "rationale": "one variable"}],
              "left_out": []}
    seen = {}
    def fake(model, key, prompt, base_url=None, max_tokens=None, json_mode=False, **kw):
        seen["prompt"] = prompt; seen["model"] = model
        return json.dumps(canned if "Structure pass" in prompt else {"decisions": [{"value": v, "action": "map", "concept_id": "trade.import_competition", "polarity": None, "why": "same variable"}
                                                                                    for v in [ln.split()[0] for ln in prompt.splitlines() if ln.startswith("V")]]})
    monkeypatch.setattr(providers, "generate_text", fake)
    assert stranger.post(f"/api/datasets/{ds}/vocabularies/propose", json={"unit": "entries", "column": "cause", "model": "m", "api_key": "k"}, headers=other).status_code in (401, 403, 404)
    assert c.post(f"/api/datasets/{ds}/vocabularies/propose", json={"unit": "entries", "column": "statement", "model": "m", "api_key": "k"}, headers=mine).status_code == 200   # any text column
    r = c.post(f"/api/datasets/{ds}/vocabularies/propose", json={"unit": "entries", "column": "point_estimate", "model": "m", "api_key": "k"}, headers=mine)
    assert r.status_code == 422                                                                       # not a text column of that unit
    r = c.post(f"/api/datasets/{ds}/vocabularies/propose", json={"unit": "entries", "column": "cause", "model": "m", "api_key": "k"}, headers=mine).json()
    assert r["ok"] and seen["model"] == "m" and "V001" in seen["prompt"] and "import competition" in seen["prompt"] and "never higher_is_less" in seen["prompt"]
    v = r["vocabulary"]
    assert v["status"] == "draft" and v["version"] == 1 and v["draft"]["concepts"][0]["id"] == "trade.import_competition" and v["repairs"] == []
    vid = v["id"]
    # review: rename, then commit
    draft = v["draft"]; draft["concepts"][0]["label"] = "Import competition (China)"
    assert c.patch(f"/api/vocabularies/{vid}", json=draft, headers=mine).json()["draft"]["concepts"][0]["label"] == "Import competition (China)"
    assert stranger.get(f"/api/vocabularies/{vid}", headers=other).status_code == 404                 # a draft is the owner's
    done = c.post(f"/api/vocabularies/{vid}/commit", headers=mine).json()
    assert done["status"] == "committed" and done["assignments"]["import competition"]["concept_id"] == "trade.import_competition" and done["unresolved"] == []
    assert c.patch(f"/api/vocabularies/{vid}", json=draft, headers=mine).status_code == 409
    t = at.build(conn, ds, "results", owner=True)
    names = [x["name"] for x in t["columns"]]
    assert "cause_concept" in names and "cause_polarity" in names
    k = names.index("cause_concept")
    assert [row["v"][k] for row in t["rows"]] == ["trade.import_competition", "trade.import_competition", None]   # S2 (not_causal) has no cause
    assert next(x for x in t["columns"] if x["name"] == "cause_concept")["vocabulary"]["labels"]["trade.import_competition"] == "Import competition (China)"
    # a release freezes it; the export carries it
    rel = releases.get_by_number(conn, ds, c.post(f"/api/datasets/{ds}/releases", json={}, headers=mine).json()["number"], with_snapshot=True)
    assert rel["snapshot"]["vocabularies"][0]["column"] == "cause" and rel["snapshot"]["vocabularies"][0]["version"] == 1
    meta = json.loads(release_export.build(conn, rel)["release.json"])
    assert meta["vocabularies"][0]["columns"] == ["cause_concept", "cause_polarity"] and meta["vocabularies"][0]["assignments"]["import competition"]["concept_id"] == "trade.import_competition"
    # the dataset grows: a new phrase → unresolved → residual pass → v2
    _seed_into(conn, _second_paper("trade exposure"), "register-claims", sess, ds)
    listed = c.get(f"/api/datasets/{ds}/vocabularies", headers=mine).json()["vocabularies"]
    assert [x for x in listed if x["column"] == "cause"][0]["unresolved"] == 1
    assert c.get(f"/api/datasets/{ds}/releases/pending", headers=mine).json()["warnings"]["vocabulary_unresolved"] == 1
    got = c.get(f"/api/vocabularies/{vid}", headers=mine).json()
    assert [x["value"] for x in got["unresolved"]] == ["trade exposure"]
    res = c.post(f"/api/datasets/{ds}/vocabularies/propose", json={"unit": "entries", "column": "cause", "vocabulary_id": vid, "model": "m", "api_key": "k"}, headers=mine).json()
    assert res["ok"] and res["residual"] and [d["value"] for d in res["decisions"]] == [res["values"][0]["idx"]] and res["decisions"][0]["action"] == "map"
    v2 = c.post(f"/api/vocabularies/{vid}/extend", json={"decisions": res["decisions"]}, headers=mine).json()
    assert v2["version"] == 2 and v2["status"] == "committed" and v2["unresolved"] == [] and v2["assignments"]["trade exposure"]["concept_id"] == "trade.import_competition"
    t2 = at.build(conn, ds, "results", owner=True)
    k2 = [x["name"] for x in t2["columns"]].index("cause_concept")
    assert sorted({row["v"][k2] for row in t2["rows"] if row["v"][k2]}) == ["trade.import_competition"] and sum(1 for row in t2["rows"] if row["v"][k2]) == 4
    assert "vocabulary_unresolved" not in c.get(f"/api/datasets/{ds}/releases/pending", headers=mine).json()["warnings"]
    # the old release still reads its own vocabulary (v1) — the new value is unresolved there
    t_old = at.build(conn, ds, "results", owner=False, release=rel)
    ko = [x["name"] for x in t_old["columns"]].index("cause_concept")
    assert next(x for x in t_old["columns"] if x["name"] == "cause_concept")["vocabulary"]["version"] == 1
    # drafts can be deleted; committed versions cannot
    d2 = c.post(f"/api/datasets/{ds}/vocabularies/propose", json={"unit": "entries", "column": "cause", "model": "m", "api_key": "k"}, headers=mine).json()["vocabulary"]
    assert d2["version"] == 3 and c.delete(f"/api/vocabularies/{d2['id']}", headers=mine).json() == {"deleted": 1}
    assert c.delete(f"/api/vocabularies/{vid}", headers=mine).json() == {"deleted": 0}
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()
