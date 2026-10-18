#!/usr/bin/env python3
"""Browser check of the preset inventory and a card page (Playwright, headless Chromium).

    python3 scripts/ui_check_presets.py --base http://127.0.0.1:8015 [--host maseminer.metalens.tech]
                                        [--admin-email a@b.c --admin-password pw] [--shots DIR]

Runs against a server you started (a throwaway database is wise). Prints one PASS/FAIL line per
check and exits non-zero on any failure. With admin credentials it registers (or signs in) that
account and expects the release controls — set PAPERLENS_ADMINS on the server accordingly; it
never clicks Release or DOI. With --shots it saves screenshots of both pages.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                   # pragma: no cover
    print("SKIP  playwright is not installed (pip install playwright && playwright install chromium)")
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, "..", "tests", "markdown_cases.json")
FAILS = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global FAILS
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f"  — {extra}"))
    if not cond:
        FAILS += 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--host", default=None, help="the MASEMiner hostname to check the brand filter with (API only)")
    ap.add_argument("--admin-email"); ap.add_argument("--admin-password")
    ap.add_argument("--shots", default=None)
    a = ap.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1200, "height": 900})
        page = ctx.new_page()
        errors: list[str] = []
        # the anonymous session probe (/api/auth/me → 401) is logged by the browser as a resource error on every page
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        def settled(sel: str = "#pe-body") -> str:
            """The page body once the script replaced the shell's 'Loading…' placeholder."""
            page.wait_for_function(f"() => {{ const e = document.querySelector('{sel}'); return e && !/Loading…/.test(e.innerText); }}", timeout=15000)
            return page.inner_text(sel)

        # 1. the inventory
        page.goto(f"{a.base}/presets"); page.wait_for_selector("#pr-tiles article.tile", timeout=15000)
        tiles = page.query_selector_all("#pr-tiles article.tile")
        check("inventory lists at least two cards", len(tiles) >= 2, str(len(tiles)))
        kickers = [t.inner_text() for t in page.query_selector_all("#pr-tiles .tile-k")]
        check("every tile kicker starts with 'Preset ·'", all(k.startswith("PRESET ·") or k.startswith("Preset ·") for k in kickers), str(kickers[:3]))
        # Chromium does not let a page override its Host header, so the brand filter is checked on the API
        masem = page.request.get(f"{a.base}/api/presets/cards", headers={"host": a.host or "maseminer.metalens.tech"}).json()["cards"]
        check("the MASEMiner host lists only the MASEM cards", masem and all(x["id"].startswith("masem-") for x in masem), str([x["id"] for x in masem]))
        both = page.request.get(f"{a.base}/api/presets/cards").json()["cards"]
        check("the Metalens host lists more than the MASEM cards", len(both) > len(masem), f"{len(both)} vs {len(masem)}")
        page.wait_for_selector(".topbar nav a.active", timeout=10000)
        active = [x.inner_text() for x in page.query_selector_all(".topbar nav a.active")]
        check("Presets is the active nav item", "Presets" in active, str(active))
        if a.shots:
            os.makedirs(a.shots, exist_ok=True); page.screenshot(path=os.path.join(a.shots, "presets.png"), full_page=True)

        # 2. a card: masem-direct
        page.goto(f"{a.base}/preset?id=masem-direct"); page.wait_for_selector(".pc h1", timeout=15000)
        check("card has a title", bool(page.inner_text(".pc h1").strip()))
        anchors = [x.get_attribute("href") for x in page.query_selector_all(".pc-toc a")]
        base = ["#introduction", "#how", "#fields", "#verification", "#versions", "#used-by", "#models"]
        check("the section anchors (related work only when the card has some)", anchors in (base, base + ["#related"]), str(anchors))
        card = page.request.get(f"{a.base}/api/presets/masem-direct/card").json()
        rows = page.query_selector_all("#fields table.fd-table > tbody > tr:not(.fd-sub)")
        want = card["fields_doc"]["counts"]["fields"] + card["fields_doc"]["counts"]["columns"]
        check("Fields section lists every field and column", len(rows) == want, f"{len(rows)} rows, {want} declared")
        tables = page.query_selector_all("#verification table.md-table")
        check("two validation tables on the direct-information card", len(tables) == 2, str(len(tables)))
        links = page.query_selector_all('.md a[href^="http"]')
        check("external links open elsewhere, safely", links and all((l.get_attribute("rel") or "").find("noopener") >= 0 and l.get_attribute("target") == "_blank" for l in links), str(len(links)))
        cite = page.query_selector("#pc-cite-text")
        check("a citation or the no-release note", bool(cite and cite.inner_text().strip()) or "No numbered release yet" in page.inner_text("#versions"))
        check("anonymous: no release form, no DOI buttons", page.query_selector("#pc-rel-go") is None and not page.query_selector_all("[data-reldoi]"))
        check("no console errors on the card", not errors, " | ".join(errors[:3]))
        if a.shots:
            page.screenshot(path=os.path.join(a.shots, "card-masem-direct.png"), full_page=True)

        # 3. the markdown cases in the browser (one source of truth with the node test)
        with open(CASES, encoding="utf-8") as f:
            cases = json.load(f)
        res = page.evaluate("""async (cases) => { const m = await import("/static/grammar.js");
            return cases.map((c) => ({ name: c.name, html: m.renderMarkdown(c.src) })); }""", cases)
        html = {r["name"]: r["html"] for r in res}
        bad = [c["name"] for c in cases if any(s not in html[c["name"]] for s in c.get("has", []))
               or any(s in html[c["name"]] for s in c.get("not", [])) or any(html[c["name"]].count(s) != k for s, k in (c.get("count") or {}).items())]
        check(f"{len(cases)} markdown cases render in the browser", not bad, str(bad))

        # 4. the hidden variant, an unknown id, the editor
        page.goto(f"{a.base}/preset?id=masem-indirect"); page.wait_for_selector(".pc h1", timeout=15000)
        check("the builder-only variant says so", "builder-only" in page.inner_text(".pc-badges"))
        page.goto(f"{a.base}/preset?id=does-not-exist")
        check("an unknown id explains itself", "load this preset" in settled())
        page.goto(f"{a.base}/preset?id=masem-direct&edit=1")
        check("the editor asks an anonymous visitor to sign in", "sign in" in settled().lower())

        # 5. a moderator sees the release controls
        if a.admin_email and a.admin_password:
            page.goto(f"{a.base}/preset?id=masem-direct"); page.wait_for_selector(".pc h1", timeout=15000)
            page.evaluate("""async ([e, pw]) => { const m = await import("/static/api.js");
                try { await m.api.register(e, pw); } catch (x) { await m.api.login(e, pw); } }""", [a.admin_email, a.admin_password])
            page.reload(); page.wait_for_selector(".pc h1", timeout=15000)
            check("admin: the release form is shown", page.query_selector("#pc-relform") is not None)
            check("admin: 'Duplicate as a personal preset' offered when signed in", "Duplicate" in page.inner_text(".pc-actions"))
        browser.close()
    print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
