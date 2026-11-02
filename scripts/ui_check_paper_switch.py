#!/usr/bin/env python3
"""Browser check of the paper's document strip and the links between a claims document and the
tables document of the same paper.

    python3 scripts/ui_check_paper_switch.py --url http://127.0.0.1:8018 --claims <doc id> --tables <doc id> --session <X-Session-Id> [--project <claims dataset id>]

Seed: one paper imported under register-claims and under register-tables (or econ-regression-tables)
in one session, the claims dataset naming the tables dataset as its companion. Checks: the strip lists
both documents with the open one active; a result row's "column ↗" opens the regression column in
the tables document, focused; that column lists the claim citing it and the link returns to the
claim; Back returns; the sidebar keeps the paper's tab active while a sibling is open; no script
error. Needs the system python with playwright installed; prints a check list and exits non-zero
on a failure. Writes two screenshots next to the output.
"""
import argparse
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright is not installed for this python; skipped")
    sys.exit(0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8018")
    ap.add_argument("--claims", required=True, help="the claims document id")
    ap.add_argument("--tables", required=True, help="the tables document id of the same paper")
    ap.add_argument("--session", required=True)
    ap.add_argument("--project", default=None, help="the claims dataset id: checks the sidebar too")
    ap.add_argument("--shot", default="/tmp/paper-switch")
    a = ap.parse_args()
    fails: list[str] = []

    def check(cond, what):
        print(("  ok  " if cond else "  FAIL") + " " + what)
        if not cond:
            fails.append(what)

    def doc_in_url(page):
        from urllib.parse import parse_qs, urlparse
        return (parse_qs(urlparse(page.url).query).get("doc") or [None])[0]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctx.add_init_script(f"localStorage.setItem('paperlens_sid', {a.session!r});")   # api.js SID_KEY: the anonymous session the documents belong to
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text and "Failed to load resource" not in m.text else None)
        q = f"&project={a.project}" if a.project else ""
        page.goto(f"{a.url}/workspace?doc={a.claims}{q}")
        page.wait_for_selector("#docbar:not([hidden]) .docbar-doc", timeout=30000)
        pills = page.locator("#docbar .docbar-doc")
        check(pills.count() == 2, f"the strip lists the paper's two documents ({pills.count()})")
        active = page.locator("#docbar .docbar-doc.active")
        check(active.count() == 1 and "laims" in active.inner_text(), f"the open document's pill is active: {active.inner_text() if active.count() else '—'}")
        check(page.locator("#docbar .docbar-doc .dt-n").first.inner_text().strip() == "0/2", "the pill shows verified / total")
        page.wait_for_selector(".chain-open", timeout=30000)
        opens = page.locator(".chain-open")
        check(opens.count() == 1, f"one result row resolved to a column and links to it ({opens.count()})")
        check(a.tables in (opens.first.get_attribute("href") or ""), "the link names the tables document of this paper")
        page.screenshot(path=f"{a.shot}-claims.png", full_page=False)
        # → the tables document, the cited column focused and listing the claim
        opens.first.click()
        page.wait_for_selector(".cited-by .cited-link", timeout=30000)
        check(doc_in_url(page) == a.tables, "the URL follows to the tables document")
        check("ables" in page.locator("#docbar .docbar-doc.active").inner_text(), "the tables pill is active now")
        cited = page.locator(".cited-by .cited-link")
        check(cited.count() == 1 and cited.first.inner_text().strip().startswith("S1 · R1"), f"the column lists the claim citing it: {cited.first.inner_text().strip() if cited.count() else '—'}")
        check("✓" in cited.first.inner_text(), "with the cross-check verdict")
        check(page.locator(".record.focus").count() == 1, "the cited column is focused")
        if a.project:
            check(page.locator("#doctabs .doctab.active").count() == 1, "the sidebar keeps the paper's tab active while its sibling is open")
        page.screenshot(path=f"{a.shot}-tables.png", full_page=False)
        # → back to the claim through the citing link
        cited.first.click()
        page.wait_for_selector(".chain-card", timeout=30000)
        check(doc_in_url(page) == a.claims, "the citing link returns to the claims document")
        page.wait_for_selector(".chain-card.focus", timeout=5000)
        check(page.locator(".chain-card.focus").count() == 1, "with the claim focused")
        # Back returns to the tables document; the pill switches too
        page.go_back()
        page.wait_for_selector(".cited-by .cited-link", timeout=30000)
        check(doc_in_url(page) == a.tables, "Back returns to the tables document")
        page.locator("#docbar .docbar-doc:not(.active)").first.click()
        page.wait_for_selector(".chain-card", timeout=30000)
        page.wait_for_function("(document.querySelector('#docbar .docbar-doc.active') || {}).textContent.includes('laims')", timeout=10000)
        check(doc_in_url(page) == a.claims and "laims" in page.locator("#docbar .docbar-doc.active").inner_text(), "the pill switches to the claims document")
        check(not errors, "no script errors" + (f": {errors[:3]}" if errors else ""))
        browser.close()
    print(f"\n{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
