"""Coder marks: the sentence under a click on a page.

A coder reading a paper marks the sentences that bear on a judgement (the complexity of a
task, say) without tying them to a cell. The page's words — the same text layer the quote
locator uses — give the word under the click, and the sentence is that word expanded within
its paragraph: backwards and forwards to a boundary, a word ending in ``.``, ``!`` or ``?``
(closing quotes and brackets allowed) followed by a word that opens with a capital or a
digit; a short abbreviation (``e.g.``, ``et al.``, ``Fig.``) is not an end. When the
paragraph holds no boundary at all (a heading, a table cell, a caption) the mark is the whole
paragraph, or the clicked line when the paragraph is long. Rectangles are one per line of the
sentence, in the viewer's image pixels like the evidence rectangles."""
from __future__ import annotations

import re

from .pdf_utils import DISPLAY_DPI

# words whose trailing period ends no sentence
_ABBREV = {"e.g.", "i.e.", "al.", "fig.", "figs.", "eq.", "eqs.", "vs.", "cf.", "no.", "nos.", "dr.", "prof.",
           "mr.", "ms.", "mrs.", "st.", "ca.", "approx.", "resp.", "ref.", "refs.", "sec.", "ch.", "vol.", "pp.", "p.", "et"}
_CLOSERS = "\"'”’)]"
_END = re.compile(r"[.!?][\"'”’)\]]*$")
_LEAD = re.compile(r"^[\"'“‘(\[]+")
LONG_PARAGRAPH = 40          # words: a boundary-less paragraph longer than this marks the clicked line only
TOLERANCE = 4.0              # points: how far from a word a click may land and still pick it
DRAG_TOLERANCE = 14.0        # points: a drag's ends may land further off (between lines, past the last word)


def ends_sentence(word: str) -> bool:
    w = (word or "").strip()
    if not _END.search(w):
        return False
    core = w.rstrip(_CLOSERS)
    if core.lower() in _ABBREV or re.fullmatch(r"[A-Z]\.", core):      # "e.g." · an initial "J."
        return False
    return True


def starts_sentence(word: str) -> bool:
    w = _LEAD.sub("", (word or "").strip())
    return bool(w) and (w[0].isupper() or w[0].isdigit())


def is_boundary(a: str, b: str) -> bool:
    """A sentence ends between words ``a`` and ``b``."""
    return ends_sentence(a) and starts_sentence(b)


def word_at(words: list, x: float, y: float, *, tolerance: float = TOLERANCE):
    """The word whose box holds the point, else the nearest within ``tolerance``; None if none.
    ``words`` are PyMuPDF word tuples ``(x0, y0, x1, y1, text, block, line, wordno)``."""
    best, best_d = None, None
    for w in words:
        x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
        dx = 0.0 if x0 <= x <= x1 else min(abs(x - x0), abs(x - x1))
        dy = 0.0 if y0 <= y <= y1 else min(abs(y - y0), abs(y - y1))
        d = (dx * dx + dy * dy) ** 0.5
        if d == 0.0:
            return w
        if d <= tolerance and (best_d is None or d < best_d):
            best, best_d = w, d
    return best


def _join(span: list) -> str:
    """The words as text; a word hyphenated at a line end joins the next word."""
    out: list[str] = []
    for i, w in enumerate(span):
        t = str(w[4])
        nxt = span[i + 1] if i + 1 < len(span) else None
        if nxt is not None and t.endswith("-") and len(t) > 1 and nxt[6] != w[6] and str(nxt[4])[:1].islower():
            out.append(t[:-1])
            out.append("\x00")                       # glue marker: no space before the next word
        else:
            out.append(t)
    return " ".join(out).replace(" \x00 ", "")


