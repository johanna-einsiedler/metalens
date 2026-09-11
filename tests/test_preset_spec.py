"""The declarative preset format itself: validation, paths, hashing, and the generated
prompt. Stdlib-only; no DB."""
from __future__ import annotations

import copy
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

from paperlens import preset_spec as ps, presets  # noqa: E402

GOOD = {
    "format": 2, "id": "demo", "meta": {"title": "Demo"},
    "prompt": {"text": "Do the thing.\n\nCodes:\n${codes}\n",
               "params": {"codes": {"type": "table", "columns": ["code", "label"],
                                    "default": [{"code": "r", "label": "Correlation"}]}}},
    "paper": {"fields": [{"name": "design", "type": "enum", "options": ["rct", "obs"], "confidence": "dq"}]},
    "entries": {"key": "samples", "label": "Sample", "id_field": "sample_id", "title": "Sample {sample_id}",
                "fields": [{"name": "sample_id", "type": "string", "required": True, "evidence": "row"},
                           {"name": "n", "type": "integer", "confidence": "meta"},
                           {"name": "matrix", "type": "table", "columns": [{"name": "a", "type": "number"}]}],
                "children": [{"key": "records", "label": "Effect size", "title": "{var1}",
                              "fields": [{"name": "var1", "type": "enum", "options": "${codes}", "required": True,
                                          "evidence": "row", "confidence": "es"},
                                         {"name": "es", "type": "number", "range": [-1, 1], "confidence": "es"}]}]},
    "confidence": {"groups": [{"id": "dq", "label": "Design", "scope": "paper"},
                              {"id": "meta", "label": "Metadata"},
                              {"id": "es", "label": "Effect sizes", "scope": "child"}]},
    "display": {"tabs": [{"id": "a", "label": "A", "fields": ["records", "sample_id", "n", "matrix"]}]},
}


def _bad(mutate):
    doc = copy.deepcopy(GOOD)
    mutate(doc)
    errs, _ = ps.validate(ps.normalize.__wrapped__(doc) if hasattr(ps.normalize, "__wrapped__") else _fill(doc))
    return errs


def _fill(doc):
    """normalize() raises on invalid input; run the default-filling part only."""
    try:
        return ps.normalize(doc)
    except ps.SpecError as e:
        return e


def _errors(mutate) -> list[str]:
    doc = copy.deepcopy(GOOD)
    mutate(doc)
    try:
        ps.normalize(doc)
    except ps.SpecError as e:
        return e.errors
    return []


def test_good_spec_normalises_with_defaults() -> None:
    spec = ps.normalize(GOOD)
    assert spec["entries"]["cardinality"] == "many" and spec["entries"]["evidence"] == "row"
    assert spec["entries"]["fields"][1]["evidence"] == "value"          # scalar default
    assert spec["entries"]["fields"][2]["evidence"] == "table"          # table default
    assert spec["entries"]["children"][0]["layout"] == "table"
    assert spec["confidence"]["levels"] == ["high", "medium", "low"]
    assert spec["confidence"]["groups"][1]["scope"] == "entry"          # scope default
    assert spec["prompt"]["generate"] == list(ps.GENERATED_SECTIONS)
    assert ps.validate(spec) == ([], [])


