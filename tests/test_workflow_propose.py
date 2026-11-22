"""The workflow proposal call: a model (monkeypatched here) answers with a preset to use or a new
preset document and a workflow around it; the proposer normalises and validates both — a new
preset's pin known to the check before it is saved — and the person saves the preset, then the
workflow, through the ordinary routes. The prompt names what the model may build with; own key
or self-hosted model leaves no ledger entry; credits cost ONE per proposal, revisions are free,
failures are refunded. Skips without Postgres."""
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

from test_workflows import _skip

from paperlens import brands, credits, providers, records, workflow_propose, workflows
from paperlens import workflow_spec as ws
from paperlens.principal import Principal

HAC_PIN = "human-ai-collab@193067f7"
NEW_PRESET = {"format": 2, "meta": {"title": "Minimum wage effects", "tagline": "one row per estimate", "mode": "extraction"},
              "prompt": {"text": "Extract every employment elasticity the paper reports."},
              "paper": {"fields": [{"name": "country", "type": "string", "label": "Country", "help": "where the data come from"}]},
              "entries": {"key": "estimates", "label": "Estimate", "cardinality": "many",
                          "fields": [{"name": "outcome", "type": "string", "label": "Outcome", "help": "the employment outcome", "evidence": "row", "confidence": "main"},
                                     {"name": "elasticity", "type": "number", "label": "Elasticity", "help": "as printed", "evidence": "value", "confidence": "main"},
                                     {"name": "se", "type": "number", "label": "SE", "help": "its standard error", "evidence": "value", "confidence": "main"}],
                          "children": []},
              "confidence": {"levels": ["high", "medium", "low"], "notes": True, "groups": [{"id": "main", "label": "Main values", "scope": "entry"}]},
              "display": {"tabs": [{"id": "details", "label": "Details", "fields": ["outcome", "elasticity", "se"]}], "triage": "low_confidence_first"}}
USE = {"preset": {"use": "human-ai-collab"},
       "workflow": {"id": "hac-audit", "meta": {"title": "Human–AI collaboration, audited"},
                    "steps": [{"id": "extract", "kind": "extract", "preset": "human-ai-collab"},
                              {"id": "contract", "kind": "check", "check": "schema", "over": ["extract"]},
                              {"id": "audit", "kind": "review", "purpose": "audit", "over": ["extract"], "mode": "sample", "unit": "paper", "n": 20, "blocks": "release"},
                              {"id": "release", "kind": "release", "gate": ["extract", "audit"]}]},
       "notes": ["The bundled human–AI preset holds the task and condition fields the questions need."], "unanswered": [{"question": "q2", "reason": "no paper reports cost"}]}
NEWP = {"preset": {"new": NEW_PRESET},
        "workflow": {"meta": {"title": "Minimum wage effects"},
                     "steps": [{"id": "extract", "kind": "extract", "preset": "new"},
                               {"id": "contract", "kind": "check", "check": "schema", "over": ["extract"]},
                               {"id": "plausible", "kind": "check", "check": "rules", "over": ["extract"], "on_fail": "flag", "params": {"rules": [{"code": "se_positive", "require": {"se": {"gt": 0}}}]}},
                               {"id": "made_up", "kind": "check", "check": "vibes", "over": ["extract"]},
                               {"id": "loop", "kind": "loop", "steps": ["extract"]},
                               {"id": "audit", "kind": "review", "purpose": "audit", "over": ["extract"], "mode": "sample", "unit": "paper", "n": 20}]},
        "notes": ["No preset records employment elasticities."], "unanswered": []}


def _signed_in(tag: str):
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    sess = f"{tag}-{uuid.uuid4().hex[:6]}"; hdr = {"X-Session-Id": sess}
    c = TestClient(appmod.app)
    uid = c.post("/api/auth/register", json={"email": f"{tag}-{uuid.uuid4().hex[:8]}@example.org", "password": "prop-test-pass-1"}, headers=hdr).json()["user"]["id"]
    return c, hdr, uid


def _stub(monkeypatch, answers: list):
    calls = []
    def fake(model, api_key, prompt, temperature=0.3, base_url=None, **kw):
        calls.append({"model": model, "key": api_key, "base_url": base_url, "prompt": prompt})
        a = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(providers, "generate_text", fake)
    return calls


