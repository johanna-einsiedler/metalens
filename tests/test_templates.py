"""Review templates (paperlens/templates.py): the grammar, the bundled templates against their
preset, the units of a document, the coder's projection (blind, masked), answers and
completeness. Stdlib-only; no database."""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import presets, templates as tp  # noqa: E402
from paperlens import workflow_spec as ws  # noqa: E402

EXT = presets.load_all()["genai-work-extraction"]

GOOD = {
    "format": 1, "id": "demo-template", "meta": {"title": "Demo"}, "for": "genai-work-extraction", "items": "evidence_items",
    "blind": {"fields": ["task_label"]},
    "stages": [
        {"id": "a", "label": "A", "help": "look", "blocks": [
            {"unit": "paper", "show": ["scale"], "questions": [{"name": "fine", "type": "enum", "options": ["yes", "no"], "required": True}]},
            {"unit": "entries", "show": ["task_name"], "mask_numbers": True,
             "questions": [{"name": "category", "type": "enum", "options_from": "task_label", "allow_other": True, "required": True},
                           {"name": "note", "type": "string"}],
             "add": {"fields": [{"name": "task_name", "type": "string"}]}},
            {"unit": "arms", "show": ["arm_label"], "group_by_entry": True, "marks": [{"when": "is_baseline", "text": "(reference arm)"}],
             "questions": [{"name": "verdict", "type": "enum", "options": ["ok", "wrong"], "required_when": {"is_baseline": False}}]},
            {"unit": {"distinct": "model_estimands.subgroup"}, "where": {"subgroup": {"not_in": [None, "", "full_sample"]}},
             "questions": [{"name": "verdict", "type": "enum", "options": ["ok", "not_in_paper"]}]},
            {"unit": "paper_metadata.claims", "where": {"kind": {"in": ["abstract"]}}, "show": ["sentence"], "title": "{sentence}", "mask_numbers": True,
             "questions": [{"name": "verdict", "type": "enum", "options": ["unit_right", "unit_wrong"], "required": True}]},
        ]},
    ],
}


def _view() -> dict:
    """A document view under the extraction contract: one task with two arms, two claims, estimands."""
    return {"document_id": "d1", "paper": {"title": "T", "doi": None, "year": 2024, "authors": [], "journal": None},
            "paper_metadata": {"title": "T", "scale": "lab", "research_design": "rct", "summary": "A secret 42",
                               "claims": [{"_rid": "o0", "claim_id": "C1", "sentence": "AI cut time by 40%", "kind": "abstract", "page": 1},
                                          {"_rid": "o1", "claim_id": "C2", "sentence": "Table 2 shows 12.5", "kind": "result", "page": 5}],
                               "model_estimands": [{"_rid": "o0", "estimand_id": "E1", "subgroup": "full_sample"}, {"_rid": "o1", "estimand_id": "E2", "subgroup": "novices"},
                                                   {"_rid": "o2", "estimand_id": "E3", "subgroup": "novices"}, {"_rid": "o3", "estimand_id": "E4", "subgroup": None}],
                               "evidence_items": [{"_rid": "o0", "n": 1, "page": 1, "kind": "passage", "text": "N = 120 workers", "bears_on": ["task_label", "task_name"]}]},
            "records": [{"id": "r1", "entry_index": 0, "verification_status": "unverified", "confidence": {"task": {"level": "high"}},
                         "field_values": {"task_id": "T1", "task_name": "Write 3 memos", "task_label": "writing",
                                          "arms": [{"_rid": "o0", "arm_id": "C", "arm_label": "no AI", "is_baseline": True, "control_type": "no_ai_concurrent"},
                                                   {"_rid": "o1", "arm_id": "T", "arm_label": "GPT-4", "is_baseline": False, "ai_role": "assistant"}]}}],
            "evidence": [{"record_id": "r1", "entry_index": 0, "page": 2, "field_path": "tasks[0].task_label", "snippet": "a writing task"},
                         {"record_id": "r1", "entry_index": 0, "page": 2, "field_path": "tasks[0].task_name", "snippet": "write 3 memos"},
                         {"record_id": None, "entry_index": None, "page": 1, "field_path": "paper_metadata.claims[0]", "snippet": "AI cut time by 40%"}],
            "paper_confidence": {"design": {"level": "high"}}, "document_confidence": {}}


