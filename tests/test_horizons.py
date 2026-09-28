"""The horizon parser: regular event-clock phrasings become numbers; everything else — calendar
months, bare years, reform windows, pre-event windows — stays None rather than a guess."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens.horizons import parse  # noqa: E402


def test_the_regular_forms_from_the_nine_seed_papers() -> None:
    # every distinct parseable label the agents actually produced
    assert parse("years 6–10") == (6, 10, "average")
    assert parse("years 0–1") == (0, 1, "average")
    assert parse("years 2–5") == (2, 5, "average")
    assert parse("the fourth year after the event") == (4, 4, "point")
    assert parse("fifth year after parental death") == (5, 5, "point")
    # the window is clear; whether the number is a total or a per-year mean is not — the
    # parental-death paper uses this exact phrase for per-year means, so the kind is judgment
    assert parse("within five years after the first parental death") == (0, 5, None)
    assert parse("average over the five years after parental death") == (0, 5, "average")
    assert parse("event time 10") == (10, 10, "point")
    assert parse("event time 20") == (20, 20, "point")


def test_the_disambiguation_the_kind_field_exists_for() -> None:
    """A point at year 5 and an average over 0–5 share numbers with different meanings."""
    assert parse("fifth year after parental death") == (5, 5, "point")
    assert parse("average over the five years after parental death") == (0, 5, "average")


def test_no_event_clock_means_none_not_a_guess() -> None:
    for label in ("January 2010", "November 2009", "1980", "2013",
                  "change from 2006 to 2007",                       # a calendar difference
                  "all months", "all months, nonshifters",
                  "medium run (post = 1 for periods 2 to 4)",       # periods, not years
                  "all reforms, three-year differences 1984-2005",
                  "1987 reform (1986-1989 three-year interval)",
                  "excluding November 2009, December 2009 and January 2010",
                  "years 1–4 before the first IVF treatment",       # pre-event window
                  None, ""):
        assert parse(label) is None, label


def test_dashes_case_and_digit_forms() -> None:
    assert parse("Years 2-5") == (2, 5, "average")                  # hyphen
    assert parse("years 2—5") == (2, 5, "average")                  # em dash
    assert parse("years 2 to 5") == (2, 5, "average")
    assert parse("year 4") == (4, 4, "point")
    assert parse("within 5 years") == (0, 5, None)
    assert parse("5-year average") == (0, 5, "average")
