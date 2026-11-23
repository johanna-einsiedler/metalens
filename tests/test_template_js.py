"""A review template's blocks in the workspace (static/template.js) and the transcribed-table helpers
(static/grammar.js), run through node on the modules themselves (skipped when node is absent; the
modules' "/static/…" imports are mapped onto paperlens/static by a resolve hook): a transcription
parsed into its panels, cells placed in it by panel, row and column, and the extraction check's
cells drawn as rows under the drawn exhibit laid out as printed."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.parse

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
STATIC = os.path.join(_ROOT, "paperlens", "static")
CHECK = os.path.join(_ROOT, "paperlens", "templates", "genai-work-check.json")

HOOK = ("export async function resolve(s, c, n) { return s.startsWith('/static/') ? n(%s + s.slice(8), c) : n(s, c); }"
        % json.dumps("file://" + STATIC + "/"))
REGISTER = "import { register } from 'node:module'; register('data:text/javascript,' + encodeURIComponent(%s));" % json.dumps(HOOK)
RUNNER = """
const g = await import('/static/grammar.js');
const t = await import('/static/template.js');
const c = JSON.parse(process.argv[process.argv.length - 1]);
const out = {};
out.parsed = g.parseTableMarkdown(c.md);
out.names = c.names.map(([text, id]) => g.captionNames(text, id));
out.placed = g.placeCells(out.parsed, c.cells);
const ctx = (extra) => ({ data: c.data, vm: c.vm, answers: c.answers, byCoder: c.byCoder, evidence: [], sampled: c.sampled,
                          items: t.itemRows(c.data, 'evidence_items'), ...extra });
out.coder = t.blockHtml(c.block, ctx({ editable: true, owner: false }));
out.owner = t.blockHtml(c.block, ctx({ editable: false, owner: true }));
out.cards = t.blockHtml({ ...c.block, layout: undefined, columns: undefined, grid: undefined }, ctx({ editable: true, owner: false }));
out.gridIds = t.gridIds({ blocks: [c.block] }, c.data, c.vm, c.sampled);
process.stdout.write(JSON.stringify(out));
"""

TABLE1 = """**Table 1. The Effect of the ChatGPT Ban on GitHub Output**

**Panel A: Pre 27/28.03 - Post 03/04.04**

**A: Pre 27/28.03 - Post 03/04.04**
|  | Events (1) | Release (2) |
|---|---|---|
| Treat×Post | 0.115 | -0.012** |
| Treat×Post (SE) | (0.109) | (0.005) |
| N | 18332 | 18332 |

**Panel B: Pre 27/28/29.03 - Post 05/06/11.04**

**B: Pre 27/28/29.03 - Post 05/06/11.04**
|  | Events (1) | Release (2) |
|---|---|---|
| Treat×Post | 0.048 | -0.003 |
| Treat×Post (SE) | (0.090) | (0.004) |
| N | 31983 | 31983 |

