"""A preset someone sent as a file, saved as a personal preset ("Load preset file" on /presets):
it keeps its own id — and so its schema id — when that id is free, and falls back to a fresh id
when a built-in, retired or personal preset already uses it. Skips without Postgres."""
from __future__ import annotations

import copy
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

from paperlens import preset_spec as ps, records  # noqa: E402


def _db_ok() -> bool:
    try:
        records.connect().close(); return True
    except Exception:
        return False


def _spec(pid: str) -> dict:
    return {"format": 2, "id": pid, "version": 1,
            "meta": {"title": "Calibration screening", "tagline": "four criteria", "mode": "extraction"},
            "prompt": {"text": "Screen the paper.\n\n${output_schema}"},
            "entries": {"key": "screening", "label": "Screening", "cardinality": "one",
                        "fields": [{"name": "included", "type": "enum", "options": ["true", "false"], "required": True, "evidence": "value"}]}}


def test_a_loaded_file_keeps_its_id_when_free_and_falls_back_otherwise() -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens.app import app
    c = TestClient(app); A = {"X-Session-Id": f"pf-a-{uuid.uuid4().hex[:6]}"}; B = {"X-Session-Id": f"pf-b-{uuid.uuid4().hex[:6]}"}
    pid = f"calib-{uuid.uuid4().hex[:6]}"
    want = ps.schema_id(ps.normalize(copy.deepcopy(_spec(pid))))
    v = c.post("/api/presets/validate", json={"spec": _spec(pid)}).json()
    assert v["ok"] and v["schema_id"] == want                       # the page shows the schema it will run under
    r = c.post("/api/presets", json={"spec": _spec(pid), "visibility": "public", "keep_id": True}, headers=A).json()
    assert r["id"] == pid and r["schema_id"] == want and r["id_kept"] is True
    # the same file loaded by someone else: the id is taken, a fresh one is given and said so
    r2 = c.post("/api/presets", json={"spec": _spec(pid), "visibility": "private", "keep_id": True}, headers=B).json()
    assert r2["id"] != pid and r2["id_kept"] is False and r2["id_wanted"] == pid
    # a built-in id, a retired id and an invalid id are never taken over
    for taken in ("masem-direct", "masem", "forestplot", "Not An Id"):
        r3 = c.post("/api/presets", json={"spec": _spec(taken), "visibility": "private", "keep_id": True}, headers=B).json()
        assert r3["id"] != taken and r3["id_kept"] is False, taken
        records.delete_personal_preset(records.connect(), r3["id"])
    # without keep_id (the editor's own save) the id is always fresh, as before
    r4 = c.post("/api/presets", json={"spec": _spec(f"calib-{uuid.uuid4().hex[:6]}"), "visibility": "private"}, headers=B).json()
    assert r4["id"].startswith("calibration-screening-") and r4["id_kept"] is False
    # the kept-id preset is listed for everyone on this brand (public), under its own schema id
    listed = {p["preset_id"]: p for p in c.get("/api/presets?usage=import", headers=B).json()["presets"]}
    assert pid in listed and listed[pid]["schema_id"] == want
    conn = records.connect()
    for x in (pid, r2["id"], r4["id"]):
        records.delete_personal_preset(conn, x)
    conn.commit(); conn.close()
