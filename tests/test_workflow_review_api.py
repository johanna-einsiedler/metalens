"""A sampled review gate end to end: the draw when the step completes, the queue, the verdicts
and corrections that satisfy it, the resume and the release, and what the badge and the frozen
release say about the sample. Skips without Postgres."""
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

from test_workflows import _client, _force_local, _pdf, _result, _sha, _skip, _who

from paperlens import records, releases, review_gate, workflows
from paperlens import workflow_spec as ws


def _declare(conn, ds: str, spec: dict, monkeypatch) -> None:
    monkeypatch.setattr(workflows, "get_workflow", lambda wid, _s=spec: _s if wid.startswith(_s["id"]) else None)
    conn.execute("UPDATE dataset SET workflow = %s WHERE id = %s::uuid", (ws.workflow_id(spec), ds)); conn.commit()


def test_a_sampled_gate_draws_queues_and_counts(monkeypatch) -> None:
    _skip()
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    sess = f"wf-{uuid.uuid4().hex[:6]}"
    who = _who(sess)
    c, H = _client(sess)
    ds = records.create_dataset(conn, title="Sampled", schema_id="econ-regression-tables@67780825", session_id=sess)["id"]; conn.commit()
    spec = ws.normalize({"format": 1, "id": "sampled", "meta": {"title": "Sampled"},
                         "steps": [{"id": "tables", "kind": "extract", "preset": "econ-regression-tables@67780825",
                                    "review": {"mode": "sample", "unit": "entry", "n": 3, "seed": 11, "stratify": ["table_number"], "blocks": "next"}}]})
    assert ws.validate(spec)[0] == [] and workflows.later_problems(spec) == []
    _declare(conn, ds, spec, monkeypatch)
    # two papers with four columns each land: eight new records, three drawn
    pdfs = {t: _pdf(t) for t in ("s1", "s2")}
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": _sha(b), "filename": f"{t}.pdf"} for t, b in pdfs.items()]}).json()
    for t, b in pdfs.items():
        r = c.post("/api/ingest-pdf", headers=H, files={"pdf": (f"{t}.pdf", b, "application/pdf")},
                   data={"result": json.dumps(_result(t, n=4)), "run_id": run["id"], "step_id": "tables"})
        assert r.status_code == 200, r.text
    view = c.get(f"/api/workflow-runs/{run['id']}", headers=H).json()
    assert view["status"] == "awaiting_review:tables"
    gate = view["steps"][0]["review"]
    assert gate["mode"] == "sample" and gate["total"] == 3 and gate["frame_n"] == 8 and gate["n_target"] == 3 and gate["seed"] == 11
    assert gate["exhaustive"] is False and gate["strata"] == {"Table 3": {"N": 8, "n": 3}}
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM workflow_sample WHERE run_id = %s::uuid AND kind = 'random'", (run["id"],)).fetchone()[0] == 3
    # the queue: three items in draw order, their documents, nothing checked
    q = c.get(f"/api/workflow-runs/{run['id']}/queue/tables", headers=H).json()
    assert q["mode"] == "sample" and [i["ord"] for i in q["items"]] == [0, 1, 2] and q["progress"] == {"drawn": 3, "checked": 0, "pending": 3, "disagreed": 0}
    assert sum(d["n_items"] for d in q["documents"]) == 3 and all(d["filename"] for d in q["documents"]) and q["satisfied"] is False and q["next"] is None
    # the same draw again (idempotent on the seed): the engine never redraws on a read
    assert c.get(f"/api/workflow-runs/{run['id']}/queue/tables", headers=H).json()["items"] == q["items"]
    # verdicts: one plain verify, one flag, one correction then verify — two disagreements
    ids = [i["record_id"] for i in q["items"]]
    records.verify_record(conn, ids[0], status="verified")
    records.verify_record(conn, ids[1], status="flagged")
    records.verify_record(conn, ids[2], status="corrected", diff=[{"field_path": "notes", "original_value": None, "final_value": "fixed"}],
                          patch=[{"field_path": "notes", "value": "fixed"}])
    conn.commit()
    q = c.get(f"/api/workflow-runs/{run['id']}/queue/tables", headers=H).json()
    assert q["progress"] == {"drawn": 3, "checked": 2, "pending": 1, "disagreed": 1}
    assert c.post(f"/api/workflow-runs/{run['id']}/resume", headers=H, json={}).status_code == 409
    records.verify_record(conn, ids[2], status="verified"); conn.commit()
    q = c.get(f"/api/workflow-runs/{run['id']}/queue/tables", headers=H).json()
    assert q["progress"] == {"drawn": 3, "checked": 3, "pending": 0, "disagreed": 2} and q["satisfied"] and q["next"] == {"action": "resume"}
    # a record verified outside the draw is an ad-hoc audit: in the badge's audited share, not in the sample
    other = next(r for (r,) in conn.execute("SELECT id::text FROM record WHERE dataset_id = %s::uuid", (ds,)).fetchall() if r not in ids)
    records.verify_record(conn, other, status="verified"); conn.commit()
    cred = records.dataset_credibility(conn, ds)
    assert cred["audited"] == 4 and cred["sample"]["drawn"] == 3 and cred["sample"]["checked"] == 3 and cred["sample"]["disagreed"] == 2
    assert cred["sample"]["frame"] == 8 and cred["sample"]["coverage"] == 1.0 and cred["sample"]["error_rate"] == round(2 / 3, 4)
    assert cred["label"] == "Sampled 3/8 · 33% agree" and "3 of 8 records drawn at random (stratified by table_number)" in cred["label_long"]
    view = c.post(f"/api/workflow-runs/{run['id']}/resume", headers=H, json={}).json()
    assert view["status"] == "done"
    # the release freezes the sample; the panel's gate is satisfied
    r = c.post(f"/api/datasets/{ds}/releases", headers=H, json={"notes": "sampled"})
    assert r.status_code == 200, r.text
    rel = releases.latest(conn, ds)
    assert rel["credibility"]["sample"]["drawn"] == 3 and rel["credibility"]["label"].startswith("Sampled 3/8") and "estimated error rate" in rel["credibility"]["label_long"]
    assert all(g["satisfied"] for g in review_gate.open_gates(conn, ds))
    conn.close()


