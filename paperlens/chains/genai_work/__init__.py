"""The GenAI-at-work extraction chain inside Metalens: the review project's passes, deterministic
steps, checks and matchers as registered code, its prompts and taxonomy as files
(``scripts/export_genai_chain.py`` renders them from the project byte for byte). The passes,
matchers and computes register here as they are ported."""
from __future__ import annotations

CHAIN_ID = "genai_work"


def register() -> dict[str, dict]:
    from . import document, match, passes
    return {**passes.registrations(), **match.registrations(), **document.registrations()}
