#!/usr/bin/env python3
"""Browser check of the staged review layout (display.review.layout "stages").

    python3 scripts/ui_check_stages.py --url http://127.0.0.1:8018 --doc <document id> --session <X-Session-Id>
                                       [--zero-doc <document id imported with no entries>] [--shots DIR]

Opens the workspace on a document imported under a preset with the staged layout and checks the
two columns: the stage panel on the left (three fifths of the window) with the stage bar and the
instruction, then the EVIDENCE list (the stage's items in order as bare entries — page tag and
quote — the current one marked, the rest folded at the end), then the FIELDS block (the stage's
cells, once), then "◂ · next ▸" (next item, else "next stage ▸", else "done"); the PDF column on
the right (two fifths) with its "◀ item k of n ▶" control; `→` / `←` move the marker and the column, which puts the highlight's top just
under its edge at the coder's zoom (no automatic scale); `]` / `[` move the stage; a study cell
edit is saved; a free term typed into an open multi field becomes a chip and survives a reload;
"＋ add" on the entry stage adds an entry at once with its auto id (T1, T2 …); "＋" on a child
stage adds a row with its auto id (C, then T1 …); "same as <reference>" and "copy … from" copy;
the Fields button shows the generic cards and drops the control; the page logs no script error.
With --zero-doc, a paper imported with no entries opens on the stages and the first entry is
created from there. With --shots, screenshots of the entry and the results stage of --doc and of
the entry stage of --zero-doc. Needs the system python with playwright installed; prints a
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
        # the two columns: the stage panel left, the PDF right with the item control
        vw = 1400
        pb = page.locator("#panel").bounding_box(); gb = page.locator("#pagespane").bounding_box()
        check(not page.evaluate("() => document.body.classList.contains('stg-stack')") and page.locator("#pagespane .stg-paneh").count() == 0, "no stacked mode left")
        check(pb and gb and gb["x"] >= pb["x"] + pb["width"] - 1 and 0.35 * vw <= gb["width"] <= 0.45 * vw and gb["height"] > 0.8 * 947, f"the stage panel sits left ({pb and round(pb['width'])}px) and the PDF column right at two fifths ({gb and round(gb['width'])}px)")
        check(page.locator("#pagespane .stg-pane-nav").count() == 1 and page.locator("#pagespane .stg-pane-nav").bounding_box()["y"] < 120, "the item control sits at the top of the PDF column")
        check(page.evaluate("() => getComputedStyle(document.querySelector('#pages')).overflowY") in ("auto", "scroll"), "the pages scroll inside the column")
        # EVIDENCE as a plain list before FIELDS, nothing under the items
        check(page.locator(".stg-cells").count() == 1 and page.locator(".stg-evidence").count() == 1, "one evidence list and one fields block")
        check(page.evaluate("() => { const f = document.querySelector('.stg-cells'), e = document.querySelector('.stg-evidence'); return !!(f && e && (e.compareDocumentPosition(f) & Node.DOCUMENT_POSITION_FOLLOWING)); }"), "the evidence list comes before the fields block")
        check(page.locator(".stg-evidence .evi-cells").count() == 0, "no cells under the items")
        check(page.evaluate("() => { const it = document.querySelector('.stg-evidence > .evi-item'); if (!it) return false; const cs = getComputedStyle(it); return cs.borderTopWidth === '0px' && cs.borderRightWidth === '0px' && parseFloat(cs.borderBottomWidth) >= 1 && !!it.querySelector('.evi-head .ev-cite') && !!it.querySelector('.evi-body'); }"), "each item is a bare entry: the page tag, then the quote, parted by a rule")
        check(page.locator(".stg-evidence .evi-item .evi-id, .stg-evidence .evi-item .tag, .stg-evidence .evi-item .evi-note").count() == 0, "no item number, kind tag or note in the list")

        check(page.evaluate("() => { const o = document.querySelector('.stg-evidence .stg-other'); return !o || o === document.querySelector('.stg-evidence').lastElementChild; }"), "the 'other evidence' fold closes the list")
        all_items = page.locator(".evi-item").count()
        in_stage = page.locator(".stg-evidence > .evi-item")
        n_in = in_stage.count()
        check(0 < n_in <= all_items, f"items in the stage: {n_in} of {all_items}")
        nums = [int(x) for x in in_stage.evaluate_all("els => els.map(e => e.dataset.item)")]
        check(nums == sorted(nums), "the stage's items in order")
        foot = page.locator(".stg-foot .stg-next")
        check(foot.count() == 1 and page.evaluate("() => { const f = document.querySelector('.stg-foot'), c = document.querySelector('.stg-cells'); return !!(f && c && (c.compareDocumentPosition(f) & Node.DOCUMENT_POSITION_FOLLOWING)); }"), "the next button sits below the fields block")
        check(foot.inner_text().strip() == ("next ▸" if n_in >= 2 else "next stage ▸"), f"it reads '{foot.inner_text().strip()}'")
        check(page.locator(".stg-evidence .stg-other").count() == 1 or n_in == all_items, "the other items are folded under the box")
        check(page.locator(".stg-evidence > .evi-item .ev-cite[data-eids]").count() >= 1, "page jumps on located items")
        # the cursor and the pane
        check(cur_index() == ["0"], "the first item is current")
        check(nav_text() == f"item 1 of {n_in}", f"the pane says '{nav_text()}'")
        page.wait_for_timeout(900)
        check(zoom() in ("1", ""), f"the pane shows the page at the coder's zoom, 100% by default (zoom {zoom() or '1'})")
        # the current item's highlight: its top just under the pane's edge
        # the current highlight's top under the column's edge — or the column scrolled as far as it goes
        anchor = lambda: page.evaluate("() => { const cur = document.querySelector('.stg-evidence > .evi-item.cur .ev-cite[data-eids]'); if (!cur) return null; const ids = cur.dataset.eids.split(','); const rs = ids.flatMap((i) => [...document.querySelectorAll(`rect.hl[data-eids~='${i}']`)]); if (!rs.length) return null; const pg = document.querySelector('#pages'); const pb = pg.getBoundingClientRect(); return [Math.min(...rs.map((r) => r.getBoundingClientRect().top)) - pb.top, pg.scrollTop >= pg.scrollHeight - pg.clientHeight - 1]; }")
        anchored = lambda a: a is None or 0 <= a[0] <= 40 or (a[1] and a[0] > 40)
        def settled():                                   # the column anchors again as page images land: give it a moment
            for _ in range(20):
                a = anchor()
                if anchored(a):
                    return a
                page.wait_for_timeout(300)
            return anchor()
        a0 = settled()
        check(anchored(a0), f"the highlight's top sits just under the column's top ({a0 and round(a0[0])}px)")
        st0 = page.evaluate("() => [document.querySelector('#pages').scrollTop, document.querySelector('#pages').scrollLeft]")
        if n_in >= 2:
            page.keyboard.press("ArrowRight"); page.wait_for_timeout(900)
            check(cur_index() == ["1"] and nav_text() == f"item 2 of {n_in}", "→ marks the second item and the pane says so")
            st1 = page.evaluate("() => [document.querySelector('#pages').scrollTop, document.querySelector('#pages').scrollLeft]")
            same_spot = page.evaluate("() => { const a = document.querySelector('.stg-evidence > .evi-item[data-i=\"0\"] .ev-cite'), b = document.querySelector('.stg-evidence > .evi-item[data-i=\"1\"] .ev-cite'); return !!(a && b && a.dataset.eids && a.dataset.eids === b.dataset.eids); }")
            at_end = page.evaluate("() => { const pg = document.querySelector('#pages'); return pg.scrollTop >= pg.scrollHeight - pg.clientHeight - 1; }")
            check(st1 != st0 or same_spot or at_end, f"the column moved to the item ({st0} → {st1})")
            a1 = settled()
            check(anchored(a1), f"and anchored its highlight's top ({a1 and round(a1[0])}px)")
            check(zoom() in ("1", ""), "the zoom stayed at 100% after moving")
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
            check(foot.inner_text().strip() == "next stage ▸", "on the last item the button reads 'next stage ▸'")
            foot.click(); page.wait_for_timeout(500)
            check(letter() == "b" and cur_index() in (["0"], []), "and moves to the next stage, its cursor on the first item")
            page.keyboard.press("["); page.wait_for_timeout(500)             # back to the first stage, cursor on its first item
            foot = page.locator(".stg-foot .stg-next")
            if n_in >= 2:
                foot.click(); page.wait_for_timeout(500)
                check(cur_index() == ["1"], "'next ▸' moves to the next item")
                page.locator(".stg-foot .stg-foot-prev").click(); page.wait_for_timeout(400)
                check(cur_index() == ["0"], "'◂' moves back")
        if a.shots:
            page.screenshot(path=os.path.join(a.shots, "stage_a.png"), full_page=False)
            page.locator(".stg-tab").nth(max(1, tab_index("task|entry|sample"))).click(); page.wait_for_timeout(900)
            page.screenshot(path=os.path.join(a.shots, "tasks.png"), full_page=False)
            open_doc(a.doc)
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
            ids = page.locator('.stg-cells .stg-entry .rv-editable[data-path="task_id"], .stg-cells .stg-entry .rv-cell[data-path="task_id"]').all_inner_texts()
            ids = [x.strip() for x in ids]
            want = next(f"T{n}" for n in range(1, 100) if f"T{n}" not in ids[:-1])
            check(ids and ids[-1] == want, f"the new entry got the next free id {want} (ids now {ids})")
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
            arm_ids = [x.strip() for x in card().locator('.rv-childrow .rv-editable[data-path$=".arm_id"]').all_inner_texts()]
            check(arm_ids[-2:] == (["C", "T1"] if rows_before == 0 else [next(f"T{n}" for n in range(1, 100) if f"T{n}" not in arm_ids[:-2]), next(f"T{n}" for n in range(1, 100) if f"T{n}" not in arm_ids[:-1])]), f"the new arms got their auto ids ({arm_ids})")
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
                page.screenshot(path=os.path.join(a.shots, "results.png"), full_page=False)
        # the Fields view: the two-column window again
        open_doc(a.doc)
        page.click("#chaintoggle")
        page.wait_for_selector(".rnav-tab, .record:not(.evi-rec)", timeout=10000)
        first = page.locator('.rnav-tab[data-sel]:not([data-sel="paper"])')
        if first.count():
            first.first.click(); page.wait_for_selector(".record:not(.evi-rec)", timeout=10000)
        check(page.locator(".record:not(.evi-rec)").count() >= 1, "Fields returns to the generic cards")
        check(page.locator("#pagespane .stg-pane-nav").count() == 0, "and the item control is gone")
        check(page.locator(".record:not(.evi-rec) .rv-child + .rv-tableadd .rv-addrow, .record:not(.evi-rec) .rv-childcards .rv-addrow").count() >= 1, "the generic card offers ＋ on sub-entries")
        if a.zero_doc:
            open_doc(a.zero_doc)
            check(page.locator(".empty-records").count() == 0 and page.locator(".stg-evidence").count() == 1, "a paper with no entries opens on the stages")
            check(page.locator(".stg-evidence > .evi-item").count() >= 1, "its paper-level items are drawn")
            open_doc(a.zero_doc, entry_i)
            if a.shots:
                page.wait_for_timeout(600); page.screenshot(path=os.path.join(a.shots, "zero_tasks.png"), full_page=False)
            add = page.locator(".stg-addtask")
            check(add.count() == 1, "the entry stage offers '＋ add' on an empty paper")
            if add.count():
                had = page.locator(".stg-cells .stg-entry").count()      # 0 on a fresh paper; a rerun finds the earlier ones
                add.click(); page.wait_for_selector(".stg-bar", timeout=15000); page.wait_for_timeout(900)
                check(page.locator(".stg-cells .stg-entry").count() == had + 1, "an entry was created on the empty paper")
                check(page.locator('.stg-cells .stg-entry .rv-editable[data-path="task_id"]').last.inner_text().strip() == ("T1" if had == 0 else f"T{had + 1}"), "with its auto id")
        check(not errors, "no script errors" + (": " + "; ".join(errors[:3]) if errors else ""))
        browser.close()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