@pytest.mark.parametrize("label,mutate,needle", [
    ("format", lambda d: d.__setitem__("format", 1), "format"),
    ("id with @", lambda d: d.__setitem__("id", "a@b"), "id"),
    ("no title", lambda d: d["meta"].pop("title"), "title"),
    ("bad mode", lambda d: d["meta"].__setitem__("mode", "label"), "mode"),
    ("unknown key", lambda d: d.__setitem__("bogus", 1), "unknown key"),
    ("empty prompt", lambda d: d["prompt"].__setitem__("text", "  "), "prompt.text"),
    ("undeclared placeholder", lambda d: d["prompt"].__setitem__("text", "hi ${nope}"), "${nope}"),
    ("bad generate", lambda d: d["prompt"].__setitem__("generate", ["schema"]), "generate"),
    ("reserved field name", lambda d: d["entries"]["fields"].append({"name": "evidence"}), "reserved"),
    ("dotted field name", lambda d: d["entries"]["fields"].append({"name": "F1.2"}), "name"),
    ("duplicate name", lambda d: d["entries"]["fields"].append({"name": "n"}), "already used"),
    ("child key collides", lambda d: d["entries"]["children"][0].__setitem__("key", "n"), "collides"),
    ("nested children", lambda d: d["entries"]["children"][0].__setitem__("children", []), "may not nest"),
    ("enum without options", lambda d: d["entries"]["fields"].append({"name": "x", "type": "enum"}), "options"),
    ("options on string", lambda d: d["entries"]["fields"].append({"name": "x", "options": ["a"]}), "options"),
    ("table without columns", lambda d: d["entries"]["fields"].append({"name": "x", "type": "table"}), "columns"),
    ("bad range", lambda d: d["entries"]["fields"].append({"name": "x", "type": "number", "range": [1, 0]}), "range"),
    ("value evidence on table", lambda d: d["entries"]["fields"][2].__setitem__("evidence", "value"), "evidence"),
    ("table evidence on scalar", lambda d: d["entries"]["fields"][1].__setitem__("evidence", "table"), "evidence"),
    ("id_field unknown", lambda d: d["entries"].__setitem__("id_field", "zzz"), "id_field"),
    ("title placeholder unknown", lambda d: d["entries"].__setitem__("title", "{zzz}"), "title"),
    ("dangling group", lambda d: d["entries"]["fields"][1].__setitem__("confidence", "nope"), "not a declared group"),
    ("scope mismatch", lambda d: d["entries"]["fields"][1].__setitem__("confidence", "dq"), "scope"),
    ("bad scope", lambda d: d["confidence"]["groups"][0].__setitem__("scope", "field"), "scope"),
    ("tab ref unknown", lambda d: d["display"]["tabs"][0]["fields"].append("zzz"), "not an entry field"),
    ("tab ref to paper field", lambda d: d["display"]["tabs"][0]["fields"].append("design"), "not an entry field"),
    ("grid_rows unknown", lambda d: d["display"].__setitem__("grid_rows", "zzz"), "grid_rows"),
    ("reserved entries key", lambda d: d["entries"].__setitem__("key", "evidence"), "reserved"),
])
def test_validation_rejects(label, mutate, needle) -> None:
    errs = _errors(mutate)
    assert errs, f"{label}: expected an error"
    assert any(needle in e for e in errs), f"{label}: {errs}"


def test_validation_warns_without_blocking() -> None:
    doc = copy.deepcopy(GOOD)
    doc["display"]["tabs"][0]["fields"] = ["records"]          # n / sample_id / matrix unplaced
    doc["confidence"]["groups"].append({"id": "lonely", "label": "Lonely"})
    spec = ps.normalize(doc)
    _, warns = ps.validate(spec)
    assert any("'n' is in no tab" in w for w in warns)
    assert any("'lonely' is not referenced" in w for w in warns)


def test_parse_path_grammar() -> None:
    spec = ps.normalize(GOOD)
    def P(path, **want):
        got = ps.parse_path(path, spec)
        assert got is not None, path
        for k, v in want.items():
            assert got[k] == v, (path, k, got)
        return got
    P("paper_metadata.design", scope="paper", field="design", valid=True)
    P("samples", scope="entries", valid=True)
    P("samples[0]", scope="entry", entry_index=0, field=None, valid=True)
    P("samples[0].n", scope="entry", entry_index=0, field="n", valid=True)
    P("samples[0].matrix", scope="entry", table_key="matrix", row_index=None, valid=True)
    P("samples[0].matrix[3]", scope="entry", table_key="matrix", row_index=3, valid=True)
    P("samples[1].records[2]", scope="child", entry_index=1, child_key="records", child_index=2, valid=True)
    P("samples[1].records[2].es", scope="child", child_index=2, field="es", valid=True)
    P("studies._table[1]", scope="entry", entry_index=1, valid=False)        # wrong entries key
    assert ps.parse_path("studies._table[1]", None)["entry_index"] == 1       # legacy, no spec
    assert ps.parse_path("samples[0].n.deeper", spec)["valid"] is False
    assert ps.parse_path("sample identification", spec) is None
    assert ps.parse_path("", spec) is None


def test_hash_ignores_wording_and_display() -> None:
    a = ps.normalize(GOOD)
    b = copy.deepcopy(GOOD)
    b["meta"]["title"] = "Other"; b["prompt"]["text"] = "Other text ${codes}"
    b["entries"]["fields"][1]["label"] = "Sample size"; b["entries"]["fields"][1]["help"] = "…"
    b["display"] = {"entries": "table"}
    assert ps.content_hash(ps.normalize(b)) == ps.content_hash(a)
    c = copy.deepcopy(GOOD); c["entries"]["fields"][1]["type"] = "number"
    assert ps.content_hash(ps.normalize(c)) != ps.content_hash(a)
    assert ps.schema_id(a) == f"demo@{ps.content_hash(a)[:8]}"
    assert ps.label(a) == f"Demo v1 ({ps.content_hash(a)[:8]})"


