#!/usr/bin/env python3
"""lost_edits.py — which cell edits were reverted by the save race, when, and whose.

Every edit in the review screen wrote a `corrected` verification_event with a diff
{field_path, original_value, final_value}, even when the next save overwrote the value again
(the race fixed by the `patch` save). So the history tells, per record and cell:

  * REVERTED, THEN REDONE — a later correction of the same cell starts from a value that is
    not what the previous correction left: the cell had gone back when the coder edited it
    again (they noticed, and redid it). The revert happened between the two timestamps.
  * STILL LOST — the LAST correction of a cell left a value the record does not hold now:
    the edit was overwritten and nobody redid it. `--csv` writes these cells out, with what
    was typed and what is stored, so they can be put back.
  * UNCLEAR — a row was added or removed in the same list after the edit, so the cell's
    index may have shifted; counted apart, not as lost.

Read-only. Connects like the app (PAPERLENS_DATABASE_URL). Examples:

    python scripts/lost_edits.py                       # every dataset, whole history
    python scripts/lost_edits.py --dataset "self-other" --since 2026-09-01 --csv lost.csv
    python scripts/lost_edits.py --examples 20         # show more of the cells
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import sys
from collections import Counter, defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens import records  # noqa: E402

MISSING = object()
_PATH_TOKEN = re.compile(r"[^.\[\]]+|\[(\d+)\]")


def path_tokens(path):
    """'loadings._table[2].loading' → ['loadings', '_table', 2, 'loading'] (the review screen's
    grammar; kept here so the report runs on a deployment older than contract.path_tokens)."""
    return [int(m.group(1)) if m.group(1) is not None else m.group(0) for m in _PATH_TOKEN.finditer(str(path or ""))]
STRUCTURAL = {"(row added)", "(row removed)"}


def norm(v):
    """Values as a coder sees them: 0.5 and "0.5" are the same, "" and null are the same."""
    if v is None or v is MISSING:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (list, tuple)):
        return tuple(norm(x) for x in v)
    if isinstance(v, dict):
        return tuple(sorted((k, norm(x)) for k, x in v.items()))
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        try:
            return float(s)
        except ValueError:
            return s
    return v


def stored(fv, path):
    cur = fv
    for tok in path_tokens(path):
        if isinstance(tok, int):
            if not isinstance(cur, list) or tok >= len(cur):
                return MISSING
            cur = cur[tok]
        else:
            if not isinstance(cur, dict) or tok not in cur:
                return MISSING
            cur = cur[tok]
    return cur


def list_prefix(path):
    """'records[14].es' → 'records' — the list a row edit sits in (None for a top-level cell)."""
    toks = path_tokens(path)
    for i, t in enumerate(toks):
        if isinstance(t, int):
            return ".".join(str(x) for x in toks[:i])
    return None


def fmt(v):
    if v is None or v is MISSING:
        return "—"
    s = str(v)
    return s if len(s) <= 28 else s[:25] + "…"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", help="only datasets whose title, slug or id contains this")
    ap.add_argument("--since", help="only corrections made on or after this date (YYYY-MM-DD)")
    ap.add_argument("--csv", help="write the still-lost cells here")
    ap.add_argument("--examples", type=int, default=8, help="how many cells to show per group")
    a = ap.parse_args()

    conn = records.connect()
    where, params = ["e.status = 'corrected'", "e.diff IS NOT NULL"], []
    if a.since:
        where.append("e.created_at >= %s"); params.append(dt.date.fromisoformat(a.since))
    if a.dataset:
        where.append("(d.title ILIKE %s OR d.slug ILIKE %s OR d.id::text = %s)")
        params += [f"%{a.dataset}%", f"%{a.dataset}%", a.dataset]
    rows = conn.execute(
        f"""SELECT e.record_id::text, e.created_at, e.diff, coalesce(u.email, e.verifier_kind, 'unknown'),
                   coalesce(d.title, '(no dataset)'), r.entry_index, r.field_values,
                   coalesce(p.title, x.filename, r.document_id::text)
            FROM verification_event e
            JOIN record r ON r.id = e.record_id
            LEFT JOIN dataset d ON d.id = r.dataset_id
            LEFT JOIN extraction_document x ON x.id = r.document_id
            LEFT JOIN paper p ON p.id = r.paper_id
            LEFT JOIN users u ON u.id = e.verifier_user_id
            WHERE {' AND '.join(where)}
            ORDER BY e.created_at""", params).fetchall()

    # events per (record, cell), in time; structural events per (record, list)
    by_cell: dict[tuple, list] = defaultdict(list)
    shifted: dict[tuple, list] = defaultdict(list)
    meta: dict[str, tuple] = {}
    per_editor: dict[tuple, Counter] = defaultdict(Counter)
    n_events = 0
    for rid, at, diff, who, ds, entry, fv, paper in rows:
        meta[rid] = (ds, paper, entry, fv)
        for d in (diff if isinstance(diff, list) else []):
            if not isinstance(d, dict) or not d.get("field_path"):
                continue
            n_events += 1
            path = d["field_path"]
            per_editor[(ds, who)]["corrections"] += 1
            if isinstance(d.get("final_value"), str) and d["final_value"] in STRUCTURAL:   # a multi-select holds a list
                shifted[(rid, path.rsplit("[", 1)[0])].append(at)
                continue
            by_cell[(rid, path)].append((at, who, d.get("original_value"), d.get("final_value")))

    redone, lost, unclear = [], [], []
    for (rid, path), evs in by_cell.items():
        ds, paper, entry, fv = meta[rid]
        lp = list_prefix(path)
        shifts = shifted.get((rid, lp), []) if lp else []
        for (at, who, orig, final), (at2, who2, orig2, final2) in zip(evs, evs[1:]):
            if norm(orig2) != norm(final) and norm(final) != norm(orig):
                if any(at < s < at2 for s in shifts):
                    unclear.append((ds, paper, entry, path, who2, at, at2)); per_editor[(ds, who2)]["unclear"] += 1
                else:
                    redone.append((ds, paper, entry, path, who, at, final, who2, at2, orig2)); per_editor[(ds, who2)]["redone"] += 1
        at, who, orig, final = evs[-1]
        now = stored(fv, path)
        if norm(now) != norm(final):
            if any(s > at for s in shifts):
                unclear.append((ds, paper, entry, path, who, at, None)); per_editor[(ds, who)]["unclear"] += 1
            else:
                lost.append((ds, paper, entry, path, who, at, orig, final, now, rid)); per_editor[(ds, who)]["lost"] += 1

    print(f"{n_events} corrections on {len(meta)} records ({len(by_cell)} cells)"
          + (f" since {a.since}" if a.since else "") + (f", datasets matching {a.dataset!r}" if a.dataset else ""))
    print(f"  reverted, then redone by the coder : {len(redone)}")
    print(f"  still lost (typed ≠ stored now)     : {len(lost)}")
    print(f"  unclear (rows moved after the edit) : {len(unclear)}")
    if not n_events:
        return 0
    print("\nper dataset and editor (corrections · redone after a revert · still lost · unclear):")
    for (ds, who), c in sorted(per_editor.items()):
        print(f"  {ds[:40]:40} {who[:32]:32} {c['corrections']:5} · {c['redone']:4} · {c['lost']:4} · {c['unclear']:4}")
    if redone:
        days = Counter(at2.date() for *_, at2, _o in redone)
        print("\nwhen the reverts were noticed and redone (edits per day):")
        for day, n in sorted(days.items()):
            print(f"  {day}: {n}")
        print(f"\nexamples — reverted, then redone (first {a.examples}):")
        for ds, paper, entry, path, who, at, final, who2, at2, orig2 in redone[:a.examples]:
            print(f"  {paper[:38]:38} #{entry:<3} {path:28} typed {fmt(final):>10} at {at:%m-%d %H:%M}; found {fmt(orig2):>10} again, redone {at2:%m-%d %H:%M} ({who2})")
    if lost:
        print(f"\nexamples — still lost (first {a.examples}):")
        for ds, paper, entry, path, who, at, orig, final, now, _rid in lost[:a.examples]:
            print(f"  {paper[:38]:38} #{entry:<3} {path:28} typed {fmt(final):>10} at {at:%m-%d %H:%M} ({who}); stored now {fmt(now)}")
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["dataset", "paper", "entry_index", "record_id", "cell", "editor", "typed_at", "original_value", "typed_value", "stored_now"])
            for ds, paper, entry, path, who, at, orig, final, now, rid in lost:
                w.writerow([ds, paper, entry, rid, path, who, at.isoformat(timespec="minutes"), "" if orig is None else orig, "" if final is None else final, "" if now in (None, MISSING) else now])
        print(f"\nstill-lost cells written to {a.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
