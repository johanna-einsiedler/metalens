#!/usr/bin/env python3
"""Compare the documents a ``genai-work-chain`` dataset holds with the review project's import
files (``labeling/metalens/import_pilot/<rid>.json``, the records the project assembled locally):
per paper the paper-level fields and the tasks (arms, outcomes, result rows) field for field,
the engine's own keys (``_rid`` and the like) left out. A parity report for the cut-over.

    .venv/bin/python scripts/compare_chain.py --project /path/to/49_AI_review --dataset ID [--url http://127.0.0.1:8775] [--out report.md]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

IGNORE_PAPER = {"evidence_items", "confidence"}      # the import's exhibit items are evidence rows in the engine; confidence is stored apart
SKIP_KEYS = {"confidence"}                   # the engine stores confidence apart from the field values


def _clean(o):
    """Drop the engine's own keys (leading underscore) recursively."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if not (isinstance(k, str) and k.startswith("_"))}
    return [_clean(x) for x in o] if isinstance(o, list) else o


_RUNS_NOTE = re.compile(r"^\[runs: (.*?)\] ?")


def _norm_text(s):
    """A notes field or a flag message as the engine words it: the run note's parts sorted (jsonb
    keeps no key order), a reading named 'another reading' rather than 'run r2'."""
    if not isinstance(s, str):
        return s
    m = _RUNS_NOTE.match(s)
    if m:
        parts = sorted(p.strip() for p in m.group(1).split(";"))
        s = f"[runs: {'; '.join(parts)}] " + s[m.end():]
        s = s.rstrip()
    s = re.sub(r"\brun r?\d+ gives\b", "another reading gives", s)
    return re.sub(r"has no counterpart in (run r?\d+|\d+ of \d+ readings)", "has no counterpart in another reading", s)


def _same(a, b) -> bool:
    return json.dumps(_clean(a), sort_keys=True) == json.dumps(_clean(b), sort_keys=True)


def diff(a, b, path="", out=None) -> list[str]:
    """The paths where two JSON values differ (lists compared position by position)."""
    out = [] if out is None else out
    a, b = _clean(a), _clean(b)
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in SKIP_KEYS:
                continue
            if k not in a:
                out.append(f"{path}.{k}: missing in Metalens")
            elif k not in b:
                out.append(f"{path}.{k}: missing in the project's file")
            else:
                diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} rows in Metalens, {len(b)} in the project's file")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out)
    elif a != b and json.dumps(a) != json.dumps(b):
        if (path.endswith("_notes") or path.endswith(".message")) and _norm_text(a) == _norm_text(b):
            return out
        out.append(f"{path}: Metalens {json.dumps(a, ensure_ascii=False)[:80]} | project {json.dumps(b, ensure_ascii=False)[:80]}")
    return out


def compare_paper(theirs: dict, export_paper: dict) -> dict:
    """One paper: the differing paths of its paper fields and its tasks, with counts."""
    tm, mm = theirs.get("paper_metadata") or {}, export_paper.get("paper_metadata") or {}
    paper_diffs = []
    for k in sorted(set(tm) | set(mm)):
        if k in IGNORE_PAPER:
            continue
        diff(mm.get(k), tm.get(k), f"paper_metadata.{k}", paper_diffs)
    mine_tasks = [r.get("values") or {} for r in sorted(export_paper.get("records") or [], key=lambda r: r.get("entry_index") or 0)]
    task_diffs = diff(mine_tasks, theirs.get("tasks") or [], "tasks")
    n_fields = sum(1 for k in tm if k not in IGNORE_PAPER) + sum(len(t) for t in theirs.get("tasks") or [])
    return {"paper_fields_differing": len(paper_diffs), "task_paths_differing": len(task_diffs), "fields_compared": n_fields,
            "diffs": paper_diffs + task_diffs, "flags": (len(mm.get("flags") or []), len(tm.get("flags") or [])), "routed": (mm.get("routed"), tm.get("routed"))}


def compare(export: dict, project: Path, ids: list[str] | None = None) -> dict:
    pilot = project / "labeling" / "metalens" / "import_pilot"
    rows = []
    for p in export.get("papers") or []:
        rid = Path(p.get("filename") or "").stem
        if ids and rid not in ids:
            continue
        f = pilot / f"{rid}.json"
        if not f.exists():
            rows.append({"rid": rid, "status": "no import file in the project"}); continue
        theirs = json.loads(f.read_text(encoding="utf-8"))
        if theirs.get("paper_metadata", {}).get("exhibits") is None:
            rows.append({"rid": rid, "status": "the project's import file predates the v20 review fields"}); continue
        res = compare_paper(theirs, p)
        rows.append({"rid": rid, "status": "equal" if not res["diffs"] else f"{len(res['diffs'])} difference(s)", **res})
    return {"n_papers": len(rows), "equal": sum(1 for r in rows if r.get("status") == "equal"), "rows": rows}


def report(res: dict) -> str:
    lines = [f"# Chain parity: {res['equal']} of {res['n_papers']} papers equal to the project's import files", ""]
    for r in res["rows"]:
        lines.append(f"## {r['rid']} — {r['status']}")
        if r.get("diffs"):
            lines.append(f"flags {r['flags'][0]} vs {r['flags'][1]}; routed {r['routed'][0]} vs {r['routed'][1]}")
            lines += [f"- {d}" for d in r["diffs"][:40]]
            if len(r["diffs"]) > 40:
                lines.append(f"- … {len(r['diffs']) - 40} more")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--dataset", required=True, help="the dataset id in Metalens")
    ap.add_argument("--url", default="http://127.0.0.1:8775")
    ap.add_argument("--ids", help="a file of record ids to compare (default: every paper of the dataset)")
    ap.add_argument("--out", help="write the report here (markdown)")
    a = ap.parse_args()
    import httpx
    c = httpx.Client(base_url=a.url, timeout=600.0, headers={"X-Session-Id": "chain-seed"})
    r = c.get(f"/api/datasets/{a.dataset}/export")
    r.raise_for_status()
    ids = [x for x in Path(a.ids).read_text().split() if x] if a.ids else None
    res = compare(r.json(), Path(a.project).resolve(), ids)
    text = report(res)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8"); print(f"{res['equal']}/{res['n_papers']} equal -> {a.out}")
    else:
        print(text)
    return 0 if res["equal"] == res["n_papers"] else 1


if __name__ == "__main__":
    sys.exit(main())
