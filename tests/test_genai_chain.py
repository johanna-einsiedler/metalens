"""The GenAI-at-work chain package (paperlens/chains/genai_work): its prompt files reproduce the
review project's hashes, its taxonomy snapshot the project's counts, its text helpers the
project's behaviour. No DB, no model."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens.chains.genai_work import hashes, taxonomy, text  # noqa: E402


def test_the_prompt_files_reproduce_the_projects_hashes() -> None:
    assert hashes.split_hash() == "2b9af957a9" and hashes.design_hash() == "afbb8492d2" and hashes.claims_hash() == "de773e57b5"
    assert hashes.exported() == {"split": "2b9af957a9", "design": "afbb8492d2", "claims": "de773e57b5"}
    assert taxonomy.model() == "gemini-3.1-pro-preview" and taxonomy.inputs_version() == "2" and taxonomy.schema_hash() == "b5ed1ca67c"
    assert len(hashes.prompt("design")) == 59092 and len(hashes.prompt("estimands")) == 12305
    for name in ("exhibits", "estimands", "estimates", "design", "claims_abstract", "claims_map", "plan_task_evidence", "plan_unit_attr", "plan_atomic", "plan_chain", "match_runs"):
        assert hashes.schema(name)["type"] == "object", name
    assert hashes.short("estimates").startswith("\n\nThe previous answer was too long")


def test_the_taxonomy_snapshot_has_the_projects_variables() -> None:
    assert len(taxonomy.variables()) == 102
    assert {lvl: len(taxonomy.by_level(lvl)) for lvl in taxonomy.LEVELS} == {"study": 27, "task": 15, "arm": 17, "outcome": 9, "result": 34}
    assert {lvl: len(taxonomy.model_fields(lvl)) for lvl in taxonomy.LEVELS} == {"study": 17, "task": 11, "arm": 16, "outcome": 0, "result": 32}
    v = taxonomy.field("control_type", "arm")
    assert v["type"] == "enum" and "no_ai_concurrent" in taxonomy.options("control_type", "arm")
    assert taxonomy.kind_finding()["duration"] and taxonomy.quote_groups() == {"stats": ("stats_quote", "stats_page"), "effect": ("effect_quote", "effect_page")}
    assert taxonomy.wrapped(taxonomy.field("scale", "study")) is True
    assert taxonomy.field("model_estimates", "study")["columns"]


def test_the_text_helpers_check_snap_and_cut_as_the_project_does() -> None:
    p = text.Pages(["Title line\nAbstract\nWe study the effect of generative AI on writing speed among 100 workers.\nKeywords: AI\n1 Introduction\nThe eﬀect was large, as Table 1 shows.",
                    "2 Methods\nParticipants wrote memos; the AI arm finished 0.138*** (0.0199) faster.\nTable 1: Results\n0.138 0.0199 100"])
    assert p.raw.startswith("=== PAGE 1 ===\nTitle line") and "=== PAGE 2 ===" in p.raw and set(p.pages) == {1, 2}
    assert text.norm("diﬀerent eﬀect") == text.norm("di erent e ect") == "dierenteect"
    assert text.check_quote("The effect was large, as Table 1 shows.", 1, p.pages) == "page"
    assert text.check_quote("The effect was large, as Table 1 shows.", 2, p.pages) == "other_page"
    assert text.check_quote("nothing of this is in the paper at all, really", 1, p.pages) == "not_found" and text.check_quote("short", 1, p.pages) == "empty"
    assert text.check_quote("generative AI ... writing speed among 100 workers", 1, p.pages) == "fragments"
    hit = text.snap_quote("We study the effects of generative AI on writing speed among 100 workers", 2, p.rpages)
    assert hit and hit[1] == 1 and hit[0].startswith("We study the effect of generative AI")
    assert text.table_cells("0.138*** (0.0199)", 1, p.pages) == 2 and text.actual_page("Participants wrote memos", 1, p.pages) == 2
    assert text.number_in_quote(0.138, "faster by 0.138 (0.0199)") and not text.number_in_quote(0.4, "10.45 units") and text.number_in_quote(1571, "N = 1,571")
    assert text.number_on_page(0.0199, 2, p.pages)
    cut, dropped = text.without_abstract(p.raw)
    assert dropped and "We study the effect" not in cut and "=== PAGE 1 ===" in cut and "1 Introduction" in cut
    cut2, dropped2 = text.without_abstract(p.raw, "We study the effect of generative AI on writing speed among 100 workers.")
    assert dropped2 and "We study the effect" not in cut2
    assert text.split_sentences("We used GPT-4 et al. in 2023. Results follow in Fig. 2. Done!") == ["We used GPT-4 et al. in 2023.", "Results follow in Fig. 2.", "Done!"]
    assert text.page_text(["a hyphen-", "ated word", "next"]) == "a hyphenated word next"
    lines, heads = text.scan("=== PAGE 1 ===\n" + "x" * 1600 + "\n1 Introduction\n" + "y" * 500 + "\n2 Methods\n" + "z" * 500 + "\nReferences\n")
    assert [(k, s) for _i, k, s in heads] == [("intro", "1 Introduction"), ("methods", "2 Methods"), ("references", "References")]
    assert text.table_text({"caption": "Table 1", "header": ["a", "b"], "rows": [["1", "2"], ["3"]]}) == "**Table 1**\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 |  |"
    assert text.without_running_lines({1: ["Journal of AI 2024", "body one"], 2: ["Journal of AI 2024", "body two"], 3: ["Journal of AI 2024", "body three"]}) == {1: ["body one"], 2: ["body two"], 3: ["body three"]}
