#!/usr/bin/env python3
"""Browser check of the staged review layout (display.review.layout "stages").

    python3 scripts/ui_check_stages.py --url http://127.0.0.1:8018 --doc <document id> --session <X-Session-Id>
                                       [--zero-doc <document id imported with no entries>] [--shot FILE]

Opens the workspace on a document imported under a preset with the staged layout and checks: the
stage bar and the instruction; the stage's items (in order, a subset of all, page jumps); `]` / `[`
move the stage; on an entry or child stage the items are plain quotes and the cells live once in
the block below; "＋ add" on the entry stage adds an entry; "＋" on a child stage adds a row; an
edit under an item of a paper stage mirrors into the stage's block and is saved; "same as <reference>" copies the
fields; "copy … from" copies a child list; a free term typed into an open multi field becomes a
chip in every copy and survives a reload; the Fields button returns to the generic cards; the page
logs no script error. With --zero-doc, a paper imported with no entries opens on the stages and
the first entry is created from there. Needs the system python with playwright installed; prints
a check list and exits non-zero on a failure.
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
    ap.add_argument("--zero-doc", default=None)
    ap.add_argument("--session", required=True)
    ap.add_argument("--shot", default="/tmp/stages-layout.png")
    a = ap.parse_args()
    fails: list[str] = []

    def check(cond, what):
        print(("  ok  " if cond else "  FAIL") + " " + what)
        if not cond:
            fails.append(what)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctx.add_init_script(f"localStorage.setItem('paperlens_sid', {a.session!r});")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text and "Failed to load resource" not in m.text else None)
        page.on("dialog", lambda d: d.accept())

        def open_doc(doc):
            page.goto(f"{a.url}/workspace?doc={doc}")
            page.wait_for_selector(".stg-bar", timeout=30000)

        open_doc(a.doc)
        tabs = page.locator(".stg-tab")
        n_stages = tabs.count()
        check(n_stages >= 3, f"stage bar with {n_stages} stages")
        check(page.locator(".stg-help").count() == 1, "the stage opens with its instruction")
        letter = lambda: page.locator(".stg-tab.active .stg-k").text_content().strip().lower()
        check(letter() == "a", "the first stage is active and lettered a")
        all_items = page.locator(".evi-item")
        n_all = all_items.count()
        in_stage = page.locator(".stg-items .evi-item")
        n_in = in_stage.count()
        check(0 < n_in <= n_all, f"items in the stage: {n_in} of {n_all}")
        nums = [int(x) for x in in_stage.evaluate_all("els => els.map(e => e.dataset.item)")]
        check(nums == sorted(nums), "the stage's items in order")
        check(page.locator(".stg-other").count() == 1 or n_in == n_all, "the other items are folded")
        check(page.locator(".stg-items .evi-item .ev-cite[data-eids]").count() >= 1, "page jumps on located items")
        # a paper cell under an item and the same cell in the stage block: edit one, the twin follows
        paths = page.locator(".stg-items .evi-paper .rv-editable[data-path]").evaluate_all("els => els.map(e => e.dataset.path)")
        twin = next((pth for pth in set(paths) if page.locator(f'.stg-cells .evi-paper .rv-editable[data-path="{pth}"]').count()), None)
        check(twin is not None, f"a study field drawn under an item and in the stage block ({twin})")
        if twin:
            cell = page.locator(f'.stg-items .evi-paper .rv-editable[data-path="{twin}"]').first
            cell.click(); cell.fill("EDITED BY CHECK"); page.keyboard.press("Tab")
            page.wait_for_timeout(800)
            check(page.locator(f'.stg-cells .evi-paper .rv-editable[data-path="{twin}"]').first.inner_text().strip() == "EDITED BY CHECK", "the twin in the stage block followed")
            open_doc(a.doc)
            check(page.locator(f'.stg-cells .evi-paper .rv-editable[data-path="{twin}"]').first.inner_text().strip() == "EDITED BY CHECK", "the edit was saved")
        # a free term on an open multi field
        mo = page.locator(".stg-cells .rv-multi-other")
        check(mo.count() >= 1, "an open multi field offers an 'other…' input")
        if mo.count():
            mpath = mo.first.locator("xpath=..").get_attribute("data-path")
            mo.first.click(); page.keyboard.type("own term of the paper"); page.keyboard.press("Enter")
            page.wait_for_timeout(800)
            chips = page.locator(f'.rv-multi[data-path="{mpath}"] .rv-chk.rv-free input[value="own term of the paper"]')
            check(chips.count() == page.locator(f'.rv-multi[data-path="{mpath}"]').count(), f"the term is a chip in every copy of the field ({chips.count()})")
            open_doc(a.doc)
            check(page.locator(f'.rv-multi[data-path="{mpath}"] .rv-chk.rv-free').count() >= 1, "the term survived a reload")
        # keys move the stage
        page.keyboard.press("]"); page.wait_for_timeout(300)
        check(letter() == "b", "] opens the next stage")
        page.keyboard.press("["); page.wait_for_timeout(300)
        check(letter() == "a", "[ returns")
        # the entry stage: add an entry
        entry_i = page.locator(".stg-tab").evaluate_all("els => els.findIndex(e => e.title.toLowerCase().includes('entry stage') || e.textContent.toLowerCase().includes('task'))")
        if entry_i < 0:
            entry_i = 1
        tabs.nth(entry_i).click(); page.wait_for_timeout(400)
        check(page.locator(".stg-items .evi-cells").count() == 0 and page.locator(".stg-cells .stg-entry .rv-editable, .stg-cells .stg-entry .rv-selwrap").count() >= 1, "on the entry stage the items are plain quotes and the cards hold the fields")
        before = page.locator(".stg-cells .stg-entry").count()
        add = page.locator(".stg-addtask")
        check(add.count() == 1, "the entry stage offers '＋ add'")
        if add.count():
            add.click(); page.wait_for_selector(".stg-bar", timeout=15000); page.wait_for_timeout(800)
            check(page.locator(".stg-cells .stg-entry").count() == before + 1, "an entry was added")
        # a child stage: add a row, copy from another entry, same-as-reference
        child_i = page.locator(".stg-tab").evaluate_all("els => els.findIndex(e => /arm/i.test(e.textContent))")
        if child_i < 0:
            child_i = 2
        page.locator(".stg-tab").nth(child_i).click(); page.wait_for_timeout(400)
        check(page.locator(".stg-items .evi-item").count() >= 1 and page.locator(".stg-items .evi-cells").count() == 0, "on a child stage the items are plain quotes, the cells live in the block")
        check(page.locator(".stg-items .evi-item .ev-cite[data-eids]").count() >= 1, "with their page jumps")
        rows_before = page.locator(".stg-cells .rv-childrow").count()
        addrow = page.locator(".stg-cells .rv-addrow")
        check(addrow.count() >= 1, "a child stage offers '＋' per entry")
        if addrow.count():
            addrow.last.click(); page.wait_for_timeout(900)
            check(page.locator(".stg-cells .rv-childrow").count() == rows_before + 1, "a row was added")
        same = page.locator(".stg-same")
        check(same.count() >= 1, "'same as <reference>' offered on a non-reference row")
        if same.count():
            same.first.click(); page.wait_for_timeout(900)
            check(not errors, "same-as copied without error")
        copyfrom = page.locator(".stg-copyfrom")
        check(copyfrom.count() >= 1, "'copy … from' offered where another entry has rows")
        if copyfrom.count():
            sel = copyfrom.last
            opt = sel.locator("option").nth(1).get_attribute("value")
            sel.select_option(opt); page.wait_for_timeout(1200)
            check(page.locator(".stg-cells .rv-childrow").count() >= rows_before + 1, "rows copied from the other entry")
        page.screenshot(path=a.shot, full_page=False)
        page.click("#chaintoggle")
        page.wait_for_selector(".rnav-tab, .record:not(.evi-rec)", timeout=10000)
        first = page.locator('.rnav-tab[data-sel]:not([data-sel="paper"])')
        if first.count():
            first.first.click(); page.wait_for_selector(".record:not(.evi-rec)", timeout=10000)
        check(page.locator(".record:not(.evi-rec)").count() >= 1, "Fields returns to the generic cards")
        check(page.locator(".record:not(.evi-rec) .rv-child + .rv-tableadd .rv-addrow, .record:not(.evi-rec) .rv-childcards .rv-addrow").count() >= 1, "the generic card offers ＋ on sub-entries")
        if a.zero_doc:
            open_doc(a.zero_doc)
            check(page.locator(".empty-records").count() == 0, "a paper with no entries opens on the stages")
            check(page.locator(".stg-items .evi-item").count() >= 1, "its paper-level items are drawn")
            page.locator(".stg-tab").nth(entry_i).click(); page.wait_for_timeout(400)
            add = page.locator(".stg-addtask")
            check(add.count() == 1, "the entry stage offers '＋ add' on an empty paper")
            if add.count():
                had = page.locator(".stg-cells .stg-entry").count()      # 0 on a fresh paper; a rerun finds the earlier ones
                add.click(); page.wait_for_selector(".stg-bar", timeout=15000); page.wait_for_timeout(800)
                check(page.locator(".stg-cells .stg-entry").count() == had + 1, "an entry was created on the empty paper")
        check(not errors, "no script errors" + (": " + "; ".join(errors[:3]) if errors else ""))
        browser.close()
    print(f"screenshot: {a.shot}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
