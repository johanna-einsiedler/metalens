"""Backfill ``personal_preset.spec`` for rows created before the declarative format.

Each old row keeps its stored prompt + grammar authoritative (nothing about how its
documents render changes); this only materialises the best-effort spec the app would
otherwise recompute on every read, so the row is a first-class format-2 preset the editor
can open. Dry-run by default; ``--write`` persists. Rows are never deleted.

    uv run python scripts/migrate_presets_v2.py            # report
    uv run python scripts/migrate_presets_v2.py --write    # persist
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from paperlens import preset_spec, presets_legacy, records  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="persist the upgraded specs")
    args = ap.parse_args()
    conn = records.connect()
    rows = conn.execute(f"SELECT {records._PRESET_COLS} FROM personal_preset WHERE spec IS NULL").fetchall()
    n_ok = n_bad = 0
    for r in rows:
        meta = records._preset_meta_from_row(r)
        try:
            spec = presets_legacy.upgrade_preset(meta)
        except preset_spec.SpecError as exc:
            n_bad += 1
            print(f"  SKIP  {meta['id']:36} {exc}")
            continue
        n_ok += 1
        fields = [f["name"] for f in spec["entries"]["fields"]]
        print(f"  {'WRITE' if args.write else 'would'} {meta['id']:36} entries={spec['entries']['key']} "
              f"fields={len(fields)} tabs={len(spec['display'].get('tabs') or [])} "
              f"→ {preset_spec.schema_id(spec)}")
        if args.write:
            records.update_personal_preset(conn, meta["id"], spec=spec)
    conn.close()
    print(f"\n{len(rows)} rows without a spec: {n_ok} upgradable, {n_bad} skipped"
          + ("" if args.write else " (dry run — pass --write to persist)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