def _errors(mutate) -> list[str]:
    doc = copy.deepcopy(GOOD); mutate(doc)
    return tp.validate(tp.normalize(doc), EXT)[0]


def test_a_head_never_prints_a_blind_field() -> None:
    errs = _errors(lambda d: d["stages"][0]["blocks"][1].__setitem__("title", "{task_name} · {task_label}"))
    assert any("{task_label} is blind" in e for e in errs), errs
    assert _errors(lambda d: d["stages"][0]["blocks"][1].__setitem__("title", "{task_name}")) == []
    jud = tp.load_all()["genai-work-judgment"]
    out = next(b for b in jud["stages"][1]["blocks"] if b["unit"] == "outcomes")
    assert out["title"] == "{outcome_id} · {outcome_name}"        # the preset child's own head names outcome_finding, the category the coder gives


def test_a_block_stacks_its_shown_fields_only_when_told() -> None:
    tpl = tp.normalize(copy.deepcopy(GOOD))
    assert all(bl["stack"] is False for st in tpl["stages"] for bl in st["blocks"])          # side by side by default
    assert any("stack must be a boolean" in e for e in _errors(lambda d: d["stages"][0]["blocks"][1].__setitem__("stack", "yes")))
    assert _errors(lambda d: d["stages"][0]["blocks"][1].__setitem__("stack", True)) == []
    assert tp.template_id(tp.normalize(copy.deepcopy(GOOD))) == tp.template_id(tp.normalize({**copy.deepcopy(GOOD), "stages": [
        {**GOOD["stages"][0], "blocks": [GOOD["stages"][0]["blocks"][0], {**GOOD["stages"][0]["blocks"][1], "stack": True}, *GOOD["stages"][0]["blocks"][2:]]},
        *GOOD["stages"][1:]]}))                                                              # layout, not contract


def test_the_good_template_normalizes_validates_and_hashes_what_it_asks() -> None:
    tpl = tp.normalize(copy.deepcopy(GOOD))
    assert tp.validate(tpl, EXT) == ([], [])
    assert tpl["verify"] == "paper" and tpl["layout"] == "stages" and tpl["stages"][0]["evidence"] == "items"
    assert tpl["stages"][0]["blocks"][1]["questions"][1]["label"] == "Note" and tpl["stages"][0]["blocks"][1]["questions"][1]["required"] is False
    tid = tp.template_id(tpl)
    assert tid.startswith("demo-template@") and len(tid) == len("demo-template@") + 8
    # wording, shown fields and marks are outside the id; a question, its options or a where are inside
    same = copy.deepcopy(GOOD); same["stages"][0]["label"] = "Other"; same["stages"][0]["blocks"][1]["show"] = []; same["stages"][0]["blocks"][2]["marks"] = []
    assert tp.template_id(tp.normalize(same)) == tid
    for mutate in (lambda d: d["stages"][0]["blocks"][0]["questions"].append({"name": "extra", "type": "string"}),
                   lambda d: d["stages"][0]["blocks"][0]["questions"][0]["options"].append("maybe"),
                   lambda d: d["stages"][0]["blocks"][0]["questions"][0].__setitem__("required", False),
                   lambda d: d["stages"][0]["blocks"][4].__setitem__("where", {"kind": {"in": ["abstract", "result"]}})):
        other = copy.deepcopy(GOOD); mutate(other)
        assert tp.template_id(tp.normalize(other)) != tid
    # the resolved form: options_from expanded into the preset's options, with prefixes
    res = tp.resolved(tpl, EXT)
    cat = res["stages"][0]["blocks"][1]["questions"][0]
    assert cat["options"] and all({"value", "label"} <= set(o) for o in cat["options"]) and "options_from" not in cat
    assert any(o["value"] == "writing" for o in cat["options"]) and res["template_id"] == tid
    assert res["stages"][0]["blocks"][3]["unit_kind"] == "distinct" and res["stages"][0]["blocks"][3]["unit_label"] == "subgroup"


