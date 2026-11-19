#!/usr/bin/env python3
"""Render the GenAI-at-work chain's prompts, schemas and taxonomy snapshot from the review
project's own code into ``paperlens/chains/genai_work/`` — byte for byte what the local chain
sends — so the chain inside Metalens runs the same prompts and its pins are the hashes of these
files. Re-run after a prompt, a codebook or a taxonomy change in the project; the tests assert
the resulting split_hash and design_hash.

    .venv/bin/python scripts/export_genai_chain.py --project /path/to/49_AI_review

Reads the project only (its ``scripts/extraction`` package and ``labeling/headline_plan/
trial_plan.py``); writes nothing there.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent / "paperlens" / "chains" / "genai_work"


def load_project(root: Path):
    sys.path.insert(0, str(root))
    from scripts.extraction import claims, llm, match_runs, split  # type: ignore  # noqa: PLC0415
    from scripts.extraction import schema as S  # type: ignore  # noqa: PLC0415
    from scripts.extraction import taxonomy as T  # type: ignore  # noqa: PLC0415
    return claims, llm, match_runs, split, S, T, trial_plan_constants(root)


_PLAN_NAMES = ("RUBRIC", "TASK_EVIDENCE_PROMPT", "TASK_EVIDENCE_SCHEMA", "UNIT_ATTR_PROMPT", "UNIT_ATTR_SCHEMA", "ATOMIC_PROMPT", "ATOMIC_SCHEMA",
               "DISC_PROMPT", "CHAIN_PROMPT", "CHAIN_SCHEMA")


def trial_plan_constants(root: Path):
    """The headline plan is a straight-line script (it reads sys.argv and runs at import): its
    prompt and schema constants are taken from the source, each assignment evaluated on its own
    with ``ROOT`` and ``Path`` in scope, nothing else of the script run."""
    import ast
    from types import SimpleNamespace
    src = (root / "labeling" / "headline_plan" / "trial_plan.py").read_text(encoding="utf-8")
    ns: dict = {"ROOT": root, "Path": Path}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in _PLAN_NAMES:
            ns[node.targets[0].id] = eval(compile(ast.Expression(node.value), "trial_plan.py", "eval"), ns)  # noqa: S307 - the project's own constants
    missing = [n for n in _PLAN_NAMES if n not in ns]
    if missing:
        raise SystemExit(f"trial_plan.py: constants not found: {', '.join(missing)}")
    return SimpleNamespace(**{n: ns[n] for n in _PLAN_NAMES})


def files(claims, match_runs, split, tp) -> dict[str, str | dict]:
    return {
        "exhibits.system.md": split.EXHIBITS_PROMPT, "exhibits.schema.json": split.EXHIBITS_SCHEMA, "exhibits.short.md": split.SHORT_B,
        "estimands.system.md": split.estimands_prompt(), "estimands.schema.json": split.ESTIMANDS_SCHEMA, "estimands.short.md": split.SHORT_A,
        "estimates.system.md": split.estimates_prompt(), "estimates.schema.json": split.ESTIMATES_SCHEMA, "estimates.short.md": split.SHORT_C,
        "design.system.md": split.design_prompt(), "design.schema.json": split.DESIGN_SCHEMA,
        "claims_abstract.system.md": claims.ABSTRACT_PROMPT, "claims_abstract.schema.json": claims.ABSTRACT_SCHEMA,
        "claims_map.system.md": claims.MAP_PROMPT, "claims_map.schema.json": claims.MAP_SCHEMA,
        "plan_task_evidence.system.md": tp.TASK_EVIDENCE_PROMPT, "plan_task_evidence.schema.json": tp.TASK_EVIDENCE_SCHEMA,
        "plan_unit_attr.system.md": tp.UNIT_ATTR_PROMPT, "plan_unit_attr.schema.json": tp.UNIT_ATTR_SCHEMA,
        "plan_atomic.system.md": tp.ATOMIC_PROMPT, "plan_atomic.schema.json": tp.ATOMIC_SCHEMA,
        "plan_discussion.system.md": tp.DISC_PROMPT,
        "plan_chain.system.md": tp.CHAIN_PROMPT, "plan_chain.schema.json": tp.CHAIN_SCHEMA,
        "match_runs.system.md": match_runs.SYSTEM, "match_runs.schema.json": match_runs.SCHEMA,
    }


def snapshot(claims, llm, split, S, T) -> dict:
    return {"schema_hash": T.schema_hash(), "model": llm.MODEL, "inputs_version": split.INPUTS_VERSION, "levels": list(T.LEVELS),
            "kind_finding": dict(T.KIND_FINDING), "quote_groups": {k: list(v) for k, v in S.QUOTE_GROUPS.items()},
            "variables": T.variables(),
            "hashes": {"split": split.split_hash(), "design": split.design_hash(), "claims": claims.claims_hash()}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, help="the review project's root (holds scripts/extraction)")
    ap.add_argument("--out", default=str(PKG))
    a = ap.parse_args()
    claims, llm, match_runs, split, S, T, tp = load_project(Path(a.project).resolve())
    out = Path(a.out); (out / "prompts").mkdir(parents=True, exist_ok=True)
    n = 0
    for name, body in files(claims, match_runs, split, tp).items():
        text = body if isinstance(body, str) else json.dumps(body, indent=1, ensure_ascii=False) + "\n"
        (out / "prompts" / name).write_text(text, encoding="utf-8"); n += 1
    snap = snapshot(claims, llm, split, S, T)
    (out / "taxonomy.json").write_text(json.dumps(snap, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{n} prompt files + taxonomy.json ({len(snap['variables'])} variables, schema {snap['schema_hash']}) -> {out}")
    print(f"hashes: split {snap['hashes']['split']} · design {snap['hashes']['design']} · claims {snap['hashes']['claims']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
