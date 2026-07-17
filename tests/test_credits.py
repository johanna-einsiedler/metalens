"""WS5 — per-user extraction credits (server-key quota).

Unit: grant / try_consume (no oversell) / refund / balance. Config: is_allowed_model +
credit_model from env. HTTP: /api/credits requires login; /api/extract?use_credits with
zero balance is rejected (402) BEFORE any provider call. Skips without Postgres.
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

from paperlens import auth, credits  # noqa: E402


def _db_ok() -> bool:
    try:
        from paperlens import records
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def test_config_model_allowlist() -> None:
    for k in ("PAPERLENS_CREDIT_MODEL", "PAPERLENS_CREDIT_MODELS"):
        os.environ.pop(k, None)
    assert credits.credit_model() is None
    assert credits.is_allowed_model("gpt-4o") is False
    try:
        os.environ["PAPERLENS_CREDIT_MODEL"] = "gpt-4o"
        assert credits.credit_model() == "gpt-4o"
        assert credits.is_allowed_model("gpt-4o") is True
        assert credits.is_allowed_model("gpt-5") is False
        os.environ["PAPERLENS_CREDIT_MODELS"] = "gpt-4o, gpt-4o-mini"
        assert credits.is_allowed_model("gpt-4o-mini") is True
    finally:
        os.environ.pop("PAPERLENS_CREDIT_MODEL", None)
        os.environ.pop("PAPERLENS_CREDIT_MODELS", None)
    assert credits.server_key_for("openai") is None  # unset → None (never a stray key)


def test_grant_consume_refund() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    from paperlens import records
    conn = records.connect(); records.init_db(conn)
    u = auth.create_user(conn, f"credit-{uuid.uuid4().hex[:8]}", "pw")["id"]
    assert credits.balance(conn, u) == 0
    credits.grant(conn, u, 2)
    assert credits.balance(conn, u) == 2
    assert credits.try_consume(conn, u, model="gpt-4o") is True
    assert credits.try_consume(conn, u, model="gpt-4o") is True
    assert credits.balance(conn, u) == 0
    assert credits.try_consume(conn, u, model="gpt-4o") is False   # no oversell
    credits.refund(conn, u)
    assert credits.balance(conn, u) == 1
    led = credits.ledger(conn, u)
    assert [row["reason"] for row in led][:1] == ["refund"]        # newest first
    assert credits.summary(conn, u) == {"granted": 2, "used": 1, "balance": 1}
    conn.close()


def test_me_includes_credits() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    from paperlens import records
    conn = records.connect(); records.init_db(conn)
    u = auth.create_user(conn, f"credit-{uuid.uuid4().hex[:8]}", "pw")["id"]
    credits.grant(conn, u, 3)
    got = auth.get_user(conn, u)
    assert got["credits"] == {"granted": 3, "used": 0, "balance": 3}
    conn.close()


def test_endpoints_gating() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    from fastapi.testclient import TestClient
    from paperlens import records
    from paperlens.app import app
    records.init_db(records.connect())
    c = TestClient(app)
    # anon → /api/credits 401
    assert c.get("/api/credits").status_code == 401
    # register a fresh user (0 credits); cookie set on the client
    email = f"credit-{uuid.uuid4().hex[:8]}"
    r = c.post("/api/auth/register", json={"email": email, "password": "pw"},
               headers={"X-Session-Id": f"s-{uuid.uuid4().hex[:6]}"})
    assert r.status_code == 200
    cr = c.get("/api/credits")
    assert cr.status_code == 200 and cr.json()["balance"] == 0
    # use_credits with server configured but zero balance → 402 (no provider call)
    import fitz
    d = fitz.open(); d.new_page().insert_text((72, 90), "x"); pdf = d.tobytes(); d.close()
    os.environ["PAPERLENS_CREDIT_MODEL"] = "gpt-4o"
    os.environ["PAPERLENS_OPENAI_KEY"] = "sk-server-test"
    try:
        resp = c.post("/api/extract",
                      files={"pdf": ("x.pdf", pdf, "application/pdf")},
                      data={"use_credits": "true", "prompt": "Return {}", "schema_id": "x@v1"})
        assert resp.status_code == 402, (resp.status_code, resp.text)
    finally:
        os.environ.pop("PAPERLENS_CREDIT_MODEL", None)
        os.environ.pop("PAPERLENS_OPENAI_KEY", None)


def _main() -> int:
    failures = 0
    for label, fn in [
        ("credits:config", test_config_model_allowlist),
        ("credits:grant-consume-refund", test_grant_consume_refund),
        ("credits:me-includes", test_me_includes_credits),
        ("credits:endpoints", test_endpoints_gating),
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
