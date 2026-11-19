"""A two-pass chain for the engine's own tests: ``title`` reads the paper and asks the model for
its title and page count; ``words`` reads that output and asks for one word per page. No real
model is ever called for it; the tests stub ``ctx.call``."""
from __future__ import annotations


def _title(ctx) -> dict:
    out = ctx.call(ctx.prompt("system"), f"Pages: {len(ctx.pages)}\n\n{ctx.text()[:4000]}", ctx.schema(), max_tokens=ctx.max_tokens)
    out["n_pages"] = len(ctx.pages)
    return out


def _words(ctx) -> dict:
    title = ctx.read("title")
    out = ctx.call(ctx.prompt("system"), f"Title: {title.get('title')}\nPages: {title.get('n_pages')}", ctx.schema(), max_tokens=ctx.max_tokens)
    out["title_len"] = len(str(title.get("title") or ""))
    return out


def _assemble(ctx) -> dict:
    """One document per paper under the step's preset: one entry per word of the ``words`` pass,
    the entry field named by the ``field`` parameter set to the word, the paper's title from the
    ``title`` pass, and a quote from the first page behind the first entry."""
    title, words = ctx.read("title"), ctx.read("words")
    key = ((ctx.preset or {}).get("entries") or {}).get("key") or "records"
    field = ctx.params.get("field") or "statement"
    entries = [{field: w.get("word"), "cause": f"c{i}", "effect": f"e{i}", "sign": "0"} for i, w in enumerate(words.get("words") or [])]
    first = (ctx.pages[0] if ctx.pages else "").strip().split("\n")[0][:60]
    evidence = [{"snippet": first, "page": 1, "source": None, "field": f"{key}[0]"}] if first and entries else []
    return {"paper_metadata": {"title": title.get("title")}, key: entries, "evidence": evidence,
            "_record_chain": {str(i): {"kind": "compute", "support": {"n": 1, "k": 1}, "flags": [], "candidates": {}} for i in range(len(entries))}}


def _mark(ctx) -> dict:
    """Edits on the document the step names: every record's ``field`` set to ``value``, and one
    finding per record that already had it."""
    field, value = ctx.params.get("field") or "sign", ctx.params.get("value") or "+"
    out = {"records": {}, "paper": {}, "findings": []}
    for r in (ctx.record or {}).get("records") or []:
        if r["field_values"].get(field) == value:
            out["findings"].append({"record_id": r["id"], "path": field, "code": "already_marked", "severity": "info", "message": f"{field} was {value!r} already"})
        out["records"][r["id"]] = {field: value}
    return out


def register() -> dict[str, dict]:
    return {
        "assemble": {"kind": "compute", "label": "Assemble a document from the passes", "produces": "document", "reads": ["title", "words"],
                     "params": {"field": {"type": "string", "default": "statement"}}, "hidden": True, "fn": _assemble},
        "mark": {"kind": "compute", "label": "Mark every record", "produces": "fields", "reads": [],
                 "params": {"field": {"type": "string", "default": "sign"}, "value": {"type": "string", "default": "+"}}, "hidden": True, "fn": _mark},
        "title": {"kind": "pass", "label": "Title and pages", "prompts": {"system": "prompts/title.system.md"}, "schema": "prompts/title.schema.json",
                  "ids": ["words[].id"], "k_ok": True, "hidden": True, "fn": _title},
        "words": {"kind": "pass", "label": "One word per page", "prompts": {"system": "prompts/words.system.md"}, "schema": "prompts/words.schema.json",
                  "reads": ["title"], "refs": [{"path": "words[].title_id", "to": "title:words[].id"}], "k_ok": True, "hidden": True,
                  "params": {"style": {"type": "string", "default": "plain"}}, "fn": _words},
    }
