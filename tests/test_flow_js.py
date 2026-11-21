"""The workflow flow diagram (static/flow.js), run through node on the module itself (skipped when
node is absent): the edges a step list declares, the layering by depth, the rows of boxes a width
allows, the right-angled arrows, and the SVG's boxes."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

FLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paperlens", "static", "flow.js")

RUNNER = """
import { flowEdges, flowLayout, flowSvg } from %s;
const [stepsJson, optsJson] = process.argv.slice(-2);
const steps = JSON.parse(stepsJson), opts = JSON.parse(optsJson);
process.stdout.write(JSON.stringify({ edges: flowEdges(steps), layout: flowLayout(steps, opts), svg: flowSvg(steps, { current: "estimands", ...opts }) }));
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


EDGE_D = re.compile(r'<path class="flow-edge[^"]*" d="([^"]+)"')
SEGMENTS = re.compile(r"^M[\d.]+,[\d.]+( [HV][\d.]+)+$")      # a move, then horizontal and vertical segments only


def _run(steps, opts=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, "--input-type=module", "-e", RUNNER % json.dumps("file://" + FLOW), "--", json.dumps(steps), json.dumps(opts or {})], capture_output=True, text=True, timeout=60)
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
    assert "Study, task and arm…" in svg and "×3" in svg and "· 2 coders" in svg


def test_an_empty_or_dependency_free_list_still_renders() -> None:
    assert _run([])["svg"] == ""
    out = _run([{"id": "a", "kind": "extract", "label": "A"}, {"id": "b", "kind": "check", "label": "B"}])
    assert out["layout"]["cols"] == [["a", "b"]] and out["edges"] == [] and out["svg"].count("<rect") == 2
    out = _run([{"id": "x", "kind": "pass", "label": "X", "reads": ["nowhere"]}])     # a read of a step not in the list: no arrow, no crash
    assert out["edges"] == [] and out["svg"].count("<rect") == 1


def test_every_arrow_is_straight_segments_joined_at_right_angles() -> None:
    out = _run(CHAIN)
    ds = EDGE_D.findall(out["svg"])
    assert len(ds) == len(out["edges"]) and all(SEGMENTS.match(d) for d in ds), ds
    assert all(d.count(" ") in (1, 3) for d in ds)                   # straight across, or out – turn – in; nothing wraps in one row


def test_columns_that_do_not_fit_the_width_go_on_in_a_second_row_of_boxes() -> None:
    one = _run(CHAIN)["layout"]
    assert one["bands"] == [one["cols"]] and one["wraps"] == []
    out = _run(CHAIN, {"width": 760})          # three columns of boxes fit, with the margins the wrapping arrows need
    lay = out["layout"]
    assert lay["cols"] == one["cols"] and [len(b) for b in lay["bands"]] == [3, 3]
    assert lay["w"] <= 760 < one["w"]
    for p in lay["pos"].values():
        assert 0 < p["x"] and p["x"] + 156 < lay["w"] and p["y"] + 40 < lay["h"]
    wraps = {(e["from"], e["to"]) for e in lay["wraps"]}
    assert ("estimands", "extraction") in wraps and ("exhibits", "estimands") not in wraps and ("extraction", "release") not in wraps
    assert len(wraps) == 9 and set(lay["lane"]) == {"exhibits", "estimands", "estimates", "design"}
    assert lay["lane"]["design"] == 0 and lay["lane"]["estimates"] == 3     # the lowest source takes the innermost trunk, the highest the outermost
    ds = EDGE_D.findall(out["svg"])
    assert len(ds) == len(out["edges"]) and all(SEGMENTS.match(d) for d in ds), ds
    wrapped = [d for d in ds if d.count(" ") == 5]                   # out, down, back, down, in
    assert len(wrapped) == len(wraps)
    top1 = min(p["y"] for p in lay["pos"].values() if p["band"] == 1)
    bottom0 = max(p["y"] + 40 for p in lay["pos"].values() if p["band"] == 0)
    xs = {float(d.split(" ")[1][1:]) for d in wrapped}                # the trunks: one per source, in the right margin
    ys = {float(d.split(" ")[2][1:]) for d in wrapped}                # the runs back: one lane per source, in the gap between the rows
    assert len(xs) == len(ys) == 4 and all(bottom0 < y < top1 for y in ys) and all(x > lay["rightEdge"] for x in xs)
    assert out["svg"].count("<rect") == len(CHAIN)
    assert _run(CHAIN, {"width": 100})["layout"]["bands"] == [[c] for c in one["cols"]]     # too narrow for two: one column per row, still drawn
