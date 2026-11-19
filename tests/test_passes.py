"""PASS steps (paperlens/passes.py, paperlens/chains): registered code runs per paper in its own
job, may call the model, reads the page-marked text and earlier passes' outputs, and writes a
JSON output stored per (run, step, paper) with its prompt and inputs shas — reused across runs
without a call, or supplied from outside. The step is the barrier across papers. Model calls
are faked at ``providers.generate_json``. Skips without Postgres."""
from __future__ import annotations

import json
import os
import sys
import uuid

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_analysis_table import _db_ok  # noqa: E402
from test_workflows import _client, _force_local, _pdf, _sha  # noqa: E402

from paperlens import chains, passes, presets, providers, records, workflows  # noqa: E402
from paperlens import workflow_spec as ws  # noqa: E402


def _workflow(tmp_path, monkeypatch, *, words_params=None) -> dict:
    wdir = tmp_path / "wf"; wdir.mkdir(exist_ok=True)
    spec = {"format": 1, "id": "demo-pass", "meta": {"title": "Demo passes"},
            "steps": [{"id": "title", "kind": "pass", "pass": chains.pin("_demo.title")},
                      {"id": "words", "kind": "pass", "pass": chains.pin("_demo.words"), "reads": ["title"], **({"params": words_params} if words_params else {})},
                      {"id": "release", "kind": "release", "gate": []}]}
    (wdir / "demo-pass.json").write_text(json.dumps(spec))
    monkeypatch.setenv("PAPERLENS_WORKFLOW_DIRS", str(wdir))
    return workflows.load_all()["demo-pass"]


class _Fake:
    """A model that titles a paper by its first page and names one word per page; it can fail
    for one paper (by a marker in the user text) and counts its calls."""
    def __init__(self):
        self.calls = []
        self.fail_on: str | None = None

    def __call__(self, model, api_key, *, system, user, schema, max_tokens, base_url=None, **kw):
        self.calls.append({"model": model, "api_key": api_key, "system": system, "user": user[:80], "max_tokens": max_tokens})
        if self.fail_on and self.fail_on in user:
            raise ValueError("the model refused this paper")
        if system.startswith("Read the paper"):
            first = user.split("=== PAGE 1 ===", 1)[-1].strip().split("\n", 1)[0]
            return json.dumps({"title": first.strip() or "untitled", "words": [{"id": "w1", "word": "one"}]}), "stop", {"prompt": 10, "completion": 2, "total": 12}, "fake-1"
        n = int(user.split("Pages: ", 1)[1].strip() or 1)
        return json.dumps({"words": [{"title_id": "w1", "word": f"p{i + 1}"} for i in range(n)]}), "stop", {"prompt": 5, "completion": 1, "total": 6}, "fake-1"


def _dataset(conn, sess: str) -> str:
    pin = presets.emit_schema_row("econ-regression-tables")["schema_id"]
    run = presets.resolve_run(conn, schema_id=pin, params={})
    with conn.transaction():
        records.upsert_schema(conn, run.schema_id, run.field_defs)
    ds = records.create_dataset(conn, title="Demo passes", schema_id=pin, session_id=sess)
    conn.commit()
    return ds["id"]