@pytest.mark.parametrize("label, mutate, needle", [
    ("unknown root key", lambda d: d.__setitem__("zzz", 1), "unknown key"),
    ("for unknown", lambda d: d.__setitem__("for", "nope-preset"), "preset given"),
    ("layout", lambda d: d.__setitem__("layout", "chain"), "layout must be"),
    ("verify", lambda d: d.__setitem__("verify", "sometimes"), "verify must be"),
    ("items not a table", lambda d: d.__setitem__("items", "scale"), "must name a table field"),
    ("blind field unknown", lambda d: d["blind"]["fields"].append("zzz"), "not a field of the preset"),
    ("blind paper_fields", lambda d: d["blind"].__setitem__("paper_fields", "all"), "paper_fields must be"),
    ("no stages", lambda d: d.__setitem__("stages", []), "at least one stage"),
    ("stage id dup", lambda d: d["stages"].append(copy.deepcopy(d["stages"][0])), "duplicated"),
    ("stage evidence", lambda d: d["stages"][0].__setitem__("evidence", "all"), "evidence must be"),
    ("no blocks", lambda d: d["stages"][0].__setitem__("blocks", []), "at least one block"),
    ("unit unknown", lambda d: d["stages"][0]["blocks"][0].__setitem__("unit", "zzz"), "unit must be"),
    ("unit distinct bad field", lambda d: d["stages"][0]["blocks"][3].__setitem__("unit", {"distinct": "model_estimands.zzz"}), "unit must be"),
    ("show not a field", lambda d: d["stages"][0]["blocks"][1]["show"].append("zzz"), "not a field of the unit"),
    ("title placeholder", lambda d: d["stages"][0]["blocks"][4].__setitem__("title", "{zzz}"), "not a field of the unit"),
    ("group_by_entry on paper", lambda d: d["stages"][0]["blocks"][0].__setitem__("group_by_entry", True), "only applies to a child"),
    ("where column", lambda d: d["stages"][0]["blocks"][4].__setitem__("where", {"zzz": 1}), "not a field of the unit"),
    ("where operator", lambda d: d["stages"][0]["blocks"][4].__setitem__("where", {"kind": {"like": "a"}}), "unknown operator"),
    ("where sampled shape", lambda d: d["stages"][0]["blocks"][4].__setitem__("where", {"kind": {"sampled": "exhibits"}}), "sampled must be"),
    ("mark field", lambda d: d["stages"][0]["blocks"][2].__setitem__("marks", [{"when": "arm_label", "text": "x"}]), "boolean field"),
    ("mark shape", lambda d: d["stages"][0]["blocks"][2].__setitem__("marks", [{"text": "x"}]), "exactly one of when"),
    ("question name", lambda d: d["stages"][0]["blocks"][0]["questions"].append({"name": "bad name", "type": "string"}), "name must match"),
    ("question twice", lambda d: d["stages"][0]["blocks"][0]["questions"].append({"name": "fine", "type": "string"}), "asked twice"),
    ("question is a shown field", lambda d: d["stages"][0]["blocks"][1]["questions"].append({"name": "task_name", "type": "string"}), "the block shows"),
    ("question type", lambda d: d["stages"][0]["blocks"][0]["questions"].append({"name": "t", "type": "table"}), "type must be"),
    ("enum without options", lambda d: d["stages"][0]["blocks"][0]["questions"].append({"name": "e", "type": "enum"}), "exactly one of options"),
    ("enum both", lambda d: d["stages"][0]["blocks"][0]["questions"].append({"name": "e", "type": "enum", "options": ["a"], "options_from": "scale"}), "exactly one of options"),
    ("options_from not enum", lambda d: d["stages"][0]["blocks"][0]["questions"].append({"name": "e", "type": "enum", "options_from": "task_name"}), "not an enum or multi field"),
    ("options on a string", lambda d: d["stages"][0]["blocks"][0]["questions"].append({"name": "s", "type": "string", "options": ["a"]}), "only apply to an enum"),
    ("required_when column", lambda d: d["stages"][0]["blocks"][2]["questions"][0].__setitem__("required_when", {"zzz": True}), "not a field of the unit"),
    ("add on the paper", lambda d: d["stages"][0]["blocks"][0].__setitem__("add", {"fields": [{"name": "x"}]}), "nothing to add"),
    ("add shape", lambda d: d["stages"][0]["blocks"][1].__setitem__("add", {"fields": []}), "add must be"),
    ("add field is a question", lambda d: d["stages"][0]["blocks"][1].__setitem__("add", {"fields": [{"name": "note"}]}), "also a question"),
    ("sections field", lambda d: d["stages"][0]["blocks"][0].__setitem__("sections", [{"label": "S", "fields": ["zzz"]}]), "not a paper or entry field of this stage"),
    ("inline group", lambda d: d["stages"][0]["blocks"][0].__setitem__("inline", [["fine"]]), "inline must list groups"),
])
def test_validation_rejects(label, mutate, needle) -> None:
    errs = _errors(mutate)
    assert errs, f"{label}: expected an error"
    assert any(needle in e for e in errs), f"{label}: {errs}"


