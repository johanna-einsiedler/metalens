"""A dataset's living pages: prose with an audit trail, reviewed as changes.

- validate_draft: a section with no admissible source is DROPPED, unknown sources stripped,
  a change op citing no new-batch record is flagged — prose without provenance never lands.
- section_diff: verdicts (kept / revised / new / removed) are computed by string similarity,
  the inline diff is word-level, and each section's sources split into the NEW records that
  triggered the change vs carried ones; a change no new record explains is marked.
- end to end: propose (monkeypatched model) → review-as-diff → commit → grow the dataset →
  propose again → the review names only the new paper's records as triggers → release
  freezes the committed pages and the export writes pages.json.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
import fixtures  # noqa: F401,E402
from test_analysis_table import HAC, _db_ok, _seed, _seed_into  # noqa: E402

from paperlens import pages, records, releases, release_export  # noqa: E402


def test_validate_draft_drops_unsourced_sections() -> None:
    draft = {"title": "Between-subjects designs",
             "sections": [{"text": "Backed.", "sources": ["r1", "bogus"]},
                          {"text": "Unbacked claim.", "sources": []},
                          {"text": "", "sources": ["r1"]}],
             "change_log": [{"op": "add", "old_section": None, "because": ["r1"]},
                            {"op": "revise", "old_section": 0, "because": ["r1"]}]}
    clean, repairs = pages.validate_draft(draft, allowed_ids={"r1", "r2"}, new_ids={"r2"}, old_sections=[{}])
    assert [s["text"] for s in clean["sections"]] == ["Backed."]
    assert clean["sections"][0]["sources"] == ["r1"]
    assert any("unknown sources" in r for r in repairs)
    assert any("no admissible source" in r for r in repairs)
    # both ops cite only r1, which is NOT new → each flagged
    assert sum("cites no NEW record" in r for r in repairs) == 2


def test_section_diff_verdicts_and_triggers() -> None:
    old = [{"text": "Difference-in-differences compares treated and control units over time.", "sources": ["a"]},
           {"text": "Nine papers use the design.", "sources": ["a"]}]
    new = [{"text": "Difference-in-differences compares treated and control units over time.", "sources": ["a"]},
           {"text": "Ten papers use the design, one with a synthetic control.", "sources": ["a", "n1"]},
           {"text": "A new section with no new source.", "sources": ["a"]}]
    d = pages.section_diff(old, new, new_ids={"n1"})
    verdicts = [x["verdict"] for x in d]
    assert verdicts[0] == "kept" and verdicts[1] == "revised" and verdicts[2] == "new"
    assert d[1]["sources_new"] == ["n1"] and d[1]["unexplained"] is False
    assert any(x["op"] == "insert" for x in d[1]["diff"]) and any(x["op"] == "delete" for x in d[1]["diff"])
    assert d[2]["unexplained"] is True                      # a change no new evidence explains
    # dropping a section is seen too
    d2 = pages.section_diff(old, new[:1], new_ids=set())
    assert [x["verdict"] for x in d2] == ["kept", "removed"]


def _page_json(sections):
    return json.dumps({"title": "Between-subjects experiments", "sections": sections,
                       "change_log": [{"op": "add", "old_section": None, "because": [s["sources"][0] for s in sections[:1]]}]})


def test_propose_review_commit_grow_and_release(monkeypatch) -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod, providers
    conn = records.connect(); records.init_db(conn)
    sess = f"pg-{uuid.uuid4().hex[:6]}"; hdr = {"X-Session-Id": sess}
    ds, _doc = _seed(conn, HAC, "human-ai-collab", sess)
    conn.execute("""UPDATE record SET field_values = jsonb_set(field_values, '{Exp_Design}', '"Between-Subjects"')
                    WHERE dataset_id = %s::uuid""", (ds,)); conn.commit()
    rid = conn.execute("SELECT id::text FROM record WHERE dataset_id = %s::uuid", (ds,)).fetchone()[0]

    answers = {"n": 0}
    def fake(model, key, prompt, **kw):
        answers["n"] += 1
        return _page_json([{"text": "One experiment compares humans with and without AI.", "sources": [rid]},
                           {"text": "An unsourced flourish.", "sources": []}])
    monkeypatch.setattr(providers, "generate_text", fake)

    c = TestClient(appmod.app)
    email = f"pg-{uuid.uuid4().hex[:8]}@example.org"
    uid = c.post("/api/auth/register", json={"email": email, "password": "pg-test-pass-1"}, headers=hdr).json()["user"]["id"]
    conn.execute("UPDATE dataset SET owner_user_id = %s::uuid WHERE id = %s::uuid", (uid, ds))
    conn.execute("UPDATE record SET owner_user_id = %s::uuid WHERE dataset_id = %s::uuid", (uid, ds)); conn.commit()
    body = {"group": "design", "key": "Between-Subjects", "keyed_by": "Exp_Design",
            "model": "fake-model", "api_key": "k"}
    r = c.post(f"/api/datasets/{ds}/pages/propose", json=body, headers=hdr)
    assert r.status_code == 200 and r.json()["ok"], r.text
    pid = r.json()["page"]["id"]
    assert len(r.json()["page"]["sections"]) == 1           # the flourish was dropped
    assert any("no admissible source" in x for x in r.json()["repairs"])

    rev = c.get(f"/api/pages/{pid}/review", headers=hdr).json()
    assert [x["verdict"] for x in rev["diff"]] == ["new"]   # no committed page yet
    assert c.post(f"/api/pages/{pid}/commit", headers=hdr).json()["page"]["status"] == "committed"

    # the dataset grows by one paper; the next draft's review must name ONLY its records
    doc2 = _seed_into(conn, HAC, "human-ai-collab", sess, ds)
    conn.execute("""UPDATE record SET field_values = jsonb_set(field_values, '{Exp_Design}', '"Between-Subjects"')
                    WHERE document_id = %s::uuid""", (doc2,)); conn.commit()
    rid2 = conn.execute("SELECT id::text FROM record WHERE document_id = %s::uuid", (doc2,)).fetchone()[0]

    def fake2(model, key, prompt, **kw):
        return _page_json([{"text": "Two experiments compare humans with and without AI support.", "sources": [rid, rid2]}])
    monkeypatch.setattr(providers, "generate_text", fake2)
    r = c.post(f"/api/datasets/{ds}/pages/propose", json=body, headers=hdr)
    assert r.json()["ok"], r.text
    pid2 = r.json()["page"]["id"]
    rev = c.get(f"/api/pages/{pid2}/review", headers=hdr).json()
    (sec,) = [x for x in rev["diff"] if x["verdict"] != "removed"]
    assert sec["verdict"] == "revised"
    assert sec["sources_new"] == [rid2] and rid in sec["sources_carried"]
    assert sec["unexplained"] is False
    assert c.post(f"/api/pages/{pid2}/commit", headers=hdr).status_code == 200

    # the steps card: declared steps come from meta.update (HAC declares none → empty)
    got = c.get(f"/api/datasets/{ds}/pages", headers=hdr).json()
    assert {p["version"] for p in got["pages"]} == {1, 2}

    # a release freezes the newest committed version, and the export ships it
    rel = releases.create(conn, ds, reason="manual")
    snap = releases.get(conn, rel["id"], with_snapshot=True)["snapshot"]
    assert [p["version"] for p in snap["pages"]] == [2]
    files = release_export.build(conn, releases.get(conn, rel["id"], with_snapshot=True))
    assert "pages.json" in files
    shipped = json.loads(files["pages.json"])
    assert shipped["pages"][0]["sections"][0]["sources"] == sorted([rid, rid2])

    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()


def test_meta_update_is_validated() -> None:
    from paperlens import preset_spec
    base = {"format": 2, "id": "t", "version": 1,
            "meta": {"title": "T", "mode": "extraction",
                     "update": [{"type": "pages", "group": "design"}]},     # keyed_by missing
            "prompt": {"text": "x ${output_schema} ${return_format}"},
            "entries": {"key": "rows", "fields": [{"name": "a", "type": "string"}]}}
    errs, _ = preset_spec.validate(base)
    assert any("keyed_by" in e for e in errs)
    base["meta"]["update"] = [{"type": "nonsense"}]
    errs, _ = preset_spec.validate(base)
    assert any("crosscheck" in e for e in errs)
