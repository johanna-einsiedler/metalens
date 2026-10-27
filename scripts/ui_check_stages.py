#!/usr/bin/env python3
"""Browser check of the staged review layout (display.review.layout "stages").

    python3 scripts/ui_check_stages.py --url http://127.0.0.1:8018 --doc <document id> --session <X-Session-Id>
                                       [--zero-doc <document id imported with no entries>] [--shots DIR]

Opens the workspace on a document imported under a preset with the staged layout and checks the
three panes: the stage panel on the left with the stage bar and the instruction, then the FIELDS
block (the stage's cells, once), then "◂ · next ▸" (next item, else "next stage ▸", else "done");
the right column (half the window) with the EVIDENCE list in its upper pane (the stage's items in
order as bare entries — page tag and quote — the current one marked, the rest folded at the end)
and the PDF in its lower pane, open by default (half the column, the bar between them drags; "▸"
collapses it to a strip along the column's foot, the icon there opens it, a page tag opens it; the
state is kept per browser) with its "◀ item k of n ▶" control; `→` / `←` move the marker and the
pane, which puts the highlight's top just under its edge at the coder's zoom (no automatic
scale); `]` / `[` move the stage; a study cell edit is saved; a free term typed into an open multi
field becomes a chip and survives a reload; "＋ add" on the entry stage adds an entry at once with
its auto id (T1, T2 …); "＋" on a child stage adds a row with its auto id (C, then T1 …); "same as
<reference>" and "copy … from" copy; the Fields button shows the generic cards and drops the
control and the panes; the page logs no script error.
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
        has_help = page.evaluate("() => { const t = document.querySelector('.stg-tab.active'); return !!(t && t.title); }")
        if has_help:
            check(page.locator(".stg-help").count() == 1, "the stage opens with its instruction")
        else:
            print("  n/a  stage instruction: the preset gives this stage no help text")
        check(letter() == "a", "the first stage is active and lettered a")
        # the three panes: the stage panel on the left, the evidence pane over the PDF pane on the right
        vw = 1400
        closed = lambda: page.evaluate("() => document.body.classList.contains('stg-pdf-closed')")
        box = lambda sel: page.locator(sel).bounding_box()
        pb0, cb, eb, db = box("#panel"), box("#pagespane"), box("#evpane"), box("#pdfpane")
        check(page.evaluate("() => document.body.classList.contains('stg-split')") and not closed(), "a fresh browser starts with the PDF pane open under the evidence pane")
        check(cb and pb0 and 0.45 * vw <= cb["width"] <= 0.55 * vw and cb["x"] >= pb0["x"] + pb0["width"] - 1, f"the right column takes half the window ({cb and round(cb['width'])}px), the stage panel the left ({pb0 and round(pb0['width'])}px)")
        check(eb and db and cb and eb["y"] < db["y"] and eb["y"] + eb["height"] <= db["y"] + 8 and 0.4 * cb["height"] <= eb["height"] <= 0.6 * cb["height"] and abs(eb["x"] - db["x"]) < 2, f"the evidence pane sits over the PDF pane, half the column ({eb and round(eb['height'])} of {cb and round(cb['height'])}px)")
        check(page.locator("#evpane .stg-evidence").count() == 1 and page.locator("#panel .stg-evidence").count() == 0 and page.locator("#panel .stg-cells").count() == 1, "the stage's evidence list is in the upper pane, the fields block in the panel")
        check(page.locator("#pdfpane #pages").is_visible() and page.locator("#pdfpane #pdfzoom").is_visible(), "the lower pane shows the pages with the zoom control")
        nav = page.locator("#pdfpane .stg-pane-nav")
        check(nav.count() == 1 and db and nav.bounding_box()["y"] < db["y"] + 120 and page.locator("#pdfpane .stg-pane-nav .stg-pdf-close").is_visible(), "the item control sits at the top of the PDF pane, with its collapse button")
        page.locator("#pdfpane .stg-pane-nav .stg-pdf-close").click(); page.wait_for_timeout(400)
        db2, eb2 = box("#pdfpane"), box("#evpane")
        check(closed() and db2 and cb and db2["height"] <= 44 and db2["y"] + db2["height"] >= cb["y"] + cb["height"] - 2 and eb2 and eb and eb2["height"] > eb["height"], f"▸ collapses the PDF to a strip along the column's foot ({db2 and round(db2['height'])}px), the evidence taking the rest")
        check(page.locator("#pdfpane .stg-pdf-strip .stg-pdf-open svg").count() == 1 and not page.locator("#pages").is_visible() and not page.locator("#pdfzoom").is_visible(), "the strip shows the PDF icon and nothing else of the pane")
        strip_k = lambda: page.locator(".stg-pdf-strip .stg-pdf-k").text_content().strip()
        check(strip_k().startswith("item 1 of"), f"the strip says '{strip_k()}'")
        # the cursor moves while the pane is collapsed, without opening it
        page.keyboard.press("ArrowRight"); page.wait_for_timeout(300)
        check(closed() and page.locator(".stg-evidence > .evi-item.cur").evaluate_all("els => els.map(e => e.dataset.i)") == ["1"] and strip_k().startswith("item 2 of"), "→ moves the cursor while collapsed, the pane stays shut")
        page.keyboard.press("ArrowLeft"); page.wait_for_timeout(300)
        page.locator(".stg-pdf-strip .stg-pdf-open").click(); page.wait_for_timeout(600)
        db3 = box("#pdfpane")
        check(not closed() and db3 and db and abs(db3["height"] - db["height"]) < 4 and page.locator("#pages").is_visible(), "the icon opens the pane again at its height")
        page.locator("#pdfpane .stg-pane-nav .stg-pdf-close").click(); page.wait_for_timeout(400)
        check(closed(), "▸ collapses it again")
        # a page tag in the list opens the pane on that item
        tags = page.locator(".stg-evidence > .evi-item[data-i] .ev-cite[data-eids]")
        if tags.count() >= 2:
            tag_i = tags.nth(1).evaluate("e => e.closest('.evi-item').dataset.i")
            tags.nth(1).click(); page.wait_for_timeout(900)
            check(not closed() and page.locator(".stg-evidence > .evi-item.cur").evaluate_all("els => els.map(e => e.dataset.i)") == [tag_i], f"a page tag opens the pane and makes its item current (item {int(tag_i) + 1})")
            page.keyboard.press("ArrowLeft"); page.wait_for_timeout(300)
        else:
            page.locator(".stg-pdf-strip .stg-pdf-open").click(); page.wait_for_timeout(600)
        page.reload(); page.wait_for_selector(".stg-bar", timeout=30000); page.wait_for_timeout(600)
        check(not closed(), "the open state survives a reload")
        check(page.evaluate("() => getComputedStyle(document.querySelector('#pages')).overflowY") in ("auto", "scroll"), "the pages scroll inside the pane")
        # the bar between the panes drags the evidence pane's height
        hb, eb = box("#hsplitter"), box("#evpane")
        if hb and eb:
            page.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2); page.mouse.down()
            page.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] - 120, steps=6); page.mouse.up(); page.wait_for_timeout(200)
            eb4 = box("#evpane")
            check(eb4 and eb4["height"] < eb["height"] - 60, f"dragging the bar up shrinks the evidence pane ({round(eb['height'])} → {eb4 and round(eb4['height'])}px)")
            page.evaluate("() => { localStorage.removeItem('metalens_evpane_h'); document.querySelector('#pagespane').style.removeProperty('--evpane-h'); }")
        # EVIDENCE as a plain list, nothing under the items
        check(page.locator(".stg-cells").count() == 1 and page.locator(".stg-evidence").count() == 1, "one evidence list and one fields block")
        check(page.locator(".stg-evidence .evi-cells").count() == 0, "no cells under the items")
        check(page.evaluate("() => { const it = document.querySelector('.stg-evidence > .evi-item'); if (!it) return false; const cs = getComputedStyle(it); return cs.borderTopWidth === '0px' && cs.borderRightWidth === '0px' && parseFloat(cs.borderBottomWidth) >= 1 && !!it.querySelector('.evi-head .ev-cite') && !!it.querySelector('.evi-body'); }"), "each item is a bare entry: the page tag and the quote, parted by a rule")
        check(page.evaluate("() => { const it = document.querySelector('.stg-evidence > .evi-item.evi-passage'); if (!it) return true; const h = it.querySelector('.evi-head').getBoundingClientRect(), b = it.querySelector('.evi-body').getBoundingClientRect(); return b.left >= h.right - 1 && Math.abs(b.top - h.top) < 12; }"), "a passage's page tag hangs at the left of its quote, on the same line")
        check(page.evaluate("() => { const it = document.querySelector('.stg-evidence .evi-item.evi-table'); if (!it) return true; const h = it.querySelector('.evi-head').getBoundingClientRect(), b = it.querySelector('.evi-body').getBoundingClientRect(); return b.top >= h.bottom - 1; }"), "a table item keeps its tag above the table")
        check(page.locator(".stg-evidence .evi-item .evi-id, .stg-evidence .evi-item .tag, .stg-evidence .evi-item .evi-note").count() == 0, "no item number, kind tag or note in the list")

        check(page.evaluate("() => { const o = document.querySelector('.stg-evidence .stg-other'), m = document.querySelector('.stg-evidence .stg-marks'); return !!m && m === document.querySelector('.stg-evidence').lastElementChild && (!o || o.nextElementSibling === m); }"), "the 'other evidence' fold closes the list, the coder's marks block after it")
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
        # definitions on hover: a checkbox label and a select carry their option's help as title
        titled = page.locator(".stg-cells .rv-multi .rv-chk[title]:not(.rv-free)")       # a free term's chip has its own title
        if titled.count():
            check(titled.first.get_attribute("title").strip() != "", f"a multi checkbox carries its option's definition ('{titled.first.get_attribute('title')[:40]}')")
            sel = page.locator(".stg-cells .rv-selwrap:not(.rv-bool) select").filter(has=page.locator("option[title]")).first
            if sel.count():
                opts = sel.locator("option[title]")
                v1 = opts.nth(0).get_attribute("value"); t1 = opts.nth(0).get_attribute("title")
                sel.select_option(v1); page.wait_for_timeout(500)
                check(sel.get_attribute("title") == t1, "a select's title is the chosen option's definition")
                if opts.count() >= 2:
                    v2 = opts.nth(1).get_attribute("value"); t2 = opts.nth(1).get_attribute("title")
                    sel.select_option(v2); page.wait_for_timeout(500)
                    check(sel.get_attribute("title") == t2 and t2 != t1, "and follows the selection")
            check(page.locator(".stg-cells .rv-key.rv-key-help .rv-help[title]").count() >= 1, "a field label carries its help on an ⓘ")
            # the custom tooltip: at once, with the text of the hovered carrier
            def tip_after_hover(selector):
                el = page.locator(selector).first
                want = el.get_attribute("data-tip")
                el.hover(); page.wait_for_timeout(100)
                tip = page.locator(".tip")
                return want, tip.is_visible(), tip.inner_text().strip()
            want, vis, got = tip_after_hover(".stg-cells .rv-key .rv-help[data-tip]")
            check(vis and got == want.strip(), f"hovering a field's ⓘ shows its help at once ('{got[:40]}')")
            page.mouse.move(5, 5); page.wait_for_timeout(100)
            check(not page.locator(".tip").is_visible(), "and leaving it hides the tip")
            want, vis, got = tip_after_hover(".stg-cells .rv-multi .rv-chk[data-tip]")
            check(vis and got == want.strip(), f"hovering a checkbox shows its option's definition ('{got[:40]}')")
            page.mouse.move(5, 5); page.wait_for_timeout(100)
            oi = page.locator(".stg-cells .rv-selwrap .rv-opt-help[data-tip]")
            if oi.count():
                want, vis, got = tip_after_hover(".stg-cells .rv-selwrap .rv-opt-help[data-tip]")
                check(vis and got == want.strip() and "—" in got, "the ⓘ after a select lists the options' definitions")
                page.mouse.move(5, 5); page.wait_for_timeout(100)
        else:
            print("  n/a  option definitions: the preset carries none")
        # a free term on an open multi field
        mo = page.locator(".stg-cells .rv-multi-other")
        check(mo.count() >= 1, "an open multi field offers an 'other…' input")
        if mo.count():
            mpath = mo.first.locator("xpath=..").get_attribute("data-path")
            mo.first.click(); page.keyboard.type("own term of the paper"); page.keyboard.press("Enter")
            page.wait_for_timeout(900)
            open_doc(a.doc)
            check(page.locator(f'.rv-multi[data-path="{mpath}"] .rv-chk.rv-free input[value="own term of the paper"]').count() == 1, "the term is a chip after a reload")
        # the coder's highlighter: mark a sentence, keep it across a reload, remove it with a click
        marks_n = lambda: page.locator(".stg-marks .stg-mark").count()
        n0 = marks_n()
        if page.evaluate("() => document.body.classList.contains('stg-pdf-closed')"):
            page.locator(".stg-pdf-strip .stg-pdf-open").click(); page.wait_for_timeout(600)
        page.locator("#pagespane .stg-pane-nav .stg-mark").click(); page.wait_for_timeout(200)
        check(page.evaluate("() => document.body.classList.contains('stg-marking')") and page.locator("#pagespane .stg-pane-nav .stg-mark.on").count() == 1, "✎ turns marking mode on")
        # the current item's highlight is on screen (anchored) and a known sentence; click a point of
        # it that the page image answers (the column's floating control may cover its left end)
        def free_point(selector, want):
            boxes = page.locator(selector).evaluate_all("els => els.map(e => { const b = e.getBoundingClientRect(); return [b.x, b.y, b.width, b.height]; })")
            for (x, y, w, h) in boxes:
                for f in (0.9, 0.75, 0.6, 0.45, 0.3, 0.15):
                    px, py = x + w * f, y + h / 2
                    if page.evaluate("([x, y, sel]) => { const e = document.elementFromPoint(x, y); return !!(e && e.matches && e.matches(sel)); }", [px, py, want]):
                        return px, py
            return None
        settled()                                                   # the column anchors once the page images have landed
        cur_ids = page.locator(".stg-evidence > .evi-item.cur .ev-cite[data-eids]").first.get_attribute("data-eids").split(",")
        pt = free_point(", ".join(f"rect.hl[data-eids~='{i}']" for i in cur_ids), "img.pageimg")
        check(pt is not None, "a point of the current item's sentence is free of the column's controls")
        if pt:
            page.mouse.click(*pt); page.wait_for_timeout(1200)
        check(marks_n() == n0 + 1 and page.locator("rect.cm").count() >= 1, f"a click on a sentence marks it ({marks_n()} mark, painted)")
        mark_page = page.locator(".stg-marks .stg-mark .ev-cite").last.inner_text().strip()
        mark_text = page.locator(".stg-marks .stg-mark .evi-body").last.inner_text().strip()
        want_page = "p. " + str(page.evaluate("() => { const r = document.querySelector('rect.hl[data-eids]'); return r.closest('.page').id.replace('page-', ''); }"))
        check(mark_page == want_page and len(mark_text) > 10, f"the block shows it with its page ({mark_page}: {mark_text[:50]}…)")
        view = page.request.get(f"{a.url}/api/documents/{a.doc}/view", headers={"X-Session-Id": a.session}).json()
        check(any(h["text"].strip("“”") == mark_text.strip("“”") for h in view.get("highlights", [])), "the document view (the JSON download's source) carries the mark")
        page.reload(); page.wait_for_selector(".stg-bar", timeout=30000); page.wait_for_timeout(600)
        check(marks_n() == n0 + 1 and page.locator("rect.cm").count() >= 1, "the mark survives a reload")
        page.keyboard.press("h"); page.wait_for_timeout(200)
        check(page.evaluate("() => document.body.classList.contains('stg-marking')"), "h turns marking mode on")
        pt = free_point("rect.cm", "rect.cm")
        check(pt is not None, "the mark is on screen and clickable")
        if pt:
            page.mouse.click(*pt); page.wait_for_timeout(1000)
        check(marks_n() == n0 and page.locator("rect.cm").count() == 0, "a second click on the mark removes it")
        page.keyboard.press("h"); page.wait_for_timeout(200)
        check(not page.evaluate("() => document.body.classList.contains('stg-marking')"), "h turns it off again")
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
        # the head of a staged paper: no Fields or Grid toggle, no ＋ entry, no CSV, no paper arrows; the strip is icons
        open_doc(a.doc)
        check(page.locator("#chaintoggle, #gridtoggle, #addfinding, #dlcsv, .chain-head .chain-nav").count() == 0 and page.locator("#rawtoggle, #dljson").count() == 2, "the staged head keeps Raw, Save and JSON only, no view toggles, ＋ entry, CSV or paper arrows")
        sb = page.locator("#doctabs").bounding_box()
        check(sb and sb["width"] <= 56 and page.locator("#doctabs .doctab .dt-ico svg").count() >= 1 and not page.locator("#doctabs .dt-name").first.is_visible(), f"the paper strip is a column of PDF icons ({sb and round(sb['width'])}px)")
        if a.zero_doc:
            open_doc(a.zero_doc)
            check(page.locator(".empty-records").count() == 0 and page.locator(".stg-evidence").count() == 1, "a paper with no entries opens on the stages")
            check(page.locator(".stg-evidence > .evi-item").count() >= 1, "its paper-level items are drawn")
            page.evaluate("() => localStorage.removeItem('metalens_stage_pdf')")
            open_doc(a.zero_doc, entry_i)
            check(not page.evaluate("() => document.body.classList.contains('stg-pdf-closed')"), "with no stored state the pane is open")
            if a.shots:
                page.wait_for_timeout(600); page.screenshot(path=os.path.join(a.shots, "zero_tasks_open.png"), full_page=False)
            page.locator("#pdfpane .stg-pane-nav .stg-pdf-close").click(); page.wait_for_timeout(300)
            check(page.evaluate("() => document.body.classList.contains('stg-pdf-closed')"), "▸ collapses it")
            if a.shots:
                page.screenshot(path=os.path.join(a.shots, "zero_tasks_closed.png"), full_page=False)
            page.reload(); page.wait_for_selector(".stg-bar", timeout=30000); page.wait_for_timeout(500)
            check(page.evaluate("() => document.body.classList.contains('stg-pdf-closed')"), "a collapse survives a reload too")
            page.locator(".stg-pdf-strip .stg-pdf-open").click(); page.wait_for_timeout(600)
            check(not page.evaluate("() => document.body.classList.contains('stg-pdf-closed')"), "the icon opens it again")
            open_doc(a.zero_doc, entry_i)
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