def test_the_bundled_templates_validate_against_their_preset_and_hash_stably() -> None:
    allt = tp.load_all()
    assert {"genai-work-judgment", "genai-work-check", "genai-work-routed"} <= set(allt)
    for tid, tpl in allt.items():
        errs, warns = tp.validate(tpl)
        assert errs == [], (tid, errs)
        assert tpl["for"] == "genai-work-extraction" and tpl["verify"] == "paper" and tpl["items"] == "evidence_items"
        assert tp.template_id(tpl) == tp.template_id(tp.normalize(json.loads(json.dumps(tpl))))
    jud = allt["genai-work-judgment"]
    assert [s["id"] for s in jud["stages"]] == ["design", "units", "headline"] and jud["stages"][0]["evidence"] == "none"
    assert jud["blind"]["paper_fields"] == "unshown" and "task_label" in jud["blind"]["fields"]
    units = {tp.unit_label(b["unit"]): b for b in jud["stages"][1]["blocks"]}
    assert set(units) == {"entries", "arms", "outcomes", "subgroup", "horizon"} and units["arms"]["group_by_entry"] is True
    cat = next(q for q in tp.question_defs(units["arms"], EXT) if q["name"] == "category")
    assert any(o["value"].startswith("ref:") and o["label"].startswith("reference arm") for o in cat["options"])
    assert any(o["value"].startswith("ai:") for o in cat["options"]) and cat["allow_other"] is True
    design = jud["stages"][0]["blocks"][0]
    assert design["unit"] == "paper" and design["show"] == [] and len(design["questions"]) == 21 and design["sections"]
    assert all(q.get("options_from") == q["name"] for q in design["questions"] if q["type"] in ("enum", "multi"))
    chk = allt["genai-work-check"]
    cells = chk["stages"][0]["blocks"][1]
    assert cells["where"] == {"exhibit_id": {"sampled": "exhibits.exhibit_id"}} and cells["add"]["fields"][0]["name"] == "panel"
    assert next(q for q in cells["questions"] if q["name"] == "mapping_ok")["required_when"] == {"estimate_id": {"not_null": True}}
    rtd = allt["genai-work-routed"]
    assert rtd["stages"][0]["blocks"][1]["unit"] == "paper_metadata.flags" and any(o["value"] == "exclude_paper" for o in rtd["stages"][0]["blocks"][1]["questions"][0]["options"])


def test_a_presets_own_review_is_its_implicit_template() -> None:
    rc = presets.load_all()["register-claims"]
    t = tp.from_preset(rc)
    assert t["source"] == "preset" and t["for"] == "register-claims" and t["layout"] == "chain"
    assert all(t[k] == v for k, v in rc["display"]["review"].items())
    assert tp.from_preset({"id": "x", "display": {}}) is None