def test_generated_prompt_covers_the_declaration() -> None:
    spec = ps.normalize(GOOD)
    text = ps.render_prompt(spec)
    assert text.startswith("Do the thing.")
    assert '- "r" = Correlation' in text                     # table param rendered
    skel = json.loads(text.split("# OUTPUT SCHEMA (generated — follow exactly)")[1]
                      .split("\n{", 1)[1].split("\n}\n", 1)[0].join(["{", "}"]))
    assert list(skel) == ["paper_metadata", "samples", "evidence"]
    assert skel["paper_metadata"]["design"] == "rct|obs|null"
    assert skel["samples"][0]["records"][0]["var1"] == "r"           # enum from ${codes}
    assert skel["samples"][0]["records"][0]["confidence"]["es"]["level"] == "high|medium|low"
    assert skel["samples"][0]["matrix"] == [{"a": "number|null"}]
    for name in ("sample_id", "n", "matrix", "var1", "es", "design"):
        assert f"- {name} (" in text                        # every field gets a FIELDS line
    assert 'one item identifying each Sample: field "samples[i]"' in text
    assert 'one item per Effect size: field "samples[i].records[j]"' in text
    assert "samples[i].records[j].es" in text and "paper_metadata.design" in text
    assert 'For EACH element of "samples[].records"' in text
    assert ps.render_prompt(spec) == text                     # deterministic


def test_generated_sections_can_be_placed_or_disabled() -> None:
    doc = copy.deepcopy(GOOD)
    doc["prompt"]["text"] = "Intro.\n\n${output_schema}\n\nOutro."
    doc["prompt"]["generate"] = ["output_schema"]
    text = ps.render_prompt(ps.normalize(doc))
    assert text.index("Intro.") < text.index("# OUTPUT SCHEMA") < text.index("Outro.")
    assert "# EVIDENCE (generated)" not in text and "# CONFIDENCE" not in text
    doc["prompt"]["generate"] = []
    assert ps.render_prompt(ps.normalize(doc)).strip() == "Intro.\n\nOutro."


def test_params_merge_over_defaults_and_ignore_unknowns() -> None:
    spec = ps.normalize(GOOD)
    got = ps.resolve_params(spec, {"codes": [{"code": "d"}], "instrument_name": "x", "empty": ""})
    assert got == {"codes": [{"code": "d"}]}
    assert ps.render_param(spec["prompt"]["params"]["codes"], got["codes"]) == '- "d" = Cohen\'s d'
    assert ps.render_param({"type": "list", "numbered": True}, ["a", "b"]) == "1: a\n2: b"
    assert ps.render_param({"type": "list"}, ["a"]) == "- a"


def test_validate_result_flags_what_the_coder_must_check() -> None:
    spec = ps.normalize(GOOD)
    good = {"paper_metadata": {"title": "T", "design": "rct", "confidence": {"dq": {"level": "high", "notes": ""}}},
            "samples": [{"sample_id": "S1", "n": 5, "matrix": [{"a": 1}],
                         "records": [{"var1": "r", "es": 0.2, "confidence": {"es": {"level": "high", "notes": ""}}}],
                         "confidence": {"meta": {"level": "high", "notes": ""}}}],
            "evidence": [{"snippet": "x", "page": 1, "source": None, "field": "samples[0].n"},
                         {"snippet": "y", "page": 1, "source": None, "field": "samples[0].records[0].es"},
                         {"snippet": "z", "page": 1, "source": None, "field": "paper_metadata.design"},
                         {"snippet": "S1", "page": 1, "source": None, "field": "samples[0]"},
                         {"snippet": "row", "page": 1, "source": None, "field": "samples[0].records[0]"}]}
    assert ps.validate_result(good, spec) == []
    # a sample / effect-size row without its identifying item is reported
    partial = copy.deepcopy(good); partial["evidence"] = partial["evidence"][:3]
    assert {(i["code"], i["path"]) for i in ps.validate_result(partial, spec)} == {
        ("uncited_row", "samples[0]"), ("uncited_row", "samples[0].records[0]")}
    bad = copy.deepcopy(good)
    bad["samples"][0]["records"][0].update({"var1": "zzz", "es": 7})
    bad["samples"][0].pop("confidence")
    bad["evidence"].append({"snippet": "q", "page": "3", "source": None, "field": "samples[9].n"})
    codes = {(i["path"], i["code"]) for i in ps.validate_result(bad, spec)}
    assert ("samples[0].records[0].var1", "not_in_options") in codes
    assert ("samples[0].records[0].es", "out_of_range") in codes
    assert ("samples[0].confidence.meta", "missing_confidence") in codes
    assert ("evidence[5].field", "dangling_path") in codes
    assert ("evidence[5].page", "bad_page") in codes


