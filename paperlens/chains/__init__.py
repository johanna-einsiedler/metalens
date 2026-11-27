"""The chains: registered code a workflow runs per paper — PASSES that may call the model and
write a JSON output in their own shape, MATCHERS that fold a pass's repeated runs into one,
COMPUTES that write data from the passes' outputs, and CHECKS over pass outputs. A chain is a
package under ``paperlens/chains/<chain>/`` whose ``register()`` returns its entries; the
project-specific knowledge (prompts, taxonomy, rules) is the chain's content, the engine knows
only this registry (the pattern of ``checks.REGISTRY``).

An entry is keyed ``<chain>.<name>``::

    {"kind": "pass", "label": "…", "prompts": {"system": "prompts/x.system.md", …},
     "schema": "prompts/x.schema.json", "reads": ["exhibits"], "ids": [...], "refs": [...],
     "k_ok": True, "post_record": False, "calls_per_paper": 1, "max_tokens": 60000,
     "inputs_version": "1", "params": {name: {type, default}}, "model": None, "fn": callable}

A pass is PINNED by its prompt files: ``pin(code)`` is ``<code>@<sha8>`` over the prompt texts,
the schema and ``inputs_version`` (what the code puts around them), so a prompt edit re-versions
every workflow that names the pass, the way a preset pin does.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
from typing import Any

CHAINS = ("_demo", "genai_work", "robustness")   # explicit, in import order; a chain adds itself here (robustness: rendered from the project's package)
KINDS = ("pass", "match", "compute", "check")
REGISTRY: dict[str, dict] = {}
_LOADED = False


def register(chain_id: str, items: dict[str, dict], base_dir: Path) -> None:
    for name, entry in items.items():
        code = f"{chain_id}.{name}"
        e = {"id": code, "chain": chain_id, "name": name, "base_dir": Path(base_dir), "kind": "pass", "label": name,
             "prompts": {}, "schema": None, "reads": [], "ids": [], "refs": [], "k_ok": False, "post_record": False,
             "calls_per_paper": 1, "max_tokens": 60_000, "inputs_version": "1", "params": {}, "model": None, "hidden": False}
        e.update(entry)
        if e["kind"] not in KINDS:
            raise ValueError(f"{code}: kind must be one of {', '.join(KINDS)}")
        if e["kind"] == "compute":
            e.setdefault("produces", "document")       # document: one per paper under a pinned preset | fields: edits on an earlier step's documents
            e.setdefault("unit", "paper")
            if e["produces"] not in ("document", "fields"):
                raise ValueError(f"{code}: produces must be 'document' or 'fields'")
        if not callable(e.get("fn")):
            raise ValueError(f"{code}: fn must be callable")
        REGISTRY[code] = e


def installed() -> list[str]:
    """The chains installed beside the engine: importable module names in ``PAPERLENS_CHAINS``
    (os.pathsep-separated), each a package with ``CHAIN_ID`` and ``register()`` like a bundled
    one — a project's own chain, installed with ``pip install -e``, run by a local Metalens."""
    return [m.strip() for m in (os.environ.get("PAPERLENS_CHAINS") or "").split(os.pathsep) if m.strip()]


def load() -> dict[str, dict]:
    """Every chain's entries, imported once: the bundled chains, then the installed ones. A
    bundled chain id wins — an installed chain of the same id is skipped, so a rendered copy
    in the engine and the package it came from never register twice."""
    global _LOADED
    if not _LOADED:
        ids: set[str] = set()
        for cid in CHAINS:
            mod = importlib.import_module(f"paperlens.chains.{cid}")
            register(cid, mod.register(), Path(mod.__file__).parent); ids.add(cid)
        for name in installed():
            mod = importlib.import_module(name)
            cid = str(getattr(mod, "CHAIN_ID", "") or name.rpartition(".")[2])
            if cid in ids:
                continue
            register(cid, mod.register(), Path(mod.__file__).parent); ids.add(cid)
        _LOADED = True
    return REGISTRY


def reset() -> None:
    """Forget the loaded chains (tests that change ``PAPERLENS_CHAINS``)."""
    global _LOADED
    REGISTRY.clear(); _LOADED = False


def get(code: str) -> dict | None:
    return load().get(code.partition("@")[0])


def of_kind(kind: str) -> dict[str, dict]:
    return {k: v for k, v in load().items() if v["kind"] == kind}


def prompt_text(code: str, name: str) -> str:
    e = get(code)
    if e is None or name not in e["prompts"]:
        raise KeyError(f"{code}: no prompt {name!r}")
    return (e["base_dir"] / e["prompts"][name]).read_text(encoding="utf-8")


def schema(code: str) -> dict | None:
    e = get(code)
    if e is None or not e.get("schema"):
        return None
    return json.loads((e["base_dir"] / e["schema"]).read_text(encoding="utf-8"))


def prompt_sha(code: str) -> str:
    """sha256 over the pass's prompt files (by name), its schema and its inputs_version."""
    e = get(code)
    if e is None:
        raise KeyError(code)
    h = hashlib.sha256()
    for name in sorted(e["prompts"]):
        h.update(f"--- {name}\n".encode()); h.update(prompt_text(code, name).encode("utf-8"))
    sch = schema(code)
    h.update(b"--- schema\n"); h.update(json.dumps(sch, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") if sch is not None else b"null")
    h.update(b"--- inputs\n"); h.update(str(e.get("inputs_version") or "").encode("utf-8"))
    return h.hexdigest()


def pin(code: str) -> str:
    return f"{code.partition('@')[0]}@{prompt_sha(code)[:8]}"


def pin_ok(pinned: str) -> bool:
    """Whether ``<code>@<sha8>`` names a registered entry at its current prompt sha."""
    code, _, sha = pinned.partition("@")
    e = get(code)
    return e is not None and bool(sha) and prompt_sha(code).startswith(sha)


def public(e: dict) -> dict[str, Any]:
    """An entry as the API shows it (no callables, no paths)."""
    return {k: v for k, v in e.items() if k not in ("fn", "base_dir")} | {"pin": pin(e["id"]) if e["kind"] == "pass" else None}
