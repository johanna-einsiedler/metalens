"""Product surfaces ("brands") on one engine and one deployment.

Metalens is the general extraction platform; MASEMiner is the focused product for
meta-analytic SEM data. Both are the same code, the same database and the same Fly app —
a brand is a skin plus a preset filter, chosen per request from the ``Host`` header (or
pinned with ``PAPERLENS_BRAND`` for local / open-source runs where there is no hostname to
speak of). Pages are static HTML; the front end asks ``GET /api/brand`` once per page and
paints the chrome (``static/chrome.js``), so adding a brand is a Python entry here, a
landing page, and a logo.
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Brand:
    id: str
    title: str
    tagline: str
    logo: str                                   # static path
    landing: str                                # static html file served at "/"
    default_preset: str | None = None           # /extract opens this preset's flow directly
    credit_label: str = "credits"
    nav_shared: tuple[tuple[str, str], ...] = ()      # (href, label) in the left nav group
    nav_personal: tuple[tuple[str, str], ...] = ()    # (href, label) in the right nav group
    links: dict = field(default_factory=dict)         # source / cite / docs / run-locally / parent
    tokens: dict = field(default_factory=dict)        # CSS custom-property overrides (empty = theme.css)

    def shows_preset(self, spec_meta: dict | None) -> bool:
        """A preset tagged with ``meta.brands`` is listed on those brands only; untagged
        presets appear everywhere."""
        tagged = list((spec_meta or {}).get("brands") or [])
        return not tagged or self.id in tagged

    def as_json(self) -> dict:
        d = asdict(self)
        d["nav_shared"] = [{"href": h, "label": l} for h, l in self.nav_shared]
        d["nav_personal"] = [{"href": h, "label": l} for h, l in self.nav_personal]
        if d["links"].get("parent") == _PARENT:          # resolved per request, never frozen at import
            d["links"] = {**d["links"], "parent": public_url()}
        return d


_PARENT = "\x00parent"      # a placeholder the serializer swaps for the live public URL


def public_url() -> str:
    """Where this deployment is reachable — for citations, the MASEMiner parent link and the
    canonical host. One env var, so moving the site is a secret, not a deploy."""
    return os.environ.get("PAPERLENS_PUBLIC_URL", "https://metalens.tech").rstrip("/")


METALENS = Brand(
    id="metalens", title="Metalens",
    tagline="AI-assisted data extraction from research papers, with human review.",
    logo="/static/logo.svg", landing="landing.html",
    credit_label="Metalens credits",
    nav_shared=(("/dashboards", "Dashboards"), ("/catalog", "Data Catalogue"), ("/about", "About")),
    nav_personal=(("/extract", "Process papers"), ("/import", "Import"), ("/workspace", "Data review")),
    links={"source": "https://github.com/johanna-einsiedler/metalens"},
)

MASEMINER = Brand(
    id="maseminer", title="MASEMiner",
    tagline="Extract MASEM-relevant statistics and study metadata from primary studies, "
            "with human-in-the-loop review.",
    logo="/static/maseminer-mark.svg", landing="maseminer.html",
    default_preset="masem-direct",
    credit_label="MASEMiner credits",
    nav_shared=(),
    nav_personal=(("/extract", "Extract"), ("/import", "Import"), ("/workspace", "Review")),
    links={"source": "https://github.com/johanna-einsiedler/metalens",
           "run_locally": "https://github.com/johanna-einsiedler/metalens#run-maseminer-locally",
           "parent": _PARENT},
)

BRANDS: dict[str, Brand] = {b.id: b for b in (METALENS, MASEMINER)}
DEFAULT_BRAND = METALENS.id

# Host patterns → brand id. Extend without a deploy through
# PAPERLENS_BRAND_HOSTS="maseminer=maseminer.metalens.tech,maseminer.org,*.maseminer.org".
_BUILTIN_HOSTS: tuple[tuple[str, str], ...] = (
    ("maseminer.*", MASEMINER.id),
    ("*.maseminer.org", MASEMINER.id),
    ("*.maseminer.*", MASEMINER.id),
)


def _host_patterns() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    raw = os.environ.get("PAPERLENS_BRAND_HOSTS", "")
    for part in filter(None, (p.strip() for p in raw.split(";"))):
        brand_id, _, hosts = part.partition("=")
        if brand_id.strip() in BRANDS:
            out += [(h.strip().lower(), brand_id.strip()) for h in hosts.split(",") if h.strip()]
    return out + list(_BUILTIN_HOSTS)


def resolve(host: str | None) -> Brand:
    """The brand for a request: the env pin wins (local runs, tests), then the hostname
    (port stripped) against the patterns, else Metalens."""
    pinned = os.environ.get("PAPERLENS_BRAND", "").strip().lower()
    if pinned in BRANDS:
        return BRANDS[pinned]
    h = (host or "").split(":")[0].strip().lower()
    if h:
        for pattern, brand_id in _host_patterns():
            if fnmatch.fnmatchcase(h, pattern):
                return BRANDS[brand_id]
    return BRANDS[DEFAULT_BRAND]
