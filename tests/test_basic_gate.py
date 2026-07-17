"""WS1 — the shared-password beta gate (HTTP Basic Auth over the whole site).

The gate reads PAPERLENS_BASIC_PASSWORD per request: unset → no-op (dev/tests),
set → 401 without valid Basic creds, except `/healthz` which stays open for Fly's
health checks. No Postgres needed (the gate runs before any route).
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


def run() -> int:
    from fastapi.testclient import TestClient
    from paperlens.app import app

    c = TestClient(app)
    failures = 0

    def check(label, cond):
        nonlocal failures
        print(("  PASS  " if cond else "  FAIL  ") + label)
        if not cond:
            failures += 1

    # gate OFF (env unset): open
    os.environ.pop("PAPERLENS_BASIC_PASSWORD", None)
    check("gate-off /healthz -> 200", c.get("/healthz").status_code == 200)

    # gate ON
    os.environ["PAPERLENS_BASIC_PASSWORD"] = "sekret"
    try:
        check("gate-on / no creds -> 401", c.get("/").status_code == 401)
        check("gate-on WWW-Authenticate header", "WWW-Authenticate" in c.get("/").headers)
        check("gate-on /healthz exempt -> 200", c.get("/healthz").status_code == 200)
        check("gate-on good creds -> 200", c.get("/", auth=("beta", "sekret")).status_code == 200)
        check("gate-on bad creds -> 401", c.get("/", auth=("beta", "nope")).status_code == 401)
        os.environ["PAPERLENS_BASIC_USER"] = "martin"
        check("gate-on custom user good -> 200", c.get("/", auth=("martin", "sekret")).status_code == 200)
        check("gate-on custom user default-name bad -> 401", c.get("/", auth=("beta", "sekret")).status_code == 401)
    finally:
        os.environ.pop("PAPERLENS_BASIC_PASSWORD", None)
        os.environ.pop("PAPERLENS_BASIC_USER", None)

    # gate restored to OFF
    check("gate-restored / -> 200", c.get("/").status_code == 200)

    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


def test_basic_gate() -> None:
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