def test_the_prompt_names_the_questions_presets_checks_templates_and_the_grammar() -> None:
    _skip()
    conn = records.connect(); records.init_db(conn)
    who = Principal(user_id=None, session_id="prompt-sess")
    cat = workflow_propose.catalogue(conn, who, brands.resolve("metalens.tech"))
    ids = {p["id"] for p in cat["presets"]}
    assert "human-ai-collab" in ids and "econ-regression-tables" in ids
    hac = next(p for p in cat["presets"] if p["id"] == "human-ai-collab")
    assert hac["schema_id"] == HAC_PIN and any(f.startswith("N_Exp:integer") for f in hac["entry_fields"]) and hac["children"] and hac["children"][0].startswith("conditions[")
    assert {c["id"] for c in cat["checks"]} >= {"schema", "rules", "refs"} and not any(c["id"] == "genai-work-passes" for c in cat["checks"])
    assert cat["grammar"]["purposes"] == list(ws.PURPOSES) and any(w["id"] == "econ" for w in cat["workflows"])
    prompt = workflow_propose.build_prompt([{"id": "q1", "text": "Do teams beat the human alone?"}], cat, context="economics journals")
    for needle in ("q1: Do teams beat the human alone?", "economics journals", "human-ai-collab (pin human-ai-collab@", "- rules:", "audit | routed | monitor | calibrate | adjudicate",
                   '"preset": {"use": "<preset id>"} | {"new":', "format 2", "Return EXACTLY ONE JSON object"):
        assert needle in prompt, needle
    rev = workflow_propose.build_prompt([], cat, previous={"workflow": {"id": "x"}, "notes": []}, feedback="fewer steps")
    assert "# REVISION" in rev and "fewer steps" in rev


def test_an_existing_preset_is_reused_and_a_new_one_validated_with_its_pin_known(monkeypatch) -> None:
    _skip()
    c, hdr, uid = _signed_in("prop")
    conn = records.connect()
    # nothing chosen → told; a stranger → 401
    assert c.post("/api/workflows/propose", json={"questions": ["x"]}, headers=hdr).status_code == 422
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    assert TestClient(appmod.app).post("/api/workflows/propose", json={"questions": ["x"], "model": "m", "api_key": "k"}, headers={"X-Session-Id": "nobody"}).status_code == 401
    # an existing preset: the bare id becomes its live pin, the workflow validates, the cost is one call per paper
    calls = _stub(monkeypatch, ["```json\n" + json.dumps(USE) + "\n```"])
    before = credits.summary(conn, uid)["used"]
    r = c.post("/api/workflows/propose", json={"questions": ["Do teams beat the human alone?", "What does it cost?"], "model": "gpt-x", "api_key": "sk-own"}, headers=hdr).json()
    assert r["ok"] and r["errors"] == [] and r["proposal"]["charged"] is False and r["proposal"]["attempts"] == 1 and calls[-1]["key"] == "sk-own"
    assert r["preset"] == {"use": "human-ai-collab", "schema_id": HAC_PIN, "title": "Human–AI collaboration", "mode": "extraction"} or r["preset"]["schema_id"] == HAC_PIN
    wf = r["workflow"]
    assert wf["steps"][0]["preset"] == HAC_PIN and wf["steps"][0]["source"] == "live" and [s["kind"] for s in wf["steps"]] == ["extract", "check", "review", "release"]
    assert wf["steps"][2]["purpose"] == "audit" and r["contract"].startswith("hac-audit@") and r["cost"] == {"calls_per_paper": 1, "credits_per_paper": 1}
    assert r["unanswered"] == [{"question": "q2", "reason": "no paper reports cost"}] and r["questions"][1]["id"] == "q2"
    assert "sk-own" not in json.dumps(r) and len(r["proposal"]["prompt_sha256"]) == 64 and credits.summary(conn, uid)["used"] == before
    # the proposal is a document the editor's validator accepts and the routes save as it is
    assert c.post("/api/workflows/validate", json={"spec": wf}, headers=hdr).json()["ok"]
    saved = c.post("/api/workflows", json={"spec": wf}, headers=hdr)
    assert saved.status_code == 200 and saved.json()["contract"].split("@")[1] == r["contract"].split("@")[1]
    # a new preset: normalised, validated, its pin known to the workflow's check before it exists; a made-up check and a foreign kind dropped
    _stub(monkeypatch, [json.dumps(NEWP)])
    r = c.post("/api/workflows/propose", json={"questions": ["How large are minimum wage employment elasticities?"], "model": "gpt-x", "api_key": "sk-own"}, headers=hdr).json()
    assert r["ok"] and r["errors"] == [], r["errors"]
    new = r["preset"]
    assert "new" in new and new["schema_id"].startswith("minimum-wage-effects@") and new["new"]["id"] == "minimum-wage-effects" and new["errors"] == []
    wf = r["workflow"]
    assert wf["steps"][0]["preset"] == new["schema_id"] and [s["id"] for s in wf["steps"]] == ["extract", "contract", "plausible", "audit", "release"]
    assert wf["steps"][2]["params"]["rules"][0]["code"] == "se_positive" and wf["steps"][-1]["gate"] == ["extract", "audit"]
    assert sorted(d.split(":")[0] for d in r["dropped"]) == ["check 'made_up'", "step 'loop'"]
    # saving it: the preset first (the server assigns the id), the pins rewritten to the saved contract, then the workflow
    pres = c.post("/api/presets", json={"spec": new["new"], "visibility": "private"}, headers=hdr)
    assert pres.status_code == 200, pres.text
    conn.rollback()
    from paperlens import presets
    saved_pin = presets.get(pres.json()["id"], conn)["schema_id"]
    assert saved_pin != new["schema_id"] and saved_pin.startswith("minimum-wage-effects-")
    for st in wf["steps"]:
        if st["kind"] == "extract":
            st["preset"] = saved_pin
    out = c.post("/api/workflows", json={"spec": wf}, headers=hdr)
    assert out.status_code == 200, out.text
    assert out.json()["presets"] == [saved_pin] and workflows.spec_for(conn, out.json()["id"])["steps"][0]["preset"] == saved_pin


