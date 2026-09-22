"""The dashboard proposal call: a model (monkeypatched here) returns a block structure, the
validator cleans it; own key or self-hosted model leaves no ledger entry; credits cost ONE per
dashboard, re-proposals are free, failures are refunded. Skips without Postgres."""
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

from paperlens import credits, dashboard_spec, dashboards, providers, records  # noqa: E402
from test_analysis_table import HAC, _db_ok, _seed  # noqa: E402

GOOD = {"blocks": [{"template": "forest plot", "title": "Team vs human", "answers": ["q1"], "main_message": "Teams tend to do better.",
                    "bindings": {"x": {"column": "g_team_vs_human"}, "lower": {"column": "g_team_vs_human_lo"}, "upper": {"column": "g_team_vs_human_hi"}}},
                   {"template": "rows_table", "title": "Rows"}],
        "unanswered": [{"question": "q2", "reason": "no column records task difficulty"}]}


def test_prompt_names_questions_columns_and_blocks() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    ds, _ = _seed(conn, HAC, "human-ai-collab", f"pp-{uuid.uuid4().hex[:6]}")
    tables, default = dashboards.tables_for(conn, ds, owner=True)
    p = dashboard_spec.build_prompt([{"id": "q1", "text": "Does the team beat the human?"}], tables, default)
    assert "q1: Does the team beat the human?" in p and "unit 'conditions.measures'" in p
    assert "g_team_vs_human |" in p and "COMPUTED: Hedges' g" in p and "- forest [figure]" in p and "x* (number/integer)" in p
    assert 'Give the dashboard a "title"' in p and p.count('{"title"') >= 2     # asked for, in the shape and in the example
    assert "secret-name.pdf" not in p and "sample:" in p                       # values yes, filenames never
    assert "# CONTEXT" not in p and '"value_labels"' in p
    line = dashboard_spec._column_line({"name": "Mode", "label": "Mode", "type": "enum", "roles": ["dimension"], "n": 4, "distinct": 2,
                                        "options": [{"value": "advice_first", "label": "AI output shown before the human decides"}, "free_use"]})
    assert "advice_first (= AI output shown before the human decides), free_use" in line      # codes come with their codebook meaning
    ctx = dashboard_spec.build_prompt([], tables, default, context="Audience: clinicians. Only accuracy outcomes.")
    assert "# CONTEXT AND REQUESTS FROM THE USER" in ctx and "Only accuracy outcomes." in ctx
    rev = dashboard_spec.build_prompt([], tables, default, previous={"blocks": [{"id": "b1", "template": "bar"}]}, feedback="fewer bars", keep=["b1"])
    assert "fewer bars" in rev and "LOCKED" in rev and "b1" in rev
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.commit(); conn.close()


