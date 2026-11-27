"""The robustness chain: the paper-reading half of the pipeline as a Metalens chain.

A Metalens chain is a package whose ``register()`` returns its entries, keyed ``<chain>.<name>``:
PASSES that call the model over the paper and write a JSON output in their own shape, MATCHERS that
fold a pass's repeated runs into one, COMPUTES that write data from the passes' outputs, and CHECKS
over pass outputs (``paperlens/chains/__init__.py`` in the Metalens repository). A pass is pinned by
the hash of its prompt files and schema, so a prompt edit re-versions every workflow that names it.

What this package holds is the project's own content: the prompts of the five model-calling stages
(``prompts/``: classify, inventory, extract_table, extract_session, stated_claims_v2, claim_map_v1,
leak_judge), their JSON schemas rendered from ``pipeline/schema.py`` (``schemas/``, by
``sync/render_schemas.py``), and, as they are ported, the deterministic steps the pipeline and
Metalens must share: assembly of the per-paper document (``computes.assemble``), headline
regressions by the claim chain (``computes.headlines``), blinding (``computes.blind``), grading at
printed precision (``computes.grade``), the completeness check (``checks.completeness``) and the
fold of k extractions (``matchers.fold_extractions``).

Rules. The pipeline imports this package, never the reverse. The package imports nothing that needs
Stata, pydantic or PyMuPDF, so Metalens's worker can run it. The pipeline's stage scripts are thin
command lines around these functions once a step is ported; until then ``register()`` is empty for
that step and the stage script holds the implementation.

Status (2026-11-27): prompts and schemas in place; passes, computes, checks and matchers are stubs
(``registrations()`` returns ``{}``). Metalens still runs the bundled ``econ-*`` workflows over its
own presets; ``sync/render.py`` ships this package into a checkout once the chain loader exists.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

CHAIN_ID = "robustness"
HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"
SCHEMAS = HERE / "schemas"

# pass name -> (prompt file, schema file); the file names are the pipeline's, unchanged by the move,
# so the hashes recorded in the run records (run_manifest.json, cost rows) still identify them.
PASSES = {
    "classify": ("classify.md", "classify.schema.json"),
    "inventory": ("inventory.md", "inventory.schema.json"),
    "extract_table": ("extract_table.md", "extract_table.schema.json"),
    "extract_session": ("extract_session.md", "extract_session.schema.json"),
    "stated_claims": ("stated_claims_v2.md", "stated_claims.schema.json"),
    "claim_map": ("claim_map_v1.md", "claim_map.schema.json"),
    "leak_judge": ("leak_judge.md", "leak_judge.schema.json"),
}


@lru_cache(maxsize=None)
def prompt(name: str) -> str:
    """The prompt text of a pass, by pass name or by file name."""
    f = PASSES[name][0] if name in PASSES else name
    return (PROMPTS / f).read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def schema(name: str) -> dict:
    f = PASSES[name][1] if name in PASSES else name
    return json.loads((SCHEMAS / f).read_text(encoding="utf-8"))


def pin(name: str) -> str:
    """``<name>@<sha8>`` over the prompt text and the schema, the way Metalens pins a pass."""
    h = hashlib.sha256((prompt(name) + json.dumps(schema(name), sort_keys=True)).encode("utf-8")).hexdigest()[:8]
    return f"{name}@{h}"


def pins() -> dict[str, str]:
    return {n: pin(n) for n in PASSES}


def register() -> dict[str, dict]:
    from . import checks, computes, matchers, passes
    return {**passes.registrations(), **matchers.registrations(), **computes.registrations(), **checks.registrations()}