def test_a_bad_answer_is_nudged_once_and_errors_come_back_placed(monkeypatch) -> None:
    _skip()
    c, hdr, uid = _signed_in("nudge")
    broken = copy.deepcopy(USE); broken["workflow"]["steps"][2]["mode"] = "sometimes"
    calls = _stub(monkeypatch, ["not json at all", json.dumps(broken)])
    r = c.post("/api/workflows/propose", json={"questions": ["x"], "model": "m", "api_key": "k"}, headers=hdr).json()
    assert r["ok"] and r["proposal"]["attempts"] == 2 and "Return ONLY the JSON object" in calls[-1]["prompt"]
    assert any(e.startswith("$.steps[2].mode") for e in r["errors"]) and r["contract"] is None and r["cost"]["calls_per_paper"] == 0
    # a proposal with problems gets the problems back in the nudge, then the corrected answer wins
    calls = _stub(monkeypatch, [json.dumps(broken), json.dumps(USE)])
    r = c.post("/api/workflows/propose", json={"questions": ["x"], "model": "m", "api_key": "k"}, headers=hdr).json()
    assert r["ok"] and r["errors"] == [] and r["proposal"]["attempts"] == 2 and "$.steps[2].mode" in calls[-1]["prompt"]
    # a foreign private preset named by the model is refused
    other, ohdr, _ = _signed_in("other")
    pid = other.post("/api/presets", json={"spec": NEW_PRESET, "visibility": "private"}, headers=ohdr).json()["id"]
    theirs = copy.deepcopy(USE); theirs["preset"] = {"use": pid}; theirs["workflow"]["steps"][0]["preset"] = pid
    _stub(monkeypatch, [json.dumps(theirs)])
    r = c.post("/api/workflows/propose", json={"questions": ["x"], "model": "m", "api_key": "k"}, headers=hdr).json()
    assert r["ok"] and any("not a preset you can use" in e for e in r["errors"])


def test_credits_cost_one_per_proposal_revisions_are_free_and_failures_refunded(monkeypatch) -> None:
    _skip()
    c, hdr, uid = _signed_in("cred")
    conn = records.connect()
    monkeypatch.setenv("PAPERLENS_CREDIT_MODEL", "gpt-4o"); monkeypatch.setenv("PAPERLENS_OPENAI_KEY", "sk-server")
    credits.grant(conn, uid, 2); conn.commit()
    before = credits.summary(conn, uid)["used"]
    calls = _stub(monkeypatch, [json.dumps(USE)])
    r = c.post("/api/workflows/propose", json={"questions": ["x"], "use_credits": True}, headers=hdr).json()
    assert r["ok"] and r["proposal"]["charged"] is True and calls[-1]["key"] == "sk-server" and calls[-1]["model"] == "gpt-4o"
    assert credits.summary(conn, uid)["used"] == before + 1 and r["credits"]["used"] == before + 1
    assert conn.execute("SELECT reason FROM credit_ledger WHERE user_id = %s::uuid ORDER BY created_at DESC LIMIT 1", (uid,)).fetchone()[0] == "workflow"
    r2 = c.post("/api/workflows/propose", json={"questions": ["x"], "use_credits": True, "previous": {k: r[k] for k in ("preset", "workflow", "notes")}, "feedback": "drop the rules"}, headers=hdr).json()
    assert r2["ok"] and r2["proposal"]["charged"] is False and credits.summary(conn, uid)["used"] == before + 1 and "drop the rules" in calls[-1]["prompt"]
    _stub(monkeypatch, ["nope", "still nope"])
    bad = c.post("/api/workflows/propose", json={"questions": ["x"], "use_credits": True}, headers=hdr).json()
    assert bad["ok"] is False and bad["attempts"] == 2 and "cut off" in bad["error"] and bad["raw"] == "still nope" and credits.summary(conn, uid)["used"] == before + 1
    _stub(monkeypatch, [RuntimeError("quota exceeded")])
    err = c.post("/api/workflows/propose", json={"questions": ["x"], "use_credits": True}, headers=hdr).json()
    assert err["ok"] is False and "quota" in err["error"] and credits.summary(conn, uid)["used"] == before + 1
    conn.execute("UPDATE users SET credits_used = credits_granted WHERE id = %s::uuid", (uid,)); conn.commit()
    assert c.post("/api/workflows/propose", json={"questions": ["x"], "use_credits": True}, headers=hdr).status_code == 402
