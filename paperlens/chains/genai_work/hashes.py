"""The chain's versions, computed the way the review project computes them (``split.split_hash``,
``split.design_hash``, ``claims.claims_hash``) over the prompt and schema files of this package —
so a prompt file that differs from the project's by one byte shows as a different hash, and the
tests pin the hashes the project runs under.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from . import taxonomy

PROMPTS = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.system.md").read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def short(name: str) -> str:
    return (PROMPTS / f"{name}.short.md").read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def schema(name: str) -> dict:
    return json.loads((PROMPTS / f"{name}.schema.json").read_text(encoding="utf-8"))


def _h(parts: list) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:10]


def split_hash() -> str:
    """The version of passes b, a and c — the project's ``split.split_hash``."""
    return _h([prompt("exhibits"), schema("exhibits"), prompt("estimands"), schema("estimands"), prompt("estimates"), schema("estimates"),
               taxonomy.model(), taxonomy.inputs_version()])


def design_hash() -> str:
    """The version of pass d — the project's ``split.design_hash``."""
    return _h([prompt("design"), schema("design"), taxonomy.model(), taxonomy.schema_hash(), taxonomy.inputs_version()])


def claims_hash() -> str:
    return _h([prompt("claims_abstract"), schema("claims_abstract"), prompt("claims_map"), schema("claims_map"), taxonomy.model()])


def exported() -> dict:
    """The hashes the export recorded, to compare with what the files give."""
    return dict(taxonomy.snapshot().get("hashes") or {})
