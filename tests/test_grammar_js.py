"""The record-rendering grammar (static/grammar.js), run through node on the module itself
(skipped when node is absent): a sub-entry list carries ＋ / ✕ when editable, and a multi control
keeps a value outside its options as a chip and takes a term of the paper's own."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
GRAMMAR = os.path.join(_ROOT, "paperlens", "static", "grammar.js")

ARMS = {"key": "arms", "label": "Arm", "layout": "cards", "title": "{arm_id}",
        "fields": [{"name": "arm_id", "type": "string"}, {"name": "n_arm", "type": "integer"},
                   {"name": "tags", "type": "multi", "options": ["a", "b"], "allow_other": True}]}
RESULTS = {"key": "results", "label": "Result", "layout": "table",
           "fields": [{"name": "measure", "type": "string"}, {"name": "mean", "type": "number"}]}
TAGS = {"name": "tags", "type": "multi", "options": [{"value": "a", "label": "A"}, "b"], "allow_other": True}
TAGS_CLOSED = {"name": "tags", "type": "multi", "options": ["a", "b"]}

RUNNER = """
import { renderChild, renderFields, blankRow } from %s;
const cases = JSON.parse(process.argv[process.argv.length - 1]);
const out = {};
for (const [name, c] of Object.entries(cases)) {
  if (c.child) out[name] = renderChild(c.child, c.rows, c.opts || {});
  else if (c.fields) out[name] = renderFields(c.fields, c.obj, "", c.opts || {});
  else out[name] = JSON.stringify(blankRow(c.blank));
}
process.stdout.write(JSON.stringify(out));
"""


def _render(cases: dict) -> dict[str, str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, "--input-type=module", "-e", RUNNER % json.dumps("file://" + GRAMMAR), "--", json.dumps(cases)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_sub_entries_carry_add_and_remove_buttons_when_editable() -> None:
    rows = [{"arm_id": "C", "n_arm": 10, "tags": ["a"]}, {"arm_id": "T1", "n_arm": 12, "tags": []}]
    out = _render({
        "cards": {"child": ARMS, "rows": rows, "opts": {"editable": True}},
        "table": {"child": RESULTS, "rows": [{"measure": "quality", "mean": 4.1}], "opts": {"editable": True}},
        "empty": {"child": ARMS, "rows": [], "opts": {"editable": True}},
        "missing": {"child": ARMS, "rows": None, "opts": {"editable": True}},
        "readonly": {"child": ARMS, "rows": rows, "opts": {"editable": False}},
        "only": {"child": ARMS, "rows": rows, "opts": {"editable": True, "only": None}},
        "blank": {"blank": ARMS["fields"]},
    })
    cards = out["cards"]
    assert cards.count('class="rv-addrow" data-path="arms"') == 1 and "＋ Arm" in cards
    assert cards.count('class="rv-rowdel" data-path="arms" data-row=') == 2
    assert 'data-blank="{&quot;arm_id&quot;:null,&quot;n_arm&quot;:null,&quot;tags&quot;:[]}"' in cards
    assert 'data-path="arms[1].n_arm"' in cards                                  # paths keep the real index
    table = out["table"]
    assert 'class="rv-rowdel-h"' in table and table.count('class="rv-rowdel"') == 1 and "＋ Result" in table
    assert "rv-addrow" in out["empty"] and "rv-addrow" in out["missing"]      # the first row can always be created
    assert "rv-addrow" not in out["readonly"] and "rv-rowdel" not in out["readonly"]
    assert json.loads(out["blank"]) == {"arm_id": None, "n_arm": None, "tags": []}


def test_only_and_show_draw_a_subset_without_the_buttons() -> None:
    rows = [{"arm_id": "C", "n_arm": 10, "tags": []}, {"arm_id": "T1", "n_arm": 12, "tags": []}]
    # `only` / `show` travel as JSON: the runner turns lists into Sets
    runner_cases = {"sub": {"child": ARMS, "rows": rows, "opts": {"editable": True}}}
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = """
import { renderChild } from %s;
const c = %s;
process.stdout.write(renderChild(c.child, c.rows, { editable: true, only: new Set([1]), show: new Set(["n_arm"]) }));
""" % (json.dumps("file://" + GRAMMAR), json.dumps(runner_cases["sub"]))
    r = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    html = r.stdout
    assert 'data-path="arms[1].n_arm"' in html and 'data-path="arms[0].n_arm"' not in html
    assert 'data-path="arms[1].arm_id"' not in html                              # only the shown field
    assert "rv-addrow" not in html and "rv-rowdel" not in html


def test_multi_control_keeps_off_list_terms_and_offers_an_input() -> None:
    out = _render({
        "open": {"fields": [TAGS], "obj": {"tags": ["a", "zzz"]}, "opts": {"editable": True}},
        "closed": {"fields": [TAGS_CLOSED], "obj": {"tags": ["a", "zzz"]}, "opts": {"editable": True}},
        "readonly": {"fields": [TAGS], "obj": {"tags": ["a", "zzz"]}, "opts": {"editable": False}},
    })
    html = out["open"]
    assert 'class="rv-multi rv-multi-open" data-path="tags"' in html
    assert 'value="a" checked' in html and 'value="b"/>' in html and ">A<" in html
    assert 'class="rv-chk rv-free"' in html and 'value="zzz" checked data-free="1"' in html
    assert 'class="rv-other rv-multi-other"' in html
    closed = out["closed"]
    assert 'data-free="1"' in closed and "rv-multi-other" not in closed         # never dropped, but no input
    assert "rv-multi-other" not in out["readonly"] and "rv-chip" in out["readonly"]
