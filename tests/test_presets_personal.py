"""Personal (DB-backed, user-owned) presets — CRUD, ownership scoping, global-by-id
resolution (the worker path), the principal-aware picker, promote-on-publish, and
claim-on-login. Skips without Postgres.
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

from paperlens import auth, presets, records  # noqa: E402
from paperlens.principal import Principal  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _mk_preset(conn, uid, *, visibility="private", session_id=None):
    return records.create_personal_preset(
        conn, title="Screener " + uuid.uuid4().hex[:6],
        prompt='Return {"records":[{"X":1}],"evidence":[]}',
        sub_views=[{"id": "v", "label": "V", "include_keys": ["X"]}],
        owner_user_id=uid, session_id=session_id, visibility=visibility)


def test_crud_and_resolution() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    conn = records.connect(); records.init_db(conn)
    uid = auth.create_user(conn, f"pp{uuid.uuid4().hex[:8]}", "pw")["id"]
    p = _mk_preset(conn, uid)
    pid = p["id"]
    assert p["source"] == "personal" and p["visibility"] == "private"
    # global-by-id resolution (no principal) — the worker/persist path
    assert presets.get(pid, conn=conn)["title"] == p["title"]
    row = presets.emit_schema_row(pid, conn=conn)
    assert row and row["core_keys"] == ["X"]
    # ownership + update + delete
    who = Principal(session_id="s", user_id=uid)
    assert records.is_preset_owner(conn, pid, who)
    assert not records.is_preset_owner(conn, pid, Principal(session_id="other", user_id=None))
    records.update_personal_preset(conn, pid, title="Renamed", visibility="public")
    assert records.get_personal_preset(conn, pid)["title"] == "Renamed"
    assert records.get_personal_preset(conn, pid)["visibility"] == "public"
    assert records.delete_personal_preset(conn, pid)["deleted"] == 1
    assert records.get_personal_preset(conn, pid) is None
    conn.close()


def test_list_scoping() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    conn = records.connect(); records.init_db(conn)
    a = auth.create_user(conn, f"a{uuid.uuid4().hex[:8]}", "pw")["id"]
    b = auth.create_user(conn, f"b{uuid.uuid4().hex[:8]}", "pw")["id"]
    priv = _mk_preset(conn, a, visibility="private")["id"]
    pub = _mk_preset(conn, a, visibility="public")["id"]
    # owner sees both; stranger sees only the public one; owned_only hides others' public
    a_all = {p["id"] for p in records.list_personal_presets(conn, owner_user_id=a)}
    b_all = {p["id"] for p in records.list_personal_presets(conn, owner_user_id=b)}
    a_owned = {p["id"] for p in records.list_personal_presets(conn, owner_user_id=a, owned_only=True)}
    assert {priv, pub} <= a_all
    assert pub in b_all and priv not in b_all
    assert priv in a_owned and pub in a_owned
    assert pub not in {p["id"] for p in records.list_personal_presets(conn, owner_user_id=b, owned_only=True)}
    conn.close()


def test_promote_on_publish() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    conn = records.connect(); records.init_db(conn)
    uid = auth.create_user(conn, f"pr{uuid.uuid4().hex[:8]}", "pw")["id"]
    pid = _mk_preset(conn, uid, visibility="private")["id"]
    ds = records.create_dataset(conn, title="DS", schema_id=f"{pid}@v1", owner_user_id=uid)
    conn.commit()
    who = Principal(session_id="s", user_id=uid)
    assert records.promote_dataset_preset(conn, ds["id"], who) == pid
    assert records.get_personal_preset(conn, pid)["visibility"] == "public"
    # a non-owner can't promote someone else's preset
    pid2 = _mk_preset(conn, uid, visibility="private")["id"]
    ds2 = records.create_dataset(conn, title="DS2", schema_id=f"{pid2}@v1", owner_user_id=uid)
    conn.commit()
    stranger = Principal(session_id="x", user_id=None)
    assert records.promote_dataset_preset(conn, ds2["id"], stranger) is None
    assert records.get_personal_preset(conn, pid2)["visibility"] == "private"
    conn.close()


def test_claim_on_login() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    conn = records.connect(); records.init_db(conn)
    sess = f"anon-{uuid.uuid4().hex[:8]}"
    p = _mk_preset(conn, None, session_id=sess)   # anonymous preset
    assert records.get_personal_preset(conn, p["id"])["owner_user_id"] is None
    uid = auth.create_user(conn, f"cl{uuid.uuid4().hex[:8]}", "pw")["id"]
    claimed = auth.claim_anonymous(conn, session_id=sess, user_id=uid)
    assert claimed["presets"] == 1
    assert records.get_personal_preset(conn, p["id"])["owner_user_id"] == uid
    conn.close()


def test_picker_endpoint() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    from fastapi.testclient import TestClient
    from paperlens.app import app
    records.init_db(records.connect())
    owner = TestClient(app)
    sid = f"s-{uuid.uuid4().hex[:6]}"
    email = f"pk{uuid.uuid4().hex[:8]}"
    assert owner.post("/api/auth/register", json={"email": email, "password": "pw"},
                      headers={"X-Session-Id": sid}).status_code == 200
    made = owner.post("/api/presets", json={"title": "Mine", "prompt": "Return {}",
                                            "visibility": "private"})
    assert made.status_code == 200
    pid = made.json()["id"]
    # owner: /api/presets includes it (personal+owned); /api/presets/mine lists it
    picker = owner.get("/api/presets").json()["presets"]
    row = next((r for r in picker if r.get("preset_id") == pid), None)
    assert row and row["personal"] and row["owned"]
    assert any(r["id"] == pid for r in owner.get("/api/presets/mine").json()["presets"])
    # a stranger client does NOT see the private preset in the picker
    stranger = TestClient(app)
    sp = stranger.get("/api/presets", headers={"X-Session-Id": f"s-{uuid.uuid4().hex[:6]}"}).json()["presets"]
    assert not any(r.get("preset_id") == pid for r in sp)
    # stranger can't edit/delete it (403)
    assert stranger.request("PATCH", f"/api/presets/{pid}", json={"title": "hax"},
                            headers={"X-Session-Id": "z"}).status_code == 403
    assert stranger.delete(f"/api/presets/{pid}", headers={"X-Session-Id": "z"}).status_code == 403


def _main() -> int:
    failures = 0
    for label, fn in [
        ("preset:crud+resolution", test_crud_and_resolution),
        ("preset:list-scoping", test_list_scoping),
        ("preset:promote-on-publish", test_promote_on_publish),
        ("preset:claim-on-login", test_claim_on_login),
        ("preset:picker-endpoint", test_picker_endpoint),
    ]:
        try:
            fn(); print(f"  PASS  {label}")
        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ == "Skipped":
                print(f"  SKIP  {label}: {exc}"); continue
            failures += 1; print(f"  FAIL  {label}: {exc!r}")
    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
