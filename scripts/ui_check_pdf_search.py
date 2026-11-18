#!/usr/bin/env python3
"""Browser check of the PDF pane's search box and of a staged review's stage with evidence "none".

    python3 scripts/ui_check_pdf_search.py --url http://127.0.0.1:8018 --doc <document id> --session <X-Session-Id>
                                           [--q treatment] [--noev-doc <document id>] [--shots DIR]

--doc: a document with a PDF. The search box sits in the strip over the pages, left of the zoom;
typing a text lists the pages it is printed on with their match counts, the counter reads
"1 / n", the first match is painted on its page (a located rect) and scrolled to; Enter moves to
the next match and Shift+Enter back; a click on a page in the list goes to that page's first
match; `/` focuses the box; Esc clears the list, the counter and the highlight; a text found
nowhere reads "none"; the page logs no script error.
--noev-doc: a document under a staged preset whose FIRST stage says evidence "none": that stage
lists no items, has no "other evidence" fold, lights none of them in the PDF (no `.ctx` rect), its
item control reads "no items", the page tags of its paper table still open the page; the next
stage lists items and lights them again. Needs the system python with playwright installed;
prints a check list and exits non-zero on a failure.
"""
import argparse
import os
import re
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
    ap.add_argument("--q", default="treatment", help="a text printed on more than one page of --doc")
    ap.add_argument("--noev-doc", default=None)
    ap.add_argument("--shots", default=None)
    a = ap.parse_args()
    fails: list[str] = []

    def check(cond, what):
        print(("  ok  " if cond else "  FAIL") + " " + what)
        if not cond:
            fails.append(what)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctx.add_init_script(f"localStorage.setItem('paperlens_sid', {a.session!r}); localStorage.setItem('metalens_stage_pdf', 'open');")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text and "Failed to load resource" not in m.text else None)
        page.on("dialog", lambda d: d.accept())

        counter = lambda: page.locator("#pdfsearch-k").text_content().strip()
        located = lambda: page.locator("rect.hl.located").evaluate_all("els => els.map(r => r.closest('.page').id)")
        shot = lambda name: a.shots and page.screenshot(path=os.path.join(a.shots, name), full_page=False)
        if a.shots:
            os.makedirs(a.shots, exist_ok=True)

        # ── the search box ──
        page.goto(f"{a.url}/workspace?doc={a.doc}")
        page.wait_for_selector("#pages .page img.pageimg", timeout=30000)
        page.wait_for_function("() => [...document.querySelectorAll('#pages img.pageimg')].every(i => i.complete)", timeout=30000)
        box = page.locator("#pdfsearch")
        check(box.is_visible() and page.locator("#pdfzoom").is_visible(), "the search box shows in the strip over the pages, with the zoom")
        bb, zb = box.bounding_box(), page.locator("#pdfzoom").bounding_box()
        check(bb and zb and bb["x"] + bb["width"] <= zb["x"] + 1 and abs(bb["y"] - zb["y"]) < 6, "the box sits left of the zoom control, on its line")
        page.locator("#pdfsearch-q").fill(a.q)
        page.wait_for_function("() => /^\\d+ \\/ \\d+\\+?$/.test(document.querySelector('#pdfsearch-k').textContent.trim())", timeout=15000)
        m = re.match(r"^(\d+) / (\d+)", counter())
        n = int(m.group(2)) if m else 0
        check(m is not None and m.group(1) == "1" and n >= 2, f"the counter reads '{counter()}' after typing '{a.q}'")
        items = page.locator("#pdfsearch-list li")
        n_pages = items.count()
        check(n_pages >= 2 and page.locator("#pdfsearch-list").is_visible(), f"the list names {n_pages} pages")
        check(all(re.match(r"^p\. \d+\s*\d+ match(es)?$", t.strip()) for t in items.all_inner_texts()), "each row: the page and its match count")
        first = items.first.inner_text()
        check(items.first.evaluate("e => e.classList.contains('cur')"), "the first page is marked current")
        loc = located()
        check(len(loc) == 1 and loc[0] == "page-" + re.search(r"p\. (\d+)", first).group(1), f"one located highlight, on the first page listed ({loc})")
        page.wait_for_timeout(700)
        rb = page.locator("rect.hl.located").bounding_box(); pb = page.locator("#pages").bounding_box()
        check(rb and pb and pb["y"] <= rb["y"] <= pb["y"] + pb["height"], "the pane scrolled the match into view")
        shot("search-first.png")
        # Enter: the next match; Shift+Enter: back
        page.locator("#pdfsearch-q").press("Enter")
        page.wait_for_function("() => document.querySelector('#pdfsearch-k').textContent.trim().startsWith('2 /')", timeout=5000)
        check(counter().startswith("2 /") and len(located()) == 1, f"Enter moves to the second match ('{counter()}')")
        page.locator("#pdfsearch-q").press("Shift+Enter")
        page.wait_for_function("() => document.querySelector('#pdfsearch-k').textContent.trim().startsWith('1 /')", timeout=5000)
        check(counter().startswith("1 /"), "Shift+Enter moves back to the first")
        page.locator("#pdfsearch button[data-d='1']").click()
        check(counter().startswith("2 /"), "› moves on as Enter does")
        # a click on the last page in the list
        last = items.nth(n_pages - 1)
        lp = re.search(r"p\. (\d+)", last.inner_text()).group(1)
        last.click(); page.wait_for_timeout(300)
        check(located() == [f"page-{lp}"] and last.evaluate("e => e.classList.contains('cur')"), f"a click on the list goes to that page's first match (p. {lp})")
        # wrap-around and Esc
        for _ in range(n):
            page.locator("#pdfsearch-q").press("Enter")
        check(re.match(r"^\d+ / ", counter()) is not None, "Enter wraps around the matches")
        page.locator("#pdfsearch-q").press("Escape")
        page.wait_for_timeout(200)
        check(counter() == "" and not page.locator("#pdfsearch-list").is_visible() and located() == [] and page.locator("#pdfsearch-q").input_value() == "", "Esc clears the text, the list, the counter and the highlight")
        # `/` focuses the box; a text found nowhere
        page.locator("body").click(position={"x": 5, "y": 5}); page.keyboard.press("/")
        check(page.evaluate("() => document.activeElement && document.activeElement.id === 'pdfsearch-q'"), "/ focuses the search box")
        page.locator("#pdfsearch-q").fill("zqxjvwkp")
        page.wait_for_function("() => document.querySelector('#pdfsearch-k').textContent.trim() === 'none'", timeout=15000)
        check(counter() == "none" and not page.locator("#pdfsearch-list").is_visible() and located() == [], "a text found nowhere reads 'none', no list, no highlight")
        page.locator("#pdfsearch-q").press("Escape")
        shot("search-none.png")

        # ── a stage with evidence "none" ──
        if a.noev_doc:
            page.goto(f"{a.url}/workspace?doc={a.noev_doc}")
            page.wait_for_selector(".stg-bar", timeout=30000)
            page.wait_for_function("() => [...document.querySelectorAll('#pages img.pageimg')].every(i => i.complete)", timeout=30000)
            page.wait_for_timeout(400)
            noev = page.evaluate("() => document.body.classList.contains('stg-noev')")
            check(noev, "the first stage is a blind stage (body.stg-noev)")
            check(page.locator(".stg-evidence > .evi-item").count() == 0 and page.locator(".stg-evidence .stg-head").filter(has_text="Evidence").count() == 0, "no item list and no Evidence heading")
            check(page.locator(".stg-evidence .stg-other").count() == 0, "no 'other evidence' fold")
            check(page.locator(".stg-evidence .stg-noitems").count() == 0, "no 'no item bears on' note either")
            check(page.locator("rect.hl.ctx").count() == 0 and page.locator("rect.hl.sel").count() == 0, "nothing of the items is lit in the PDF")
            check(page.locator(".stg-pane-nav .stg-pane-k").text_content().strip() == "no items", "the item control reads 'no items'")
            check(page.locator(".stg-evidence .stg-marks").count() == 1, "the coder's marks block stays")
            eb, db = page.locator("#evpane").bounding_box(), page.locator("#pdfpane").bounding_box()
            check(eb and db and eb["height"] < db["height"] and not page.locator("#hsplitter").is_visible(), f"the upper pane shrinks to its content ({eb and round(eb['height'])}px), the PDF takes the column ({db and round(db['height'])}px)")
            tags = page.locator("#panel .stg-cells .evi-paper .ev-cite[data-eids]")
            check(tags.count() >= 1, f"the paper table's page tags stay ({tags.count()})")
            shot("stage-noev.png")
            if tags.count():
                tp = tags.first.get_attribute("data-page")
                tags.first.click(); page.wait_for_timeout(600)
                sel = page.locator("rect.hl.sel").evaluate_all("els => els.map(r => r.closest('.page').id)")
                check(sel and all(s == f"page-{tp}" for s in sel), f"a page tag lights its row on p. {tp} ({sel})")
            # the next stage shows its items again
            page.keyboard.press("]"); page.wait_for_timeout(600)
            check(not page.evaluate("() => document.body.classList.contains('stg-noev')"), "the next stage is not blind")
            check(page.locator(".stg-evidence > .evi-item").count() >= 1 and page.locator("rect.hl.ctx").count() >= 1, "it lists its items and lights them in the PDF")
            check(page.locator("#hsplitter").is_visible(), "the pane divider is back")
            shot("stage-items.png")
            page.keyboard.press("["); page.wait_for_timeout(400)
            check(page.locator("rect.hl.ctx").count() == 0 and page.locator("rect.hl.sel").count() == 0 and page.locator(".stg-evidence > .evi-item").count() == 0, "back on the blind stage the highlights go dark again")

        check(not errors, "no script errors" + (f": {errors[:3]}" if errors else ""))
        browser.close()
    print(("ALL OK" if not fails else f"{len(fails)} FAILED"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
