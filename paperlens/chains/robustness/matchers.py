"""Folding k runs of a pass into one (to be ported, plan step 3).

| matcher | pipeline source | does |
|---|---|---|
| fold_extractions | s07 `extraction_retest.py` (`pair`, `ckey`, `same`): tables paired by number and panel, columns by position and outcome, cells by row label and type; a cell agrees when the printed strings match after the normalisation the three-way stability measure used | the primary extraction with, per cell, how many runs agree and the candidates they gave |

A matcher takes ``(ctx, outputs)`` and returns ``{"primary": <output>, "stability": {...}}``
(``_demo._majority`` in the Metalens repository is the minimal one).
"""
from __future__ import annotations


def registrations() -> dict[str, dict]:
    return {}
