#!/usr/bin/env python3
"""Browser check of the staged review layout (display.review.layout "stages").

    python3 scripts/ui_check_stages.py --url http://127.0.0.1:8018 --doc <document id> --session <X-Session-Id>
                                       [--zero-doc <document id imported with no entries>] [--shots DIR]

Opens the workspace on a document imported under a preset with the staged layout and checks the
stacked window: the records panel spans the window with the stage bar and the instruction, then
the FIELDS block (the stage's cells, once), then the EVIDENCE box (the stage's items in order, the
current one marked, the rest folded), and the PDF pane pinned to the bottom with its "◀ item k of
n ▶" control; `→` / `←` move the marker and the pane (twice the scale on a located item); `]` / `[`
move the stage; a study cell edit is saved; a free term typed into an open multi field becomes a
chip and survives a reload; "＋ add" on the entry stage adds an entry at once; "＋" on a child
stage adds a row; "same as <reference>" and "copy … from" copy; the Fields button restores the
two-column view; the page logs no script error. With --zero-doc, a paper imported with no entries
opens on the stages and the first entry is created from there. With --shots, screenshots of the
first and the results stage of --doc. Needs the system python with playwright installed; prints a
check list and exits non-zero on a failure.
"""
import argparse
import os
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
        ctx.add_init_script(f"localStorage.setItem('paperlens_sid', {a.session!r});")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text and "Failed to load resource" not in m.text else None)
        page.on("dialog", lambda d: d.accept())

        def open_doc(doc, tab=None):
            page.goto(f"{a.url}/workspace?doc={doc}")
            page.wait_for_selector(".stg-bar", timeout=30000)
            if tab is not None:
                page.locator(".stg-tab").nth(tab).click(); page.wait_for_timeout(500)

        letter = lambda: page.locator(".stg-tab.active .stg-k").text_content().strip().lower()
        nav_text = lambda: page.locator(".stg-pane-nav .stg-pane-k").text_content().strip()
        cur_index = lambda: page.locator(".stg-evidence > .evi-item.cur").evaluate_all("els => els.map(e => e.dataset.i)")
        zoom = lambda: page.evaluate("() => getComputedStyle(document.querySelector('#pages')).getPropertyValue('--pdf-zoom').trim()")
        tab_index = lambda pat: page.locator(".stg-tab").evaluate_all(f"els => els.findIndex(e => /{pat}/i.test(e.textContent))")

        open_doc(a.doc)
        tabs = page.locator(".stg-tab")
        n_stages = tabs.count()
        check(n_stages >= 3, f"stage bar with {n_stages} stages")
        check(page.locator(".stg-help").count() == 1, "the stage opens with its instruction")
        check(letter() == "a", "the first stage is active and lettered a")
        # the stacked window
        vw, vh = 1400, 1000
        pb = page.locator("#panel").bounding_box(); gb = page.locator("#pagespane").bounding_box()
        check(page.evaluate("() => document.body.classList.contains('stg-stack')"), "the body is in stacked mode")
        check(pb and pb["width"] >= 0.6 * vw, f"the records panel spans the window ({pb and round(pb['width'])}px)")
        check(gb and abs(gb["y"] + gb["height"] - vh) <= 2 and 0.3 * vh <= gb["height"] <= 0.7 * vh, f"the PDF pane is pinned to the bottom ({gb and round(gb['height'])}px high)")
        check(page.locator("#pagespane .stg-paneh").count() == 1 and page.locator("#pagespane .stg-pane-nav").count() == 1, "the pane has its drag handle and item control")
        check(page.evaluate("() => getComputedStyle(document.querySelector('#pages')).overflowY") in ("auto", "scroll"), "the pages scroll inside the pane")
        # FIELDS before EVIDENCE, nothing under the items
        check(page.locator(".stg-cells").count() == 1 and page.locator(".stg-evidence").count() == 1, "one fields block and one evidence box")
        check(page.evaluate("() => { const f = document.querySelector('.stg-cells'), e = document.querySelector('.stg-evidence'); return !!(f && e && (f.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING)); }"), "the fields block comes before the evidence box")
        check(page.locator(".stg-evidence .evi-cells").count() == 0, "no cells under the items")
        all_items = page.locator(".evi-item").count()
        in_stage = page.locator(".stg-evidence > .evi-item")
        n_in = in_stage.count()
        check(0 < n_in <= all_items, f"items in the stage: {n_in} of {all_items}")
        nums = [int(x) for x in in_stage.evaluate_all("els => els.map(e => e.dataset.item)")]
        check(nums == sorted(nums), "the stage's items in order")
        check(page.locator(".stg-evidence .stg-other").count() == 1 or n_in == all_items, "the other items are folded under the box")
        check(page.locator(".stg-evidence > .evi-item .ev-cite[data-eids]").count() >= 1, "page jumps on located items")
        # the cursor and the pane
        check(cur_index() == ["0"], "the first item is current")
        check(nav_text() == f"item 1 of {n_in}", f"the pane says '{nav_text()}'")
        located = page.locator(".stg-evidence > .evi-item.cur .ev-cite[data-eids]").count() == 1
        page.wait_for_timeout(900)
        check(zoom() == ("2" if located else "1"), f"the pane shows the current item at {'twice the' if located else 'normal'} scale (zoom {zoom()})")
        st0 = page.evaluate("() => [document.querySelector('#pages').scrollTop, document.querySelector('#pages').scrollLeft]")
        if n_in >= 2:
            page.keyboard.press("ArrowRight"); page.wait_for_timeout(900)
            check(cur_index() == ["1"] and nav_text() == f"item 2 of {n_in}", "→ marks the second item and the pane says so")
            st1 = page.evaluate("() => [document.querySelector('#pages').scrollTop, document.querySelector('#pages').scrollLeft]")
            same_spot = page.evaluate("() => { const a = document.querySelector('.stg-evidence > .evi-item[data-i=\"0\"] .ev-cite'), b = document.querySelector('.stg-evidence > .evi-item[data-i=\"1\"] .ev-cite'); return !!(a && b && a.dataset.eids && a.dataset.eids === b.dataset.eids); }")
            check(st1 != st0 or same_spot, f"the pane moved to the item ({st0} → {st1})")
            page.locator(".stg-pane-nav button[data-d='-1']").click(); page.wait_for_timeout(600)
            check(cur_index() == ["0"], "◀ on the pane returns to the first item")
            page.locator(".stg-evidence > .evi-item[data-i='1'] .evi-body").click(); page.wait_for_timeout(600)
            check(cur_index() == ["1"], "clicking an item makes it current")
            page.keyboard.press("ArrowLeft"); page.wait_for_timeout(400)
            check(cur_index() == ["0"], "← returns")
            for _ in range(n_in + 2):
                page.keyboard.press("ArrowRight")
            page.wait_for_timeout(400)
            check(cur_index() == [str(n_in - 1)], "the cursor stops at the last item")
        if a.shots:
            page.screenshot(path=os.path.join(a.shots, "stage_a.png"), full_page=False)
        # a study cell in the fields block: edit, reload, saved
        cells = page.locator(".stg-cells .evi-paper .rv-editable[data-path]")
        check(cells.count() >= 1, "the fields block holds the study cells")
        if cells.count():
            pth = cells.first.get_attribute("data-path")
            cells.first.click(); cells.first.fill("EDITED BY CHECK"); page.keyboard.press("Tab"); page.wait_for_timeout(900)
            open_doc(a.doc)
            check(page.locator(f'.stg-cells .evi-paper .rv-editable[data-path="{pth}"]').first.inner_text().strip() == "EDITED BY CHECK", f"the edit of {pth} was saved")
        # a free term on an open multi field
        mo = page.locator(".stg-cells .rv-multi-other")
        check(mo.count() >= 1, "an open multi field offers an 'other…' input")
        if mo.count():
            mpath = mo.first.locator("xpath=..").get_attribute("data-path")
            mo.first.click(); page.keyboard.type("own term of the paper"); page.keyboard.press("Enter")
            page.wait_for_timeout(900)
            open_doc(a.doc)
            check(page.locator(f'.rv-multi[data-path="{mpath}"] .rv-chk.rv-free input[value="own term of the paper"]').count() == 1, "the term is a chip after a reload")
        # keys move the stage; the cursor restarts on the first item
        page.keyboard.press("]"); page.wait_for_timeout(500)
        check(letter() == "b", "] opens the next stage")
        check(cur_index() in (["0"], []), "the cursor restarts on the first item of the stage")
        page.keyboard.press("["); page.wait_for_timeout(400)
        check(letter() == "a", "[ returns")
        # the entry stage: add an entry, it shows at once
        entry_i = tab_index("task|entry|sample")
        if entry_i < 0:
            entry_i = 1
        open_doc(a.doc, entry_i)
        check(page.locator(".stg-evidence .evi-cells").count() == 0 and page.locator(".stg-cells .stg-entry .rv-editable, .stg-cells .stg-entry .rv-selwrap").count() >= 1, "on the entry stage the cards hold the fields and the items are plain quotes")
        before = page.locator(".stg-cells .stg-entry").count()
        add = page.locator(".stg-addtask")
        check(add.count() == 1, "the entry stage offers '＋ add'")
        if add.count():
            add.click(); page.wait_for_selector(".stg-bar", timeout=15000); page.wait_for_timeout(900)
            check(page.locator(".stg-cells .stg-entry").count() == before + 1, "an entry was added and shows in the fields block at once")
        # a child stage: add rows, same-as-reference, copy from another entry
        child_i = tab_index("arm")
        if child_i < 0:
            child_i = 2
        open_doc(a.doc, child_i)
        check(page.locator(".stg-evidence .evi-cells").count() == 0 and page.locator(".stg-evidence > .evi-item .ev-cite[data-eids]").count() >= 1, "on a child stage the items are plain quotes with page jumps")
        card = lambda: page.locator(".stg-cells .stg-entry").last
        rows_before = card().locator(".rv-childrow").count()
        addrow = card().locator(".rv-addrow")
        check(addrow.count() >= 1, "a child stage offers '＋' per entry")
        if addrow.count():
            addrow.last.click(); page.wait_for_timeout(900)
            addrow = card().locator(".rv-addrow"); addrow.last.click(); page.wait_for_timeout(900)
            check(card().locator(".rv-childrow").count() == rows_before + 2, "two rows were added")
        if not card().locator(".stg-same").count():                 # no reference row yet: make the first one the reference
            ref = card().locator('.rv-selwrap[data-path$="[0].is_baseline"] select')
            if ref.count():
                ref.first.select_option("true"); page.wait_for_timeout(800); open_doc(a.doc, child_i)
        same = card().locator(".stg-same")
        check(same.count() >= 1, "'same as <reference>' offered on a non-reference row")
        if same.count():
            same.first.click(); page.wait_for_timeout(900)
            check(not errors, "same-as copied without error")
        copyfrom = page.locator(".stg-copyfrom")
        if copyfrom.count():
            n0 = card().locator(".rv-childrow").count()
            sel = copyfrom.last
            opt = sel.locator("option").nth(1).get_attribute("value")
            sel.select_option(opt); page.wait_for_timeout(1200)
            check(page.locator(".stg-cells .rv-childrow").count() >= 1 and not errors, f"rows copied from the other entry (had {n0})")
        else:
            print("  n/a  'copy … from': no other entry with rows")
        if a.shots:
            res_i = tab_index("result")
            if res_i >= 0:
                open_doc(a.doc, res_i); page.wait_for_timeout(900)
                page.screenshot(path=os.path.join(a.shots, "stage_f.png"), full_page=False)
        # the Fields view: the two-column window again
        open_doc(a.doc)
        page.click("#chaintoggle")
        page.wait_for_selector(".rnav-tab, .record:not(.evi-rec)", timeout=10000)
        first = page.locator('.rnav-tab[data-sel]:not([data-sel="paper"])')
        if first.count():
            first.first.click(); page.wait_for_selector(".record:not(.evi-rec)", timeout=10000)
        check(page.locator(".record:not(.evi-rec)").count() >= 1, "Fields returns to the generic cards")
        gb2 = page.locator("#pagespane").bounding_box()
        check(not page.evaluate("() => document.body.classList.contains('stg-stack')") and gb2 and gb2["y"] < 100 and page.locator("#pagespane .stg-pane-nav").count() == 0, "and to the two-column window, the pane control gone")
        check(page.locator(".record:not(.evi-rec) .rv-child + .rv-tableadd .rv-addrow, .record:not(.evi-rec) .rv-childcards .rv-addrow").count() >= 1, "the generic card offers ＋ on sub-entries")
        if a.zero_doc:
            open_doc(a.zero_doc)
            check(page.locator(".empty-records").count() == 0 and page.evaluate("() => document.body.classList.contains('stg-stack')"), "a paper with no entries opens on the stacked stages")
            check(page.locator(".stg-evidence > .evi-item").count() >= 1, "its paper-level items are drawn")
            open_doc(a.zero_doc, entry_i)
            add = page.locator(".stg-addtask")
            check(add.count() == 1, "the entry stage offers '＋ add' on an empty paper")
            if add.count():
                had = page.locator(".stg-cells .stg-entry").count()      # 0 on a fresh paper; a rerun finds the earlier ones
                add.click(); page.wait_for_selector(".stg-bar", timeout=15000); page.wait_for_timeout(900)
                check(page.locator(".stg-cells .stg-entry").count() == had + 1, "an entry was created on the empty paper")
        check(not errors, "no script errors" + (": " + "; ".join(errors[:3]) if errors else ""))
        browser.close()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
