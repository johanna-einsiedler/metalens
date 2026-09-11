"""Read-only audit: does every schema row still resolve under the declarative format?

For each row prints how it resolves (format-2 / legacy-upgraded / none), whether the
read-time adapter produces a spec, and how many documents / records / datasets hang off
it. Exit code 1 if any row cannot be adapted — that row's documents would render without a
grammar. Never writes.

    uv run python scripts/check_schema_rows.py
    fly ssh console -C "python scripts/check_schema_rows.py"      # on the deployed DB
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from paperlens import presets, records  # noqa: E402


def main() -> int:
    conn = records.connect()
    rows = conn.execute(
        """SELECT s.id, s.source, s.preset_id, s.field_defs,
                  (SELECT count(*) FROM extraction_document d WHERE d.schema_id = s.id),
                  (SELECT count(*) FROM record r WHERE r.schema_id = s.id),
                  (SELECT count(*) FROM dataset ds WHERE ds.schema_id = s.id)
           FROM schema s ORDER BY s.id""").fetchall()
    bad = 0
    print(f"{'schema_id':34} {'kind':18} {'preset':20} {'docs':>5} {'recs':>5} {'sets':>4}  adapter")
    for sid, source, pid, fd, n_docs, n_recs, n_sets in rows:
        fmt = (fd or {}).get("format") if isinstance(fd, dict) else None
        if fmt == 2 and not (fd or {}).get("legacy"):
            kind = "format-2"
        elif fd:
            kind = "legacy grammar"
        else:
            kind = f"no grammar ({source})"
        meta = presets.get(pid, conn) if pid else None
        resolves = "file" if meta and meta.get("source") == "file" else (
            "personal" if meta else ("alias" if pid and presets.resolve_id(pid) != pid else "-"))
        try:
            spec = records.schema_spec(conn, sid)
            ok = "ok" if spec else "NO SPEC"
        except Exception as exc:                    # noqa: BLE001
            ok = f"FAIL {type(exc).__name__}: {exc}"
        if not ok.startswith("ok"):
            bad += 1
        print(f"{sid:34} {kind:18} {resolves:20} {n_docs:5d} {n_recs:5d} {n_sets:4d}  {ok}")
    conn.close()
    print(f"\n{len(rows)} rows, {bad} without a usable spec")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
