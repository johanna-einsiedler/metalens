"""The sentence under a click (paperlens.marks): the word at the point expanded to its sentence
within the paragraph; boundaries at . ! ? before a capital or a digit; abbreviations spared; the
whole paragraph, or the clicked line of a long one, when there is no boundary; rectangles one per
line; a line-end hyphen joined."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import marks  # noqa: E402


def _para(lines: list[str], block: int, y0: float = 100.0, x0: float = 72.0, lh: float = 12.0, cw: float = 5.0) -> list:
    """Synthetic PyMuPDF word tuples (x0, y0, x1, y1, text, block, line, wordno) for a paragraph
    given as lines of text."""
    out = []
    for li, line in enumerate(lines):
        x = x0
        for wi, w in enumerate(line.split()):
            out.append((x, y0 + li * lh, x + len(w) * cw, y0 + li * lh + 10.0, w, block, li, wi))
            x += (len(w) + 1) * cw
    return out


def _center(w):
    return (w[0] + w[2]) / 2, (w[1] + w[3]) / 2


def _word(words, text, nth=0):
    hits = [w for w in words if w[4] == text]
    return hits[nth]


PARA = _para(["This is the first sentence. It runs on", "to the second line here. Then a third", "sentence ends it all!"], 0)
WORDS = PARA + _para(["A heading without end"], 1, y0=60.0) + _para([" ".join(f"w{i}" for i in range(20))] * 4, 2, y0=200.0)


def test_middle_sentence_bounded_both_sides() -> None:
    x, y = _center(_word(WORDS, "runs"))
    hit = marks.sentence_at(WORDS, x, y)
    assert hit["text"] == "It runs on to the second line here."
    assert len(hit["rects"]) == 2                               # two lines, one rectangle each
    (x0, y0, x1, y1), (x2, y2, x3, y3) = hit["rects"]
    assert y0 == 100.0 and y2 == 112.0 and x0 == _word(WORDS, "It")[0] and x3 == _word(WORDS, "here.")[2]


def test_first_and_last_sentences_stop_at_the_paragraph_edges() -> None:
    x, y = _center(_word(WORDS, "first"))
    assert marks.sentence_at(WORDS, x, y)["text"] == "This is the first sentence."
    x, y = _center(_word(WORDS, "ends"))
    assert marks.sentence_at(WORDS, x, y)["text"] == "Then a third sentence ends it all!"


def test_a_short_paragraph_without_a_boundary_is_marked_whole() -> None:
    x, y = _center(_word(WORDS, "heading"))
    hit = marks.sentence_at(WORDS, x, y)
    assert hit["text"] == "A heading without end" and len(hit["rects"]) == 1


def test_a_long_paragraph_without_a_boundary_marks_the_clicked_line() -> None:
    w = [w for w in WORDS if w[5] == 2 and w[6] == 2][3]        # a word on the third of four lines
    hit = marks.sentence_at(WORDS, *_center(w))
    assert hit["text"] == " ".join(f"w{i}" for i in range(20)) and len(hit["rects"]) == 1 and hit["rects"][0][1] == 224.0


def test_abbreviations_and_initials_do_not_end_a_sentence() -> None:
    words = _para(["Dell'Acqua et al. (2023) found gains, e.g. in quality. Smith J. Doe agreed."], 0)
    assert marks.sentence_at(words, *_center(_word(words, "found")))["text"] == "Dell'Acqua et al. (2023) found gains, e.g. in quality."
    assert marks.sentence_at(words, *_center(_word(words, "Doe")))["text"] == "Smith J. Doe agreed."
    assert marks.is_boundary("quality.", "Smith") and not marks.is_boundary("al.", "(2023)") and not marks.is_boundary("e.g.", "in")
    assert marks.is_boundary("end.”", "Next") and not marks.is_boundary("end.", "next")   # closing quote allowed; lowercase continues


def test_a_point_off_the_text_gives_nothing_but_a_near_miss_counts() -> None:
    assert marks.sentence_at(WORDS, 400.0, 400.0) is None
    w = _word(WORDS, "first")
    assert marks.sentence_at(WORDS, w[0] - 2.0, w[1] - 2.0)["text"] == "This is the first sentence."   # 2pt off the corner


def test_a_line_end_hyphen_is_joined() -> None:
    words = _para(["Knowledge work needs knowl-", "edge of the task. Done."], 0)
    assert marks.sentence_at(words, *_center(_word(words, "work")))["text"] == "Knowledge work needs knowledge of the task."


def test_mark_at_on_a_real_page_scales_to_the_viewer() -> None:
    import fitz
    from paperlens.pdf_utils import DISPLAY_DPI
    d = fitz.open(); pg = d.new_page()
    pg.insert_text((72, 90), "First sentence here. Second one follows! Third ends.", fontsize=10)
    pdf = d.tobytes(); d.close()
    doc = fitz.open(stream=pdf, filetype="pdf"); words = doc[0].get_text("words"); doc.close()
    w = next(w for w in words if w[4] == "Second")
    scale = DISPLAY_DPI / 72.0
    hit = marks.mark_at(pdf, 1, (w[0] + w[2]) / 2 * scale, (w[1] + w[3]) / 2 * scale)
    assert hit["page"] == 1 and hit["text"] == "Second one follows!" and len(hit["rects"]) == 1
    x, y, wd, ht = hit["rects"][0]
    assert abs(x - w[0] * scale) < 0.5 and wd > 0 and ht > 0
    assert marks.mark_at(pdf, 1, 5.0, 5.0) is None and marks.mark_at(pdf, 7, 10.0, 10.0) is None
