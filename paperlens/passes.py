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


# ── references across passes ─────────────────────────────────────────────────

_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[\])?(\.[A-Za-z_][A-Za-z0-9_]*(\[\])?)*$")


def walk(obj: Any, path: str) -> list[tuple[str, Any]]:
    """Every value at ``path`` in ``obj`` with its concrete address: ``key[].child[].field`` —
    each ``[]`` expands a list (a trailing ``[]`` expands a list of values). A missing key gives
    nothing (``dangling_path`` tells); a non-list where a list is expected gives nothing."""
    if not _PATH_RE.match(path or ""):
        raise ValueError(f"bad path {path!r}")
    out: list[tuple[str, Any]] = []

    def go(node: Any, parts: list[str], at: str) -> None:
        if not parts:
            out.append((at, node)); return
        head, rest = parts[0], parts[1:]
        key, many = (head[:-2], True) if head.endswith("[]") else (head, False)
        if not isinstance(node, dict) or key not in node:
            return
        v = node[key]
        here = f"{at}.{key}" if at else key
        if many:
            if isinstance(v, list):
                for i, item in enumerate(v):
                    go(item, rest, f"{here}[{i}]")
        else:
            go(v, rest, here)
    go(obj, path.split("."), "")
    return out


def path_exists(obj: Any, path: str) -> bool:
    """Whether the path's first key is in the output at all (an empty list still counts)."""
    head = path.split(".")[0].removesuffix("[]")
    return isinstance(obj, dict) and head in obj


def check_refs(entry: dict, output: dict, upstream: dict[str, dict], *, pdf_sha256: str | None = None) -> list[dict]:
    """The generic reference check a pass registration declares: ``ids`` (paths that mint ids,
    unique and non-empty) and ``refs`` (``{path, to: "<read>:<path>" | "<path>", severity?,
    nullable?}`` — every value at ``path`` must be an id minted at ``to`` in that earlier pass's
    output, or in this one's). Findings: ``missing_id``, ``duplicate_id``, ``dangling_path``,
    ``bad_ref``, ``dangling_ref``."""
    out: list[dict] = []
    code = entry.get("id")

    def finding(c: str, sev: str, path: str, msg: str, **detail) -> None:
        out.append({"pdf_sha256": pdf_sha256, "path": path, "code": c, "severity": sev, "message": msg, "detail": detail or None, "pass": code})

    minted_here: dict[str, set] = {}
    for p in entry.get("ids") or []:
        seen: set = set()
        if not path_exists(output, p):
            finding("dangling_path", "warn", p, f"{p} is not in the output"); continue
        for at, v in walk(output, p):
            if v is None or v == "":
                finding("missing_id", "error", at, f"{at} has no id"); continue
            if not isinstance(v, (str, int)):
                finding("bad_ref", "error", at, f"{at} is not an id"); continue
            if str(v) in seen:
                finding("duplicate_id", "error", at, f"{p} mints {v!r} twice", value=v)
            seen.add(str(v))
        minted_here[p] = seen
    for ref in entry.get("refs") or []:
        path, to = ref.get("path"), ref.get("to")
        sev = ref.get("severity") or "error"
        if not isinstance(path, str) or not isinstance(to, str):
            continue
        read, _, tpath = to.rpartition(":")
        if read:
            src = upstream.get(read)
            if src is None:
                continue                                    # the engine did not give that pass: nothing to check against
            minted = {str(v) for _a, v in walk(src, tpath) if v not in (None, "")}
        else:
            minted = minted_here.get(tpath) or {str(v) for _a, v in walk(output, tpath) if v not in (None, "")}
        if not path_exists(output, path):
            finding("dangling_path", "warn", path, f"{path} is not in the output"); continue
        for at, v in walk(output, path):
            if v is None or v == "":
                if not ref.get("nullable", True):
                    finding("missing_id", sev, at, f"{at} refers to nothing")
                continue
            if not isinstance(v, (str, int)):
                finding("bad_ref", sev, at, f"{at} is not an id", value=v, to=to); continue
            if str(v) not in minted:
                finding("dangling_ref", sev, at, f"{at} = {v!r} is not an id of {to}", value=v, to=to)
    return out


