"""The horizon a result covers, as numbers — parsed from the label the paper printed.

A result's ``horizon`` field is the verbatim panel or column heading: "years 6–10",
"the fourth year after the event", "within five years after the first parental death".
Those are honest labels but only comparable as strings. The structured triple
``(horizon_lo, horizon_hi, horizon_kind)`` — years since the event, inclusive, plus what
the estimate IS over that window — makes them comparable across papers.

``parse()`` handles the regular forms deterministically and returns None for everything
else: calendar months ("January 2010"), cross-section years ("1980"), reform windows,
and any phrasing it has not seen. A None is filled by a person during review, or stays
null — a label with no event clock has no honest numbers. The parser never guesses:
wrongly equating a year-4 point with a 0–5 average is worse than a null.
"""
from __future__ import annotations

import re

_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
             "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}


def _num(tok: str) -> float | None:
    tok = tok.strip().lower()
    if tok in _WORDS:
        return float(_WORDS[tok])
    if tok in _ORDINALS:
        return float(_ORDINALS[tok])
    try:
        return float(tok)
    except ValueError:
        return None


_N = r"(\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_ORD = "|".join(_ORDINALS)
_DASH = r"[-–—]"                       # hyphen, en dash, em dash — papers use all three


def parse(text: str | None) -> tuple[float, float, str] | None:
    """(horizon_lo, horizon_hi, horizon_kind) for the regular phrasings; None otherwise.

    A pure function of the label. It reads only event-clock phrasings — years since the
    event — and refuses calendar time, so "January 2010" and "1980" come back None even
    though they contain digits.
    """
    if not text:
        return None
    t = re.sub(r"\s+", " ", str(text)).strip().lower()

    # "average over the five years after …", "five-year average" → the mean over [0, n]
    m = re.search(rf"average (?:effect )?over (?:the )?(?:first )?{_N}[- ]years?", t) \
        or re.search(rf"{_N}[- ]year average", t)
    if m:
        n = _num(m.group(1))
        return (0.0, n, "average") if n is not None else None

    # "within five years", "in the first five years" → cumulative over [0, n]
    m = re.search(rf"(?:within|in) (?:the )?(?:first )?{_N} years?", t)
    if m and "before" not in t:
        n = _num(m.group(1))
        return (0.0, n, "cumulative") if n is not None else None

    # "years 6–10", "years 0-1", "event years 2 to 5" → the average the panel reports
    m = re.search(rf"years? {_N} ?(?:{_DASH}|to) ?{_N}", t)
    if m and "before" not in t:
        lo, hi = _num(m.group(1)), _num(m.group(2))
        if lo is not None and hi is not None and lo <= hi:
            return (lo, hi, "average")

    # "the fourth year after the event", "fifth year after parental death" → a point
    m = re.search(rf"(?:the )?({_ORD}) year", t)
    if m and "before" not in t:
        n = float(_ORDINALS[m.group(1)])
        return (n, n, "point")

    # "year 5", "event time 10", "at year 4" → a point (but never a bare calendar year)
    m = re.search(rf"(?:event time|(?:at )?year) {_N}\b", t)
    if m and "before" not in t:
        n = _num(m.group(1))
        if n is not None and n < 100:              # "year 2010" is a date, not a horizon
            return (n, n, "point")

    return None
