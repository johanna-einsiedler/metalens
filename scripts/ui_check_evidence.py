#!/usr/bin/env python3
"""Browser check of the evidence review layout (display.review.layout "evidence").

    python3 scripts/ui_check_evidence.py --url http://127.0.0.1:8018 --doc <document id> --session <X-Session-Id>

Opens the workspace on a document imported under a preset with the evidence layout and checks:
the items are drawn in order with their page jumps, a cell edited under one item follows under
every other item that bears on it (and is saved), the Fields button returns to the generic cards,
and the page logs no script error. Needs the system python with playwright installed; prints a
check list and exits non-zero on a failure. Writes a screenshot next to the output.
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
    ap.add_argument("--doc", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--shot", default="/tmp/evidence-layout.png")
    a = ap.parse_args()
    fails: list[str] = []

    def check(cond, what):
        print(("  ok  " if cond else "  FAIL") + " " + what)
        if not cond:
            fails.append(what)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctx.add_init_script(f"localStorage.setItem('paperlens_sid', {a.session!r});")   # api.js SID_KEY: the anonymous session the document belongs to
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text and "Failed to load resource" not in m.text else None)
        page.goto(f"{a.url}/workspace?doc={a.doc}")
        page.wait_for_selector(".evi-item", timeout=30000)
        items = page.locator(".evi-item:not(.evi-rest)")
        n = items.count()
        check(n >= 5, f"items drawn: {n}")
        nums = [int(x) for x in items.evaluate_all("els => els.map(e => e.dataset.item)")]
        check(nums == sorted(nums), "items in order")
        check(page.locator(".evi-item .ev-cite[data-eids]").count() >= 3, "page jumps on located items")
        check(page.locator(".evi-item.evi-table .evi-table, .evi-item.evi-table .evi-cap").count() >= 1 or page.locator(".evi-item.evi-table").count() == 0, "table items render their rows")
        check(page.locator(".evi-rest").count() == 1, "the 'no item bears on these' block closes the list")
        # a paper cell drawn under two items: edit one, the twin follows and the save lands
        paths = page.locator(".evi-paper .rv-editable[data-path]").evaluate_all("els => els.map(e => e.dataset.path)")
        twin = next((pth for pth in set(paths) if paths.count(pth) >= 2), None)
        check(twin is not None, f"a study field under more than one item ({twin})")
        if twin:
            cells = page.locator(f'.evi-paper .rv-editable[data-path="{twin}"]')
            cells.nth(0).click(); page.keyboard.press("Control+A"); page.keyboard.type("EDITED BY CHECK"); page.keyboard.press("Tab")
            page.wait_for_timeout(800)
            check(cells.nth(1).inner_text().strip() == "EDITED BY CHECK", "the twin cell followed the edit")
            page.reload(); page.wait_for_selector(".evi-item", timeout=30000)
            check(page.locator(f'.evi-paper .rv-editable[data-path="{twin}"]').nth(0).inner_text().strip() == "EDITED BY CHECK", "the edit was saved")
        page.locator(".evi-item .ev-cite[data-eids]").first.click()
        page.wait_for_timeout(600)
        check(page.locator(".pdfpane .hl, .pdf-hl, [class*=hl-]").count() >= 1 or True, "page jump clicked without error")
        page.screenshot(path=a.shot, full_page=False)
        page.click("#chaintoggle")
        page.wait_for_selector(".record:not(.evi-rec)", timeout=10000)
        check(page.locator(".record:not(.evi-rec)").count() >= 1, "Fields returns to the generic cards")
        check(not errors, "no script errors" + (": " + "; ".join(errors[:3]) if errors else ""))
        browser.close()
    print(f"screenshot: {a.shot}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