def _step_run_id(conn, run_id: str, step_id: str) -> str | None:
    r = conn.execute("SELECT id::text FROM workflow_step_run WHERE run_id = %s::uuid AND step_id = %s", (run_id, step_id)).fetchone()
    return r[0] if r else None


def store_findings(conn, run_id: str, step_id: str, pdf_sha256: str, findings: list[dict], *, check: str = "refs",
                   document_id: str | None = None) -> int:
    """Replace a paper's findings of this step (``workflow_finding`` with ``pdf_sha256``; a
    compute's findings may name the document and a record too)."""
    srid = _step_run_id(conn, run_id, step_id)
    if srid is None:
        return 0
    with conn.transaction():
        conn.execute('DELETE FROM workflow_finding WHERE step_run_id = %s::uuid AND pdf_sha256 = %s AND "check" = %s', (srid, pdf_sha256, check))
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO workflow_finding (id, run_id, step_run_id, step_id, "check", pdf_sha256, record_id, document_id, path, code, severity, message, detail)
                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s)""",
                [(str(uuid.uuid4()), run_id, srid, step_id, check, pdf_sha256, f.get("record_id"), f.get("document_id") or document_id, f.get("path"),
                  f.get("code"), f.get("severity") or "warn", (f.get("message") or f.get("code") or "")[:1000],
                  Json(f["detail"]) if f.get("detail") is not None else None) for f in findings])
    return len(findings)


def findings_by_code(conn, run_id: str, step_id: str) -> dict[str, int]:
    rows = conn.execute("""SELECT f.code, count(*) FROM workflow_finding f JOIN workflow_step_run sr ON sr.id = f.step_run_id
                           WHERE sr.run_id = %s::uuid AND sr.step_id = %s GROUP BY f.code ORDER BY f.code""", (run_id, step_id)).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def refs_after(conn, run: dict, st: dict, pdf_sha256: str, entry: dict, output: dict, reads: dict[str, dict] | None = None) -> int:
    """Run the reference check on a stored primary output and record its findings; returns the count."""
    if reads is None:
        try:
            reads = _reads_for(conn, run, st, pdf_sha256)
        except LookupError:
            reads = {}
    found = check_refs(entry, output, reads, pdf_sha256=pdf_sha256)
    n = store_findings(conn, run["id"], st["id"], pdf_sha256, found)
    with conn.transaction():
        conn.execute("UPDATE workflow_pass_output SET findings = %s WHERE run_id = %s::uuid AND step_id = %s AND pdf_sha256 = %s AND role = 'primary'",
                     (n, run["id"], st["id"], pdf_sha256))
    return n


# ── what a paper is to a run ─────────────────────────────────────────────────

def paper_row(conn, run_id: str, pdf_sha256: str) -> dict | None:
    r = conn.execute("""SELECT run_id::text, pdf_sha256, filename, paper_id::text, n_pages, text_sha256, status, title, doi, abstract, journal, year, authors, peer_reviewed
                        FROM workflow_paper WHERE run_id = %s::uuid AND pdf_sha256 = %s""", (run_id, pdf_sha256)).fetchone()
    if r is None:
        return None
    return {"run_id": r[0], "pdf_sha256": r[1], "filename": r[2], "paper_id": r[3], "n_pages": r[4], "text_sha256": r[5], "status": r[6],
            "title": r[7], "doi": r[8], "abstract": r[9], "journal": r[10], "year": r[11], "authors": r[12] if isinstance(r[12], list) else None, "peer_reviewed": r[13]}


def papers_of(conn, run_id: str) -> list[dict]:
    rows = conn.execute("SELECT pdf_sha256 FROM workflow_paper WHERE run_id = %s::uuid ORDER BY created_at", (run_id,)).fetchall()
    return [paper_row(conn, run_id, r[0]) for r in rows]


def stage_paper(conn, run_id: str, pdf_bytes: bytes, *, filename: str | None, title: str | None = None, doi: str | None = None,
                abstract: str | None = None, journal: str | None = None, pages: list[str] | None = None, year: int | None = None,
                authors: list[str] | None = None, peer_reviewed: str | None = None, store=None) -> dict:
    """A PDF arrives for a run: keep it under the run, extract its page text once (content-addressed
    by the PDF's hash, shared across runs) and record the paper as ready. ``pages`` supplied (the
    pipeline's own page text, one string per page) stand in for the extraction and replace a
    stored text."""
    from . import pdf_utils
    store = store or storage.get_store()
    sha = _sha(pdf_bytes)
    store.put(storage.run_pdf_key(run_id, sha), pdf_bytes, "application/pdf")
    tkey = storage.paper_text_key(sha)
    if pages is not None:
        pages = [str(p) for p in pages]
        store.put(tkey, json.dumps(pages, ensure_ascii=False).encode("utf-8"), "application/json")
    elif store.exists(tkey):
        pages = json.loads(store.get(tkey).decode("utf-8"))
    else:
        pages = pdf_utils.page_texts(pdf_bytes)
        store.put(tkey, json.dumps(pages, ensure_ascii=False).encode("utf-8"), "application/json")
    with conn.transaction():
        conn.execute("""INSERT INTO workflow_paper (run_id, pdf_sha256, filename, n_pages, text_sha256, status, title, doi, abstract, journal, year, authors, peer_reviewed)
                        VALUES (%s::uuid, %s, %s, %s, %s, 'ready', %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (run_id, pdf_sha256) DO UPDATE SET filename = COALESCE(EXCLUDED.filename, workflow_paper.filename),
                          n_pages = EXCLUDED.n_pages, text_sha256 = EXCLUDED.text_sha256, status = 'ready',
                          title = COALESCE(EXCLUDED.title, workflow_paper.title), doi = COALESCE(EXCLUDED.doi, workflow_paper.doi),
                          abstract = COALESCE(EXCLUDED.abstract, workflow_paper.abstract), journal = COALESCE(EXCLUDED.journal, workflow_paper.journal),
                          year = COALESCE(EXCLUDED.year, workflow_paper.year), authors = COALESCE(EXCLUDED.authors, workflow_paper.authors),
                          peer_reviewed = COALESCE(EXCLUDED.peer_reviewed, workflow_paper.peer_reviewed)""",
                     (run_id, sha, filename, len(pages), _sha(_canon(pages)), title, doi, abstract, journal, year,
                      Json([str(a) for a in authors]) if authors else None, peer_reviewed))
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


def no_nul(x):
    """NUL characters out of every string and key (PDF text debris): Postgres' jsonb cannot hold
    ``\u0000``, and one in a pass output used to fail the whole call."""
    if isinstance(x, str):
        return x.replace("\x00", "") if "\x00" in x else x
    if isinstance(x, dict):
        return {no_nul(k): no_nul(v) for k, v in x.items()}
    if isinstance(x, list):
        return [no_nul(v) for v in x]
    return x


def store_output(conn, *, run_id: str, step_id: str, code: str, pdf_sha256: str, output: dict, prompt_sha256: str | None,
                 inputs_sha256: str | None, model: str | None, usage: dict | None, n_calls: int, role: str = "primary", pass_index: int = 0,
                 stability: dict | None = None, status: str = "done", error: str | None = None, reused_from: str | None = None,
                 supplied: bool = False, log: list[str] | None = None, raw: list[str] | None = None, store=None) -> dict:
    oid = str(uuid.uuid4())
    output, stability, usage, error = no_nul(output), no_nul(stability), no_nul(usage), no_nul(error)
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
        row = store_output(conn, run_id=run["id"], step_id=st["id"], code=code, pdf_sha256=pdf_sha256, output=prior["output"], prompt_sha256=psha,
                           inputs_sha256=isha, model=model, usage=None, n_calls=0, reused_from=prior["id"], supplied=prior["supplied"],
                           stability=prior.get("stability"), log=[f"reused the output of run {prior['run_id'][:8]}"], store=store)
        row["findings"] = refs_after(conn, run, st, pdf_sha256, entry, prior["output"], reads)
        return row
    def make_ctx(i: int) -> PassContext:
        return PassContext(entry=entry, run_id=run["id"], step_id=st["id"], pdf_sha256=pdf_sha256, pages=pages, paper=paper, params=params,
                           reads=reads, model=model, api_key=api_key, base_url=base_url, generate=generate, store=store, pass_index=i)
    if k <= 1:
        ctx = make_ctx(0)
        output = entry["fn"](ctx)
        if not isinstance(output, dict):
            raise ValueError(f"{code} returned {type(output).__name__}, not a JSON object")
        row = store_output(conn, run_id=run["id"], step_id=st["id"], code=code, pdf_sha256=pdf_sha256, output=output, prompt_sha256=psha,
                           inputs_sha256=isha, model=model, usage=ctx.usage, n_calls=len(ctx.calls), log=ctx.log_lines, raw=ctx.raw, store=store)
        row["findings"] = refs_after(conn, run, st, pdf_sha256, entry, output, reads)
        return row
    # k runs, each stored, then the chain's matcher folds them into the primary the later steps read
    from . import chain as _chain
    matcher = chains.get(str(st.get("match") or ""))
    if matcher is None or matcher.get("kind") != "match":
        raise LookupError(f"{st['id']}: k = {k} needs a matcher of the chain (match)")
    runs: list[dict] = []
    failed: list[dict] = []
    usage = {"prompt": 0, "completion": 0, "total": 0}
    n_calls = 0
    raw: list[str] = []
    log: list[str] = []
    for i in range(k):
        ctx = make_ctx(i)
        try:
            out_i = entry["fn"](ctx)
            if not isinstance(out_i, dict):
                raise ValueError(f"{code} returned {type(out_i).__name__}, not a JSON object")
        except Exception as exc:  # noqa: BLE001 - a run may fail; the paper decides
            failed.append({"pass_index": i, "error": str(exc)[:300]}); log.append(f"run {i + 1} of {k} failed: {str(exc)[:120]}")
            n_calls += len(ctx.calls); raw += ctx.raw
            continue
        store_output(conn, run_id=run["id"], step_id=st["id"], code=code, pdf_sha256=pdf_sha256, output=out_i, prompt_sha256=psha, inputs_sha256=isha,
                     model=model, usage=ctx.usage, n_calls=len(ctx.calls), role="run", pass_index=i, log=ctx.log_lines, raw=ctx.raw, store=store)
        runs.append(out_i)
        n_calls += len(ctx.calls); raw += ctx.raw
        for key in usage:
            usage[key] += int(ctx.usage.get(key) or 0)
    if len(runs) < _chain.runs_needed(k):
        raise RuntimeError(f"{len(failed)} of {k} runs failed" + (f": {failed[0]['error']}" if failed else ""))
    mctx = make_ctx(k)
    res = matcher["fn"](mctx, runs)
    if not isinstance(res, dict) or not isinstance(res.get("primary"), dict):
        raise ValueError(f"{matcher['id']} returned no primary output")
    n_calls += len(mctx.calls); raw += mctx.raw; log += mctx.log_lines
    for key in usage:
        usage[key] += int(mctx.usage.get(key) or 0)
    stability = {**(res.get("stability") or {}), "n_runs": len(runs), "k": k, "matcher": matcher["id"], "failed": failed}
    row = store_output(conn, run_id=run["id"], step_id=st["id"], code=code, pdf_sha256=pdf_sha256, output=res["primary"], prompt_sha256=psha,
                       inputs_sha256=isha, model=model, usage=usage, n_calls=n_calls, stability=stability, log=log, raw=raw, store=store)
    row["findings"] = refs_after(conn, run, st, pdf_sha256, entry, res["primary"], reads)
    return row


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


# ── compute steps: registered deterministic code that writes data ─────────────────────────

@dataclass
class ComputeContext:
    """What a compute sees: the paper's pages and identity, the pass outputs it declared
    (``read``), the pinned preset (a document step) or the document it edits (a fields step:
    ``record = {document_id, records: [{id, entry_index, field_values}], paper_metadata}``),
    the findings of the check steps it named (``findings[step_id]``), its params."""
    entry: dict
    run_id: str
    step_id: str
    pdf_sha256: str
    pages: list[str]
    paper: dict
    params: dict
    reads: dict[str, dict]
    preset: dict | None = None
    record: dict | None = None
    findings: dict[str, list[dict]] = field(default_factory=dict)
    log_lines: list[str] = field(default_factory=list)

    @property
    def title(self) -> str | None:
        return self.paper.get("title")

    def text(self, marker: str = DEFAULT_MARKER) -> str:
        return "\n".join(f"{marker.format(i=i + 1)}\n{p}" for i, p in enumerate(self.pages))

    def read(self, pass_name: str) -> dict:
        if pass_name not in self.reads:
            raise KeyError(f"{self.entry['id']} did not declare reads {pass_name!r} (or the workflow gave no step for it)")
        return self.reads[pass_name]

    def log(self, line: str) -> None:
        self.log_lines.append(str(line)[:500])


def document_of(conn, run_id: str, step_id: str, pdf_sha256: str) -> str | None:
    """The document a run's step made for this paper (the job row's document)."""
    r = conn.execute("""SELECT document_id::text FROM workflow_job WHERE run_id = %s::uuid AND step_id = %s AND pdf_sha256 = %s
                        AND document_id IS NOT NULL""", (run_id, step_id, pdf_sha256)).fetchone()
    return r[0] if r else None


def record_of(conn, document_id: str) -> dict:
    rows = conn.execute("""SELECT id::text, entry_index, field_values FROM record WHERE document_id = %s::uuid
                           AND NOT COALESCE(screened_empty, false) ORDER BY entry_index""", (document_id,)).fetchall()
    pm = conn.execute("SELECT paper_metadata_raw FROM extraction_document WHERE id = %s::uuid", (document_id,)).fetchone()
    return {"document_id": document_id, "records": [{"id": r[0], "entry_index": r[1], "field_values": r[2] if isinstance(r[2], dict) else {}} for r in rows],
            "paper_metadata": pm[0] if pm and isinstance(pm[0], dict) else {}}


def findings_of(conn, run_id: str, step_ids: list[str], document_id: str | None, pdf_sha256: str) -> dict[str, list[dict]]:
    """The run's findings of the named check and pass steps that concern this paper."""
    out: dict[str, list[dict]] = {}
    for sid in step_ids:
        rows = conn.execute(
            """SELECT record_id::text, document_id::text, path, code, severity, message, detail FROM workflow_finding
               WHERE run_id = %s::uuid AND step_id = %s AND (document_id = %s::uuid OR pdf_sha256 = %s) ORDER BY severity, code, path""",
            (run_id, sid, document_id, pdf_sha256)).fetchall()
        out[sid] = [{"record_id": r[0], "document_id": r[1], "path": r[2], "code": r[3], "severity": r[4], "message": r[5], "detail": r[6]} for r in rows]
    return out


def _compute_ctx(conn, run: dict, st: dict, pdf_sha256: str, entry: dict, *, document_id: str | None = None, store=None) -> ComputeContext:
    from . import workflow_spec as ws
    store = store or storage.get_store()
    pages = page_texts_of(conn, pdf_sha256, store) if store.exists(storage.paper_text_key(pdf_sha256)) else []
    paper = paper_row(conn, run["id"], pdf_sha256) or {"pdf_sha256": pdf_sha256}
    reads = _reads_for(conn, run, st, pdf_sha256)
    params = {**{n: d.get("default") for n, d in (entry.get("params") or {}).items()}, **(st.get("params") or {})}
    preset = None
    if st.get("preset"):
        from . import presets
        preset = presets.resolve_run(conn, schema_id=st["preset"]).spec
    rec = record_of(conn, document_id) if document_id else None
    fnd = findings_of(conn, run["id"], list(st.get("findings") or []), document_id, pdf_sha256)
    by_id = ws.steps_by_id(run["workflow"])
    return ComputeContext(entry=entry, run_id=run["id"], step_id=st["id"], pdf_sha256=pdf_sha256, pages=pages, paper=paper, params=params,
                          reads=reads, preset=preset, record=rec, findings=fnd), by_id


def compute_document(conn, run: dict, st: dict, pdf_sha256: str, *, store=None) -> dict:
    """A document-producing compute for one paper: the registered code builds the document
    object under the step's pinned preset from the passes' outputs, and it is persisted through
    the ordinary path (quotes located, the contract checked) with the passes as its provenance.
    Returns ``run_extraction``'s result."""
    from . import extract, presets
    from .extract import LLMResult
    code = str(st.get("fn") or "")
    entry = chains.get(code)
    if entry is None or entry.get("produces") != "document":
        raise LookupError(f"{st['id']}: {code!r} is not a document compute")
    store = store or storage.get_store()
    ctx, by_id = _compute_ctx(conn, run, st, pdf_sha256, entry, store=store)
    obj = entry["fn"](ctx)
    if not isinstance(obj, dict):
        raise ValueError(f"{code} returned {type(obj).__name__}, not a document object")
    rc = obj.pop("_record_chain", None)
    record_chain = {int(k): v for k, v in rc.items()} if isinstance(rc, dict) else None
    stages = []
    for sid in st.get("reads") or []:
        row = get_output(conn, run["id"], sid, pdf_sha256)
        stages.append({"step": sid, "code": (by_id.get(sid) or {}).get("pass"), "prompt_sha256": (row or {}).get("prompt_sha256"),
                       "output_id": (row or {}).get("id"), "n_calls": (row or {}).get("n_calls"), "supplied": (row or {}).get("supplied")})
    provenance = {"kind": "compute", "code": code, "k": 1, "n_runs": 1, "identity": [], "aggregate": {"kind": "compute", "code": code},
                  "stages": stages, "calls": [], "n_calls": 0, "failed_runs": [], "fallback": None,
                  "chain_sha256": _sha(_canon([code, [s_.get("prompt_sha256") for s_ in stages]]))}
    key = storage.run_pdf_key(run["id"], pdf_sha256)
    if not store.exists(key):
        raise LookupError("no stored PDF for this paper (upload it again)")
    pdf_bytes = store.get(key)
    run_spec = presets.resolve_run(conn, schema_id=st["preset"])
    text = json.dumps(obj, ensure_ascii=False)

    def supplied(pdf_b, prompt_, **kw):
        return LLMResult(text=text, finish_reason="stop", usage={"prompt": 0, "completion": 0, "total": 0}, resolved_model=f"compute:{code}")

    paper = ctx.paper or {}
    return extract.run_extraction(conn, pdf_bytes, f"compute:{code}", complete=supplied, schema_id=run_spec.schema_id, spec=run_spec.spec,
                                  params=run_spec.params, step_id=st["id"], role="final", chain=provenance, record_chain=record_chain,
                                  session_id=run.get("session_id"), owner_user_id=run.get("started_by"), filename=paper.get("filename"),
                                  run_id=run["id"], store=store)


def _write_allowed(path: str, writes: list[str]) -> bool:
    norm = re.sub(r"\[\d+\]", "[]", path)
    return norm in writes


def compute_fields(conn, run: dict, st: dict, pdf_sha256: str, *, document_id: str, store=None) -> dict:
    """An editing compute for one paper: the registered code reads the document the step names
    and returns ``{records: {record id: {path: value}}, paper: {field: value}, findings: [...]}``;
    every edit lands through the audited path (a ``corrected`` event by the engine, so later
    steps know the value moved) and may only touch the fields the step ``writes``."""
    from . import records as _records
    code = str(st.get("fn") or "")
    entry = chains.get(code)
    if entry is None or entry.get("produces") != "fields":
        raise LookupError(f"{st['id']}: {code!r} is not a fields compute")
    ctx, _by_id = _compute_ctx(conn, run, st, pdf_sha256, entry, document_id=document_id, store=store)
    out = entry["fn"](ctx)
    if not isinstance(out, dict):
        raise ValueError(f"{code} returned {type(out).__name__}, not {{records, paper, findings}}")
    writes = list(st.get("writes") or [])
    n_records = n_fields = 0
    known = {r["id"] for r in (ctx.record or {}).get("records") or []}
    for rid, patch in (out.get("records") or {}).items():
        if rid not in known or not isinstance(patch, dict) or not patch:
            continue
        for path in patch:
            if not _write_allowed(path, writes):
                raise ValueError(f"{code} writes {path!r}, which the step does not declare in writes")
        _records.verify_record(conn, rid, status="corrected", patch=[{"field_path": p_, "value": v} for p_, v in patch.items()],
                               verifier_kind="engine", notes=f"compute:{code}",
                               diff=[{"field_path": p_, "original_value": (ctx.record or {}).get("records", []) and next((r["field_values"].get(p_) for r in ctx.record["records"] if r["id"] == rid), None), "final_value": v} for p_, v in patch.items()])
        n_records += 1; n_fields += len(patch)
    paper_fields = {k: v for k, v in (out.get("paper") or {}).items() if isinstance(k, str)}
    if paper_fields:
        for name in paper_fields:
            if f"paper.{name}" not in writes:
                raise ValueError(f"{code} writes paper.{name!r}, which the step does not declare in writes")
        _records.set_paper_metadata_fields(conn, document_id, paper_fields, verifier_kind="engine")
        n_fields += len(paper_fields)
    found = [{**f, "document_id": f.get("document_id") or document_id} for f in (out.get("findings") or []) if isinstance(f, dict)]
    n_found = store_findings(conn, run["id"], st["id"], pdf_sha256, found, check=code, document_id=document_id)
    return {"edited_records": n_records, "edited_fields": n_fields, "findings": n_found, "log": ctx.log_lines}



def run_compute(conn, run_id: str, step_id: str, pdf_sha256: str, *, advance_run: bool = True) -> dict:
    """One paper of a compute step as the worker runs it (``compute_job``): the job row's
    paper through ``workflows.compute_paper`` (done or failed), committed, then the run
    advances — a step whose every paper landed goes on. A job no longer pending or queued
    (the run cancelled, the paper already made) is left alone."""
    from . import workflows
    from . import workflow_spec as ws
    run = workflows.get_run(conn, run_id)
    if run is None:
        raise LookupError("Run not found.")
    sr = next((x for x in workflows.step_runs(conn, run_id) if x["step_id"] == step_id), None)
    st = ws.steps_by_id(run["workflow"]).get(step_id)
    job = next((j for j in workflows.jobs(conn, run_id, step_id) if j["pdf_sha256"] == pdf_sha256), None)
    if sr is None or st is None or st.get("kind") != "compute" or job is None:
        return {"status": "skipped", "why": "not a compute job of this run"}
    if run["status"] in workflows.TERMINAL or job["status"] not in ("pending", "queued"):
        return {"status": "skipped", "why": f"run {run['status']}, job {job['status']}"}
    with conn.transaction():                                   # the job row and the document land together, before the run is advanced
        workflows.compute_paper(conn, run, sr, st, job)
    if advance_run:
        workflows.advance(conn, run_id)
    job = next(j for j in workflows.jobs(conn, run_id, step_id) if j["pdf_sha256"] == pdf_sha256)
    return {"status": job["status"], "error": job.get("error")}

# ── retries ─────────────────────────────────────────────────────────

def requeue_papers(conn, run: dict, job_rows: list[dict], *, api_key: str | None = None, base_url: str | None = None,
                   model: str | None = None) -> dict:
    """Run the failed papers of a chain again: the paper's failed or cancelled jobs of every pass
    and compute step go back to pending and its chain is dispatched again from the PDF and page
    text the run kept (a pass with its output stored is skipped). An own-key run needs the key
    here, as a retry of an extract step does; a credit run spends the passes' calls again."""
    from . import credits, worker, workflows
    from . import workflow_spec as ws
    by_id = ws.steps_by_id(run["workflow"])
    chain_steps = {sid for sid, st in by_id.items() if st.get("kind") in ("pass", "compute")}
    out = {"retried": [], "skipped": []}
    staged = {p["pdf_sha256"] for p in papers_of(conn, run["id"])}
    own = not run.get("use_credits")
    if own and not (api_key or base_url):
        raise workflows.RunRefused("This run uses your own API key: pass it to run the papers again.", 422, {"needs_key": next(iter(chain_steps), None)})
    seen: set[str] = set()
    for j in job_rows:
        sha = j["pdf_sha256"]
        if j["step_id"] not in chain_steps or sha in seen:
            continue
        seen.add(sha)
        if sha not in staged:
            out["skipped"].append({"pdf_sha256": sha, "why": "no stored PDF (upload it again)"}); continue
        n_calls = 0
        for sid in chain_steps:
            job = next((x for x in workflows.jobs(conn, run["id"], sid) if x["pdf_sha256"] == sha), None)
            if job and job["status"] in ("failed", "cancelled"):
                credits_ = workflows.calls_per_paper(run["workflow"], sid) if (run.get("use_credits") and by_id[sid].get("kind") == "pass") else 0
                workflows._set_job(conn, job["id"], status="pending", error=None, credits=credits_, refunded=False)  # noqa: SLF001
                n_calls += credits_
                sr = next((x for x in workflows.step_runs(conn, run["id"]) if x["step_id"] == sid), None)
                if sr and sr["status"] in ("done", "failed"):          # the step counts its papers again once this one lands
                    workflows._set_sr(conn, sr["id"], status="running", error=None, finished_at=None)  # noqa: SLF001
        if run.get("use_credits") and n_calls:
            uid = run.get("started_by")
            if not (uid and credits.try_consume(conn, uid, model=run.get("model") or credits.credit_model(), n=n_calls, reason="workflow retry")):
                out["skipped"].append({"pdf_sha256": sha, "why": "not enough credits"}); continue
            workflows._touch(conn, run["id"], credits_consumed=run["credits"]["consumed"] + n_calls, credits_planned=run["credits"]["planned"] + n_calls)  # noqa: SLF001
        conn.commit()
        job_id = worker.enqueue("pass_job", run["id"], sha, api_key=api_key or "", base_url=base_url, model=model or None, _expires=3600)
        if job_id:
            for sid in chain_steps:
                if by_id[sid].get("kind") != "pass":
                    continue                                       # a compute step's job stays pending: advance dispatches it when its inputs are there
                job = next((x for x in workflows.jobs(conn, run["id"], sid) if x["pdf_sha256"] == sha and x["status"] == "pending"), None)
                if job:
                    workflows.mark_queued(conn, job["id"], job_id)
        else:
            run_paper(conn, run["id"], sha, model=model, api_key=api_key or "", base_url=base_url, advance_run=False)
        out["retried"].append(sha)
    return out
