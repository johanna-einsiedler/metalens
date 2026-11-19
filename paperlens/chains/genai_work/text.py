"""The paper's text as the GenAI-at-work chain reads it — copied from the review project
(``scripts/textnorm.py``, ``scripts/screening/fulltext_judge.py``, ``scripts/extraction/
{split,sections,sentences,passages,checks}.py``, ``scripts/screening/fulltext_metalens.py``) so
the chain inside Metalens checks, snaps and cuts quotes exactly as the local chain did.

A ``Pages`` object stands in for the project's ``labeling/split`` text files: ``raw`` is the
text with ``=== PAGE i ===`` markers (what the prompts name), ``rpages`` the pages with
manuscript line numbers stripped (what quotes snap to), ``pages`` the normalised pages (what
quotes are checked against).
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

MARKER = "=== PAGE {i} ==="
MAX_CHARS = 600_000              # longer texts are cut (reported); none of the papers so far comes close
NEAR = 0.9                       # similarity for a near match in the quote check
SNAP = 0.8                       # similarity at which an edited quote is replaced by the paper's passage
LINE_NO = re.compile(r"^[ \t]*\d{1,4}[ \t]+", re.M)
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}
GLYPH = re.compile(r"/C\d+|/[A-Za-z]\d+")
FOUND = {"page", "near_page", "other_page", "elsewhere", "fragments", "snapped"}
FOUND_ALL = FOUND | {"table_cells"}     # a quote of table cells: every number on the cited page (or next to it)
NUM_TOKEN = re.compile(r"-?\d[\d.,]*\d|\d")
SOFT = {"se", "ci_low", "ci_high", "p_value", "n", "n_obs"}     # printed apart from the quoted cell more often than not


def norm(s: str) -> str:
    """Fold a string down to comparable [a-z0-9], repairing PDF text-layer damage."""
    for lig, rep in LIGATURES.items():
        s = s.replace(lig, rep)
    s = GLYPH.sub("", s)
    s = s.replace("\x00", "")
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    # Some PDFs drop ff/fi/fl ligatures from the text layer entirely ("different" -> "di erent").
    # Erase them from both sides so a quote that restored them still matches the source.
    for lig in ("ffi", "ffl", "ff", "fi", "fl"):
        s = s.replace(lig, "")
    return s


def strip_line_numbers(body: str) -> str:
    """Manuscripts with line numbering interleave them with the text; drop them when most lines
    of the page start with one."""
    lines = [l for l in body.splitlines() if l.strip()]
    if lines and sum(bool(LINE_NO.match(l)) for l in lines) / len(lines) > 0.5:
        return LINE_NO.sub("", body)
    return body


class Pages:
    """A paper's pages as the chain reads them (1-based page numbers, as the prompts name)."""

    def __init__(self, pages: list[str]):
        self.list = list(pages or [])
        self.raw = "\n".join(f"{MARKER.format(i=i + 1)}\n{p}" for i, p in enumerate(self.list))[:MAX_CHARS]
        self.rpages = {i + 1: strip_line_numbers(p) for i, p in enumerate(self.list)}
        self.pages = {k: norm(v) for k, v in self.rpages.items()}

    @classmethod
    def from_raw(cls, raw: str) -> "Pages":
        """From a page-marked text (the project's ``labeling/split`` files, a fixture)."""
        out = []
        for blk in raw.split("=== PAGE ")[1:]:
            num, _, body = blk.partition("===")
            try:
                int(num.strip())
            except ValueError:
                continue
            out.append(body.lstrip("\n"))
        return cls(out)


# ── quotes ───────────────────────────────────────────────────────────

def _simple(s: str):
    """Lower-case alphanumerics with the raw index of each, for mapping a match back."""
    out, idx = [], []
    for i, ch in enumerate(s):
        for c in unicodedata.normalize("NFKD", ch).lower():
            if c.isalnum() and c.isascii():
                out.append(c)
                idx.append(i)
    return "".join(out), idx


