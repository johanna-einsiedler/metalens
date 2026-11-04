#!/usr/bin/env python3
"""Browser check of the workflow inventory and a workflow card.

    python3 scripts/ui_check_workflow_cards.py --url http://127.0.0.1:8019 --session <X-Session-Id> [--admin] [--shot /tmp/wfc]

Checks /workflows (one tile per curated workflow with its steps and counts) and
/workflow?id=econ-regressions (the sections, the steps with their preset links and review chips,
the review-policy table, the checks with their codes, the citation, "used by"); with --admin
(a server in local mode moderates itself; pass --email/--password for an account listed in
PAPERLENS_ADMINS on a hosted one) it cuts a release from the form and
checks the versions list and the bundle link. Needs the system python with playwright; prints a
check list and exits non-zero on a failure.
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
    ap.add_argument("--url", default="http://127.0.0.1:8019")
    ap.add_argument("--session", required=True)
    ap.add_argument("--admin", action="store_true")
    ap.add_argument("--email"); ap.add_argument("--password")
    ap.add_argument("--shot", default="/tmp/wfc")
    a = ap.parse_args()
    fails: list[str] = []

    def check(cond, what):
        print(("  ok  " if cond else "  FAIL") + " " + what)
        if not cond:
            fails.append(what)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 1100})
        ctx.add_init_script(f"localStorage.setItem('paperlens_sid', {a.session!r});")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text and "Failed to load resource" not in m.text else None)
        page.on("dialog", lambda d: d.accept())
        # the inventory
        page.goto(f"{a.url}/workflows")
        page.wait_for_selector("#wf-tiles .tile", timeout=30000)
        tiles = page.locator("#wf-tiles .tile")
        check(tiles.count() >= 3, f"the inventory lists the curated workflows ({tiles.count()} tiles)")
        econ = page.locator("#wf-tiles .tile", has_text="Economics regression tables")
        check(econ.count() == 1 and "workflow ·" in econ.inner_text().lower(), "the econ tile carries the Workflow kicker")
        check("review: sample of 30" in econ.inner_text() and "dataset" in econ.locator(".tile-foot").inner_text(), f"the tile names the review policy and counts: {econ.locator('.tile-foot').inner_text()}")
        nav = page.locator("header a", has_text="Workflows")
        check(nav.count() >= 1, "Workflows sits in the navigation")
        page.screenshot(path=f"{a.shot}-1-inventory.png", full_page=True)
        # the card
        econ.locator("a.tile-a").click()
        page.wait_for_selector("#wc-body h1", timeout=30000)
        check(page.url.endswith("/workflow?id=econ-regressions"), f"the tile opens the card: {page.url}")
        check(page.locator("#wc-body h1").inner_text() == "Economics regression tables", "the card's title")
        anchors = [x.get_attribute("href") for x in page.locator("nav.pc-toc a").all()]
        check(anchors[:7] == ["#introduction", "#steps", "#review", "#checks", "#verification", "#versions", "#used-by"], f"the section nav: {anchors}")
        steps = page.locator("#steps .wc-step")
        check(steps.count() == 4, f"the steps list has four steps ({steps.count()})")
        first = steps.first
        check(first.locator("a[href='/preset?id=econ-regression-tables']").count() == 1 and "econ-regression-tables@" in first.inner_text(), "the extract step links its preset card and shows the pin")
        check("review: random sample of 30 records, stratified by table_number" in first.inner_text(), f"the extract step's review chip: {first.locator('.wc-review').inner_text()}")
        rows = page.locator("#review table tbody tr")
        check(rows.count() == 1 and "sample" in rows.first.inner_text() and "the release" in rows.first.inner_text(), "the review-policy table has the sampled step holding the release")
        codes = page.locator("#checks code", has_text="se_dangling_ref")
        check(codes.count() == 1 and page.locator("#checks code", has_text="regression-cells").count() == 1, "the checks section lists regression-cells with its codes")
        cite = page.locator("#wc-cite-text").inner_text()
        check("Metalens workflow" in cite and "Einsiedler" in cite, f"the citation: {cite[:90]}")
        check(page.locator("#used-by a[href^='/dataset?id=']").count() >= 1, "a public dataset shows under 'used by'")
        check(page.locator("#verification .ds-stats").count() == 1, "the verification roll-up is drawn")
        page.screenshot(path=f"{a.shot}-2-card.png", full_page=True)
        if a.admin:
            if a.email:                  # a listed moderator on a hosted server; local mode needs no account
                r = page.request.post(f"{a.url}/api/auth/login", data={"email": a.email, "password": a.password}, headers={"X-Session-Id": a.session})
                check(r.ok, f"signed in as the moderator ({r.status})")
                page.reload(); page.wait_for_selector("#wc-body h1", timeout=30000)
            go = page.locator("#wc-rel-go")
            check(go.count() == 1, "the moderator sees the release form")
            page.fill("#wc-rel-notes", "cut from the browser check")
            go.click()
            page.wait_for_selector("#versions .ds-dashrow", timeout=30000)
            row = page.locator("#versions .ds-dashrow").first
            check(row.inner_text().startswith("v1") and "no DOI" in row.inner_text() and "cut from the browser check" in row.inner_text(), f"v1 is listed with its notes: {row.inner_text()[:100]}")
            files = row.locator("a", has_text="files")
            check(files.count() == 1 and "/releases/1/bundle" in (files.get_attribute("href") or ""), "the bundle link")
            r = page.request.get(a.url + files.get_attribute("href"))
            check(r.status == 200 and r.headers.get("content-type") == "application/zip", "the bundle downloads as a zip")
            check(page.locator("#wc-rel-go").is_disabled(), "the form is disabled: nothing changed since v1")
            check("v1" in page.locator(".pc-badges").inner_text(), "the header badge names v1")
            page.goto(f"{a.url}/workflows")
            page.wait_for_selector("#wf-tiles .tile", timeout=30000)
            econ = page.locator("#wf-tiles .tile", has_text="Economics regression tables")
            check("workflow · v1" in econ.inner_text().lower(), "the tile now reads v1")
            page.screenshot(path=f"{a.shot}-3-released.png", full_page=True)
        check(not errors, f"no script errors ({errors[:3]})")
        browser.close()
    print(("ALL OK" if not fails else f"{len(fails)} FAILED") + f" — {len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