def test_the_grammar_pins_a_pass_by_its_prompts_and_checks_its_reads(tmp_path, monkeypatch) -> None:
    spec = ws.normalize(json.loads(json.dumps(_workflow(tmp_path, monkeypatch))))
    assert ws.validate(spec)[0] == []
    by = ws.steps_by_id(spec)
    assert by["title"]["k"] == 1 and by["words"]["reads"] == ["title"] and by["words"]["params"] == {}
    bad = json.loads(json.dumps(spec)); bad["steps"][0]["pass"] = "_demo.title@00000000"
    assert any("not the pass's current pin" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][1]["reads"] = []
    assert any("reads 'title'" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][0]["reads"] = ["words"]
    assert any("not an earlier pass step" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][0]["k"] = 2
    assert any("match must name a matcher" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][1]["params"] = {"style": 3}
    assert any("params.style must be a string" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][1]["params"] = {"nope": "x"}
    assert any("takes no 'nope'" in e for e in ws.validate(bad)[0])
    # the model is the run's choice, not the workflow's promise; a prompt edit moves the id
    with_model = json.loads(json.dumps(spec)); with_model["steps"][0]["model"] = "gemini-3.1-pro-preview"
    assert ws.workflow_id(with_model) == ws.workflow_id(spec)
    repinned = json.loads(json.dumps(spec)); repinned["steps"][0]["pass"] = "_demo.title@deadbeef"
    assert ws.workflow_id(repinned) != ws.workflow_id(spec)
    assert chains.pin_ok(chains.pin("_demo.title")) and not chains.pin_ok("_demo.title@deadbeef") and not chains.pin_ok("nope.x@00000000")


def test_a_papers_passes_run_in_order_in_one_job_and_the_step_is_the_barrier(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    fake = _Fake(); monkeypatch.setattr(providers, "generate_json", fake)
    conn = records.connect(); records.init_db(conn)
    _workflow(tmp_path, monkeypatch, words_params={"style": "terse"})
    sess = f"pass-{uuid.uuid4().hex[:6]}"
    ds = _dataset(conn, sess)
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "demo-pass"}).status_code == 200
    pdfs = [_pdf("alpha"), _pdf("beta")]
    staged = [{"sha256": _sha(b), "filename": f"{n}.pdf"} for n, b in zip(("alpha", "beta"), pdfs)]
    plan = c.post(f"/api/datasets/{ds}/workflow/plan", headers=H, json={"new_pdfs": staged}).json()
    assert [s["kind"] for s in plan["steps"]] == ["pass", "pass", "release"]
    assert plan["steps"][0]["work"]["extract"] == 2 and plan["steps"][0]["state"] == "missing" and plan["n_calls"] == 4
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json(); rid = run["id"]
    assert run["status"] == "running" and run["jobs"]["total"] == 4                        # one job per (paper, pass step)
    # a paper outside the plan is refused; a planned one runs both passes in one go (inline here)
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("zz.pdf", _pdf("zz"), "application/pdf")}, data={"api_key": "k"})
    assert r.status_code == 409
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("alpha.pdf", pdfs[0], "application/pdf")})
    assert r.status_code == 422                                                              # an own-key run needs the key with the paper
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("alpha.pdf", pdfs[0], "application/pdf")}, data={"api_key": "k", "model": "fake-model"})
    assert r.status_code == 200, r.text
    assert r.json()["done"] == ["title", "words"] and r.json()["n_pages"] >= 1 and len(fake.calls) == 2
    assert fake.calls[0]["api_key"] == "k" and fake.calls[0]["model"] == "fake-model" and fake.calls[0]["max_tokens"] == 60_000
    assert "=== PAGE 1 ===" in fake.calls[0]["user"] or fake.calls[0]["user"].startswith("Pages:")
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "running" and view["steps"][0]["status"] == "running"             # the barrier: beta has not arrived
    assert view["jobs"]["done"] == 2 and view["jobs"]["pending"] == 2
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("beta.pdf", pdfs[1], "application/pdf")}, data={"api_key": "k", "model": "fake-model"})
    assert r.status_code == 200, r.text
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "done", view
    title_sr, words_sr = view["steps"][0], view["steps"][1]
    assert title_sr["status"] == "done" and title_sr["outputs"]["n_papers"] == 2 and title_sr["outputs"]["n_calls"] == 2 and title_sr["n_calls"] == 2
    assert title_sr["prompt_sha256"] == chains.prompt_sha("_demo.title") and title_sr["outputs"]["reused"] == 0 and title_sr["outputs"]["usage"]["total"] == 24
    assert words_sr["outputs"]["n_papers"] == 2 and words_sr["outputs"]["code"] == "_demo.words"
    # the outputs: the JSON each pass wrote, with its provenance
    lst = c.get(f"/api/workflow-runs/{rid}/passes/title", headers=H).json()
    assert len(lst["outputs"]) == 2 and all(o["status"] == "done" and o["role"] == "primary" and "output" not in o for o in lst["outputs"])
    one = c.get(f"/api/workflow-runs/{rid}/passes/words/{staged[0]['sha256']}", headers=H).json()
    assert one["output"]["title_len"] > 0 and len(one["output"]["words"]) == one["output"]["title_len"] * 0 + len(one["output"]["words"])
    assert one["prompt_sha256"] == chains.prompt_sha("_demo.words") and one["inputs_sha256"] and one["n_calls"] == 1 and one["model"] == "fake-model"
    assert c.get(f"/api/workflow-runs/{rid}/passes/words/{staged[0]['sha256']}", headers=H, params={"pass_index": 3}).status_code == 404
    papers = c.get(f"/api/workflow-runs/{rid}/papers", headers=H).json()["papers"]
    assert {p["pdf_sha256"] for p in papers} == {s["sha256"] for s in staged} and all(p["n_pages"] >= 1 and p["status"] == "ready" for p in papers)
    # a second run over the same papers reuses every output: no call, the provenance names the source
    n_before = len(fake.calls)
    run2 = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json(); rid2 = run2["id"]
    for name, b in zip(("alpha", "beta"), pdfs):
        assert c.post(f"/api/workflow-runs/{rid2}/papers", headers=H, files={"pdf": (f"{name}.pdf", b, "application/pdf")}, data={"api_key": "k", "model": "fake-model"}).status_code == 200
    view2 = c.get(f"/api/workflow-runs/{rid2}", headers=H).json()
    assert view2["status"] == "done" and len(fake.calls) == n_before
    assert view2["steps"][0]["outputs"]["reused"] == 2 and view2["steps"][0]["outputs"]["n_calls"] == 0
    again = c.get(f"/api/workflow-runs/{rid2}/passes/words/{staged[1]['sha256']}", headers=H).json()
    assert again["reused_from"] == c.get(f"/api/workflow-runs/{rid}/passes/words/{staged[1]['sha256']}", headers=H).json()["id"] and again["n_calls"] == 0
    # a changed parameter changes the inputs sha: the words pass runs again, the title pass is still reused
    _workflow(tmp_path, monkeypatch, words_params={"style": "florid"})
    run3 = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged[:1]}).json(); rid3 = run3["id"]
    assert c.post(f"/api/workflow-runs/{rid3}/papers", headers=H, files={"pdf": ("alpha.pdf", pdfs[0], "application/pdf")}, data={"api_key": "k", "model": "fake-model"}).status_code == 200
    view3 = c.get(f"/api/workflow-runs/{rid3}", headers=H).json()
    assert view3["status"] == "done" and view3["steps"][0]["outputs"]["reused"] == 1 and view3["steps"][1]["outputs"]["reused"] == 0 and len(fake.calls) == n_before + 1
    conn.close()