def test_the_units_of_a_document_and_their_keys() -> None:
    tpl = tp.normalize(copy.deepcopy(GOOD)); view = _view()
    blocks = tpl["stages"][0]["blocks"]
    paper = tp.unit_rows(blocks[0], view, EXT)
    assert [u["key"] for u in paper] == [""] and paper[0]["record_id"] is None and paper[0]["row"]["scale"] == "lab" and paper[0]["row"]["title"] == "T"
    ents = tp.unit_rows(blocks[1], view, EXT)
    assert [(u["key"], u["record_id"], u["path"]) for u in ents] == [("", "r1", "tasks[0]")]
    arms = tp.unit_rows(blocks[2], view, EXT)
    assert [(u["key"], u["record_id"], u["index"], u["path"]) for u in arms] == [("arms#o0", "r1", 0, "tasks[0].arms[0]"), ("arms#o1", "r1", 1, "tasks[0].arms[1]")]
    assert tp.unit_title(blocks[2], arms[0], EXT) == "C · no AI"                   # the child's title from the preset
    dist = tp.unit_rows(blocks[3], view, EXT)
    assert [(u["key"], u["row"]) for u in dist] == [("model_estimands.subgroup=novices", {"subgroup": "novices", "n": 2})]   # full_sample and None left out
    claims = tp.unit_rows(blocks[4], view, EXT)
    assert [(u["key"], u["path"]) for u in claims] == [("claims#o0", "paper_metadata.claims[0]")] and tp.unit_title(blocks[4], claims[0], EXT) == "AI cut time by 40%"
    # keys parse back to their kind; the blocks a key names
    assert tp.parse_unit_key("") == {"kind": "self"} and tp.parse_unit_key("arms#o1") == {"kind": "row", "name": "arms", "rid": "o1"}
    assert tp.parse_unit_key("entries#+c9")["kind"] == "added" and tp.parse_unit_key("model_estimands.subgroup=a=b") == {"kind": "distinct", "name": "model_estimands", "field": "subgroup", "value": "a=b"}
    assert [b["unit"] for b in tp.blocks_for_key(tpl, EXT, "", has_record=True)] == ["entries"]
    assert [b["unit"] for b in tp.blocks_for_key(tpl, EXT, "", has_record=False)] == ["paper"]
    assert [b["unit"] for b in tp.blocks_for_key(tpl, EXT, "claims#o0", has_record=False)] == ["paper_metadata.claims"]
    assert tp.blocks_for_key(tpl, EXT, "claims#o0", has_record=True) == [] and tp.blocks_for_key(tpl, EXT, "arms#o0", has_record=True)[0]["unit"] == "arms"
    assert tp.blocks_for_key(tpl, EXT, "entries#+c9", has_record=True)[0]["unit"] == "entries"
    # where: the small grammar
    assert tp.where_match({"a": 1}, {"a": 1}) and not tp.where_match({"a": 1}, {"a": 2}) and tp.where_match({"a": None}, {"a": {"null": True}})
    assert tp.where_match({"a": 3}, {"a": {"gt": 2, "lte": 3}}) and not tp.where_match({"a": "x"}, {"a": {"gt": 2}})
    assert tp.where_match({"x": 1}, {"paper_metadata.routed": True}, paper={"routed": True}) and not tp.where_match({"x": 1}, {"paper_metadata.routed": True}, paper={})
    assert tp.where_match({"exhibit_id": "X1"}, {"exhibit_id": {"sampled": "exhibits.exhibit_id"}})                       # no sample given: every row
    assert tp.where_match({"exhibit_id": "X1"}, {"exhibit_id": {"sampled": "exhibits.exhibit_id"}}, sampled={"exhibits.exhibit_id": {"X1"}})
    assert not tp.where_match({"exhibit_id": "X2"}, {"exhibit_id": {"sampled": "exhibits.exhibit_id"}}, sampled={"exhibits.exhibit_id": {"X1"}})


