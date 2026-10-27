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
HELPED_MULTI = {"name": "src", "type": "multi", "label": "Source", "help": "all that apply",
                "options": [{"value": "exp", "label": "experimental", "help": "data from an experiment"}, {"value": "survey", "label": "survey"}]}
HELPED_ENUM = {"name": "design", "type": "enum", "label": "Design", "help": "the main comparison",
               "options": [{"value": "rct", "label": "RCT", "help": "random assignment"}, {"value": "obs", "label": "observational", "help": "no assignment"}]}

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


def test_option_help_becomes_hover_titles() -> None:
    out = _render({
        "multi": {"fields": [HELPED_MULTI], "obj": {"src": ["exp"]}, "opts": {"editable": True}},
        "enum_rct": {"fields": [HELPED_ENUM], "obj": {"design": "rct"}, "opts": {"editable": True}},
        "enum_none": {"fields": [HELPED_ENUM], "obj": {"design": None}, "opts": {"editable": True}},
        "plain": {"fields": [TAGS_CLOSED], "obj": {"tags": []}, "opts": {"editable": True}},
    })
    m = out["multi"]
    assert '<label class="rv-chk" title="data from an experiment" data-tip="data from an experiment">' in m and '<label class="rv-chk"><input type="checkbox" value="survey"' in m
    assert 'class="rv-key rv-key-help" data-path="src" title="all that apply" data-tip="all that apply">Source<span class="rv-help" title="all that apply" data-tip="all that apply" tabindex="0">ⓘ</span>' in m
    e = out["enum_rct"]
    assert '<option value="rct" selected title="random assignment">RCT</option>' in e and '<option value="obs" title="no assignment">' in e
    assert '<select class="rv-select" data-help="the main comparison" title="random assignment" data-tip="random assignment">' in e      # the chosen option's definition
    assert '<select class="rv-select" data-help="the main comparison" title="the main comparison" data-tip="the main comparison">' in out["enum_none"]   # nothing chosen: the field's help
    assert "rv-help" not in out["plain"] and 'title=' not in out["plain"]


def test_help_becomes_immediate_tips() -> None:
    """Every carrier of help carries data-tip for the custom tooltip: the field label and its ⓘ
    (the field's help), each multi checkbox label (its option's definition), the select (the chosen
    option's definition) and a ⓘ after the select listing every option as "label — definition"
    under the field's help."""
    out = _render({
        "multi": {"fields": [HELPED_MULTI], "obj": {"src": ["exp"]}, "opts": {"editable": True}},
        "enum_rct": {"fields": [HELPED_ENUM], "obj": {"design": "rct"}, "opts": {"editable": True}},
        "plain": {"fields": [TAGS_CLOSED], "obj": {"tags": []}, "opts": {"editable": True}},
    })
    m = out["multi"]
    assert 'class="rv-key rv-key-help" data-path="src" title="all that apply" data-tip="all that apply">Source<span class="rv-help" title="all that apply" data-tip="all that apply" tabindex="0">ⓘ</span>' in m
    assert '<label class="rv-chk" title="data from an experiment" data-tip="data from an experiment">' in m
    e = out["enum_rct"]
    assert '<select class="rv-select" data-help="the main comparison" title="random assignment" data-tip="random assignment">' in e
    assert '<span class="rv-help rv-opt-help" data-tip="the main comparison\nRCT — random assignment\nobservational — no assignment" title="the main comparison\nRCT — random assignment\nobservational — no assignment" tabindex="0">ⓘ</span></span>' in e
    assert "data-tip" not in out["plain"]


def test_select_title_follows_the_selection() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = """
import { selectTitle, optionsTip } from %s;
const mk = (opts, chosen, help) => ({ dataset: { help }, selectedOptions: chosen === null ? [] : [opts[chosen]] });
const opts = [{ value: "rct", title: "random assignment" }, { value: "obs", title: "" }];
process.stdout.write(JSON.stringify([selectTitle(mk(opts, 0, "the main comparison")), selectTitle(mk(opts, 1, "the main comparison")), selectTitle(mk(opts, null, "")), selectTitle(mk(opts, 1, "")),
  optionsTip({ help: "H", options: [{ value: "a", label: "A", help: "def a" }, "b"] })]));
""" % json.dumps("file://" + GRAMMAR)
    r = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["random assignment", "the main comparison", "", "", "H\nA — def a\nb"]