def snap_quote(quote, page, rpages):
    """The paper's own passage closest to an edited quote, if it is at least SNAP similar.
    Looks on the cited page first, then on every page. Returns (passage, page) or None."""
    nq, _ = _simple(quote or "")
    if len(nq) < 20:
        return None
    order = [page] + [p for p in rpages if p != page]
    for p in order:
        body = rpages.get(p)
        if not body:
            continue
        nb, idx = _simple(body)
        m = SequenceMatcher(None, nq, nb, autojunk=False).find_longest_match(0, len(nq), 0, len(nb))
        if m.size < 12:
            continue
        lo = max(0, m.b - m.a - 5)
        hi = min(len(nb), m.b + (len(nq) - m.a) + 5)
        blocks = [b for b in SequenceMatcher(None, nq, nb[lo:hi], autojunk=False).get_matching_blocks() if b.size]
        if not blocks:
            continue
        s, e = lo + blocks[0].b, lo + blocks[-1].b + blocks[-1].size
        if SequenceMatcher(None, nq, nb[s:e], autojunk=False).ratio() < SNAP:
            continue
        a, b = idx[s], idx[e - 1] + 1
        while 0 < a < b and body[a - 1].isalnum() and body[a].isalnum():     # never start mid-word
            a += 1
        while a < b and (body[a].isspace() or body[a] in ".,;:!?)]"):       # nor on a sentence's tail
            a += 1
        while b < len(body) and body[b - 1].isalnum() and body[b].isalnum():  # nor end mid-word
            b += 1
        passage = re.sub(r"-\s*\n\s*", "", body[a:b])
        return " ".join(passage.split()), p
    return None


def _near(q: str, page: str) -> bool:
    if len(q) < 20 or not page:
        return False
    m = SequenceMatcher(None, q, page, autojunk=False).find_longest_match(0, len(q), 0, len(page))
    lo = max(0, m.b - m.a - 10)
    window = page[lo:lo + int(len(q) * 1.2) + 20]
    return SequenceMatcher(None, q, window, autojunk=False).ratio() >= NEAR


def check_quote(quote, page, pages) -> str:
    """'page' | 'near_page' | 'other_page' | 'elsewhere' | 'fragments' | 'not_found' | 'empty'.
    A quote the model elided ("A ... B") is verbatim in its parts: it passes as 'fragments'
    when every part of at least 8 characters is found."""
    parts = [s for s in re.split(r"\.\.\.|…|\[\.\.\.\]", quote or "") if len(norm(s)) >= 8]
    if len(parts) > 1:
        sts = [check_quote(s, page, pages) for s in parts]
        return "fragments" if all(s in FOUND for s in sts) else "not_found"
    q = norm(quote or "")
    if len(q) < 8:
        return "empty"
    body = pages.get(page, "")
    if q in body:
        return "page"
    if _near(q, body):
        return "near_page"
    for p in (page - 1, page + 1):
        if q in pages.get(p, "") or _near(q, pages.get(p, "")):
            return "other_page"
    if any(q in b for b in pages.values()):
        return "elsewhere"
    if len(q) <= 2000 and any(_near(q, b) for b in pages.values()):
        return "elsewhere"
    return "not_found"


def actual_page(quote, cited, pages):
    q = norm(quote or "")
    if q and q in pages.get(cited, ""):
        return cited
    for p, body in pages.items():
        if q and q in body:
            return p
    return cited


def table_cells(quote, page, pages):
    """A table cell and its standard error sit on different lines of the text layer, with other
    columns between them, so a quote like "0.138*** (0.0199)" is never contiguous. It passes
    when every number of the quote is on one page and there are at most twelve of them.
    Returns the page the cells were found on, or None."""
    nums = [norm(x) for x in NUM_TOKEN.findall(quote or "")]
    nums = [x for x in nums if x]
    if not nums or len(nums) > 12:
        return None
    words = [w for w in re.findall(r"[A-Za-z]{4,}", quote or "")]
    order = [page, page - 1, page + 1] + [p for p in sorted(pages) if abs(p - page) > 1]
    for p in order:
        body = pages.get(p, "")
        if body and all(x in body for x in nums) and all(norm(w) in body for w in words[:3]):
            return p
    return None


# ── numbers in quotes ────────────────────────────────────────────────