def test_the_projection_blinds_masks_and_keeps_what_the_blocks_show() -> None:
    tpl = tp.normalize(copy.deepcopy(GOOD)); tpl["blind"]["paper_fields"] = "unshown"
    v = tp.project(_view(), tpl, EXT)
    rec = v["records"][0]
    assert "task_label" not in rec["field_values"] and "confidence" not in rec                      # blind: the value and the model's doubt about it
    assert rec["field_values"]["task_name"] == "Write # memos"                                       # masked where the block says so
    assert [e["field_path"] for e in v["evidence"]] == ["tasks[0].task_name", "paper_metadata.claims[0]"]   # the blind field's quote is gone
    assert v["evidence"][0]["snippet"] == "write # memos" and v["evidence"][1]["snippet"] == "AI cut time by ##%"
    pm = v["paper_metadata"]
    assert pm["claims"][0]["sentence"] == "AI cut time by ##%" and pm["claims"][1]["sentence"] == "Table 2 shows 12.5"     # only the block's rows (abstract) are masked
    assert "summary" not in pm and "research_design" not in pm and pm["scale"] == "lab"              # unshown paper fields dropped; the shown one kept
    assert "model_estimands" in pm and "evidence_items" in pm and pm["title"] == "T"               # a unit's table, the items and the identity stay
    assert pm["evidence_items"][0]["bears_on"] == ["task_name"] and pm["evidence_items"][0]["text"] == "N = ### workers"
    assert v["template"]["template_id"] == tp.template_id(tpl) and v["template"]["masked"] is True and v["template"]["blind"] is True
    assert v["paper_confidence"] == {}
    # without blind or masks the view is left alone
    plain = {"format": 1, "id": "plain", "meta": {"title": "P"}, "for": "genai-work-extraction", "stages": [{"id": "s", "label": "S", "blocks": [{"unit": "entries", "show": ["task_name"]}]}]}
    v2 = tp.project(_view(), tp.normalize(plain), EXT)
    assert v2["records"][0]["field_values"]["task_label"] == "writing" and v2["paper_metadata"]["summary"] == "A secret 42" and v2["template"]["blind"] is False


def test_answers_are_checked_against_the_questions_and_completeness_counts_what_is_owed() -> None:
    tpl = tp.normalize(copy.deepcopy(GOOD)); view = _view()
    tp.validate_answers(tpl, EXT, "", {"fine": "yes"}, has_record=False)
    tp.validate_answers(tpl, EXT, "", {"category": "writing", "note": None}, has_record=True)
    tp.validate_answers(tpl, EXT, "", {"category": "something else"}, has_record=True)             # allow_other
    tp.validate_answers(tpl, EXT, "arms#o1", {"verdict": "ok"}, has_record=True)
    tp.validate_answers(tpl, EXT, "entries#+c1", {"task_name": "A task the list lacks", "category": "writing"}, has_record=True)
    for key, vals, rec, needle in (("", {"fine": "maybe"}, False, "not an option"), ("", {"zzz": 1}, False, "not a question"),
                                   ("claims#o0", {"verdict": "unit_right"}, True, "no unit"), ("arms#o1", {"verdict": 3}, True, "not an option"),
                                   ("", {}, False, "must be an object")):
        with pytest.raises(ValueError) as info:
            tp.validate_answers(tpl, EXT, key, vals, has_record=rec)
        assert needle in str(info.value), (key, vals, str(info.value))
    # completeness: the paper's required question, the entry's category, the treatment arm's verdict
    # (the reference arm's is not required), the abstract claim's verdict; the distinct block asks nothing required
    c = tp.completeness(tpl, view, EXT, {})
    assert c["required"] == 4 and c["complete"] is False
    assert {(m["unit"], m["question"], m["record_id"]) for m in c["missing"]} == {("", "fine", None), ("", "category", "r1"), ("arms#o1", "verdict", "r1"), ("claims#o0", "verdict", None)}
    answers = {"paper": {"": {"fine": "yes"}, "claims#o0": {"verdict": "unit_right"}}, "records": {"r1": {"": {"category": "writing"}, "arms#o1": {"verdict": "ok"}}}}
    assert tp.completeness(tpl, view, EXT, answers)["complete"] is True
    # a unit the coder added owes its required questions too
    answers["records"]["r1"]["entries#+c1"] = {"task_name": "another"}
    c2 = tp.completeness(tpl, view, EXT, answers)
    assert c2["complete"] is False and c2["missing"] == [{"stage": "a", "block": 1, "unit": "entries#+c1", "record_id": "r1", "question": "category"}]
    assert tp.answers_at(answers, "r1", "arms#o1") == {"verdict": "ok"} and tp.answers_at(answers, None, "") == {"fine": "yes"} and tp.answers_at(None, None, "") == {}


