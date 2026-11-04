#!/usr/bin/env python3
"""Browser check of the derived-units page and the dataset panel's derive row.

    python3 scripts/ui_check_derived.py --url http://127.0.0.1:8019 --dataset <id> --session <X-Session-Id> [--shot /tmp/dv]

Seed: a dataset whose workflow declares a transform-only derive step with a draft awaiting its
commit (the scratchpad's seed_derived.py). Checks: the panel's derive row reads "draft" with a
review link; the derived page lists the step with its state and versions; the review shows the
rows with their verdict, sources and decisions; reject one row, commit; the version reads
committed and the panel's row reads fresh; a new draft can be proposed from the form. Needs the
system python with playwright; prints a check list and exits non-zero on a failure.
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
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--shot", default="/tmp/dv")
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
        # the dataset panel: the derive row holds a draft
        page.goto(f"{a.url}/dataset?id={a.dataset}")
        page.wait_for_selector("#ds-update:not([hidden]) .wf-row", timeout=30000)
        row = page.locator("#ds-update .wf-row", has_text="Columns per table")
        check(row.count() == 1 and "draft" in row.locator(".badge").first.inner_text(), f"the derive row reads draft: {row.inner_text()[:90]}")
        link = row.locator("a", has_text="review the draft")
        check(link.count() == 1, "the row links to the draft's review")
        check("holds" in page.locator("#ds-update").inner_text() and "per_table" in page.locator("#ds-update").inner_text(), "the release gate holds on the derive step")
        page.screenshot(path=f"{a.shot}-1-panel.png", full_page=True)
        link.click()
        page.wait_for_selector(".dv-table .dv-row", timeout=30000)
        check("/derived?dataset=" in page.url and "&id=" in page.url, f"the review opened: {page.url[-60:]}")
        rows = page.locator(".dv-table .dv-row")
        check(rows.count() == 2, f"the draft has two rows ({rows.count()})")
        verdicts = [v.strip() for v in page.locator(".dv-table .dv-row .pg-verdict").all_inner_texts()]
        check(verdicts == ["new", "new"], f"both rows are new: {verdicts}")
        check(page.locator(".dv-table .pg-chip").count() >= 3, "the rows cite their source records")
        check(page.locator("#dv-commit").count() == 1 and "Commit v1" in page.locator("#dv-commit").inner_text(), "the owner may commit v1")
        page.screenshot(path=f"{a.shot}-2-review.png", full_page=True)
        # reject the second row, commit
        page.locator(".dv-table .dv-row").nth(1).locator("[data-reject]").click()
        page.wait_for_selector(".dv-table .dv-row.rejected", timeout=15000)
        check(page.locator(".dv-table .dv-row.rejected").count() == 1, "a rejected row is struck through")
        page.locator("#dv-commit").click()
        page.wait_for_selector(".badge.tier-human_verified", timeout=15000)
        check("committed" in page.locator(".ws-head .badge").first.inner_text(), "the version reads committed")
        check(page.locator(".dv-table .dv-row").count() == 1, "the rejected row left the committed rows")
        page.screenshot(path=f"{a.shot}-3-committed.png", full_page=True)
        # the list: v1 committed, the form offers a new draft; the panel row reads fresh
        page.locator("a", has_text="All derived units").click()
        page.wait_for_selector(".ds-relform[data-step] .dv-go", timeout=30000)
        check(page.locator(".ds-dashrow", has_text="v1").count() == 1 and "committed" in page.locator(".ds-dashrow", has_text="v1").inner_text(), "the list shows v1 committed")
        page.wait_for_function("document.querySelector('.dv-plan') && !document.querySelector('.dv-plan').textContent.includes('planning')", timeout=15000)
        check("deterministic, free" in page.locator(".dv-plan").inner_text(), f"the plan line: {page.locator('.dv-plan').inner_text()}")
        page.locator(".dv-go").click()
        page.wait_for_selector(".dv-table .dv-row", timeout=30000)
        check("v2" in page.locator(".ws-head h3").inner_text() and "revised" in page.locator(".dv-filter").inner_text() or "new" in page.locator(".dv-filter").inner_text(), f"v2 drafted from the form: {page.locator('.ws-head h3').inner_text()}")
        page.goto(f"{a.url}/dataset?id={a.dataset}")
        page.wait_for_selector("#ds-update:not([hidden]) .wf-row", timeout=30000)
        row = page.locator("#ds-update .wf-row", has_text="Columns per table")
        check("draft" in row.locator(".badge").first.inner_text() and "v1" in row.inner_text(), f"the panel row: v1 committed, a draft again: {row.inner_text()[:90]}")
        check(not errors, f"no script errors ({errors[:3]})")
        browser.close()
    print(("ALL OK" if not fails else f"{len(fails)} FAILED") + f" — {len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