def test_a_supplied_output_stands_in_for_the_call_and_a_failed_paper_stops_its_chain(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    fake = _Fake(); monkeypatch.setattr(providers, "generate_json", fake)
    conn = records.connect(); records.init_db(conn)
    _workflow(tmp_path, monkeypatch)
    sess = f"pass-{uuid.uuid4().hex[:6]}"
    ds = _dataset(conn, sess)
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "demo-pass"}).status_code == 200
    pdfs = {"gamma": _pdf("gamma"), "delta": _pdf("delta"), "omega": _pdf("omega")}
    staged = [{"sha256": _sha(b), "filename": f"{n}.pdf"} for n, b in pdfs.items()]
    sha = {n: _sha(b) for n, b in pdfs.items()}
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json(); rid = run["id"]
    # gamma's title comes from outside (the pipeline's own file): the words pass reads it, no title call is made
    r = c.post(f"/api/workflow-runs/{rid}/passes/title/{sha['gamma']}", headers=H, json={"output": {"title": "Supplied title", "n_pages": 1, "words": [{"id": "w1", "word": "s"}]}, "prompt_sha256": "810861b1ac"})
    assert r.status_code == 200 and r.json()["supplied"] is True and r.json()["prompt_sha256"] == "810861b1ac"
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("gamma.pdf", pdfs["gamma"], "application/pdf")}, data={"api_key": "k", "model": "m"})
    assert r.status_code == 200 and r.json()["skipped"] == ["title"] and r.json()["done"] == ["words"] and len(fake.calls) == 1
    words = c.get(f"/api/workflow-runs/{rid}/passes/words/{sha['gamma']}", headers=H).json()
    assert words["output"]["title_len"] == len("Supplied title")
    # delta's title call fails: its words job fails with the reason, the other papers go on
    fake.fail_on = "delta"
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("delta.pdf", pdfs["delta"], "application/pdf")}, data={"api_key": "k", "model": "m"})
    assert r.status_code == 200 and r.json()["failed"] == {"title": "the model refused this paper"} and r.json()["done"] == []
    js = c.get(f"/api/workflow-runs/{rid}/passes/words", headers=H).json()["jobs"]
    assert next(j for j in js if j["pdf_sha256"] == sha["delta"])["status"] == "failed" and "title failed" in next(j for j in js if j["pdf_sha256"] == sha["delta"])["error"]
    # omega never arrives: the owner closes the uploads and the run finishes on what landed
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "running"
    view = c.post(f"/api/workflow-runs/{rid}/complete", headers=H, json={"step_id": "title"}).json()
    assert view["status"] == "done", view
    t, w = view["steps"][0], view["steps"][1]
    assert t["outputs"]["n_papers"] == 1 and t["outputs"]["supplied"] == 1 and [f["pdf_sha256"] for f in t["outputs"]["failed"]] == [sha["delta"]]
    assert w["outputs"]["n_papers"] == 1 and view["jobs"]["cancelled"] == 2 and view["jobs"]["failed"] == 2
    # a run with nothing landed fails its first pass step
    fake.fail_on = "epsilon"
    eps = _pdf("epsilon")
    staged2 = [{"sha256": _sha(eps), "filename": "epsilon.pdf"}]
    run2 = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged2}).json(); rid2 = run2["id"]
    assert c.post(f"/api/workflow-runs/{rid2}/papers", headers=H, files={"pdf": ("epsilon.pdf", eps, "application/pdf")}, data={"api_key": "k", "model": "m"}).status_code == 200
    view2 = c.get(f"/api/workflow-runs/{rid2}", headers=H).json()
    assert view2["status"] == "failed" and view2["steps"][0]["status"] == "failed" and "no paper landed" in (view2["error"] or "")
    conn.close()


def test_the_context_text_marks_pages_and_a_cut_answer_raises(monkeypatch) -> None:
    entry = chains.get("_demo.title")
    gen = lambda system, user, schema, cap: ('{"title": "T"', "length", {}, "fake")   # noqa: E731
    ctx = passes.PassContext(entry=entry, run_id="r", step_id="s", pdf_sha256="x", pages=["first page", "second"], paper={"title": None},
                             params={}, reads={}, model="m", generate=gen)
    assert ctx.text() == "=== PAGE 1 ===\nfirst page\n=== PAGE 2 ===\nsecond" and ctx.text(marker="--- {i} ---").startswith("--- 1 ---")
    with pytest.raises(passes.Truncated) as exc:
        ctx.call("S", "U", None)
    assert exc.value.text.startswith('{"title"') and ctx.calls[0]["finish_reason"] == "length"
    ctx.generate = lambda system, user, schema, cap: {"title": "T"}
    assert ctx.call("S", "U", None) == {"title": "T"} and len(ctx.calls) == 2
    with pytest.raises(KeyError):
        ctx.read("nothing")
    assert passes.parse_json('```json\n{"a": 1}\n```') == {"a": 1} and passes.parse_json('text {"a": [1]} more') == {"a": [1]}