*Notes: Robust standard errors in parentheses.*"""

PANEL_A, PANEL_B = "A: Pre 27/28.03 - Post 03/04.04", "B: Pre 27/28/29.03 - Post 05/06/11.04"


def _cell(rid, panel, row, col, value, est):
    return {"_rid": rid, "exhibit_id": "Table 1", "panel": panel, "row_label": row, "column": col, "value_as_printed": value,
            "se_as_printed": None, "estimate_id": est, "estimand_text": f"unit of {est}", "role": "contrast"}


def _run() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    with open(CHECK, encoding="utf-8") as f:
        tpl = json.load(f)
    block = next(b for b in tpl["stages"][0]["blocks"] if b["unit"] == "paper_metadata.exhibit_cells")
    cells = [_cell("o0", PANEL_A, "Treat×Post", "Events (1)", "0.115", "est_E1"),
             _cell("o1", PANEL_B, "Treat×Post", "Release (2)", "-0.003", "est_E2"),
             _cell("o2", "C: no such panel", "Treat×Post", "Events (1)", "0.5", "est_E3")]
    col = lambda n, t="string", **k: {"name": n, "type": t, **k}  # noqa: E731
    vm = {"entries": {"key": "records", "children": [], "fields": []},
          "paper": {"fields": [
              {"name": "exhibits", "type": "table", "columns": [col("exhibit_id"), col("kind"), col("page", "integer")]},
              {"name": "exhibit_cells", "type": "table", "columns": [col(n) for n in ("exhibit_id", "panel", "row_label", "column", "value_as_printed", "se_as_printed", "estimate_id", "estimand_text")]
                                                                   + [col("role", "enum", options=["contrast", "arm_stat"])]},
              {"name": "evidence_items", "type": "table", "columns": [col("n", "integer"), col("kind"), col("text", "text")]}]}}
    data = {"paper_metadata": {"exhibits": [{"_rid": "o0", "exhibit_id": "Table 1", "kind": "table", "page": 9},
                                            {"_rid": "o1", "exhibit_id": "Table 10", "kind": "table", "page": 30}],
                               "exhibit_cells": cells + [_cell("o9", PANEL_A, "N", "Events (1)", "18332", None) | {"exhibit_id": "Table 10"}],
                               "evidence_items": [{"n": 1, "kind": "table", "page": 30, "text": "**Table 10. Another**\n\n| | x |\n|---|---|\n| a | 1 |"},
                                                  {"n": 2, "kind": "table", "page": 9, "text": TABLE1}]},
            "records": []}
    sampled = {"_keys": ["exhibits#o0"], "exhibits.exhibit_id": ["Table 1"]}
    answers = {"paper": {"exhibit_cells#o0": {"value_ok": "yes"}, "exhibit_cells#+c1": {"row_label": "Post", "value_as_printed": "0.052"}}}
    by_coder = {"coder_4": answers}
    case = {"md": TABLE1, "names": [[TABLE1, "Table 1"], ["**Table 10. Another**", "Table 1"], ["**Table A.2. Placebo**", "Table A.2"], ["**Table A.2**", "Table A.2"], ["", "Table 1"]],
            "cells": [{"key": "a", "panel": PANEL_A, "row": "Treat×Post", "column": "Release (2)"},
                      {"key": "b", "panel": "Panel B: Pre 27/28/29.03 - Post 05/06/11.04", "row": "treat×post", "column": "events (1)"},
                      {"key": "c", "panel": "Panel Z", "row": "Treat×Post", "column": "Events (1)"},
                      {"key": "d", "panel": PANEL_A, "row": "Treat×Post", "column": "Output (3)"}],
            "block": block, "vm": vm, "data": data, "sampled": sampled, "answers": answers, "byCoder": by_coder}
    loader = "data:text/javascript," + urllib.parse.quote(REGISTER, safe="")
    r = subprocess.run([node, "--import", loader, "--input-type=module", "-e", RUNNER, "--", json.dumps(case)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_transcription_parses_into_its_panels_and_places_cells_by_panel_row_and_column() -> None:
    out = _run()
    p = out["parsed"]
    assert p["caption"] == "Table 1. The Effect of the ChatGPT Ban on GitHub Output"        # the first bold line, not a panel's
    assert p["notes"] == "Notes: Robust standard errors in parentheses."
    assert [x["label"] for x in p["panels"]] == [f"Panel {PANEL_A}", f"Panel {PANEL_B}"]
    assert p["panels"][0]["labels"] == [f"Panel {PANEL_A}", PANEL_A]
    assert p["panels"][1]["header"] == ["", "Events (1)", "Release (2)"] and p["panels"][1]["rows"][0] == ["Treat×Post", "0.048", "-0.003"]
    assert out["names"] == [True, False, True, True, False]                                  # "Table 10" is not "Table 1"
    placed = out["placed"]
    assert placed["at"] == {"0:0:2": "a", "1:0:1": "b"}                                       # b in panel B, by a "Panel …" label, any case
    assert placed["se"] == {"0:1:2": "a", "1:1:1": "b"}                                       # the "(SE)" line beneath
    assert placed["unplaced"] == ["c", "d"]                                                  # an unknown panel is never guessed; no such column


def test_the_check_tasks_cells_are_rows_under_the_drawn_exhibit_as_printed() -> None:
    out = _run()
    html = out["coder"]
    assert out["gridIds"] == ["Table 1"]                                                      # what the pane opens on
    # the grid: the drawn table's transcription (Table 1, not Table 10), both panels, the placed cells numbered as their rows
    assert html.count('class="tq-grid"') == 1 and "Table 1. The Effect of the ChatGPT Ban" in html and "Another" not in html
    assert html.count('<tr class="tq-gp">') == 2
    assert re.search(r'<td class="tq-gc tq-cited" data-unit="exhibit_cells#o0" title="row 1 · est_E1 · unit of est_E1[^"]*">0\.115<sup>1</sup></td>', html)
    assert re.search(r'<td class="tq-gc tq-cited" data-unit="exhibit_cells#o1"[^>]*>-0\.003<sup>2</sup></td>', html)
    assert '<td class="tq-gc tq-cited-se" data-unit="exhibit_cells#o0">(0.109)</td>' in html
    assert "row 3 not found in the transcription's grid" in html                              # the cell of an unknown panel
    # the rows: one per cell of the drawn exhibit plus the coder's added one, the questions as controls, the answer kept
    rows = re.findall(r'<tr class="tq-unit tq-row( tq-added)?" data-unit="([^"]+)"', html)
    assert [k for _, k in rows] == ["exhibit_cells#o0", "exhibit_cells#o1", "exhibit_cells#o2", "exhibit_cells#+c1"]
    assert [a for a, _ in rows] == ["", "", "", " tq-added"]
    assert html.count('<td class="tq-q rv-root">') == 4 * 3                                  # value_ok, mapping_ok, note per row
    assert re.search(r'<td class="tq-q rv-root"><span class="rv-selwrap" data-path="value_ok"><select[^>]*>.*?<option value="yes" selected>', html)
    assert '<th>Cell</th><th>As printed</th><th>Estimate</th><th>Role</th>' in html
    assert 'data-path="exhibit_cells[0].value_as_printed"' in html                             # a shown cell saves into the coding as a card's does
    added = html[html.index('data-unit="exhibit_cells#+c1"'):]
    assert 'data-scope="answers"' in added and 'class="tq-del"' in added and 'data-path="row_label"' in added
    assert 'class="tq-add"' in html
    # the owner: no controls, every coder's answers in a last column
    owner = out["owner"]
    assert "<select" not in owner.split('class="tq-rows"')[1] and "<th>Answers</th>" in owner and "<b>coder_4</b>" in owner
    # without the layout keys, the same block is the cards it was
    assert 'class="tq-rows"' not in out["cards"] and 'class="tq-grid"' not in out["cards"] and out["cards"].count('<div class="tq-unit') == 4
