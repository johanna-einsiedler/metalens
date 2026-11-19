"""The ``genai-work-passes`` check: the review project's whole issue list — the record checks
over the assembled record, the pass checks over the exhibits, estimands and estimates, the cell
checks and the readings' disagreements — as findings per paper, computed from the four pass
outputs the way the project's ``finish`` does (``record.finish``). Registered in the engine's
check registry as a check over PASS steps (exhibits, estimands, estimates, design, in that
order); each finding carries the paper's ``pdf_sha256`` and the project's path and code, a
``fail`` as ``error``.
"""
from __future__ import annotations

from . import record as R
from . import text as X
from .codes import CODES  # noqa: F401 - the registry's codes table

SEVERITY = {"fail": "error", "warn": "warn", "info": "info"}
PASSES = ("genai_work.exhibits", "genai_work.estimands", "genai_work.estimates", "genai_work.design")


def findings_of(sha: str, ex: dict, es: dict, em: dict, de: dict, pages: list[str], stability: dict[str, dict] | None = None) -> list[dict]:
    """One paper's findings: the project's issues over its record and passes, then the readings' issues."""
    fin = R.finish(ex, es, em, de, X.Pages(pages))
    out = [{"pdf_sha256": sha, "path": i.get("path"), "code": i.get("code"), "severity": SEVERITY.get(i.get("severity"), "warn"),
            "message": i.get("message"), "detail": {"source": "record" if not str(i.get("path") or "").startswith(("estimates[", "estimands[", "exhibits[")) else "passes"}}
           for i in fin["issues"]]
    for name, st in (stability or {}).items():
        if name == "estimates":
            continue                                   # the rows' disagreements are derived from the annotations above (record.runs_issues)
        for i in (st or {}).get("issues") or []:
            out.append({"pdf_sha256": sha, "path": f"{name}:{i.get('path')}", "code": i.get("code"), "severity": SEVERITY.get(i.get("severity"), "warn"),
                        "message": i.get("message"), "detail": {"source": f"runs:{name}"}})
    return out


def check_genai_passes(inputs: list[dict], params: dict) -> list[dict]:
    """The check over the four pass steps: ``inputs[i]`` holds ``outputs`` (by pdf sha), ``stability`` and ``pages``."""
    if len(inputs) < 4:
        return []
    ex_in, es_in, em_in, de_in = inputs[:4]
    out: list[dict] = []
    for sha, ex in (ex_in.get("outputs") or {}).items():
        es = (es_in.get("outputs") or {}).get(sha)
        em = (em_in.get("outputs") or {}).get(sha)
        de = (de_in.get("outputs") or {}).get(sha)
        if es is None or em is None or de is None:
            continue
        pages = (ex_in.get("pages") or {}).get(sha) or (es_in.get("pages") or {}).get(sha) or []
        stability = {inp.get("name") or inp.get("pass") or str(i): (inp.get("stability") or {}).get(sha) for i, inp in enumerate(inputs[:4])}
        out += findings_of(sha, ex, es, em, de, pages, {k: v for k, v in stability.items() if v})
    return out