def test_an_exhaustive_draw_a_release_override_and_the_catalogue_count_alike(monkeypatch) -> None:
    _skip()
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    sess = f"wf-{uuid.uuid4().hex[:6]}"
    c, H = _client(sess)
    ds = records.create_dataset(conn, title="Few", schema_id="econ-regression-tables@67780825", session_id=sess)["id"]; conn.commit()
    spec = ws.normalize({"format": 1, "id": "few", "meta": {"title": "Few"},
                         "steps": [{"id": "tables", "kind": "extract", "preset": "econ-regression-tables@67780825",
                                    "review": {"mode": "sample", "n": 30, "blocks": "release"}}]})
    _declare(conn, ds, spec, monkeypatch)
    pdf = _pdf("f1")
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": _sha(pdf), "filename": "f1.pdf"}]}).json()
    assert c.post("/api/ingest-pdf", headers=H, files={"pdf": ("f1.pdf", pdf, "application/pdf")},
                  data={"result": json.dumps(_result("f1", n=2)), "run_id": run["id"], "step_id": "tables"}).status_code == 200
    view = c.get(f"/api/workflow-runs/{run['id']}", headers=H).json()
    assert view["status"] == "done" and view["steps"][0]["review"]["exhaustive"] is True and view["steps"][0]["review"]["total"] == 2
    # the release holds; the owner overrides, and the release says so
    p = c.get(f"/api/datasets/{ds}/releases/pending", headers=H).json()
    assert p["gate"]["gates_blocking"] == ["tables"] and p["credibility"]["label"] == "AI-only" or p["credibility"]["tier"] == "ai_only"
    assert c.post(f"/api/datasets/{ds}/releases", headers=H, json={}).status_code == 409
    r = c.post(f"/api/datasets/{ds}/releases", headers=H, json={"override_gates": True, "allow_stale": True})
    assert r.status_code == 200 and r.json()["gates"]["gates_overridden"] is True and r.json()["gates"]["gates_blocking"] == ["tables"]
    # the badge with a pending draw says so; a withdrawn verdict is no audit on either counting path
    rec = conn.execute("SELECT id::text FROM record WHERE dataset_id = %s::uuid ORDER BY entry_index LIMIT 1", (ds,)).fetchone()[0]
    records.verify_record(conn, rec, status="verified"); conn.commit()
    cred = records.dataset_credibility(conn, ds)
    assert cred["label"] == "Sampled 1 of 2 · 100% agree" and cred["sample"]["pending"] == 1
    records.verify_record(conn, rec, status="unverified"); conn.commit()
    cred = records.dataset_credibility(conn, ds)
    assert cred["audited"] == 0 and cred["tier"] == "ai_only" and cred["sample"]["checked"] == 0
    records.set_dataset_visibility(conn, ds, "public")
    conn.execute("UPDATE dataset SET publish_status = 'published', catalogue = true WHERE id = %s::uuid", (ds,)); conn.commit()
    listed = next(d for d in records.public_datasets_with_badges(conn, limit=200) if d["id"] == ds)
    assert (listed["credibility"]["n_records"], listed["credibility"]["audited"], listed["credibility"]["tier"]) == (cred["n_records"], cred["audited"], cred["tier"])
    records.verify_record(conn, rec, status="flagged"); conn.commit()
    cred = records.dataset_credibility(conn, ds)
    listed = next(d for d in records.public_datasets_with_badges(conn, limit=200) if d["id"] == ds)
    assert (listed["credibility"]["audited"], listed["credibility"]["agreed"] if "agreed" in listed["credibility"] else cred["agreed"]) == (cred["audited"], cred["agreed"])
    assert listed["credibility"]["label"] == cred["label"]                      # the catalogue chip equals the dataset page's
    conn.close()