def test_a_review_step_may_name_a_template_for_the_preset_it_reads() -> None:
    spec = ws.normalize({"format": 1, "id": "tasked", "meta": {"title": "Tasked"},
                         "steps": [{"id": "extraction", "kind": "extract", "preset": "genai-work-extraction@ddf3b7fd", "source": "supplied"},
                                   {"id": "judgment", "kind": "review", "over": ["extraction"], "template": "genai-work-judgment", "mode": "all", "unit": "paper", "coders": 2}]})
    assert ws.validate(spec)[0] == []
    assert ws.review_of(spec["steps"][1])["template"] == "genai-work-judgment"
    plain = copy.deepcopy(spec); del plain["steps"][1]["template"]
    assert ws.workflow_id(ws.normalize(plain)) != ws.workflow_id(spec)                             # the template is part of the contract
    bad = copy.deepcopy(spec); bad["steps"][1]["template"] = "nope"
    assert any("unknown template" in e for e in ws.validate(bad)[0])
    bad = copy.deepcopy(spec); bad["steps"][0]["preset"] = "masem-direct@eb5f0287"
    assert any("not for the preset" in e for e in ws.validate(bad)[0])


def test_a_block_lays_its_units_out_as_rows_with_the_exhibit_as_a_grid() -> None:
    """``layout: rows`` (a table, a row per unit), ``columns`` (the shown fields grouped) and
    ``grid`` (the exhibit as printed above it) are layout, outside the id; each is checked against
    the unit's fields, and a grid needs the items table its transcription sits in."""
    cells = {"unit": "paper_metadata.exhibit_cells", "show": ["row_label", "column", "panel", "value_as_printed", "estimate_id"],
             "questions": [{"name": "value_ok", "type": "enum", "options": ["yes", "no"], "required": True}]}
    rows = {**cells, "layout": "rows", "columns": [{"label": "Cell", "fields": ["row_label", "column", "panel"]}, "value_as_printed", ["estimate_id"]],
            "grid": {"of": "exhibit_id", "panel": "panel", "row": "row_label", "column": "column", "tip": "{estimate_id}"}}

    def with_block(bl: dict, items: str | None = "evidence_items") -> dict:
        d = copy.deepcopy(GOOD); d["stages"][0]["blocks"].append(bl)
        if items is None:
            d.pop("items")
        return tp.normalize(d)

    assert tp.validate(with_block(rows), EXT) == ([], [])
    assert tp.template_id(with_block(rows)) == tp.template_id(with_block(cells))           # layout, not contract
    bad = {
        "layout": ({**rows, "layout": "grid"}, "layout must be 'cards'"),
        "columns on cards": ({**rows, "layout": "cards", "grid": None}, "columns applies to layout 'rows'"),
        "column not shown": ({**rows, "columns": ["role"]}, "'role' is not among the block's shown fields"),
        "column shape": ({**rows, "columns": [{"label": "X"}]}, "must be a shown field's name"),
        "column key": ({**rows, "columns": [{"label": "X", "fields": ["panel"], "width": 3}]}, "unknown key 'width'"),
        "grid field": ({**rows, "grid": {**rows["grid"], "row": "row_name"}}, "grid.row: 'row_name' is not a field of the unit"),
        "grid of": ({**rows, "grid": {"row": "row_label", "column": "column"}}, "grid.of must name a field of the unit"),
        "grid tip": ({**rows, "grid": {**rows["grid"], "tip": "{nope}"}}, "grid.tip: {nope} is not a field of the unit"),
    }
    for name, (bl, msg) in bad.items():
        bl = {k: v for k, v in bl.items() if v is not None}
        errs = tp.validate(with_block(bl), EXT)[0]
        assert any(msg in e for e in errs), (name, errs)
    errs = tp.validate(with_block(rows, items=None), EXT)[0]
    assert any("name it in $.items" in e for e in errs), errs
    # the extraction check draws its cells this way, under the id its gates recorded
    chk = tp.load_all()["genai-work-check"]
    blk = next(b for b in chk["stages"][0]["blocks"] if b["unit"] == "paper_metadata.exhibit_cells")
    assert blk["layout"] == "rows" and blk["grid"]["of"] == "exhibit_id" and tp.validate(chk, EXT) == ([], [])
    assert tp.template_id(chk) == "genai-work-check@a0267cc9"
