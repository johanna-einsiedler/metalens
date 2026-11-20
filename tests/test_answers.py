"""Answers (paperlens/templates.py + coders.py): a review step with a template over an
extraction dataset; the coder's projected view, their answers per unit, what the verdict needs,
the owner's view of every coder's answers, the export. Skips without Postgres."""
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

from test_coders import _account
from test_genai_work_workflow import _extraction_for
from test_workflows import _pdf, _skip

from paperlens import coders, presets, records, review_gate, templates as tp, workflows
from paperlens import workflow_spec as ws

EXT_PIN = "genai-work-extraction@ddf3b7fd"
SPEC = {"format": 1, "id": "tasked", "meta": {"title": "Tasked"},
        "steps": [{"id": "extraction", "kind": "extract", "preset": EXT_PIN, "source": "supplied"},
                  {"id": "judgment", "kind": "review", "over": ["extraction"], "template": "genai-work-judgment", "mode": "all", "unit": "paper", "coders": 1}]}


def _declare(conn, ds: str, spec: dict, monkeypatch) -> None:
    monkeypatch.setattr(workflows, "get_workflow", lambda wid, _s=spec: _s if wid.startswith(_s["id"]) else None)
    conn.execute("UPDATE dataset SET workflow = %s WHERE id = %s::uuid", (ws.workflow_id(spec), ds)); conn.commit()


def _record(spec: dict, tag: str) -> dict:
    """An extraction file with what the judgment template asks about: a task with a description, two
    arms, one outcome, estimands with a subgroup, one abstract claim and one result claim."""
    obj = _extraction_for(spec, tag)
    t = obj["tasks"][0]
    t["task_description"] = "Write 3 memos in 45 minutes"
    t["task_label"] = "writing"
    t["arms"][1]["treatment"] = "GPT-4 access"
    t["outcomes"] = [{"outcome_id": "O1", "outcome_name": "Minutes per memo", "outcome_unit": "minutes", "how_measured": "logged", "outcome_finding": "productivity_time"}]
    pm = obj["paper_metadata"]
    pm["summary"] = "The model's secret summary with 42 numbers"
    pm["model_estimands"] = [{"estimand_id": "E1", "task": "T1", "subgroup": "full_sample"}, {"estimand_id": "E2", "task": "T1", "subgroup": "novices"}]
    pm["claims"] = [{"claim_id": "C1", "sentence": "AI cut time by 20 percent", "kind": "abstract", "page": 1},
                    {"claim_id": "C2", "sentence": "Table 2 reports 12.5 minutes", "kind": "result", "page": 4}]
    pm["evidence_items"] = [{"n": 1, "page": 1, "kind": "passage", "text": "N = 100 students", "bears_on": ["task_label", "task_name"]}]
    return obj


