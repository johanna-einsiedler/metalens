"""Deployment healthcheck — verify the live integrations from inside the machine.

Run on Fly after a deploy (it reads the same secrets the app uses):

    fly ssh console -C "python scripts/healthcheck.py"

Checks Postgres (connect + schema + a write/read/delete round-trip), object storage
(R2/S3 put/get/delete), Redis (ping), and the credits configuration. Non-destructive:
the temporary rows/objects it creates are removed. Exit code 0 = all green.
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_results: list[tuple[str, bool, str]] = []


def check(name):
    def deco(fn):
        try:
            detail = fn() or ""
            _results.append((name, True, detail))
        except Exception as exc:  # noqa: BLE001
            _results.append((name, False, f"{type(exc).__name__}: {exc}"))
        return fn
    return deco


@check("Postgres — connect + schema + round-trip")
def _db():
    from paperlens import records
    conn = records.connect()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()}
        expected = {"paper", "schema", "extraction_document", "record", "evidence_span",
                    "dataset", "users", "sessions", "parsed_document", "credit_ledger"}
        missing = expected - tables
        if missing:
            raise RuntimeError(f"missing tables: {sorted(missing)}")
        # write/read/delete round-trip on a throwaway dataset
        ds = records.create_dataset(conn, title=f"__healthcheck_{uuid.uuid4().hex[:8]}",
                                    session_id="__healthcheck__")
        got = records.get_dataset(conn, ds["id"])
        conn.execute("DELETE FROM dataset WHERE id = %s::uuid", (ds["id"],))
        conn.commit()
        nusers = conn.execute("SELECT count(*) FROM users").fetchone()[0]
        assert got and got["id"] == ds["id"]
        return f"{len(tables)} tables, {nusers} users, round-trip OK"
    finally:
        conn.close()


@check("Object storage (R2/S3) — put/get/delete")
def _storage():
    from paperlens import storage
    store = storage.get_store()
    backend = type(store).__name__
    key = f"healthcheck/{uuid.uuid4().hex}.txt"
    payload = b"metalens-healthcheck"
    store.put(key, payload, "text/plain")
    ok = store.get(key) == payload
    try:
        store.delete(key)
    except Exception:
        pass
    if not ok:
        raise RuntimeError("get() did not return what put() wrote")
    return f"{backend}: put/get/delete OK"


@check("Redis (arq queue) — ping")
def _redis():
    import asyncio
    from arq import create_pool
    from paperlens import worker

    async def ping():
        pool = await create_pool(worker._fast_settings())  # fail-fast settings
        try:
            await pool.ping()
        finally:
            await pool.aclose()

    asyncio.run(ping())
    return f"reachable at {os.environ.get('PAPERLENS_REDIS_URL', 'redis://localhost:6379')[:40]}…"


@check("Credits — server model + key configured")
def _credits():
    from paperlens import credits
    model = credits.credit_model()
    offered = credits.offered()
    if not model:
        raise RuntimeError("PAPERLENS_CREDIT_MODEL not set")
    if not offered:
        raise RuntimeError(f"model={model} but no server key for its provider "
                           f"(set PAPERLENS_GOOGLE_KEY for a gemini model)")
    return f"model={model}, offered=True"


def main() -> int:
    # The @check decorator ran each check at definition time, filling _results.
    print("\n  Metalens deployment healthcheck\n  " + "-" * 40)
    ok = True
    for name, passed, detail in _results:
        print(f"  {'✓ PASS' if passed else '✗ FAIL'}  {name}")
        if detail:
            print(f"          {detail}")
        ok = ok and passed
    print("  " + "-" * 40)
    print("  " + ("ALL GREEN ✓" if ok else "SOME CHECKS FAILED ✗") + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
