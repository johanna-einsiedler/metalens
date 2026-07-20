"""Seed library presets into the DB as a user's personal presets.

The human-AI presets live as source files under paperlens/presets/_library/ (NOT in the
loader's glob, so they're not global). This creates/updates them as personal presets
owned by a user (default: martin), keeping their ids so any existing extraction that
referenced them stays valid. Idempotent — safe to re-run.

    uv run python scripts/seed_presets.py                       # martin, all library presets
    uv run python scripts/seed_presets.py --email martin --ids human-ai-collab hai-screening
    fly ssh console -C "python scripts/seed_presets.py"         # on the deployed DB
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from paperlens import records  # noqa: E402

_LIB = os.path.join(_ROOT, "paperlens", "presets", "_library")


def _load_meta(json_path: str) -> dict:
    with open(json_path) as fh:
        meta = json.load(fh)
    prompt = meta.get("prompt")
    if not prompt and meta.get("prompt_file"):          # resolve prompt_file sibling verbatim
        with open(os.path.join(os.path.dirname(json_path), meta["prompt_file"])) as fh:
            prompt = fh.read()
    meta["_prompt"] = prompt or ""
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="martin", help="owner account (email/username); default martin")
    ap.add_argument("--visibility", default="private", choices=["private", "public"])
    ap.add_argument("--ids", nargs="*", help="only seed these preset ids (default: all in _library)")
    args = ap.parse_args()

    conn = records.connect()
    records.init_db(conn)
    row = conn.execute("SELECT id FROM users WHERE email = %s", (args.email.strip().lower(),)).fetchone()
    if not row:
        print(f"! no user with email {args.email!r} — create it first (scripts/create_user.py)")
        conn.close()
        return
    uid = str(row[0])

    for fn in sorted(os.listdir(_LIB)):
        if not fn.endswith(".json"):
            continue
        meta = _load_meta(os.path.join(_LIB, fn))
        pid = meta["id"]
        if args.ids and pid not in args.ids:
            continue
        # field_types (per-field dropdown/enum metadata) rides inside template_params so it
        # survives into the DB-backed personal preset without a new column.
        tparams = dict(meta.get("template_params") or {})
        if meta.get("field_types"):
            tparams["field_types"] = meta["field_types"]
        fields = {
            "title": meta["title"], "prompt": meta["_prompt"], "tagline": meta.get("tagline"),
            "description": meta.get("description"), "mode": meta.get("mode", "extraction"),
            "sub_views": meta.get("sub_views"), "template_params": tparams or None,
            "accent_color": meta.get("accent_color"), "visibility": args.visibility,
        }
        if records.get_personal_preset(conn, pid) is not None:
            records.update_personal_preset(conn, pid, **{k: v for k, v in fields.items() if v is not None})
            with conn.transaction():
                conn.execute("UPDATE personal_preset SET owner_user_id = %s::uuid WHERE id = %s", (uid, pid))
            print(f"updated personal preset {pid} (owner {args.email})")
        else:
            records.create_personal_preset(conn, preset_id=pid, owner_user_id=uid, **fields)
            print(f"created personal preset {pid} (owner {args.email})")
    conn.close()


if __name__ == "__main__":
    main()
