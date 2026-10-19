#!/usr/bin/env python3
"""package_as.py — rewrite pyproject.toml for the second distribution built from this repository.

The engine repository is one Python package with two commands. It is published twice:

  * ``maseminer`` — the MASEMiner package: ``scripts/masem_only.py`` trims the checkout to the
    MASEM presets and drops the ``metalens`` command; pyproject already names it ``maseminer``;
  * ``metalens`` — the full engine with every bundled preset: this script renames the project to
    ``metalens``, gives it its own description and keywords, and keeps only the ``metalens``
    command (``paperlens.local:main_metalens``: brand Metalens, data in ~/.metalens, port 8775).

    python3 scripts/package_as.py metalens        # in a THROWAWAY checkout, before `uv build`

The release workflow runs it; never commit its output.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISTS = {
    "metalens": {
        "description": "Metalens — AI-assisted data extraction from research papers with human review, every value "
                       "next to the passage it was read from; runs on one machine (`metalens`) or hosted",
        "keywords": ["data extraction", "systematic review", "meta-analysis", "evidence", "LLM", "research synthesis"],
        "scripts": {"metalens": "paperlens.local:main_metalens"},
    },
}


def rewrite(text: str, name: str) -> str:
    d = DISTS[name]
    out = re.sub(r'(?m)^name = "[^"]*"$', f'name = "{name}"', text, count=1)
    out = re.sub(r'(?m)^description = "[^"]*"$', 'description = "' + d["description"].replace('"', '\\"') + '"', out, count=1)
    out = re.sub(r'(?m)^keywords = \[[^\]]*\]$', "keywords = [" + ", ".join(f'"{k}"' for k in d["keywords"]) + "]", out, count=1)
    scripts = "[project.scripts]\n" + "".join(f'{k} = "{v}"\n' for k, v in d["scripts"].items())
    out = re.sub(r"(?ms)^\[project\.scripts\]\n.*?(?=^\[|\Z)", scripts + "\n", out, count=1)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in DISTS:
        print(f"usage: package_as.py {{{'|'.join(DISTS)}}}", file=sys.stderr)
        return 2
    py = ROOT / "pyproject.toml"
    new = rewrite(py.read_text(), argv[0])
    try:                                                  # the result must still be valid TOML
        import tomllib
        project = tomllib.loads(new)["project"]
        assert project["name"] == argv[0] and project["scripts"] == DISTS[argv[0]]["scripts"], project
    except ImportError:                                   # Python < 3.11: the build will catch it
        pass
    py.write_text(new)
    print(f"pyproject.toml → {argv[0]} (commands: {', '.join(DISTS[argv[0]]['scripts'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
