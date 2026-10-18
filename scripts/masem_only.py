#!/usr/bin/env python3
"""masem_only.py — turn a fresh checkout into the MASEMiner distribution.

The engine repository ships every preset. MASEMiner — the PyPI package, the Docker image and
the `maseminer` mirror repository — ships only its own, so a MASEM user does not receive the
general and research presets with it. Run this in a THROWAWAY checkout (the release and mirror
workflows do), before `uv build`, the image build, or the push to the mirror. It removes:

  * presets tagged for other brands only (``meta.brands`` without "maseminer"; untagged presets
    are shared and stay), together with their prompt files;
  * tests, scripts and memos that are about a removed preset (they name it);
  * the hosted service's deployment, release and mirror workflows and ``fly.toml``;

and writes one line at the top of the README saying what this copy is. ``--dry-run`` lists what
would go. Never run it in the engine repository itself.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAND = "maseminer"
README_NOTE = ("> **The MASEMiner distribution** of the Metalens engine, kept in step with "
               "[johanna-einsiedler/metalens](https://github.com/johanna-einsiedler/metalens) by its release workflow. "
               "It ships the MASEMiner presets only; the general presets live in the engine repository, and any preset "
               "placed in `<data>/presets/` is loaded alongside the built-in ones.\n")


def presets() -> tuple[list[str], list[str]]:
    """(kept, removed) preset ids by their brand tags."""
    kept, removed = [], []
    for f in sorted((ROOT / "paperlens" / "presets").glob("*.json")):
        spec = json.loads(f.read_text())
        brands = list(((spec.get("meta") or {}).get("brands")) or [])
        (kept if not brands or BRAND in brands else removed).append(spec.get("id") or f.stem)
    return kept, removed


def mentions(text: str, pid: str) -> bool:
    """Does a file speak about this preset? Its id as a preset reference — or bare, when the id
    is distinctive enough (a hyphen) not to be an ordinary word like "summarize"."""
    forms = [f'"{pid}"', f"'{pid}'", f"{pid}@", f"{pid}.json", f"{pid}.prompt"]
    if "-" in pid:
        forms.append(pid)
    return any(x in text for x in forms)


def plan() -> list[Path]:
    kept, removed = presets()
    gone: list[Path] = []
    pdir = ROOT / "paperlens" / "presets"
    for pid in removed:
        gone += sorted(pdir.glob(f"{pid}.json")) + sorted(pdir.glob(f"{pid}.*.md")) + sorted(pdir.glob(f"{pid}.md"))
    for folder, pattern in (("tests", "*.py"), ("scripts", "*.py"), ("docs", "*.md")):
        for f in sorted((ROOT / folder).glob(pattern)):
            if f.name == "masem_only.py":
                continue
            text = f.read_text(errors="replace")
            if any(mentions(text, pid) for pid in removed):
                gone.append(f)
    for extra in (".github", "fly.toml"):
        if (ROOT / extra).exists():
            gone.append(ROOT / extra)
    return gone


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="list what would be removed, change nothing")
    a = ap.parse_args()
    kept, removed = presets()
    gone = plan()
    print(f"presets kept: {', '.join(kept)}")
    print(f"presets removed: {', '.join(removed) or '—'}")
    for p in gone:
        print(f"  {'would remove' if a.dry_run else 'removed'}  {p.relative_to(ROOT)}{'/' if p.is_dir() else ''}")
    if a.dry_run:
        return 0
    for p in gone:
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    readme = ROOT / "README.md"
    lines = readme.read_text().splitlines(keepends=True)
    if lines and README_NOTE not in "".join(lines[:5]):
        lines.insert(1, "\n" + README_NOTE)
        readme.write_text("".join(lines))
    print(f"README: distribution note added · {len(gone)} paths removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
