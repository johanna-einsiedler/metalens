"""PASS steps of a workflow: registered code (``paperlens.chains``) that runs per paper, may
call the model, reads the page-marked PDF text and the outputs of earlier passes of the same
paper, and writes one JSON output in its own shape. Outputs live in ``workflow_pass_output``
per (run, step, paper, pass_index) with the pass's prompt sha and an inputs sha; an output
made elsewhere under the same shas is reused without a call. A paper's passes run in order in
one job (``run_paper``): the key travels with that job (an own-key run) or is the server's (a
credit run); a pass that already has its output is skipped, so a retry resumes where the paper
stopped. The step barrier (``workflows._advance_pass``) is per step across papers.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from psycopg.types.json import Json

from . import chains, records, storage

DEFAULT_MARKER = "=== PAGE {i} ==="
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.S)


class Truncated(Exception):
    """The model's answer hit the output cap; ``text`` is what came back (the caller may ask
    again in a shorter form)."""

    def __init__(self, text: str):
        super().__init__("the answer was cut by the output cap")
        self.text = text


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_json(text: str) -> Any:
    s = _FENCE.sub("", text or "").strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        a, b = s.find("{"), s.rfind("}")
        if a >= 0 and b > a:
            return json.loads(s[a:b + 1])
        raise


@dataclass
class PassContext:
    """What a pass sees: the paper's pages, its identity, the earlier passes' outputs it declared
    (``read``), its parameters, and ``call`` to the model. ``generate`` (tests) replaces the
    provider: ``generate(system, user, schema, max_tokens) -> dict | str``."""
    entry: dict
    run_id: str
    step_id: str
    pdf_sha256: str
    pages: list[str]
    paper: dict
    params: dict
    reads: dict[str, dict]
    model: str
    api_key: str = ""
    base_url: str | None = None
    pass_index: int = 0
    generate: Callable | None = None
    store: Any = None
    calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"prompt": 0, "completion": 0, "total": 0})
    log_lines: list[str] = field(default_factory=list)
    raw: list[str] = field(default_factory=list)

    @property
    def title(self) -> str | None:
        return self.paper.get("title")

    @property
    def max_tokens(self) -> int:
        return int(self.entry.get("max_tokens") or 60_000)

    def text(self, marker: str = DEFAULT_MARKER) -> str:
        return "\n".join(f"{marker.format(i=i + 1)}\n{p}" for i, p in enumerate(self.pages))

    def prompt(self, name: str) -> str:
        return chains.prompt_text(self.entry["id"], name)

    def schema(self) -> dict | None:
        return chains.schema(self.entry["id"])

    def read(self, pass_name: str) -> dict:
        """The primary output of the earlier pass this one declared under ``reads``."""
        if pass_name not in self.reads:
            raise KeyError(f"{self.entry['id']} did not declare reads {pass_name!r} (or the workflow gave no step for it)")
        return self.reads[pass_name]

    def log(self, line: str) -> None:
        self.log_lines.append(str(line)[:500])

    def call(self, system: str, user: str, schema: dict | None, *, max_tokens: int | None = None) -> dict:
        """One JSON answer. Raises ``Truncated`` when the output cap cut it."""
        from . import providers
        cap = int(max_tokens or self.max_tokens)
        if self.generate is not None:
            res = self.generate(system, user, schema, cap)
            text, finish, usage, served = (res if isinstance(res, tuple) else (res if isinstance(res, str) else json.dumps(res), "stop", {}, "fake"))
        else:
            text, finish, usage, served = providers.generate_json(self.model, self.api_key, system=system, user=user, schema=schema,
                                                                 max_tokens=cap, base_url=self.base_url)
        n = len(self.calls)
        self.calls.append({"n": n, "kind": "pass", "step": self.step_id, "pass_index": self.pass_index, "model": self.model,
                           "resolved_model": served, "usage": usage, "finish_reason": finish, "chars": len(text or "")})
        for k in ("prompt", "completion", "total"):
            self.usage[k] = self.usage.get(k, 0) + int((usage or {}).get(k) or 0)
        self.raw.append(text or "")
        if finish == "length":
            raise Truncated(text or "")
        obj = parse_json(text or "")
        if not isinstance(obj, dict):
            raise ValueError("the pass's answer is not a JSON object")
        return obj


# ── what a paper is to a run ─────────────────────────────────────────────────

def paper_row(conn, run_id: str, pdf_sha256: str) -> dict | None:
    r = conn.execute("""SELECT run_id::text, pdf_sha256, filename, paper_id::text, n_pages, text_sha256, status, title, doi, abstract, journal
                        FROM workflow_paper WHERE run_id = %s::uuid AND pdf_sha256 = %s""", (run_id, pdf_sha256)).fetchone()
    if r is None:
        return None
    return {"run_id": r[0], "pdf_sha256": r[1], "filename": r[2], "paper_id": r[3], "n_pages": r[4], "text_sha256": r[5], "status": r[6],
            "title": r[7], "doi": r[8], "abstract": r[9], "journal": r[10]}


def papers_of(conn, run_id: str) -> list[dict]:
    rows = conn.execute("SELECT pdf_sha256 FROM workflow_paper WHERE run_id = %s::uuid ORDER BY created_at", (run_id,)).fetchall()
    return [paper_row(conn, run_id, r[0]) for r in rows]


def stage_paper(conn, run_id: str, pdf_bytes: bytes, *, filename: str | None, title: str | None = None, doi: str | None = None,
                abstract: str | None = None, journal: str | None = None, store=None) -> dict:
    """A PDF arrives for a run: keep it under the run, extract its page text once (content-addressed
    by the PDF's hash, shared across runs) and record the paper as ready."""
    from . import pdf_utils
    store = store or storage.get_store()
    sha = _sha(pdf_bytes)
    store.put(storage.run_pdf_key(run_id, sha), pdf_bytes, "application/pdf")
    tkey = storage.paper_text_key(sha)
    if store.exists(tkey):
        pages = json.loads(store.get(tkey).decode("utf-8"))
    else:
        pages = pdf_utils.page_texts(pdf_bytes)
        store.put(tkey, json.dumps(pages, ensure_ascii=False).encode("utf-8"), "application/json")
    with conn.transaction():
        conn.execute("""INSERT INTO workflow_paper (run_id, pdf_sha256, filename, n_pages, text_sha256, status, title, doi, abstract, journal)
                        VALUES (%s::uuid, %s, %s, %s, %s, 'ready', %s, %s, %s, %s)
                        ON CONFLICT (run_id, pdf_sha256) DO UPDATE SET filename = COALESCE(EXCLUDED.filename, workflow_paper.filename),
                          n_pages = EXCLUDED.n_pages, text_sha256 = EXCLUDED.text_sha256, status = 'ready',
                          title = COALESCE(EXCLUDED.title, workflow_paper.title), doi = COALESCE(EXCLUDED.doi, workflow_paper.doi),
                          abstract = COALESCE(EXCLUDED.abstract, workflow_paper.abstract), journal = COALESCE(EXCLUDED.journal, workflow_paper.journal)""",
                     (run_id, sha, filename, len(pages), _sha(_canon(pages)), title, doi, abstract, journal))
    return paper_row(conn, run_id, sha)


def page_texts_of(conn, pdf_sha256: str, store=None) -> list[str]:
    store = store or storage.get_store()
    key = storage.paper_text_key(pdf_sha256)
    if not store.exists(key):
        raise LookupError(f"no page text stored for {pdf_sha256[:12]}… (upload the PDF again)")
    return json.loads(store.get(key).decode("utf-8"))


# ── outputs ─────────────────────────────────────────────────────────

_OUT_COLS = ("id::text, run_id::text, step_id, code, pdf_sha256, pass_index, role, output, stability, prompt_sha256, inputs_sha256, model, usage, "
             "n_calls, findings, status, error, reused_from::text, supplied, log, created_at")


def _out_row(r, *, with_output: bool = True) -> dict:
    d = {"id": r[0], "run_id": r[1], "step_id": r[2], "code": r[3], "pdf_sha256": r[4], "pass_index": r[5], "role": r[6],
         "stability": r[8], "prompt_sha256": r[9], "inputs_sha256": r[10], "model": r[11], "usage": r[12], "n_calls": r[13],
         "findings": r[14], "status": r[15], "error": r[16], "reused_from": r[17], "supplied": r[18], "log": r[19] or [],
         "created_at": r[20].isoformat(timespec="seconds") if r[20] else None}
    if with_output:
        d["output"] = r[7]
    return d


def get_output(conn, run_id: str, step_id: str, pdf_sha256: str, *, role: str = "primary", pass_index: int = 0) -> dict | None:
    r = conn.execute(f"SELECT {_OUT_COLS} FROM workflow_pass_output WHERE run_id = %s::uuid AND step_id = %s AND pdf_sha256 = %s AND role = %s AND pass_index = %s",
                     (run_id, step_id, pdf_sha256, role, pass_index)).fetchone()
    return _out_row(r) if r else None


def outputs_of(conn, run_id: str, step_id: str, *, with_output: bool = False) -> list[dict]:
    rows = conn.execute(f"SELECT {_OUT_COLS} FROM workflow_pass_output WHERE run_id = %s::uuid AND step_id = %s ORDER BY created_at, pdf_sha256, role, pass_index",
                        (run_id, step_id)).fetchall()
    return [_out_row(r, with_output=with_output) for r in rows]


def find_reusable(conn, *, pdf_sha256: str, code: str, prompt_sha256: str, inputs_sha256: str, model: str, role: str = "primary") -> dict | None:
    """An output of this paper made under the same prompt, inputs and model by any run that was not
    cancelled: the same call would give the same answer up to the model's own variance."""
    r = conn.execute(
        f"""SELECT {', '.join('o.' + c for c in _OUT_COLS.split(', '))} FROM workflow_pass_output o JOIN workflow_run r ON r.id = o.run_id
            WHERE o.pdf_sha256 = %s AND o.code = %s AND o.prompt_sha256 = %s AND o.inputs_sha256 = %s AND o.model = %s AND o.role = %s
              AND o.status = 'done' AND r.status <> 'cancelled' ORDER BY o.created_at DESC LIMIT 1""",
        (pdf_sha256, code, prompt_sha256, inputs_sha256, model, role)).fetchone()
    return _out_row(r) if r else None


def store_output(conn, *, run_id: str, step_id: str, code: str, pdf_sha256: str, output: dict, prompt_sha256: str | None,
                 inputs_sha256: str | None, model: str | None, usage: dict | None, n_calls: int, role: str = "primary", pass_index: int = 0,
                 stability: dict | None = None, status: str = "done", error: str | None = None, reused_from: str | None = None,
                 supplied: bool = False, log: list[str] | None = None, raw: list[str] | None = None, store=None) -> dict:
    oid = str(uuid.uuid4())
    with conn.transaction():
        conn.execute("DELETE FROM workflow_pass_output WHERE run_id = %s::uuid AND step_id = %s AND pdf_sha256 = %s AND role = %s AND pass_index = %s",
                     (run_id, step_id, pdf_sha256, role, pass_index))
        conn.execute(
            """INSERT INTO workflow_pass_output (id, run_id, step_id, code, pdf_sha256, pass_index, role, output, stability, prompt_sha256, inputs_sha256,
                                                 model, usage, n_calls, status, error, reused_from, supplied, log)
               VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, %s, %s)""",
            (oid, run_id, step_id, code, pdf_sha256, pass_index, role, Json(output), Json(stability) if stability is not None else None,
             prompt_sha256, inputs_sha256, model, Json(usage) if usage is not None else None, n_calls, status, error, reused_from, supplied,
             Json(list(log or []))))
    if raw:
        st = store or storage.get_store()
        for i, t in enumerate(raw):
            try:
                st.put(storage.pass_raw_key(oid, i), (t or "").encode("utf-8"), "text/plain; charset=utf-8")
            except Exception:  # noqa: BLE001, S110 - a debugging copy never fails a pass
                pass
    return get_output(conn, run_id, step_id, pdf_sha256, role=role, pass_index=pass_index)


# ── running ───────────────────────────────────────────────────────────

def pass_steps(spec: dict) -> list[dict]:
    return [st for st in spec.get("steps") or [] if isinstance(st, dict) and st.get("kind") == "pass"]


def _reads_for(conn, run: dict, st: dict, pdf_sha256: str) -> dict[str, dict]:
    """{declared read name: the primary output of the workflow step that fills it}."""
    from . import workflow_spec as ws
    by_id = ws.steps_by_id(run["workflow"])
    out: dict[str, dict] = {}
    for sid in st.get("reads") or []:
        src = by_id.get(sid) or {}
        code = str(src.get("pass") or "").partition("@")[0]
        name = code.partition(".")[2]
        row = get_output(conn, run["id"], sid, pdf_sha256)
        if row is None or row["status"] != "done":
            raise LookupError(f"{st['id']} reads {sid!r}, which has no output for this paper yet")
        out[name] = row["output"]
    return out


def inputs_sha(pages: list[str], reads: dict[str, dict], params: dict, k: int, match: str | None) -> str:
    return _sha(_canon({"text": _sha(_canon(pages)), "reads": {k_: _sha(_canon(v)) for k_, v in sorted(reads.items())},
                        "params": params or {}, "k": k, "match": match}))


def run_pass(conn, run: dict, st: dict, pdf_sha256: str, *, model: str, api_key: str = "", base_url: str | None = None,
             generate: Callable | None = None, store=None) -> dict:
    """One pass of one paper: build the context, reuse an equal output when one exists, else run
    the registered code and store its output (role primary). Raises on failure; the caller marks
    the job."""
    code = str(st["pass"]).partition("@")[0]
    entry = chains.get(code)
    if entry is None:
        raise LookupError(f"{st['id']}: unknown pass {code!r}")
    store = store or storage.get_store()
    pages = page_texts_of(conn, pdf_sha256, store)
    paper = paper_row(conn, run["id"], pdf_sha256) or {"pdf_sha256": pdf_sha256}
    reads = _reads_for(conn, run, st, pdf_sha256)
    params = {**{n: d.get("default") for n, d in (entry.get("params") or {}).items()}, **(st.get("params") or {})}
    k = int(st.get("k") or 1)
    psha = chains.prompt_sha(code)
    isha = inputs_sha(pages, reads, params, k, st.get("match"))
    prior = find_reusable(conn, pdf_sha256=pdf_sha256, code=code, prompt_sha256=psha, inputs_sha256=isha, model=model)
    if prior is not None and generate is None:
        return store_output(conn, run_id=run["id"], step_id=st["id"], code=code, pdf_sha256=pdf_sha256, output=prior["output"], prompt_sha256=psha,
                            inputs_sha256=isha, model=model, usage=None, n_calls=0, reused_from=prior["id"], supplied=prior["supplied"],
                            stability=prior.get("stability"), log=[f"reused the output of run {prior['run_id'][:8]}"], store=store)
    ctx = PassContext(entry=entry, run_id=run["id"], step_id=st["id"], pdf_sha256=pdf_sha256, pages=pages, paper=paper, params=params,
                      reads=reads, model=model, api_key=api_key, base_url=base_url, generate=generate, store=store)
    output = entry["fn"](ctx)
    if not isinstance(output, dict):
        raise ValueError(f"{code} returned {type(output).__name__}, not a JSON object")
    return store_output(conn, run_id=run["id"], step_id=st["id"], code=code, pdf_sha256=pdf_sha256, output=output, prompt_sha256=psha,
                        inputs_sha256=isha, model=model, usage=ctx.usage, n_calls=len(ctx.calls), log=ctx.log_lines, raw=ctx.raw, store=store)


def run_paper(conn, run_id: str, pdf_sha256: str, *, model: str | None = None, api_key: str = "", base_url: str | None = None,
              generate: Callable | None = None, store=None, advance_run: bool = True) -> dict:
    """Every pass step of the run for one paper, in order: a pass with its output already stored
    is skipped (a retry resumes), each job row is marked as the pass lands or fails, a failure
    stops the paper's chain (the later steps' jobs fail with the reason), then the run advances.
    Returns ``{done: [...], failed: {step: error}, skipped: [...]}``."""
    from . import credits, providers, workflows
    run = workflows.get_run(conn, run_id)
    if run is None:
        raise LookupError("Run not found.")
    out = {"done": [], "failed": {}, "skipped": []}
    model = model or run.get("model") or ""
    key = api_key or ""
    if run.get("use_credits") and not key:
        key = credits.server_key_for(providers.get_provider(model, None)) or ""
    stopped: str | None = None
    for st in pass_steps(run["workflow"]):
        if not model:
            model = (chains.get(str(st["pass"]).partition("@")[0]) or {}).get("model") or ""
        job = next((j for j in workflows.jobs(conn, run_id, st["id"]) if j["pdf_sha256"] == pdf_sha256), None)
        if stopped:
            if job and job["status"] not in workflows.JOB_TERMINAL:
                workflows._set_job(conn, job["id"], status="failed", error=f"{stopped} failed for this paper"); conn.commit()  # noqa: SLF001
            continue
        if get_output(conn, run_id, st["id"], pdf_sha256) is not None and (job is None or job["status"] in workflows.JOB_DONE):
            out["skipped"].append(st["id"]); continue
        if job is None:
            jid = str(uuid.uuid4())
            with conn.transaction():
                conn.execute("INSERT INTO workflow_job (id, run_id, step_id, pdf_sha256, status) VALUES (%s::uuid, %s::uuid, %s, %s, 'running')",
                             (jid, run_id, st["id"], pdf_sha256))
            job = next(j for j in workflows.jobs(conn, run_id, st["id"]) if j["pdf_sha256"] == pdf_sha256)
        else:
            workflows._set_job(conn, job["id"], status="running", tries=int(job.get("tries") or 0) + 1, error=None)  # noqa: SLF001
        conn.commit()
        try:
            row = run_pass(conn, run, st, pdf_sha256, model=model, api_key=key, base_url=base_url, generate=generate, store=store)
        except Exception as exc:  # noqa: BLE001 - the job records it
            msg = providers.extract_provider_message(exc)[:500] if not isinstance(exc, (LookupError, ValueError, KeyError)) else str(exc)[:500]
            workflows._set_job(conn, job["id"], status="failed", error=msg, refunded=bool(job["credits"]))  # noqa: SLF001
            conn.commit()
            if run.get("use_credits") and job["credits"] and run.get("started_by"):
                credits.refund(conn, run["started_by"], model=model, n=job["credits"], reason="workflow refund")
            out["failed"][st["id"]] = msg
            stopped = st["id"]
            continue
        made = int(row.get("n_calls") or 0)
        if run.get("use_credits") and job["credits"] and made < job["credits"] and run.get("started_by"):
            credits.refund(conn, run["started_by"], model=model, n=job["credits"] - made, reason="workflow refund")
        workflows._set_job(conn, job["id"], status="done", error=None)  # noqa: SLF001
        conn.commit()
        out["done"].append(st["id"])
    if advance_run:
        workflows.advance(conn, run_id)
    return out
