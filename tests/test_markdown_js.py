"""renderMarkdown (static/grammar.js) — the markdown the preset cards, dataset READMEs and prompt
previews are drawn with. No JS test runner: the cases in tests/markdown_cases.json run through node
on the module itself (skipped when node is absent); scripts/ui_check_presets.py runs the same cases
in a browser."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
CASES = os.path.join(_HERE, "markdown_cases.json")
GRAMMAR = os.path.join(_ROOT, "paperlens", "static", "grammar.js")

RUNNER = """
import { renderMarkdown } from %s;
import { readFileSync } from "node:fs";
const cases = JSON.parse(readFileSync(process.argv.find((a) => a.endsWith(".json")), "utf8"));
const out = cases.map((c) => ({ name: c.name, html: renderMarkdown(c.src) }));
process.stdout.write(JSON.stringify(out));
"""


def _render_all() -> dict[str, str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    url = json.dumps("file://" + GRAMMAR)
    r = subprocess.run([node, "--input-type=module", "-e", RUNNER % url, "--", CASES],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return {c["name"]: c["html"] for c in json.loads(r.stdout)}


def test_markdown_cases() -> None:
    html = _render_all()
    with open(CASES, encoding="utf-8") as f:
        cases = json.load(f)
    problems = []
    for c in cases:
        got = html[c["name"]]
        for s in c.get("has", []):
            if s not in got:
                problems.append(f"{c['name']}: missing {s!r} in {got!r}")
        for s in c.get("not", []):
            if s in got:
                problems.append(f"{c['name']}: unexpected {s!r} in {got!r}")
        for s, n in (c.get("count") or {}).items():
            if got.count(s) != n:
                problems.append(f"{c['name']}: {s!r} ×{got.count(s)}, wanted ×{n} in {got!r}")
    assert not problems, "\n".join(problems)
    sys.stdout.write(f"{len(cases)} markdown cases\n")