def _renderings(v: float) -> set[str]:
    """Ways a number may be printed: as is, with more decimals (0.090), with thousands
    separators (1,571), as a percentage or a share, absolute value."""
    out: set[str] = set()
    for x in {v, abs(v)}:
        out |= {f"{x}", f"{x:g}", f"{x:.1f}", f"{x:.2f}", f"{x:.3f}", f"{x:.4f}", f"{x:,}", f"{x:,.1f}", f"{x:,.2f}",
                f"{x:,.3f}", f"{x * 100:g}", f"{x / 100:g}", f"{x * 100:.1f}", f"{x * 100:.2f}"}
        if float(x).is_integer():
            out |= {str(int(x)), f"{int(x):,}", f"{int(x) / 100:g}"}
    return {s for s in out if s and s != "."}


def number_in_quote(v, quote) -> bool:
    """Whether the number is printed in the quote: a rendering of it bounded by non-digits (so
    0.4 is not found in 10.45, and 130 not in 0.61130); signs are ignored."""
    if v is None or quote is None:
        return True
    q = quote.replace("−", "-").replace("–", "-").replace(" ", " ").replace("\xa0", " ")
    for s in _renderings(float(v)):
        pat = r"(?<![\d.,])" + re.escape(s) + r"(?![\d])"
        if re.search(pat, q):
            return True
        if s.startswith("0."):                      # a leading zero dropped (".45" for 0.45)
            if re.search(r"(?<![\d.,])" + re.escape(s[1:]) + r"(?![\d])", q):
                return True
        if "." in s and re.search(r"(?<![\d.,])" + re.escape(s.replace(".", ",")) + r"(?![\d])", q):   # a decimal comma
            return True
    return False


def number_on_page(v, page, pages) -> bool:
    for p in (page, page - 1, page + 1):
        body = pages.get(p, "")
        if body and any(norm(s) and norm(s) in body for s in _renderings(float(v))):
            return True
    return False


# ── sections ─────────────────────────────────────────────────────────

# a heading is classified by the words it contains, the later entries winning over the earlier
KEYWORDS = [
    ("references", r"\breferences?\b|\bbibliography\b|works\s+cited|acknowledg|\bfunding\b|\bdeclarations?\b|conflicts?\s+of\s+interest|data\s+availability|author\s+contributions|\bethics\b|competing\s+interests|\bdisclosures?\b"),
    ("intro", r"\bintroduction\b|\bmotivation\b|\boverview\b|technical\s+primer"),
    ("literature", r"related\s+(work|literature|research)|prior\s+(work|literature|research)|previous\s+(work|literature|research)|\bliterature\b|theoretical\s+(background|framework)|\btheory\b|\bhypothes[ie]s\b|conceptual\s+(framework|background)"),
    ("discussion", r"\bdiscussion\b|\bimplications?\b|\blimitations?\b|\bconclusions?\b|concluding\s+remarks|future\s+(work|research|directions)|general\s+discussion"),
    ("methods", r"\bmethods?\b|\bmethodology\b|\bdata\b|study\s+design|experimental\s+(design|setup|set-up|procedure)|research\s+design|\bdesign\b|empirical\s+(strategy|approach|framework|design|setting|specification)|\bsetting\b|\bparticipants?\b|\bprocedure\b|\bsample\b|\bmeasures?\b|\bidentification\b|\bestimation\b|\bexperiments?\b|\bintervention\b|\btreatment\b|\bdeployment\b|summary\s+statistics|\bvariables?\b|\btasks?\b|\bmaterials\b|\brecruitment\b|\bsubjects\b|\binstitutional\s+(background|setting|context)\b|\bstudy\s+[1-9]\b"),
    ("results", r"\bresults?\b|\bfindings\b|\banalys[ie]s\b|\boutcomes?\b|\bevaluation\b|\bestimates\b|treatment\s+effects?|\bheterogeneity\b|\bmechanisms?\b|\badoption\b|\badherence\b|\blearning\b|\battrition\b|\bproductivity\b|\bperformance\b|\bquality\b|robustness"),
    ("appendix", r"\bappendix\b|\bappendices\b|\bsupplement(ary|al)?\b|online\s+appendix|additional\s+(tables|results|analyses|exhibits)"),
]
KEEP = {"methods", "results", "appendix"}
NUM = r"(?:(?:[1-9]\d?|[IVX]{1,4}|[A-H])(?:\.\d+)*\.?\s+)?"
HEADING = re.compile(r"^\s*" + NUM + r"(?P<name>[A-Za-z][A-Za-z0-9 ,&/:\-]{2,60})\s*$")
TABLE_LINE = re.compile(r"^\s*(Table|Figure|Panel|Exhibit)\s+[A-Z]?\d+", re.I)
ABSTRACT = re.compile(r"^\s*(abstract|summary)\b[:.]?\s*", re.I)
MIN_HEADING_GAP = 400          # a heading closer than this to the previous one is not a section start
TITLE_BLOCK = 1500             # characters at the top where author lines look like headings


