"""Purge accumulated TEST rows from a Metalens database (default: dry-run).

Before test isolation existed (see tests/conftest.py), the suite wrote directly
to the dev DB with no teardown, so every `pytest` run left datasets / documents /
users behind — some PUBLIC, leaking into the Data Catalogue & observatory. This
script removes ONLY rows that match known test signatures; real user data (any
non-test account, real extractions, and the seed_demo demo) is preserved.

Signatures are conservative on purpose:
  • sessions  authz-*/proj-*/cat-sess/cred-sess/ds-test-session/view-sess/sess-1 + anon-*
  • job ids   the fixed source_job_id literals used across tests/*.py
  • users     any @example.org or @test.dev account (never @gmx.at etc.)
It never touches session_id=NULL docs, demo-* (seed_demo), or hex Arq job ids —
those are real user extractions.

Usage:
    uv run python scripts/purge_test_rows.py            # dry-run: report only
    uv run python scripts/purge_test_rows.py --apply    # actually delete
    PAPERLENS_DATABASE_URL=dbname=paperlens uv run python scripts/purge_test_rows.py --apply

Documents are removed via records.delete_document so their stored PDFs + page
images are cleaned up too (not just DB rows).
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens import records  # noqa: E402

TEST_SESSIONS = [
    "authz-alice", "authz-bob", "proj-alice", "proj-bob",
    "cat-sess", "cred-sess", "ds-test-session", "view-sess", "sess-1",
]
TEST_JOBS = [
    "authz", "authz2", "authz-del", "cat-test", "cred-http", "cred-test",
    "cred-test2", "ds-test", "proj-a", "proj-b", "proj-c", "proj-nofn",
    "view-test", "view-test-2", "smoke-forestplot", "smoke-masem", "smoke-masem_rich",
]
# test accounts: the random registrations from test_auth / test_authz / save flows.
TEST_EMAIL_LIKE = ["%@example.org", "%@test.dev"]

_SESSION_PRED = "(session_id = ANY(%s) OR session_id LIKE 'anon-%%')"


def _email_pred(alias: str = "") -> tuple[str, list]:
    a = f"{alias}." if alias else ""
    clause = " OR ".join([f"{a}email LIKE %s"] * len(TEST_EMAIL_LIKE))
    return f"({clause})", list(TEST_EMAIL_LIKE)


def main(apply: bool) -> int:
    conn = records.connect()
    print(f"target DB   : {records.dsn()}")
    print(f"mode        : {'APPLY (deleting)' if apply else 'DRY-RUN (report only)'}\n")

    epred, eparams = _email_pred()
    test_user_ids = [str(r[0]) for r in conn.execute(
        f"SELECT id FROM users WHERE {epred}", eparams).fetchall()]

    # documents: by test session, anon-* session, test job id, or owned by a test user
    doc_rows = conn.execute(
        f"""SELECT id FROM extraction_document
            WHERE {_SESSION_PRED}
               OR source_job_id = ANY(%s)
               OR (owner_user_id IS NOT NULL AND owner_user_id::text = ANY(%s))""",
        (TEST_SESSIONS, TEST_JOBS, test_user_ids),
    ).fetchall()
    doc_ids = [str(r[0]) for r in doc_rows]

    ds_ids = [str(r[0]) for r in conn.execute(
        f"""SELECT id FROM dataset
            WHERE {_SESSION_PRED}
               OR (owner_user_id IS NOT NULL AND owner_user_id::text = ANY(%s))""",
        (TEST_SESSIONS, test_user_ids)).fetchall()]

    view_ids = [str(r[0]) for r in conn.execute(
        f"""SELECT id FROM saved_view
            WHERE {_SESSION_PRED}
               OR (owner_user_id IS NOT NULL AND owner_user_id::text = ANY(%s))""",
        (TEST_SESSIONS, test_user_ids)).fetchall()]

    print(f"test users    : {len(test_user_ids)}")
    print(f"documents     : {len(doc_ids)}  (cascades records/evidence + removes PDFs/pages)")
    print(f"datasets      : {len(ds_ids)}")
    print(f"saved_views   : {len(view_ids)}")

    if not apply:
        print("\nDRY-RUN — nothing deleted. Re-run with --apply to delete the above.")
        conn.close()
        return 0

    for did in doc_ids:
        records.delete_document(conn, did)        # DB cascade + storage blobs
    if ds_ids:
        conn.execute("DELETE FROM dataset WHERE id::text = ANY(%s)", (ds_ids,))
    if view_ids:
        conn.execute("DELETE FROM saved_view WHERE id::text = ANY(%s)", (view_ids,))
    if test_user_ids:
        conn.execute("DELETE FROM users WHERE id::text = ANY(%s)", (test_user_ids,))  # cascades sessions
    conn.close()
    print("\n✓ purge complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(apply="--apply" in sys.argv))