def _line_rects(span: list) -> list[list[float]]:
    """One rectangle per text line of the span, in PDF points as [x0, y0, x1, y1]."""
    rects: list[list[float]] = []
    key = None
    for w in span:
        k = (w[5], w[6])
        if k != key:
            rects.append([w[0], w[1], w[2], w[3]])
            key = k
        else:
            r = rects[-1]
            r[0], r[1], r[2], r[3] = min(r[0], w[0]), min(r[1], w[1]), max(r[2], w[2]), max(r[3], w[3])
    return rects


def sentence_at(words: list, x: float, y: float, *, tolerance: float = TOLERANCE) -> dict | None:
    """The sentence holding the point: ``{text, rects, words}`` (rects in points, [x0, y0, x1, y1]
    per line), or None when no word is near the point."""
    hit = word_at(words, x, y, tolerance=tolerance)
    if hit is None:
        return None
    block = [w for w in words if w[5] == hit[5]]
    block.sort(key=lambda w: (w[6], w[7]))
    k = next(i for i, w in enumerate(block) if w is hit or (w[0], w[1], w[2], w[3], w[4]) == (hit[0], hit[1], hit[2], hit[3], hit[4]))
    j, found_start = k, False
    while j > 0:
        if is_boundary(str(block[j - 1][4]), str(block[j][4])):
            found_start = True
            break
        j -= 1
    m, found_end = k, False
    while m < len(block) - 1:
        if is_boundary(str(block[m][4]), str(block[m + 1][4])):
            found_end = True
            break
        m += 1
    if found_start or found_end:
        span = block[j:m + 1]
    elif len(block) <= LONG_PARAGRAPH:
        span = block                                  # a heading, a caption, a short cell: the paragraph
    else:
        span = [w for w in block if w[6] == hit[6]]   # a long run without a boundary: the clicked line
    return {"text": _join(span), "rects": _line_rects(span), "words": span}


def span_between(words: list, x: float, y: float, x2: float, y2: float, *, tolerance: float = DRAG_TOLERANCE) -> dict | None:
    """The words from the one under ``(x, y)`` to the one under ``(x2, y2)``, in reading order
    (block, line, word) whichever end comes first — a drag across the page: ``{text, rects,
    words}``, or None when either end is near no word."""
    a = word_at(words, x, y, tolerance=tolerance)
    b = word_at(words, x2, y2, tolerance=tolerance)
    if a is None or b is None:
        return None
    order = sorted(words, key=lambda w: (w[5], w[6], w[7]))
    ia = next(i for i, w in enumerate(order) if w is a)
    ib = next(i for i, w in enumerate(order) if w is b)
    if ia > ib:
        ia, ib = ib, ia
    span = order[ia:ib + 1]
    return {"text": _join(span), "rects": _line_rects(span), "words": span}


def mark_at(pdf_bytes: bytes, page_1indexed: int, x_px: float, y_px: float, *, x2_px: float | None = None,
            y2_px: float | None = None, dpi: int = DISPLAY_DPI) -> dict | None:
    """The sentence under a click at image pixel ``(x_px, y_px)`` of a page of the PDF — or,
    with ``(x2_px, y2_px)``, the words from the first point to the second — with its rectangles
    as the viewer draws them: ``{page, text, rects: [[x, y, w, h], …]}``; None when the page
    has no word there."""
    import fitz
    scale = dpi / 72.0
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if page_1indexed < 1 or page_1indexed > len(doc):
            return None
        words = doc[page_1indexed - 1].get_text("words")
    finally:
        doc.close()
    if x2_px is not None and y2_px is not None:
        hit = span_between(words, x_px / scale, y_px / scale, x2_px / scale, y2_px / scale)
    else:
        hit = sentence_at(words, x_px / scale, y_px / scale)
    if hit is None:
        return None
    rects = [[round(x0 * scale, 2), round(y0 * scale, 2), round((x1 - x0) * scale, 2), round((y1 - y0) * scale, 2)]
             for x0, y0, x1, y1 in hit["rects"]]
    return {"page": page_1indexed, "text": hit["text"], "rects": rects}