def test_propose_own_key_credits_and_refunds(monkeypatch) -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    sess = f"prop-{uuid.uuid4().hex[:6]}"; hdr = {"X-Session-Id": sess}
    c = TestClient(appmod.app)
    email = f"prop-{uuid.uuid4().hex[:8]}@example.org"
    uid = c.post("/api/auth/register", json={"email": email, "password": "prop-test-pass-1"}, headers=hdr).json()["user"]["id"]
    ds, _ = _seed(conn, HAC, "human-ai-collab", sess)
    conn.execute("UPDATE dataset SET owner_user_id = %s::uuid WHERE id = %s::uuid", (uid, ds))
    conn.execute("UPDATE record SET owner_user_id = %s::uuid WHERE dataset_id = %s::uuid", (uid, ds)); conn.commit()
    calls = []
    answers = ["```json\n" + json.dumps(GOOD) + "\n```"]
    def fake(model, api_key, prompt, temperature=0.3, base_url=None, **kw):
        calls.append({"model": model, "key": api_key, "base_url": base_url, "prompt": prompt})
        a = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(providers, "generate_text", fake)
    body = {"dataset_id": ds, "questions": ["Does the team beat the human?", "Does it depend on task difficulty?"]}

    # nothing chosen → told what to do; a stranger cannot even see the dataset
    assert c.post("/api/dashboards/propose", json=body, headers=hdr).status_code == 422
    stranger = TestClient(appmod.app)                     # no login cookie: building dashboards is account work
    assert stranger.post("/api/dashboards/propose", json={**body, "model": "m", "api_key": "k"}, headers={"X-Session-Id": "stranger"}).status_code == 401

    # own key: used once, no ledger entry
    before = credits.summary(conn, uid)["used"]
    r = c.post("/api/dashboards/propose", json={**body, "model": "gpt-x", "api_key": "sk-own"}, headers=hdr).json()
    assert r["ok"] and [b["template"] for b in r["spec"]["blocks"]] == ["forest", "rows_table"] and calls[-1]["key"] == "sk-own"
    assert r["spec"]["blocks"][0]["origin"] == "llm" and r["spec"]["blocks"][0]["sufficiency"]["n_rows"] == 2
    assert [u["question"] for u in r["spec"]["unanswered"]] == ["q2"] and r["proposal"]["charged"] is False
    assert len(r["proposal"]["prompt_sha256"]) == 64 and "sk-own" not in json.dumps(r) and credits.summary(conn, uid)["used"] == before
    # the model's verbatim answer and the prompt come back (to debug, to keep); saved with the
    # dashboard is the answer only, and only its owner gets it
    assert "Team vs human" in r["proposal"]["raw"] and r["prompt"].startswith("You design a dashboard")
    saved = c.post("/api/dashboards", json={"dataset_id": ds, "spec": r["spec"], "proposal": {**r["proposal"], "prompt": r["prompt"]}}, headers=hdr).json()
    got = c.get(f"/api/dashboards/{saved['id']}", headers=hdr).json()
    assert "Team vs human" in got["proposal"]["raw"] and "prompt" not in got["proposal"]
    assert c.delete(f"/api/dashboards/{saved['id']}", headers=hdr).status_code == 200
    # a self-hosted model needs no key
    assert c.post("/api/dashboards/propose", json={**body, "model": "qwen", "base_url": "http://models.example:11434"}, headers=hdr).json()["ok"]
    assert calls[-1]["base_url"] == "http://models.example:11434"

    # credits: the server's key and model, ONE credit; the revision that follows is free
    monkeypatch.setenv("PAPERLENS_CREDIT_MODEL", "gpt-4o"); monkeypatch.setenv("PAPERLENS_OPENAI_KEY", "sk-server")
    credits.grant(conn, uid, 2); conn.commit()
    r = c.post("/api/dashboards/propose", json={**body, "use_credits": True}, headers=hdr).json()
    assert r["ok"] and r["proposal"]["charged"] is True and calls[-1]["key"] == "sk-server" and calls[-1]["model"] == "gpt-4o"
    assert credits.summary(conn, uid)["used"] == before + 1
    assert conn.execute("SELECT reason FROM credit_ledger WHERE user_id = %s::uuid ORDER BY created_at DESC LIMIT 1", (uid,)).fetchone()[0] == "dashboard"
    r2 = c.post("/api/dashboards/propose", json={**body, "use_credits": True, "previous": r["spec"], "feedback": "drop the table",
                                                 "keep": [r["spec"]["blocks"][0]["id"]]}, headers=hdr).json()
    assert r2["ok"] and r2["proposal"]["charged"] is False and credits.summary(conn, uid)["used"] == before + 1
    assert r2["spec"]["blocks"][0]["id"] == r["spec"]["blocks"][0]["id"] and "drop the table" in calls[-1]["prompt"]   # the locked block survived

    # garbage twice → refunded; a provider error → refunded
    answers[:] = ["I cannot help with that.", "still not json"]; calls.clear()
    bad = c.post("/api/dashboards/propose", json={**body, "use_credits": True}, headers=hdr).json()
    assert bad["ok"] is False and len(calls) == 2 and credits.summary(conn, uid)["used"] == before + 1
    assert bad["raw"] == "still not json" and "cut off" in bad["error"] and bad["attempts"] == 2      # the user sees what came back
    answers[:] = [RuntimeError("quota exceeded")]; calls.clear()
    err = c.post("/api/dashboards/propose", json={**body, "use_credits": True}, headers=hdr).json()
    assert err["ok"] is False and "quota" in err["error"] and credits.summary(conn, uid)["used"] == before + 1

    # no credits left → 402; logged out → 401
    conn.execute("UPDATE users SET credits_used = credits_granted WHERE id = %s::uuid", (uid,)); conn.commit()
    assert c.post("/api/dashboards/propose", json={**body, "use_credits": True}, headers=hdr).status_code == 402
    c.post("/api/auth/logout", headers=hdr); c.cookies.clear()
    records.set_dataset_visibility(conn, ds, "public"); conn.commit()
    assert c.post("/api/dashboards/propose", json={**body, "use_credits": True}, headers={"X-Session-Id": "visitor"}).status_code == 401
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.commit(); conn.close()