def classify(name: str):
    """The kind a heading announces, from the words it contains; a discussion or conclusion word
    wins over methods or results words ('4.4 Discussion'), references over everything."""
    n = name.strip().lower().rstrip(":")
    hit = None
    for kind, pat in KEYWORDS:
        if re.search(pat, n):
            if kind == "references":
                return kind
            if kind == "discussion":
                return kind
            hit = hit or kind if kind not in ("intro", "literature") else (hit or kind)
            if kind in KEEP and hit in ("intro", "literature"):
                hit = kind
    return hit


def scan(raw: str):
    """(lines, headings): the text's lines and the section headings recognised in them as (line
    index, kind, text), a heading too close to the previous one dropped, tables and figures
    after the references counted as the appendix."""
    lines = raw.splitlines()
    marks, seen_chars = [], 0
    for i, line in enumerate(lines):
        seen_chars += len(line) + 1
        if line.startswith("=== PAGE "):
            continue
        s = line.strip()
        if not s or len(s) > 70 or not s[0].isalnum() or seen_chars < TITLE_BLOCK:
            continue
        if TABLE_LINE.match(s):
            marks.append((i, "table", s))           # a table or figure block: kept when it follows the references
            continue
        m = HEADING.match(s)
        if not m:
            continue
        # a heading is numbered, Title Case or ALL CAPS; ordinary short lines are not headings
        if not (re.match(r"^\s*(\d|[IVX]+\.|[A-H]\s)", s) or s.isupper() or s.istitle() or s[0].isupper() and len(s.split()) <= 4):
            continue
        kind = classify(m.group("name"))
        if kind:
            marks.append((i, kind, s))
    filtered, last_i = [], None
    after_refs = False
    for i, kind, s in marks:
        if kind == "table":
            if after_refs:                             # tables and figures after the references are the appendix
                filtered.append((i, "appendix", s)); last_i = i
            continue
        if last_i is None or sum(len(l) for l in lines[last_i:i]) >= MIN_HEADING_GAP or kind == "references":
            filtered.append((i, kind, s))
            last_i = i
            if kind == "references":
                after_refs = True
    return lines, filtered


def without_abstract(raw: str, abstract: str = ""):
    """The text with the abstract block dropped: the lines that print the paper's abstract (its
    first and last words located in the first pages), else from an 'Abstract' line to the first
    heading after it (a keywords line, or 3,000 characters); the page markers kept."""
    lines = raw.splitlines()
    if abstract:
        words = abstract.split()
        head, tail = norm(" ".join(words[:6])), norm(" ".join(words[-6:]))
        i = next((k for k, l in enumerate(lines[:600]) if head and head in norm(l)), None)
        if i is not None:
            j = next((k for k in range(i, min(len(lines), i + 80)) if tail and tail in norm(lines[k])), None)
            if j is None:                                  # the last words straddle a line break: the next blank line
                j = next((k for k in range(i + 1, min(len(lines), i + 80)) if not lines[k].strip()), min(len(lines) - 1, i + 30))
            an = norm(abstract)                            # in a two-column page the abstract's lines interleave with others: drop its own only
            own = lambda l: len(norm(l)) >= 12 and norm(l) in an   # noqa: E731
            kept = [l for k, l in enumerate(lines) if not (i <= k <= j and (own(l) or not l.strip())) or l.startswith("=== PAGE ")]
            return "\n".join(kept), True
    start = next((i for i, l in enumerate(lines[:400]) if ABSTRACT.match(l.strip())), None)
    if start is None:
        return raw, False
    end, chars = None, 0
    for j in range(start + 1, min(len(lines), start + 400)):
        s = lines[j].strip()
        chars += len(s) + 1
        m = HEADING.match(s) if s and len(s) <= 70 and s[0].isalnum() else None
        if (m and classify(m.group("name")) and chars > 200) or re.match(r"^(keywords?|jel\b)", s, re.I) or chars > 3000:
            end = j
            break
    end = end or min(len(lines), start + 60)
    kept = [l for k, l in enumerate(lines) if not (start <= k < end) or l.startswith("=== PAGE ")]
    return "\n".join(kept), True


