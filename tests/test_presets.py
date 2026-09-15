"""Built-in presets in the declarative format: discovery, the schema row they emit, legacy
id aliases, and content-addressed schema ids. Stdlib-only; no DB."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import preset_spec as ps, presets  # noqa: E402


def test_all_presets_discovered() -> None:
    assert set(presets.load_all()) == {"masem-direct", "masem-indirect", "summarize", "human-ai-collab"}


def test_schema_row_carries_spec_and_legacy_grammar() -> None:
    fd = presets.emit_schema_row("masem-direct")
    assert fd["format"] == 2 and fd["spec"]["entries"]["key"] == "samples"
    assert [c["key"] for c in fd["spec"]["entries"]["children"]] == ["records"]
    # the grammar the current review UI reads is synthesised from the same declaration
    assert [sv["label"] for sv in fd["sub_views"]] == ["Effect sizes", "Descriptives"]
    assert "records" in fd["sub_views"][0]["include_keys"]
    assert fd["field_types"]["pubtype"]["type"] == "select" and 1 in fd["field_types"]["pubtype"]["options"]
    assert fd["confidence_keys"] == ["effect_sizes", "reliabilities", "metadata"]
    assert fd["mode"] == "extraction" and fd["schema_id"] == ps.schema_id(fd["spec"])


def test_indirect_uses_long_format_tables() -> None:
    fd = presets.emit_schema_row("masem-indirect")
    fields = {f["name"]: f for f in fd["spec"]["entries"]["fields"]}
    assert fields["factor_loadings"]["type"] == "table"
    assert [c["name"] for c in fields["factor_loadings"]["columns"]] == ["item", "factor", "loading"]
    assert [c["name"] for c in fields["factor_correlations"]["columns"]] == ["factor_a", "factor_b", "r"]
    assert [t["id"] for t in fd["spec"]["display"]["tabs"]] == ["loadings", "correlations", "descriptives"]
    assert fd["spec"]["meta"]["hidden"] is True          # reached from the MASEMiner builder, not the picker


def test_legacy_preset_ids_still_resolve() -> None:
    # Documents extracted before the rename carry `masem@v3` / `masem-ncs18@v1` in
    # record.schema_id, and add-papers re-resolves the prompt by that id.
    assert presets.get("masem")["id"] == "masem-direct"
    assert presets.get("masem-ncs18")["id"] == "masem-indirect"
    assert presets.emit_schema_row("masem")["preset_id"] == "masem-direct"
    assert presets.prompt_for("masem-ncs18")
    for pid in presets.ADAPTER_ONLY_IDS:
        assert presets.get(pid) is None                  # files gone; rows served by the adapter


def test_indirect_preset_is_scale_agnostic() -> None:
    tp = presets.get("masem-indirect")["template_params"]
    # the generic placeholder the original template used; the builder treats it as "unset"
    assert tp.get("scale_name") == "the target instrument" and tp.get("item_texts") == [] and tp.get("n_items") is None


def test_masem_prompts_are_frozen_and_declared_presets_generate() -> None:
    # MASEMiner keeps its original, hand-written prompt: no generated section at all, the
    # original schema / evidence / confidence sections verbatim.
    p = presets.prompt_for("masem-direct")
    assert p.startswith("# TASK") and "(generated" not in p
    for section in ("# EVIDENCE RULES", "# SELF-ASSESS EXTRACTION CONFIDENCE", "# OUTPUT SCHEMA", "# VALIDATION RULES"):
        assert section in p
    assert "samples[0].records[2].es" in p and '"effect_sizes":' in p and '"reliabilities":' in p
    q = presets.prompt_for("masem-indirect")
    assert "(generated" not in q and '"factor_loadings": [' in q and "F1.2" not in q
    # a preset built on the declaration gets the generated sections
    h = presets.prompt_for("human-ai-collab")
    for section in ("# OUTPUT SCHEMA (generated", "# EVIDENCE (generated)", "# CONFIDENCE (generated)",
                    "# RETURN FORMAT (generated)"):
        assert section in h
    # builder parameters flow into the author text
    custom = presets.prompt_for("masem-direct", params={
        "effect_sizes": [{"code": "smd", "label": "Standardised mean difference"}],
        "variables": [{"name": "bm", "definition": "body mass", "synonyms": ["BMI"]}]})
    assert '- "smd" = Standardised mean difference' in custom
    assert '"bm"' in custom and "BMI" in custom
    assert "bm" in custom.split("# EXTRACTION RULES")[0]  # in the DOMAIN CONFIGURATION block


def test_schema_id_is_content_addressed() -> None:
    spec = presets.load_all()["masem-direct"]
    sid = ps.schema_id(spec)
    assert sid.startswith("masem-direct@") and len(sid.split("@")[1]) == 8
    assert sid == presets.schema_id_for("masem-direct") == presets.schema_id_for("masem")
    # wording and layout do not re-version; the data contract does
    import copy
    same = copy.deepcopy(spec); same["display"]["tabs"][0]["label"] = "ES"; same["meta"]["title"] = "X"
    same["entries"]["fields"][0]["help"] = "other words"; same["prompt"]["text"] = "different"
    assert ps.schema_id(same) == sid
    changed = copy.deepcopy(spec); changed["entries"]["fields"].append({"name": "extra", "type": "string"})
    assert ps.schema_id(ps.normalize(changed)) != sid


def test_unknown_preset_returns_none() -> None:
    assert presets.emit_schema_row("does-not-exist") is None
    assert presets.prompt_for("does-not-exist") is None
    assert presets.render("does-not-exist") is None


def test_meta_brands_is_validated_and_exposed() -> None:
    assert presets.get("summarize")["brands"] == ["metalens"]
    assert presets.get("masem-direct")["brands"] == []                 # untagged: every surface
    spec = presets.load_all()["summarize"]
    bad = dict(spec); bad["meta"] = {**spec["meta"], "brands": "metalens"}
    errs, _ = ps.validate(bad)
    assert any("meta.brands" in e for e in errs)
