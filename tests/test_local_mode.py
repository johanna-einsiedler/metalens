"""Single-user local mode (PAPERLENS_LOCAL_MODE=1): every request is the fixed local owner,
accounts are gone, exports are allowed, jobs run inline, retention is off, and extra preset
directories are loaded. Skips the DB-backed parts without Postgres.
"""
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

from paperlens import app as appmod, localmode, presets, records, retention, worker  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def test_switches_read_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("PAPERLENS_LOCAL_MODE", raising=False)
    monkeypatch.delenv("PAPERLENS_INLINE_JOBS", raising=False)
    assert not localmode.enabled() and not localmode.inline_jobs()
    monkeypatch.setenv("PAPERLENS_INLINE_JOBS", "1")
    assert localmode.inline_jobs() and not localmode.enabled()
    assert worker.enqueue("extract_job") is None                 # never touches Redis
    monkeypatch.setenv("PAPERLENS_LOCAL_MODE", "1")
    assert localmode.enabled() and localmode.inline_jobs()


def test_local_mode_requests_act_as_the_local_owner(monkeypatch) -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    conn = records.connect(); records.init_db(conn)
    monkeypatch.setenv("PAPERLENS_LOCAL_MODE", "1")
    monkeypatch.setenv("PAPERLENS_LOCAL_USER_ID", str(uuid.uuid4()))
    monkeypatch.delenv("PAPERLENS_BASIC_PASSWORD", raising=False)
    uid = localmode.ensure_local_user(conn)
    assert localmode.ensure_local_user(conn) == uid               # idempotent
    c = TestClient(appmod.app)

    me = c.get("/api/auth/me").json()
    assert me["id"] == uid and me["local_mode"] is True
    cfg = c.get("/api/extraction-config").json()
    assert cfg["local_mode"] is True and cfg["logged_in"] is True and cfg["can_download"] is True
    assert cfg["offered"] is False                                # no credits, no trial locally
    for path in ("/api/auth/register", "/api/auth/login"):
        assert c.post(path, json={"email": "x@y.z", "password": "pw"}).status_code == 404
    assert c.post("/api/auth/logout").status_code == 404
    # a dataset made in local mode belongs to the local owner
    ds = c.post("/api/datasets", json={"title": "local ds", "visibility": "private"}).json()
    assert str(conn.execute("SELECT owner_user_id FROM dataset WHERE id = %s::uuid", (ds["id"],)).fetchone()[0]) == uid
    # retention does nothing here
    retention.touch(conn, "some-session", None, force=True)
    assert conn.execute("SELECT 1 FROM anon_session WHERE session_id = 'some-session'").fetchone() is None
    assert retention.sweep(conn)["sessions"] == 0
    ver = c.get("/api/version").json()
    assert ver["local_mode"] is True and "masem-direct" in ver["presets"] and ver["presets"]["masem-direct"].startswith("masem-direct@")
    records.delete_dataset(conn, ds["id"]); conn.commit(); conn.close()


def test_extra_preset_directories_are_loaded(tmp_path, monkeypatch) -> None:
    spec = presets.load_all()["summarize"]
    mine = json.loads(json.dumps(spec)); mine["id"] = "my-summary"; mine["meta"]["title"] = "My summary"
    mine["prompt"] = {"text": spec["prompt"]["text"], "generate": spec["prompt"].get("generate", [])}
    (tmp_path / "my-summary.json").write_text(json.dumps(mine), encoding="utf-8")
    monkeypatch.setenv("PAPERLENS_PRESET_DIRS", str(tmp_path))
    allp = presets.load_all()
    assert "my-summary" in allp and allp["my-summary"]["meta"]["title"] == "My summary"
    assert "summarize" in allp                                    # built-ins still there
    monkeypatch.setenv("PAPERLENS_PRESET_DIRS", str(tmp_path / "missing"))
    assert "my-summary" not in presets.load_all()                 # a missing dir is not fatal
