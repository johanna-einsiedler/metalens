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


def register() -> dict[str, dict]:
    return {
        "title": {"kind": "pass", "label": "Title and pages", "prompts": {"system": "prompts/title.system.md"}, "schema": "prompts/title.schema.json",
                  "ids": ["words[].id"], "k_ok": True, "hidden": True, "fn": _title},
        "words": {"kind": "pass", "label": "One word per page", "prompts": {"system": "prompts/words.system.md"}, "schema": "prompts/words.schema.json",
                  "reads": ["title"], "refs": [{"path": "words[].title_id", "to": "title:words[].id"}], "k_ok": True, "hidden": True,
                  "params": {"style": {"type": "string", "default": "plain"}}, "fn": _words},
    }
