"""The logged-out story: a default model chosen server-side, one free paper, no download.

The extract page no longer asks anyone to pick a model or paste a key, so the server has
to answer three things on its own: which model runs an extraction, how much a logged-out
visitor may run, and who is allowed to take the data away as a file.

Skips without Postgres.
"""
from __future__ import annotations

import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402
from paperlens import app as appmod, credits, records  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _skip_without_db():
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")


def test_default_model_needs_no_configuration() -> None:
    """A deployment that sets nothing still has a model — the UI has no picker to fall
    back on, so ``None`` here would strand every user."""
    saved = {k: os.environ.pop(k, None) for k in
             ("PAPERLENS_CREDIT_MODEL", "PAPERLENS_CREDIT_MODELS")}
    try:
        assert credits.credit_model() == credits.DEFAULT_CREDIT_MODEL
        assert credits.is_allowed_model(credits.DEFAULT_CREDIT_MODEL)
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_config_endpoint_is_public_and_names_the_model() -> None:
    _skip_without_db()
    from fastapi.testclient import TestClient
    records.init_db(records.connect())
    r = TestClient(appmod.app).get("/api/extraction-config",
                                   headers={"X-Session-Id": f"cfg-{uuid.uuid4().hex[:6]}"})
    assert r.status_code == 200                      # public: no login, no 401
    b = r.json()
    assert b["model"] == credits.credit_model()
    assert b["logged_in"] is False
    assert b["can_download"] is False                # exports are an account feature
    assert b["anon_free_extractions"] >= 1
    assert b["anon_extractions_used"] == 0           # fresh session


def test_config_reports_the_used_free_trial() -> None:
    """The counter drives the page's 'N free papers left' line, so it has to see the
    documents this anonymous session already produced."""
    _skip_without_db()
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = f"anon-used-{uuid.uuid4().hex[:6]}"
    records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None,
                    source_job_id="anon-trial", session_id=sess)

    b = TestClient(appmod.app).get("/api/extraction-config",
                                   headers={"X-Session-Id": sess}).json()
    assert b["anon_extractions_used"] == 1
    assert records.count_documents_for_session(conn, sess) == 1


def test_config_reports_credits_when_logged_in() -> None:
    _skip_without_db()
    from fastapi.testclient import TestClient
    records.init_db(records.connect())
    c = TestClient(appmod.app)
    sess = f"cfg-in-{uuid.uuid4().hex[:6]}"
    r = c.post("/api/auth/register",
               json={"email": f"cfg-{uuid.uuid4().hex[:8]}", "password": "pw123456"},
               headers={"X-Session-Id": sess})
    assert r.status_code == 200, r.text
    b = c.get("/api/extraction-config", headers={"X-Session-Id": sess}).json()
    assert b["logged_in"] is True and b["can_download"] is True
    assert b["credits"]["balance"] == 0             # fresh account


def test_anon_free_trial_is_capped_then_asks_for_an_account() -> None:
    """Second keyless paper from the same anonymous session → 402, not a provider error."""
    _skip_without_db()
    import fitz
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    sess = f"anon-cap-{uuid.uuid4().hex[:6]}"
    # already used the allowance
    for _ in range(appmod.ANON_FREE_EXTRACTIONS):
        records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None,
                        source_job_id="anon-cap", session_id=sess)

    d = fitz.open(); d.new_page().insert_text((72, 90), "x"); pdf = d.tobytes(); d.close()
    saved = os.environ.get("PAPERLENS_CREDIT_MODEL"), os.environ.get("PAPERLENS_OPENAI_KEY")
    os.environ["PAPERLENS_CREDIT_MODEL"] = "gpt-4o"      # make keyless extraction "offered"
    os.environ["PAPERLENS_OPENAI_KEY"] = "sk-server-test"
    try:
        r = TestClient(appmod.app).post(
            "/api/extract", data={"prompt": "extract"},
            files={"pdf": ("p.pdf", pdf, "application/pdf")},
            headers={"X-Session-Id": sess})
        assert r.status_code == 402, r.text
        assert "account" in r.json()["detail"].lower()
    finally:
        for k, v in zip(("PAPERLENS_CREDIT_MODEL", "PAPERLENS_OPENAI_KEY"), saved):
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_keyless_gate_is_off_where_no_server_key_is_configured() -> None:
    """A deployment with no server key never promised keyless runs — such a request must
    fall through to the caller's own key rather than be refused."""
    _skip_without_db()
    saved = {k: os.environ.pop(k, None) for k in
             ("PAPERLENS_CREDIT_MODEL", "PAPERLENS_CREDIT_MODELS",
              "PAPERLENS_GOOGLE_KEY", "PAPERLENS_OPENAI_KEY")}
    try:
        assert credits.offered() is False
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_a_self_hosted_model_is_not_the_free_trial(monkeypatch) -> None:
    """A run that names its own server (``base_url``) needs no key and costs us nothing: it is
    the caller's own provider, so a used-up trial does not block it and no server key is lent."""
    _skip_without_db()
    import fitz
    from fastapi.testclient import TestClient
    from paperlens import worker as wk
    conn = records.connect(); records.init_db(conn)
    sess = f"anon-local-{uuid.uuid4().hex[:6]}"
    for _ in range(appmod.ANON_FREE_EXTRACTIONS):                      # trial already used
        records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id=None,
                        source_job_id="anon-local", session_id=sess)
    seen = {}
    def fake_enqueue(name, *args, **kw):
        seen.update(kw); return "job-local"
    monkeypatch.setattr(wk, "enqueue", fake_enqueue)
    monkeypatch.setenv("PAPERLENS_CREDIT_MODEL", "gpt-4o")             # keyless runs ARE offered here
    monkeypatch.setenv("PAPERLENS_OPENAI_KEY", "sk-server-test")
    d = fitz.open(); d.new_page().insert_text((72, 90), "x"); pdf = d.tobytes(); d.close()
    r = TestClient(appmod.app).post(
        "/api/extract", data={"prompt": "extract", "model": "qwen2.5vl:7b", "base_url": "http://models.example:11434"},
        files={"pdf": ("p.pdf", pdf, "application/pdf")}, headers={"X-Session-Id": sess})
    assert r.status_code == 200, r.text
    assert r.json()["queued"] is True
    assert seen["base_url"] == "http://models.example:11434" and seen["model"] == "qwen2.5vl:7b"
    assert seen["api_key"] == ""                                       # the server key was not lent
