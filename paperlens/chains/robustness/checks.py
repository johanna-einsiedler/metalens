"""Checks over pass outputs (to be ported, plan step 3).

| check | pipeline source | finds |
|---|---|---|
| completeness | s07 `completeness_audit.py`: every table caption in the PDF's text layer whose region carries a regression signature (coefficient rows with standard errors in parentheses, N, R²) against the tables the extraction holds | a table that reports regressions and was not extracted |

A check function takes the engine's context and returns findings
(``{"path", "code", "severity", "message"}`` per finding, as ``paperlens.checks`` records them).
"""
from __future__ import annotations


def registrations() -> dict[str, dict]:
    return {}
