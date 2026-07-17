"""Phase 2b — accounts: register / login / me / logout + claim-on-login ownership.

Exercises the HTTP flow with cookie sessions, and verifies the claimed ownership
committed on a FRESH connection. Skips without Postgres.
"""
from __future__ import annotations

import os
import sys
import uuid
import warnings

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

warnings.filterwarnings("ignore")


def _db_ok() -> bool:
    try:
        from paperlens import records
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def run() -> int:
    if not _db_ok():
        print("  SKIP  no Postgres available")
        return 0
    from fastapi.testclient import TestClient
    from paperlens import records
    from paperlens.app import app

    records.init_db(records.connect())
    email = f"u{uuid.uuid4().hex[:10]}@example.org"
    pw = "s3cret-pass-123"
    sess = f"anon-{uuid.uuid4().hex[:8]}"
    failures = 0

    def check(label, cond):
        nonlocal failures
        print(("  PASS  " if cond else "  FAIL  ") + label)
        if not cond:
            failures += 1

    c = TestClient(app)

    # anonymous: not logged in
    check("anon me -> 401", c.get("/api/auth/me").status_code == 401)

    # anonymous creates a dataset under its session
    ds = c.post("/api/datasets", json={"title": "My anon set"},
                headers={"X-Session-Id": sess}).json()
    check("anon dataset created (no owner, has session)",
          ds["owner_user_id"] is None and ds["session_id"] == sess)

    # register with the SAME session -> claims the anon dataset
    r = c.post("/api/auth/register", json={"email": email, "password": pw},
               headers={"X-Session-Id": sess})
    check("register -> 200", r.status_code == 200)
    check("register claimed >=1 dataset", r.json().get("claimed", {}).get("datasets", 0) >= 1)

    # cookie now set -> me works
    me = c.get("/api/auth/me")
    check("me after register -> user", me.status_code == 200 and me.json()["email"] == email)
    uid = me.json()["id"]

    # ownership committed (fresh connection)
    fresh = records.connect()
    owner = fresh.execute("SELECT owner_user_id::text FROM dataset WHERE id = %s::uuid",
                          (ds["id"],)).fetchone()[0]
    fresh.close()
    check("claimed dataset is owned by the user (committed)", owner == uid)

    # logged-in listing is now scoped by user and includes the claimed dataset
    mine = c.get("/api/datasets").json()["datasets"]
    check("logged-in list shows claimed dataset", any(d["id"] == ds["id"] for d in mine))

    # duplicate registration -> 409
    check("duplicate email -> 409",
          TestClient(app).post("/api/auth/register",
                               json={"email": email, "password": pw}).status_code == 409)

    # wrong password -> 401
    check("wrong password -> 401",
          TestClient(app).post("/api/auth/login",
                               json={"email": email, "password": "nope"}).status_code == 401)

    # fresh client: login works, then logout invalidates the session
    c2 = TestClient(app)
    check("login -> 200", c2.post("/api/auth/login",
                                  json={"email": email, "password": pw}).status_code == 200)
    check("me after login -> user", c2.get("/api/auth/me").json().get("email") == email)
    c2.post("/api/auth/logout")
    check("me after logout -> 401", c2.get("/api/auth/me").status_code == 401)

    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


def test_accounts() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