def test_shipped_prompt_snapshot() -> None:
    """The exact prompt masem-direct renders is pinned: a generator change shows up as a
    diff here, not as a silent extraction-quality drift. Set PAPERLENS_UPDATE_SNAPSHOTS=1
    to accept a change."""
    snap = os.path.join(_HERE, "snapshots", "masem-direct.prompt.txt")
    text = presets.prompt_for("masem-direct")
    assert isinstance(text, str) and text.strip(), "masem-direct did not render"
    stale = not os.path.exists(snap) or not open(snap, encoding="utf-8").read().strip()
    if stale or os.environ.get("PAPERLENS_UPDATE_SNAPSHOTS"):
        os.makedirs(os.path.dirname(snap), exist_ok=True)
        open(snap, "w", encoding="utf-8").write(text)
    assert text == open(snap, encoding="utf-8").read()


def test_validate_result_follows_the_prompt_mode() -> None:
    """A declared preset (generated sections) wants a citation per value and the new
    confidence block; a frozen prompt is judged by its own rules — the legacy per-entry
    ``extraction_confidence`` counts, and value coverage is not checked."""
    import copy
    spec = ps.load_dir()["human-ai-collab"]
    out = {"paper_metadata": {"title": "T"},
           "experiments": [{"Exp_ID": "1", "Exp_Design": "Between-Subjects", "Comp_Type": "Independent Samples",
                            "N_Exp": 92, "Task_Desc": "Do x", "Task_Type": "Decide", "conditions": [],
                            "extraction_confidence": {"design": "high", "task": "high", "participants": "high"}}],
           "evidence": []}
    codes = [(i["code"], i["path"]) for i in ps.validate_result(out, spec)]
    assert ("uncited_value", "experiments[0].N_Exp") in codes
    assert ("missing_confidence", "experiments[0].confidence.design") in codes
    frozen = copy.deepcopy(spec); frozen["prompt"]["generate"] = []
    codes = [(i["code"], i["path"]) for i in ps.validate_result(out, frozen)]
    assert not any(c == "uncited_value" for c, _ in codes)
    assert not any(c == "missing_confidence" for c, _ in codes)


def test_validate_result_counts_rows_and_linked_fields() -> None:
    """One quote may be reused for several values (``field`` as a list of paths), and a
    condition or table row without its identifying item is reported — so a model that
    stops citing after the first condition shows up in the issues, not only in the cells."""
    spec = ps.load_dir()["human-ai-collab"]

    def cond(name: str, model: str) -> dict:
        return {"Condition_Name": name, "AI_Type": "Generative", "LLM_Model": model, "Final_Decision": "AI",
                "measures": [{"Perf_Metric": "Agreement (%)", "Avg_Perf_HumanAI": 94}],
                "confidence": {"condition": {"level": "high", "notes": ""}, "results": {"level": "high", "notes": ""}}}
    out = {"paper_metadata": {"title": "T"},
           "experiments": [{"Exp_ID": "PRISMA", "Task_Type": "Decide", "conditions": [cond("A", "Claude-3-Opus"), cond("B", "GPT-4")],
                            "confidence": {g: {"level": "high", "notes": ""} for g in ("design", "task", "participants")}}],
           "evidence": [
               {"snippet": "id", "page": 1, "source": None, "field": "experiments[0]"},
               {"snippet": "deferred", "page": 1, "source": None,
                "field": ["experiments[0].conditions[0].Final_Decision", "experiments[0].conditions[1].Final_Decision"]},
               {"snippet": "row", "page": 7, "source": "Table 3", "field": "experiments[0].conditions[0]"},
               {"snippet": "row", "page": 7, "source": "Table 3", "field": "experiments[0].conditions[0].measures[0]"}]}
    codes = {(i["code"], i["path"]) for i in ps.validate_result(out, spec)}
    assert not any(p.endswith("Final_Decision") for c, p in codes if c == "uncited_value")   # the list covered both
    assert ("uncited_row", "experiments[0].conditions[1]") in codes
    assert ("uncited_row", "experiments[0].conditions[1].measures[0]") in codes
    assert ("uncited_row", "experiments[0].conditions[0]") not in codes
    assert ("uncited_row", "experiments[0]") not in codes
    text = presets.prompt_for("human-ai-collab")
    assert "Coverage is per instance" in text and "a LIST of paths" in text
