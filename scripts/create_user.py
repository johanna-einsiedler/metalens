"""Create (or reset) a Metalens user account. Idempotent — safe to re-run.

There is no username in the schema; the identity column is `email`, and no email
format is enforced, so a bare handle like "martin" is a valid account id.

    uv run python scripts/create_user.py                       # martin / testtest
    uv run python scripts/create_user.py --email a@b.co --password s3cret
    uv run python scripts/create_user.py --credits 25          # + grant credits (needs WS5)

Reads PAPERLENS_DATABASE_URL from the environment (default dbname=paperlens), so
in prod:  fly ssh console -C "python scripts/create_user.py --credits 25"
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paperlens import auth, records  # noqa: E402


def upsert_user(conn, email: str, password: str) -> str:
    """Create the user, or reset the password if the email already exists. Returns the id."""
    email = (email or "").strip().lower()
    try:
        user = auth.create_user(conn, email, password)
        print(f"created user {email} ({user['id']})")
        return user["id"]
    except ValueError:
        # already registered → reset the password (idempotent re-run)
        row = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
        if not row:
            raise
        uid = str(row[0])
        with conn.transaction():
            conn.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s::uuid",
                (auth.hash_password(password), uid),
            )
        print(f"reset password for existing user {email} ({uid})")
        return uid


def grant_credits(conn, uid: str, n: int) -> None:
    if n <= 0:
        return
    try:
        from paperlens import credits  # noqa: WPS433 (optional until WS5 lands)
    except ImportError:
        print(f"! skipped --credits {n}: the credits module isn't available yet")
        return
    credits.grant(conn, uid, n, reason="grant")
    print(f"granted {n} credits; balance = {credits.balance(conn, uid)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Create or reset a Metalens user.")
    ap.add_argument("--email", default="martin", help="account id (email column); default 'martin'")
    ap.add_argument("--password", default="testtest", help="password; default 'testtest'")
    ap.add_argument("--credits", type=int, default=0, help="grant N extraction credits (needs WS5)")
    args = ap.parse_args()

    conn = records.connect()
    try:
        records.init_db(conn)  # ensure the schema exists before touching users
        uid = upsert_user(conn, args.email, args.password)
        grant_credits(conn, uid, args.credits)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
