"""Quotations that leave the server: the 200-character rule.

A record's evidence is a verbatim passage of the paper. Inside Metalens the passage is shown in
full to the people who own the data: the review screen highlights it on their own copy of the
PDF. Everything a reader who does NOT own the data can see — release files on GitHub and Zenodo,
dashboards built with the devkit, the dashboard tooltips here, the public dataset and record
APIs — shows at most ``LIMIT`` characters of each quotation, cut around the extracted value, with
the page and the source label kept. That is the snippet length publishers' text-and-data-mining
terms allow for published mined output (Elsevier's TDM licence: at most 200 characters per
quotation, with a link to the article — every record carries its DOI).

``shorten`` is deterministic: the same quotation and value give the same cut, so a release's
``evidence.json`` is reproducible. Cuts fall on word boundaries where that costs little and never
cut into the located value.
"""
from __future__ import annotations

import copy
import re

LIMIT = 200
MARK = "…"

_POLICY = {
    "max_chars": LIMIT,
    "mark": MARK,
    "text": "Quotations in these files are cut to at most 200 characters around the extracted value, with the "
            "page and the source kept: the snippet length publishers' text-and-data-mining terms allow for "
            "published output (Elsevier TDM licence). A cut end is marked with an ellipsis. The full quotations "
            "are kept with the dataset on Metalens, where its owner reviews them against the papers.",
}


def policy(dataset_url: str | None = None) -> dict:
    """The policy block a release carries (``release.json`` → ``evidence_policy``), with the
    dataset's page on Metalens."""
    out = dict(_POLICY)
    if dataset_url:
        out["dataset_url"] = dataset_url
    return out


def forms_of(value) -> list[str]:
    """How a value may be printed, exact forms first: 0.775 → '0.775', '.775', then the rounded
    '0.78', '0.8'; 12345 → '12345', '12,345'; -0.5 also with a typographic minus; a string value
    as itself. Booleans and None have no printed form worth centring on."""
    if value is None or isinstance(value, bool):
        return []
    out: list[str] = []

    def add(f: str) -> None:
        if f and f not in out:
            out.append(f)
    if isinstance(value, (int, float)):
        s = repr(value) if isinstance(value, float) else str(value)
        if isinstance(value, float) and s.endswith(".0"):
            s = s[:-2]
        add(s)
        if s.startswith("0."):
            add(s[1:])
        if s.startswith("-0."):
            add("-" + s[2:])
        try:
            add(f"{value:,}")
        except (TypeError, ValueError):
            pass
        for f in list(out):
            if f.startswith("-"):
                add("−" + f[1:])                                 # typographic minus
        if isinstance(value, float):
            for d in (3, 2, 1):                                 # rounded forms last: a fallback, never preferred
                r = f"{value:.{d}f}"
                add(r)
                if r.startswith("0."):
                    add(r[1:])
    else:
        s = str(value).strip()
        if s:
            add(s[:80])
    return out


def _locate(text: str, value) -> tuple[int, int] | None:
    """Where the value is printed in the text — as a whole number, never inside a longer one."""
    for form in forms_of(value):
        if form[0].isdigit() or form[0] in "-−.":
            m = re.search(r"(?<![\d.])" + re.escape(form) + r"(?![\d])", text)
        else:
            m = re.search(re.escape(form), text, flags=re.IGNORECASE)
        if m:
            return m.start(), m.end()
    return None


def shorten(text, *, value=None, limit: int = LIMIT) -> tuple[str, bool]:
    """``(shown text, was it cut?)``. A text within the limit is returned as it is. A longer one is
    cut to at most ``limit`` characters INCLUDING the ellipsis marks, centred on the value when it
    is found in the text, from the start otherwise."""
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    if len(s) <= limit:
        return s, False
    n = len(s)
    loc = _locate(s, value)
    start = max(0, (loc[0] + loc[1]) // 2 - limit // 2) if loc else 0
    lead = start > 0
    if lead:                                                # a lead mark takes one character: limit-1 remain
        start = max(1, min(start, n - (limit - 1)))         # and reaching the end needs this start at most
    body = limit - (1 if lead else 0)
    end = min(n, start + body)
    tail = end < n
    if tail:
        end = start + body - 1
    lo, hi = loc if loc else (start, start)
    if lead:                                                # begin on a word, unless that would cut into the value
        j = s.find(" ", start, min(start + 20, lo))
        if j >= 0:
            start = j + 1
    if tail:                                                # end on a word, unless that would cut into the value
        j = s.rfind(" ", max(end - 20, hi), end)
        if j >= 0:
            end = j
    return (MARK if lead else "") + s[start:end].strip() + (MARK if tail else ""), True


def shorten_item(item: dict, *, value=None, limit: int = LIMIT) -> dict:
    """An evidence item (``{snippet, page, source, …}``) with its snippet cut; ``truncated: True``
    marks a cut, an item within the limit is returned unchanged (the same object)."""
    snippet = item.get("snippet")
    if not isinstance(snippet, str) or len(snippet) <= limit:
        return item
    cut, _ = shorten(snippet, value=value, limit=limit)
    return {**item, "snippet": cut, "truncated": True}


_STEP = re.compile(r"([A-Za-z_][\w]*)(?:\[(\d+)\])?")


def value_at(field_values: dict | None, path: str | None):
    """The scalar at a record-relative field path (``n``, ``factor_loadings[2].loading``,
    ``conditions[0].measures[1].mean``) — None when the path does not resolve to a scalar."""
    if not isinstance(field_values, dict) or not path:
        return None
    cur = field_values
    for part in str(path).split("."):
        m = _STEP.fullmatch(part)
        if not m:
            return None
        name, idx = m.group(1), m.group(2)
        if not isinstance(cur, dict) or name not in cur:
            return None
        cur = cur[name]
        if idx is not None:
            i = int(idx)
            if not isinstance(cur, list) or i >= len(cur):
                return None
            cur = cur[i]
    if isinstance(cur, dict) and "value" in cur and len(cur) <= 3:          # a wrapped {value, …}
        cur = cur["value"]
    return cur if isinstance(cur, (str, int, float)) and not isinstance(cur, bool) else None


def cap_document(doc, *, limit: int = LIMIT):
    """A canonical result (what ``reconstruct_publishable`` returns) with every evidence quotation
    cut to the public limit, centred on the value its ``field`` path names. Returns a new
    structure; the input is left as it is. This is what a dataset's ``results.json`` carries
    when it is published to the datasets repository."""
    out = copy.deepcopy(doc)

    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get("snippet"), str) and ("field" in x or "page" in x or "source" in x):
                return shorten_item(x, value=value_at(out, x.get("field")), limit=limit)
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return walk(out)
