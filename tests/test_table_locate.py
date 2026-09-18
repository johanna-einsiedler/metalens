"""A number inside a table the preset lists under ``display.locate`` is pinpointed on the
printed row of its anchor text (an item's wording), so a loading that occurs several times
on the page lights up once, on the right row. Offline: a generated PDF, no database."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import copy  # noqa: E402

from paperlens import pdf_utils, preset_spec, presets  # noqa: E402


def _table_pdf() -> bytes:
    import fitz
    d = fitz.open(); page = d.new_page()
    rows = [(".714", "I often use social media and social networking sites"),
            (".699", "I would prefer complex to simple problems."),
            (".714", "It’s enough for me that something gets the job done; I don’t care how or why it works."),
            (".753", "I would prefer a task that is intellectual, difficult, and important")]
    for k, (fl, text) in enumerate(rows):
        y = 100 + 14 * k
        page.insert_text((72, y), fl, fontsize=8); page.insert_text((120, y), text, fontsize=8)
    out = d.tobytes(); d.close()
    return out


def test_a_repeated_loading_is_found_on_the_row_of_its_item() -> None:
    pdf = _table_pdf()
    everywhere = pdf_utils.locate_value_rects(pdf, 1, 0.714)
    assert len(everywhere) == 2                                        # ambiguous on the page
    item = "It's enough for me that something gets the job done; I don't care how or why it works.*"
    bands = pdf_utils.anchor_bands(pdf, 1, item)                        # straight apostrophes, trailing marker
    on_row = pdf_utils.rects_in_bands(everywhere, bands)
    assert len(on_row) == 1 and on_row[0][1] == max(r[1] for r in everywhere)   # the lower of the two
    # a prefix shared by two items is ambiguous at that length, the full wording is not
    assert pdf_utils.anchor_bands(pdf, 1, "I would prefer complex to simple problems.")
    assert pdf_utils.anchor_bands(pdf, 1, "I would prefer") == []
    assert pdf_utils.anchor_bands(pdf, 1, "An item that is not printed anywhere here") == []


def test_display_locate_is_validated_and_not_hashed() -> None:
    spec = presets.load_all()["masem-indirect"]
    assert spec["display"]["locate"]["factor_loadings"] == {"anchor_column": "item", "anchor_param": "item_texts"}
    bare = copy.deepcopy(spec); bare["display"].pop("locate")
    assert preset_spec.schema_id(preset_spec.normalize(bare)) == preset_spec.schema_id(spec)   # display is not hashed
    bad = copy.deepcopy(spec); bad["display"]["locate"] = {"nfac": {}, "factor_loadings": {"anchor_column": "nope"}}
    errors = preset_spec.validate(bad)[0] if isinstance(preset_spec.validate(bad), tuple) else preset_spec.validate(bad)["errors"]
    assert any("not a table field" in e for e in errors) and any("anchor_column" in e for e in errors)


def test_an_effect_size_is_found_on_the_row_of_its_variable_label() -> None:
    """masem-direct: a correlation printed twice on the page is pinpointed on the row that
    carries the paper's name for one of the two variables (the record's desc1 / desc2)."""
    import fitz
    d = fitz.open(); page = d.new_page()
    for k, (label, vals) in enumerate([("1. Extraversion (self)", "1.00"), ("2. Extraversion (peer)", ".45  1.00"),
                                       ("3. Neuroticism (self)", "-.20  .45  1.00")]):
        page.insert_text((72, 100 + 14 * k), label, fontsize=8); page.insert_text((260, 100 + 14 * k), vals, fontsize=8)
    pdf = d.tobytes(); d.close()
    hits = pdf_utils.locate_value_rects(pdf, 1, 0.45)
    assert len(hits) == 2
    bands = pdf_utils.anchor_bands(pdf, 1, "Neuroticism (self)")
    on_row = pdf_utils.rects_in_bands(hits, bands)
    assert len(on_row) == 1 and on_row[0][1] == max(r[1] for r in hits)
    assert pdf_utils.anchor_bands(pdf, 1, "Extraversion") == []                     # two rows carry it: ambiguous
    spec = presets.load_all()["masem-direct"]
    assert spec["display"]["locate"] == {"records": {"anchor_fields": ["desc1", "desc2"]}} and spec["display"]["citation_flash"] is False
    bad = copy.deepcopy(spec); bad["display"]["locate"] = {"records": {"anchor_fields": ["nope"]}}
    res = preset_spec.validate(bad); errors = res[0] if isinstance(res, tuple) else res["errors"]
    assert any("anchor_fields" in e for e in errors)


def test_triangular_matrix_row_choice() -> None:
    """Both variables' rows show the value: the pair's cell is on the row with more numbers."""
    import fitz
    d = fitz.open(); page = d.new_page()
    for k, (label, vals) in enumerate([("1. Extraversion (self)", "1.00"), ("2. Extraversion (peer)", ".45  1.00"),
                                       ("3. Neuroticism (self)", "-.20  .45  1.00")]):
        page.insert_text((72, 100 + 14 * k), label, fontsize=8); page.insert_text((260, 100 + 14 * k), vals, fontsize=8)
    pdf = d.tobytes(); d.close()
    peer = pdf_utils.anchor_bands(pdf, 1, "Extraversion (peer)")[0]; neuro = pdf_utils.anchor_bands(pdf, 1, "Neuroticism (self)")[0]
    assert pdf_utils.numbers_in_band(pdf, 1, neuro) > pdf_utils.numbers_in_band(pdf, 1, peer) >= 2
