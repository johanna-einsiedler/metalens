#!/usr/bin/env python3
"""Browser check of a stage's `sections` and of a table drawn as stacked blocks (`display: "stacked"`).

    python3 scripts/ui_check_stage_sections.py --url http://127.0.0.1:8018 --doc <document id> --session <X-Session-Id>
                                               [--sections-stage 0] [--stacked-stage 1] [--table j_tasks] [--typed-table j_arms] [--shots DIR]

--doc: a document under a staged preset whose --sections-stage declares `sections` and an `inline`
pair inside one of them, with at least one field of the stage in no section, and whose
--stacked-stage shows a paper table declared `display: "stacked"` with row evidence. Checks: the
sections render as labelled blocks in order, the help under the label, each block holding its
fields, an inline pair kept on one line inside its block, the fields in no section after the
last block; a select edited inside a block is saved; the stacked table shows one block per row —
the row's page tag before the lead column, the lead's first line set off from the rest, the other
columns as label-and-control lines, ✕ at the top right — no table; a select in a block saves into
that row; ＋ row adds a block and ✕ removes it; a page tag lights the row's passage, and so does a
click on the block's head (the head keeps the caret; a further click inside it moves the caret
only) and on the text of a row of --typed-table, a paper table drawn as a table with row evidence
on the same stage; the tag says what it does on hover; no script errors. Needs the system python with playwright installed; prints a check list and exits non-zero
on a failure.
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
    ap.add_argument("--session", required=True)
    ap.add_argument("--sections-stage", type=int, default=0)
    ap.add_argument("--stacked-stage", type=int, default=1)
    ap.add_argument("--table", default="j_tasks")
    ap.add_argument("--typed-table", default="j_arms")
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
        H = {"X-Session-Id": a.session}
        pm = lambda: page.request.get(f"{a.url}/api/documents/{a.doc}/view", headers=H).json()["paper_metadata"]
        shot = lambda name: a.shots and page.screenshot(path=os.path.join(a.shots, name), full_page=False)
        if a.shots:
            os.makedirs(a.shots, exist_ok=True)

        def open_stage(i):
            page.goto(f"{a.url}/workspace?doc={a.doc}")
            page.wait_for_selector(".stg-bar", timeout=30000)
            page.locator(".stg-tab").nth(i).click(); page.wait_for_timeout(500)

        # ── sections ──
        open_stage(a.sections_stage)
        secs = page.locator("#panel .stg-cells .stg-section")
        n = secs.count()
        check(n >= 2, f"the stage draws {n} sections")
        labels = page.locator("#panel .stg-cells .stg-section .stg-section-h").all_inner_texts()
        check(len(labels) == n and all(x.strip() for x in labels), f"each block carries its label: {labels}")
        check(page.locator("#panel .stg-cells .stg-section .stg-section-help").count() >= 1, "a section's help sits under its label")
        per = page.evaluate("() => [...document.querySelectorAll('#panel .stg-cells .stg-section')].map(s => [...s.querySelectorAll('.rv-row[data-field]')].map(r => r.dataset.field))")
        check(all(len(x) >= 1 for x in per), f"each block holds its fields: {per}")
        inl = page.evaluate("() => [...document.querySelectorAll('#panel .stg-cells .stg-section .rv-inline')].map(w => [...w.querySelectorAll('.rv-row[data-field]')].map(r => r.dataset.field))")
        check(any(len(x) >= 2 for x in inl), f"an inline pair stays on one line inside its block: {inl}")
        rest = page.evaluate("() => [...document.querySelectorAll('#panel .stg-cells .rv-row[data-field]')].filter(r => !r.closest('.stg-section')).map(r => r.dataset.field)")
        check(len(rest) >= 1, f"the fields in no section still render: {rest}")
        after = page.evaluate("() => { const last = [...document.querySelectorAll('#panel .stg-cells .stg-section')].pop(); return [...document.querySelectorAll('#panel .stg-cells .rv-row[data-field]')].filter(r => !r.closest('.stg-section')).every(r => last && (last.compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING)); }")
        check(after, "they come after the last block")
        order = page.evaluate("() => [...document.querySelectorAll('#panel .stg-cells .stg-section, #panel .stg-cells .evi-sub')].map(e => e.className.includes('stg-section') ? 'S' : 'h').join('')")
        check("SS" in order, f"the blocks are stacked one under the other ({order})")
        gap = page.evaluate("() => { const s = [...document.querySelectorAll('#panel .stg-cells .stg-section')]; if (s.length < 2) return null; const a = s[0].getBoundingClientRect(), b = s[1].getBoundingClientRect(); return [b.top - a.bottom, getComputedStyle(s[0]).borderTopWidth, getComputedStyle(s[0]).borderRadius]; }")
        check(gap and 6 <= gap[0] <= 16 and gap[1] != "0px" and gap[2] != "0px", f"bordered, rounded, a small gap between them ({gap})")
        shot("sections.png")
        sel = page.locator("#panel .stg-cells .stg-section .rv-selwrap select").first
        path = sel.evaluate("s => s.closest('.rv-selwrap').dataset.path")
        before = pm().get(path)
        opts = sel.evaluate("s => [...s.options].map(o => o.value).filter(v => v && v !== '__other__')")
        want = next((o for o in opts if o != str(before)), None)
        if want:
            sel.select_option(want); page.wait_for_timeout(700)
            got = pm().get(path)
            check(str(got) == want, f"a select edited inside a block is saved ({path}: {before!r} → {got!r})")
            sel.select_option(str(before) if before is not None else ""); page.wait_for_timeout(500)   # put it back
        else:
            print("  n/a  select edit: no other option to pick")

        # ── the stacked table ──
        open_stage(a.stacked_stage)
        decl = page.locator(f'#panel .stg-cells .evi-paper .rv-decl[data-field="{a.table}"]')
        check(decl.count() == 1 and decl.locator("table.rv-typed").count() == 0 and decl.locator(".rv-stack").count() == 1, f"{a.table} is drawn as blocks, not as a table")
        rows = decl.locator(".rv-stack > .rv-stackrow")
        n0 = rows.count()
        check(n0 >= 2, f"one block per row ({n0})")
        first = rows.first
        check(first.locator(".rv-stacklead .rv-rowcite .ev-cite").count() == 1, "the row's page tag opens the block")
        lead = first.locator(".rv-stacklead > .rv-editable, .rv-stacklead > .rv-cell").first
        lead_txt = lead.inner_text()
        check("\n" in lead_txt and lead_txt.index("\n") > 0, f"the lead column shows its first line then the rest ({lead_txt.splitlines()[0]!r} …)")
        geo = page.evaluate("""() => { const b = document.querySelector('.rv-stackrow'); const tag = b.querySelector('.rv-stacklead .ev-cite'), lead = b.querySelector('.rv-stacklead > .rv-editable, .rv-stacklead > .rv-cell');
            const t = tag.getBoundingClientRect(), l = lead.getBoundingClientRect(); const cs = getComputedStyle(lead);
            return { tagLeftOfText: t.right <= l.right && Math.abs(t.top - l.top) < 14, block: cs.display === 'block', wrap: cs.whiteSpace.startsWith('pre'), lines: Math.round(l.height / parseFloat(cs.lineHeight)) }; }""")
        check(geo["tagLeftOfText"] and geo["block"] and geo["wrap"] and geo["lines"] >= 2, f"the tag sits on the head's first line, the text wraps as a paragraph under it ({geo})")
        fields = first.locator(".rv-stackrest .rv-row[data-field]").evaluate_all("els => els.map(e => e.dataset.field)")
        check(len(fields) >= 2 and all(f not in ("task",) for f in fields), f"the other columns follow as label-and-control lines: {fields}")
        one_per_line = page.evaluate("() => { const rs = [...document.querySelector('.rv-stackrow .rv-stackrest').querySelectorAll('.rv-row[data-field]')]; return rs.every((r, i) => i === 0 || r.getBoundingClientRect().top >= rs[i - 1].getBoundingClientRect().bottom - 1); }")
        check(one_per_line, "each on its own line")
        check(first.locator(".rv-stackrest .rv-selwrap select").count() >= 1, "an enum column is the usual select")
        xb = first.locator(":scope > .rv-rowdel")
        pos = page.evaluate("() => { const b = document.querySelector('.rv-stackrow'), x = b.querySelector(':scope > .rv-rowdel'); const bb = b.getBoundingClientRect(), xb = x.getBoundingClientRect(); return xb.right <= bb.right && xb.top - bb.top < 20 && bb.right - xb.right < 20; }")
        check(xb.count() == 1 and pos, "✕ sits at the block's top right")
        check(decl.locator(".rv-tableadd .rv-addrow").count() == 1, "＋ row under the list")
        shot("stacked.png")
        # an edit in a block writes the table's cell
        vsel = first.locator('.rv-stackrest .rv-row[data-field="verdict"] select').first if first.locator('.rv-stackrest .rv-row[data-field="verdict"] select').count() else first.locator(".rv-stackrest .rv-selwrap select").first
        vpath = vsel.evaluate("s => s.closest('.rv-selwrap').dataset.path")
        vopts = vsel.evaluate("s => [...s.options].map(o => o.value).filter(v => v && v !== '__other__')")
        cur = vsel.evaluate("s => s.value")
        pick = next((o for o in vopts if o != cur), None)
        vsel.select_option(pick); page.wait_for_timeout(700)
        col = vpath.split(".")[-1]; idx = int(vpath.split("[")[1].split("]")[0])
        got = (pm().get(a.table) or [{}])[idx].get(col)
        check(got == pick, f"a select in a block saves into that row ({vpath}: {cur!r} → {got!r})")
        # ＋ row / ✕
        decl.locator(".rv-tableadd .rv-addrow").click(); page.wait_for_timeout(800)
        decl = page.locator(f'#panel .stg-cells .evi-paper .rv-decl[data-field="{a.table}"]')
        n1 = decl.locator(".rv-stack > .rv-stackrow").count()
        check(n1 == n0 + 1 and len(pm().get(a.table) or []) == n0 + 1, f"＋ row adds a block ({n0} → {n1})")
        last = decl.locator(".rv-stack > .rv-stackrow").nth(n1 - 1)
        last.hover(); last.locator(":scope > .rv-rowdel").click(); page.wait_for_timeout(800)
        decl = page.locator(f'#panel .stg-cells .evi-paper .rv-decl[data-field="{a.table}"]')
        n2 = decl.locator(".rv-stack > .rv-stackrow").count()
        check(n2 == n0 and len(pm().get(a.table) or []) == n0, f"✕ removes it again ({n1} → {n2})")
        # the page tag
        tag = decl.locator(".rv-stack > .rv-stackrow").first.locator(".rv-stacklead .ev-cite").first
        tp = tag.get_attribute("data-page")
        tag.click(); page.wait_for_timeout(600)
        sel_pages = page.locator("rect.hl.sel").evaluate_all("els => els.map(r => r.closest('.page').id)")
        check(sel_pages and all(s == f"page-{tp}" for s in sel_pages), f"a block's page tag lights its passage on p. {tp}")

        # ── the row's text opens the passage too ──
        sel_of = lambda: page.locator("rect.hl.sel").evaluate_all("els => els.map(r => r.closest('.page').id)")
        unsel = lambda: page.evaluate("() => document.querySelectorAll('rect.hl.sel').forEach(r => r.classList.remove('sel'))")
        blocks = decl.locator(".rv-stack > .rv-stackrow")
        tag0, tag1 = blocks.nth(0).locator(".rv-stacklead .ev-cite").first, blocks.nth(1).locator(".rv-stacklead .ev-cite").first
        p0, p1 = tag0.get_attribute("data-page"), tag1.get_attribute("data-page")
        title = tag0.get_attribute("title") or ""
        check(title.startswith(f"open the passage, p. {p0}"), f"the tag says what it does: {title.splitlines()[0]!r}")
        check(tag0.evaluate("b => getComputedStyle(b).cursor") == "pointer", "and shows a pointer")
        check(blocks.nth(0).locator(".rv-stacklead").evaluate("h => getComputedStyle(h).cursor") == "pointer", "the head shows a pointer")
        tag1.click(); page.wait_for_timeout(500)   # the selection sits on the second block's page first
        lead0 = blocks.nth(0).locator(".rv-stacklead > .rv-editable, .rv-stacklead > .rv-cell").first
        lead0.click(); page.wait_for_timeout(600)
        sp = sel_of()
        check(p0 != p1 and sp and all(s == f"page-{p0}" for s in sp), f"a click on the block's head lights its passage on p. {p0} (was p. {p1})")
        check(page.evaluate("() => { const r = document.querySelector('rect.hl.sel'); if (!r) return false; const b = r.getBoundingClientRect(); return b.top >= 0 && b.bottom <= innerHeight; }"), "and scrolls the PDF to it")
        check(page.evaluate("() => !!document.activeElement && document.activeElement.classList.contains('rv-editable') && !!document.activeElement.closest('.rv-stacklead')"), "the head keeps the caret for editing")
        unsel(); lead0.click(); page.wait_for_timeout(400)
        check(not sel_of(), "a further click inside the head being edited only moves the caret")
        # a table drawn as a table: its row's text does the same
        tdecl = page.locator(f'#panel .stg-cells .evi-paper .rv-decl[data-field="{a.typed_table}"]')
        check(tdecl.count() == 1 and tdecl.locator("table.rv-typed").count() == 1, f"{a.typed_table} is drawn as a table")
        trs = tdecl.locator("table.rv-typed tbody tr.stg-cited")
        check(trs.count() >= 2, f"its rows carry page tags ({trs.count()})")
        tp0 = trs.nth(0).locator("td.rv-rowcite .ev-cite").get_attribute("data-page"); tp1 = trs.nth(1).locator("td.rv-rowcite .ev-cite").get_attribute("data-page")
        trs.nth(1).locator("td.rv-rowcite .ev-cite").click(); page.wait_for_timeout(400)
        trs.nth(0).locator("td .rv-editable").first.click(); page.wait_for_timeout(600)
        sp = sel_of()
        check(tp0 != tp1 and sp and all(s == f"page-{tp0}" for s in sp), f"a click on a table row's text lights its passage on p. {tp0} (was p. {tp1})")
        shot("row-click.png")

        check(not errors, "no script errors" + (f": {errors[:3]}" if errors else ""))
        browser.close()
    print(("ALL OK" if not fails else f"{len(fails)} FAILED"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
