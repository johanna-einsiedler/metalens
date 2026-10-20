"""The colour-coded prompt of the MASEMiner builder: preset_spec.render_prompt_segments cuts a
rendered prompt into the template's own text and what the user's entries inserted, and
static/prompt_colors.js paints the pieces and carries them over a hand edit. The segments must
join to exactly the prompt a run sends; the JS half runs under node (skipped when absent)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens import preset_spec as ps, presets, records  # noqa: E402

JS = os.path.join(_ROOT, "paperlens", "static", "prompt_colors.js")


def _joined(segs: list[dict]) -> str:
    return "".join(s["text"] for s in segs)


def test_segments_join_to_the_prompt_of_every_bundled_preset() -> None:
    for pid, spec in presets.load_all().items():
        segs = ps.render_prompt_segments(spec)
        assert _joined(segs) == ps.render_prompt(spec), pid
        assert all(s["text"] for s in segs), pid
        assert not any(ch in _joined(segs) for ch in ""), pid


def test_entries_examples_and_template_text_are_told_apart() -> None:
    spec = presets.load_all()["masem-direct"]
    given = {"variables": [{"name": "se", "definition": "self-esteem", "synonyms": ["SES"]}],
             "variable_guidance": "Self-reports only.\n\n\nIgnore peer ratings.",
             "effect_sizes": []}                                   # empty: the default stays
    segs = ps.render_prompt_segments(spec, given, ps.given_params(spec, given))
    assert _joined(segs) == ps.render_prompt(spec, given)
    by = {s.get("param"): s for s in segs if s.get("param")}
    assert by["variables"]["kind"] == "param" and by["variables"]["given"] is True
    assert by["variables"]["label"] == "Variables" and '"se" = self-esteem' in by["variables"]["text"]
    assert by["effect_sizes"]["kind"] == "param" and by["effect_sizes"]["given"] is False   # the example
    assert by["variable_rules"]["kind"] == "template"         # its `empty` text is the template's sentence
    assert by["variable_rules"]["text"].startswith("Extract ONLY these canonical variables.")
    assert by["variable_guidance"]["given"] is True
    assert by["variable_guidance"]["text"] == "Self-reports only.\n\nIgnore peer ratings."   # collapsed as in a run
    plain = [s for s in segs if s["kind"] == "template" and "param" not in s]
    assert plain[0]["text"].startswith("# TASK") and len(plain) >= 3
    # a value's surrounding blank lines stay outside the marked piece
    assert not by["variable_guidance"]["text"].startswith("\n")


def test_a_value_cannot_forge_a_marker() -> None:
    spec = presets.load_all()["masem-indirect"]
    given = {"scale_name": "NCSg xy-18"}
    segs = ps.render_prompt_segments(spec, given, ps.given_params(spec, given))
    assert not any(ch in _joined(segs) for ch in "")
    assert any(s.get("param") == "scale_name" and s["text"] == "NCSg xy-18" for s in segs)


def test_given_params_are_the_names_resolve_params_lets_through() -> None:
    spec = presets.load_all()["masem-indirect"]
    assert sorted(ps.given_params(spec, {"scale_name": "TAS-20", "n_items": None, "item_texts": [],
                                         "nope": 1, "instrument_name": ""})) == ["scale_name"]


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def test_the_builder_endpoint_returns_segments_on_request() -> None:
    if not _db_ok():
        pytest.skip("no Postgres available")
    from fastapi.testclient import TestClient
    from paperlens.app import app
    c = TestClient(app); h = {"X-Session-Id": "colors-" + uuid.uuid4().hex[:6]}
    body = {"preset_id": "masem-indirect", "template_params": {"scale_name": "NCS-18", "n_items": 18}}
    plain = c.post("/api/build-preset-prompt", json=body, headers=h).json()
    assert "segments" not in plain
    r = c.post("/api/build-preset-prompt", json={**body, "segments": True}, headers=h).json()
    assert _joined(r["segments"]) == r["prompt"] == plain["prompt"]
    mine = {s["param"]: s["given"] for s in r["segments"] if s["kind"] == "param"}
    assert mine == {"scale_name": True, "n_items": True}          # no item texts: the default list is empty
    d = c.post("/api/build-preset-prompt", json={"preset_id": "masem-indirect", "segments": True}, headers=h).json()
    assert {s["param"]: s["given"] for s in d["segments"] if s["kind"] == "param"} == {"scale_name": False}


RUNNER = """
import { paintPrompt, editedSegments } from %s;
const T = (text, extra) => ({ text, kind: "template", ...(extra || {}) });
const P = (text, given, param) => ({ text, kind: "param", given, param: param || "v", label: "Variables" });
const segs = [T("# TASK\\n\\nExtract the effect sizes.\\n\\n## VARIABLES\\n"), P("- \\"bm\\" = body mass\\n- \\"vg\\" = gaming", true),
              T("\\n\\n"), P("- \\"r\\" = Correlation", false, "es"), T("\\nIGNORE all other variables.", { param: "rules", label: "Eligibility rules" }),
              T("\\nReturn <json> & stop.\\n")];
const joined = segs.map((s) => s.text).join("");
const out = { html: paintPrompt(segs) };
out.same = editedSegments(segs, joined) === segs;
const edited = joined.replace("Extract the effect sizes.", "Extract the effect sizes, adults only.").replace("gaming", "gaming hours");
const e = editedSegments(segs, edited);
out.editJoined = e.map((s) => s.text).join("") === edited;
out.editPieces = e.filter((s) => s.kind === "edit").map((s) => s.text);
out.keptParam = e.filter((s) => s.kind === "param" && s.given).map((s) => s.text).join("|");
const big = joined.replace(/a/g, "A");                     // edits all over: every changed word is typed
const b = editedSegments(segs, big);
out.bigJoined = b.map((s) => s.text).join("") === big;
out.html2 = paintPrompt(e);
process.stdout.write(JSON.stringify(out));
"""


def _run_js() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, "--input-type=module", "-e", RUNNER % json.dumps("file://" + JS)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_preview_paints_entries_examples_and_template() -> None:
    out = _run_js()
    html = out["html"]
    assert html.startswith('<b class="pp-h"># TASK</b>\n\n<span class="pp-t">Extract the effect sizes.</span>')
    assert '<b class="pp-h">## VARIABLES</b>' in html
    assert '<span class="pp-u" title="Your entry: Variables">- &quot;bm&quot; = body mass</span>\n' in html
    assert 'class="pp-u pp-ex" title="Variables: the preset\'s example, used until you fill in this field"' in html
    assert 'title="Template text while “Eligibility rules” is empty; your entry replaces it">IGNORE all other variables.</span>' in html
    assert '<span class="pp-t">Return &lt;json&gt; &amp; stop.</span>' in html
    assert "\n\n<span" in html and "<span class=\"pp-t\">\n" not in html   # blank lines stay unpainted


def test_a_hand_edit_keeps_the_colours_of_what_survived() -> None:
    out = _run_js()
    assert out["same"] is True and out["editJoined"] is True and out["bigJoined"] is True
    assert out["editPieces"] == ["sizes, adults only.", " hours"]     # word level: "sizes." became "sizes,"
    assert '- "bm" = body mass' in out["keptParam"]
    assert 'class="pp-u pp-ed" title="Typed by hand">hours</span>' in out["html2"]
