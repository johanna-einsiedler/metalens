"""The GenAI-at-work taxonomy as the chain knows it: a frozen snapshot (``taxonomy.json``, written
by ``scripts/export_genai_chain.py`` from the review project's ``taxonomy.py`` with its base sheet,
amendments and redefinitions applied) and the accessors the ported code reads — never the
project's live taxonomy, so the chain's behaviour moves only when the snapshot is re-exported.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "taxonomy.json"
JTYPE = {"string": "string", "text": "string", "integer": "integer", "number": "number", "boolean": "boolean",
         "enum": "string", "multi": "array", "list": "array"}


@lru_cache(maxsize=1)
def snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def schema_hash() -> str:
    return snapshot()["schema_hash"]


def model() -> str:
    return snapshot()["model"]


def inputs_version() -> str:
    return str(snapshot().get("inputs_version") or "")


LEVELS = ("study", "task", "arm", "outcome", "result")


def kind_finding() -> dict[str, str]:
    return dict(snapshot()["kind_finding"])


def quote_groups() -> dict[str, tuple[str, str]]:
    return {k: tuple(v) for k, v in snapshot()["quote_groups"].items()}


def variables() -> list[dict]:
    return snapshot()["variables"]


def by_level(level: str) -> list[dict]:
    return [v for v in variables() if v["level"] == level]


def field(name: str, level: str | None = None) -> dict:
    for v in variables():
        if v["field"] == name and (level is None or v["level"] == level):
            return v
    raise KeyError(name)


def options(name: str, level: str | None = None) -> list[str]:
    """The option codes of an enum or multi field."""
    return [o["code"] for o in (field(name, level)["options"] or [])]


def option_rows(name: str, level: str | None = None) -> list[dict]:
    return field(name, level)["options"] or []


def model_fields(level: str) -> list[dict]:
    """The fields the model fills at a level (not the code-, human- or summary-filled ones)."""
    return [v for v in by_level(level) if v["filled"] == "model"]


def wrapped(v: dict) -> bool:
    """Whether the field is returned as {value, ...}: fields with their own quote (other than the
    result cells that share a row quote) and open-vocabulary fields."""
    return (v["ev"] == "value" and not v["qg"]) or v["open"]


def term_mapping() -> dict[tuple[str, str], str]:
    """{(field, normalised term): code}: the free terms proposed for `other` that an accepted amendment folded into a code."""
    return {(m["field"], " ".join(str(m["term"]).split()).lower()): m["code"] for m in snapshot().get("mapping") or []}


def _val(c):
    return c.get("value") if isinstance(c, dict) else c


def mapped(field: str, cell):
    """A cell's code with the term mapping applied: `other` with a proposed term the mapping knows
    becomes that code; anything else is the cell's value as stored."""
    v = _val(cell)
    term = (cell.get("proposed") or "").strip() if isinstance(cell, dict) else ""
    if v == "other" and term:
        return term_mapping().get((field, " ".join(term.split()).lower()), v)
    return v


def ai_alone_arm(arm: dict) -> bool:
    """An arm in which no person did the work (the user, 20 Nov 2026): its participant type is
    ai_alone (the code or a mapped term), or its control type is ai_alone. Such arms are left out
    of the with-versus-without-AI analysis."""
    return _val(arm.get("control_type")) == "ai_alone" or mapped("sample_population_type", arm.get("sample_population_type")) == "ai_alone"


def groups(level: str) -> list[str]:
    seen: list[str] = []
    for v in by_level(level):
        if v["conf"] and v["conf"] not in seen:
            seen.append(v["conf"])
    return seen