def test_a_task_projects_the_document_takes_answers_and_closes_on_them(monkeypatch) -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    spec_ext = presets.load_all()["genai-work-extraction"]
    assert presets.emit_schema_row("genai-work-extraction")["schema_id"] == EXT_PIN
    owner, _oid, _ = _account("ans-owner")
    a, a_id, a_email = _account("ans-a")
    b, b_id, b_email = _account("ans-b")
    ds = owner.post("/api/datasets", json={"title": "Tasked", "schema_id": EXT_PIN, "visibility": "private"}).json()["id"]
    spec = ws.normalize(copy.deepcopy(SPEC))
    assert ws.validate(spec)[0] == []
    _declare(conn, ds, spec, monkeypatch)
    for email in (a_email, b_email):
        assert owner.post(f"/api/datasets/{ds}/coders", json={"email": email}).status_code == 200
    run = owner.post(f"/api/datasets/{ds}/workflow/runs", json={}).json()
    rid = run["id"]
    tag = uuid.uuid4().hex[:6]
    pdf = _pdf(f"ans-{tag}")
    r = owner.post("/api/ingest-pdf", files={"pdf": ("a.pdf", pdf, "application/pdf")},
                   data={"result": json.dumps(_record(spec_ext, tag)), "run_id": rid, "step_id": "extraction"})
    assert r.status_code == 200, r.text
    doc = r.json()["document_id"]
    view = owner.post(f"/api/workflow-runs/{rid}/complete", json={"step_id": "extraction"}).json()
    assert view["status"] == "done", view
    gate = next(s for s in view["steps"] if s["step_id"] == "judgment")["review"]
    assert gate["template"] == "genai-work-judgment" and gate["template_id"].startswith("genai-work-judgment@") and gate["assigned"] and gate["total"] == 1
    conn.rollback()
    coder_uid = conn.execute("SELECT coder_user_id::text FROM workflow_assignment WHERE run_id = %s::uuid", (rid,)).fetchone()[0]
    c, c_id = (a, a_id) if coder_uid == a_id else (b, b_id)
    other = b if c is a else a
    # the queue names the task and its template
    q = c.get(f"/api/workflow-runs/{rid}/queue/judgment").json()
    assert q["task"] == f"{rid}:judgment" and q["template"] == "genai-work-judgment" and q["viewer_coder"] == c_id and len(q["items"]) == 1
    # the coder's view through the task: blind fields gone, numbers masked, the template there, no answers yet
    v = c.get(f"/api/documents/{doc}/view?task={rid}:judgment").json()
    assert v["task"] == {"run_id": rid, "step_id": "judgment", "template": "genai-work-judgment", "template_id": gate["template_id"]}
    assert v["template"]["blind"] is True and v["template"]["masked"] is True and [s["id"] for s in v["template"]["stages"]] == ["design", "units", "headline"]
    rec = v["records"][0]
    assert "task_label" not in rec["field_values"] and "confidence" not in rec and rec["field_values"]["task_name"] == "Write a memo"
    assert rec["field_values"]["task_description"] == "Write # memos in ## minutes" and "control_type" not in rec["field_values"]["arms"][0]
    pm = v["paper_metadata"]
    assert "summary" not in pm and "scale" not in pm and pm["claims"][0]["sentence"] == "AI cut time by ## percent" and pm["claims"][1]["sentence"].endswith("12.5 minutes")
    assert pm["evidence_items"][0]["bears_on"] == ["task_name"] and pm["evidence_items"][0]["text"] == "N = ### students" and pm["title"]
    assert v["answers"] == {"paper": {}, "records": {}} and v["coding"]["as"] == c_id
    plain = c.get(f"/api/documents/{doc}/view").json()
    assert "task" not in plain and plain["records"][0]["field_values"]["task_label"] == "writing"      # the plain view is as it was
    # answers: refused outside the questions; accepted into the coding, merged per unit; an answer is no verdict
    bad = c.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "unit": "", "answers": {"scale": "no-such-scale"}})
    assert bad.status_code == 422 and "not an option" in bad.text
    assert c.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "unit": "zzz#1", "answers": {"x": 1}}).status_code == 422
    assert owner.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "unit": "", "answers": {"scale": "lab"}}).status_code == 403
    scale_opt = next(o["value"] if isinstance(o, dict) else o for f in spec_ext["paper"]["fields"] if f["name"] == "scale" for o in f["options"])
    screen = {f["name"]: (f["options"][0]["value"] if isinstance(f["options"][0], dict) else f["options"][0]) for f in spec_ext["paper"]["fields"] if f["name"].startswith("screen_")}
    r = c.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "unit": "", "answers": {"scale": scale_opt, **screen, "notes": "fine"}})
    assert r.status_code == 200 and r.json()["answers"]["scale"] == scale_opt and r.json()["template"] == "genai-work-judgment"
    r = c.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "unit": "", "answers": {"notes": None}})
    assert "notes" not in r.json()["answers"] and r.json()["answers"]["scale"] == scale_opt                # None clears, the rest stays
    rec_id = rec["id"]
    assert c.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "record_id": rec_id, "unit": "", "answers": {"category": "writing", "verdict": "ok"}}).status_code == 200
    arms = {x["arm_id"]: x["_rid"] for x in rec["field_values"]["arms"]}
    for aid, cat in (("C", "ref:no_ai_concurrent"), ("T", "ai:assistant")):
        r = c.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "record_id": rec_id, "unit": f"arms#{arms[aid]}", "answers": {"category": cat, "verdict": "ok"}})
        assert r.status_code == 200, r.text
    out_rid = rec["field_values"]["outcomes"][0]["_rid"]
    assert c.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "record_id": rec_id, "unit": f"outcomes#{out_rid}", "answers": {"category": "productivity_time", "verdict": "ok"}}).status_code == 200
    assert c.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "unit": "model_estimands.subgroup=novices", "answers": {"verdict": "ok"}}).status_code == 200
    assert c.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "record_id": rec_id, "unit": "entries#+c1", "answers": {"task_name": "A task the list lacks", "category": "writing", "verdict": "ok"}}).status_code == 200
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM coding_event WHERE document_id = %s::uuid AND status = 'noted' AND answers IS NOT NULL", (doc,)).fetchone()[0] >= 7
    assert conn.execute("SELECT verification_status FROM record_coding WHERE record_id = %s::uuid AND coder_user_id = %s::uuid", (rec_id, c_id)).fetchone()[0] == "unverified"
    # the verdict in the task waits for the required answers (the abstract's claim, the paper's full-review flag)
    r = c.post(f"/api/records/{rec_id}/verify", json={"status": "verified", "task": f"{rid}:judgment"})
    assert r.status_code == 422, r.text
    missing = r.json()["detail"]["missing"]
    assert {(m["unit"], m["question"]) for m in missing} == {("claims#" + pm["claims"][0]["_rid"], "verdict"), ("", "full_review")}
    assert c.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "unit": f"claims#{pm['claims'][0]['_rid']}", "answers": {"verdict": "unit_right"}}).status_code == 200
    assert c.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "unit": "", "answers": {"full_review": "no"}}).status_code == 200
    v = c.get(f"/api/documents/{doc}/view?task={rid}:judgment").json()
    assert v["answers"]["paper"][""]["full_review"] == "no" and v["answers"]["records"][rec_id]["arms#" + arms["T"]]["category"] == "ai:assistant"
    assert v["answers"]["records"][rec_id]["entries#+c1"]["task_name"] == "A task the list lacks"
    comp = tp.completeness(tp.get("genai-work-judgment"), v, spec_ext, v["answers"])
    assert comp["complete"] is True and comp["required"] >= 12, comp
    for it in q["items"]:
        assert c.post(f"/api/records/{it['record_id']}/verify", json={"status": "verified", "task": f"{rid}:judgment"}).status_code == 200
    conn.rollback()
    sr = workflows.get_step_run(conn, conn.execute("SELECT id::text FROM workflow_step_run WHERE run_id = %s::uuid AND step_id = 'judgment'", (rid,)).fetchone()[0])
    st = review_gate.state(conn, sr)
    assert st["satisfied"] is True and st["checked"] == 1
    # answers never count as a disagreement; the record as stored is untouched
    assert all(not x["disagreed"] for x in coders.badge_counts(conn, ds).values())
    assert conn.execute("SELECT verification_status FROM record WHERE id = %s::uuid", (rec_id,)).fetchone()[0] == "unverified"
    # the other coder's answers are their own; the owner sees every coder's, unblinded
    assert other.post(f"/api/documents/{doc}/answers", json={"task": f"{rid}:judgment", "unit": "", "answers": {"scale": scale_opt}}).status_code == 200
    v2 = c.get(f"/api/documents/{doc}/view?task={rid}:judgment").json()
    assert "notes" not in v2["answers"]["paper"][""] and v2["answers"]["paper"][""]["full_review"] == "no"
    ov = owner.get(f"/api/documents/{doc}/view?task={rid}:judgment").json()
    assert ov["template"]["blind"] is False and ov["records"][0]["field_values"]["task_label"] == "writing" and "summary" in ov["paper_metadata"]
    by = ov["answers_by_coder"]
    assert set(by) == {"coder_1", "coder_2"} and any(x["paper"][""].get("full_review") == "no" for x in by.values())
    assert any(x["records"].get(rec_id, {}).get("", {}).get("category") == "writing" for x in by.values())
    # the export carries the answers with each coding
    ex = owner.get(f"/api/datasets/{ds}/export").json()
    paper = next(p for p in ex["papers"] if p["document_id"] == doc)
    pc = [x for x in paper["paper_codings"] if x.get("answers")]
    assert pc and pc[0]["answers"]["genai-work-judgment"][""]["scale"] == scale_opt
    rc = [x for x in paper["records"][0]["codings"] if x.get("answers")]
    assert rc and rc[0]["answers"]["genai-work-judgment"][""]["category"] == "writing" and rc[0]["status"] == "verified"
    # the templates API
    lst = owner.get("/api/templates").json()["templates"]
    assert {t["id"] for t in lst} >= {"genai-work-judgment", "genai-work-check", "genai-work-routed"} and all(t["for"] == "genai-work-extraction" for t in lst)
    one = owner.get("/api/templates/genai-work-judgment").json()
    assert one["template_id"] == gate["template_id"] and one["stages"][1]["blocks"][1]["questions"][0]["options"]
    bad = owner.post("/api/templates/validate", json={"template": {"format": 1, "id": "x", "meta": {"title": "X"}, "for": "genai-work-extraction", "stages": [{"id": "s", "label": "S", "blocks": [{"unit": "zzz"}]}]}}).json()
    assert bad["ok"] is False and any("unit must be" in e for e in bad["errors"])
    assert owner.get("/api/templates/nope").status_code == 404
    conn.close()