# ── sentences ────────────────────────────────────────────────────────

ABBREV = re.compile(r"(?:\b(?:e\.g|i\.e|et al|Fig|Figs|No|Nos|vs|cf|approx|Dr|Prof|Mr|Ms|Mrs|St|ca|resp|Ref|Refs|Sec|Ch|Vol|pp|p|Eq|Eqs"
                    r"|Tab|Col|Inc|Ltd|Jr|U\.S|U\.K)|\b[A-Z])\.$")
SENTENCE = re.compile(r"\S.*?[.!?]+[\"”’)\]]*(?=\s+[\"“(\[]?[A-Z0-9]|\s*$)", re.S)
MIN_LEN, MAX_LEN = 25, 600                  # characters: a fragment or a table block is no sentence
RUNNING_PAGES = 3                           # a line found on this many pages is a running header or footer
STAMP = re.compile(r"Downloaded from\b|This content downloaded|Terms (and Conditions|of Use)|\bJSTOR\b|Electronic copy available", re.I)


def without_running_lines(pages: dict[int, list[str]]) -> dict[int, list[str]]:
    """The pages' lines with the running headers and footers dropped: a line (normalised) that
    recurs on RUNNING_PAGES pages or more, and the download stamps of a publisher's PDF."""
    key = lambda l: re.sub(r"\d+", "#", re.sub(r"\s+", " ", l.strip().lower()))     # noqa: E731 - page numbers vary: 1 of 6, 2 of 6
    on: dict[str, set] = {}
    for page, lines in pages.items():
        for k in {key(l) for l in lines if len(l.strip()) >= 12}:
            on.setdefault(k, set()).add(page)
    running = {k for k, ps in on.items() if len(ps) >= RUNNING_PAGES}
    return {page: [l for l in lines if key(l) not in running and not STAMP.search(l)] for page, lines in pages.items()}


def split_sentences(text: str) -> list[str]:
    """The sentences of a run of text: one ends at . ! or ? followed by a capital or a digit; an
    abbreviation (e.g., et al., Fig., an initial) ends none."""
    text = " ".join(text.split())
    out, buf, pos = [], "", 0
    for m in SENTENCE.finditer(text):
        buf = f"{buf} {m.group(0)}".strip()
        pos = m.end()
        if ABBREV.search(buf):
            continue
        out.append(buf)
        buf = ""
    tail = f"{buf} {text[pos:]}".strip()
    if tail:
        out.append(tail)
    return out


def page_text(lines: list[str]) -> str:
    """A page's lines as one run of text, a word hyphenated at a line end joined."""
    text = "\n".join(lines)
    text = re.sub(r"-\n(?=[a-z])", "", text)
    return text.replace("\n", " ")


# ── tables ───────────────────────────────────────────────────────────

def table_text(tb: dict | None) -> str:
    """Markdown of a table item, for Metalens and the second reader."""
    if not tb:
        return ""
    head = [h.strip() for h in tb.get("header") or []]
    out = [f"**{tb.get('caption', '').strip()}**" if tb.get("caption") else ""]
    if head:
        out.append("| " + " | ".join(head) + " |")
        out.append("|" + "---|" * len(head))
    for row in tb.get("rows") or []:
        cells = [str(c).strip() for c in row]
        if head and len(cells) < len(head):
            cells += [""] * (len(head) - len(cells))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(x for x in out if x)
