"""The workflow flow diagram (static/flow.js), run through node on the module itself (skipped when
node is absent): the edges a step list declares, the layering by depth, and the SVG's boxes."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

FLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paperlens", "static", "flow.js")

RUNNER = """
import { flowEdges, flowLayout, flowSvg } from %s;
const steps = JSON.parse(process.argv[process.argv.length - 1]);
process.stdout.write(JSON.stringify({ edges: flowEdges(steps), layout: flowLayout(steps), svg: flowSvg(steps, { current: "estimands" }) }));
"""

CHAIN = [
    {"id": "exhibits", "kind": "pass", "label": "Exhibits as printed", "k": 2, "state": "fresh"},
    {"id": "estimands", "kind": "pass", "label": "Estimands", "reads": ["exhibits"], "k": 2, "state": "stale"},
    {"id": "estimates", "kind": "pass", "label": "Estimates", "reads": ["exhibits", "estimands"], "k": 2},
    {"id": "design", "kind": "pass", "label": "Study, task and arm variables", "reads": ["estimands"], "k": 3},
    {"id": "extraction", "kind": "compute", "label": "Extraction record", "reads": ["exhibits", "estimands", "estimates", "design"], "preset": "p@1"},
    {"id": "record", "kind": "check", "label": "Record checks", "over": ["extraction"]},
    {"id": "passes_check", "kind": "check", "label": "Chain checks", "over": ["exhibits", "estimands", "estimates", "design"]},
    {"id": "route", "kind": "compute", "label": "Routing", "reads": ["estimands"], "edits": "extraction"},
    {"id": "review_judgment", "kind": "review", "label": "Judgment", "over": ["extraction"], "review": {"coders": 2}, "gate": {"total": 2}},
    {"id": "release", "kind": "release", "label": "Release", "gate_steps": ["extraction", "review_judgment"]},
]


def _run(steps):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, "--input-type=module", "-e", RUNNER % json.dumps("file://" + FLOW), "--", json.dumps(steps)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_diagram_draws_every_dependency_a_step_declares_and_layers_by_depth() -> None:
    out = _run(CHAIN)
    edges = {(e["from"], e["to"], e["kind"]) for e in out["edges"]}
    assert ("exhibits", "estimands", "reads") in edges and ("extraction", "route", "edits") in edges and ("extraction", "record", "over") in edges
    assert ("review_judgment", "release", "gate") in edges and ("extraction", "release", "gate") in edges
    assert not any(e["to"] == "review_judgment" and e["kind"] == "gate" for e in out["edges"])   # a review's own gate dict is not a dependency list
    cols = out["layout"]["cols"]
    assert cols[0] == ["exhibits"] and "estimands" in cols[1] and "extraction" in cols[3] and "release" in cols[-1]
    depth = {sid: i for i, col in enumerate(cols) for sid in col}
    for e in out["edges"]:
        assert depth[e["from"]] < depth[e["to"]], e
    svg = out["svg"]
    assert svg.startswith("<svg") and svg.count("<rect") == len(CHAIN) and svg.count("<path class=\"flow-edge") == len(out["edges"])
    assert 'class="flow-box flow-kind-pass flow-current"' in svg and "flow-state-stale" in svg and "flow-state-fresh" in svg
    assert "Study, task and arm v…" in svg and "×3" in svg and "· 2 coders" in svg


def test_an_empty_or_dependency_free_list_still_renders() -> None:
    assert _run([])["svg"] == ""
    out = _run([{"id": "a", "kind": "extract", "label": "A"}, {"id": "b", "kind": "check", "label": "B"}])
    assert out["layout"]["cols"] == [["a", "b"]] and out["edges"] == [] and out["svg"].count("<rect") == 2
    out = _run([{"id": "x", "kind": "pass", "label": "X", "reads": ["nowhere"]}])     # a read of a step not in the list: no arrow, no crash
    assert out["edges"] == [] and out["svg"].count("<rect") == 1
