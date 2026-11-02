#!/usr/bin/env python3
"""Browser check of display.sheet: a regression column's cells laid out as the printed table.

    python3 scripts/ui_check_sheet.py --url http://127.0.0.1:8019 --doc <doc id> --session <X-Session-Id> [--shot /tmp/sheet]

Seed: one paper under econ-regression-tables (or register-tables) with several columns of one table in
one anonymous session. Checks: the open column's Cells tab shows the printed table with one column per
entry of the same table and the open one tinted; rows are aligned by label (an se row under its
coefficient, label hidden); a sibling heading opens that entry; "▤ every field" shows the generic list
and the choice sticks; a correction of the printed text carries the parsed number along; no script
error. Needs the system python with playwright installed; prints a check list and exits non-zero on a
failure. Writes a screenshot per view.
"""
import argparse
import json
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright is not installed for this python; skipped")
    sys.exit(0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8019")
    ap.add_argument("--doc", required=True, help="the tables document id")
    ap.add_argument("--session", required=True)
    ap.add_argument("--shot", default="/tmp/sheet")
    a = ap.parse_args()
    fails: list[str] = []

    def check(cond, what):
        print(("  ok  " if cond else "  FAIL") + " " + what)
        if not cond:
            fails.append(what)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        # api.js SID_KEY: the anonymous session the document belongs to; the remembered view is cleared once, not on every load
        ctx.add_init_script(f"localStorage.setItem('paperlens_sid', {a.session!r});"
                            " if (!sessionStorage.getItem('sheet_check')) { localStorage.removeItem('metalens_sheet'); sessionStorage.setItem('sheet_check', '1'); }")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text and "Failed to load resource" not in m.text else None)
        verifies: list[dict] = []
        page.on("request", lambda r: verifies.append(json.loads(r.post_data or "{}")) if r.method == "POST" and "/verify" in r.url else None)
        page.goto(f"{a.url}/workspace?doc={a.doc}")
        page.wait_for_selector(".record .sheet table.sh-t", timeout=30000)
        sheet = page.locator(".record .sheet").first
        heads = sheet.locator("thead tr:first-child th.sh-col")
        check(heads.count() == 3, f"one column per entry of the same table ({heads.count()}; the other table's column is not among them)")
        check(sheet.locator("thead th.sh-col.sh-me").count() == 1 and "(1)" in sheet.locator("thead th.sh-col.sh-me").inner_text(), "the open entry's column is the tinted one, headed by its printed label")
        check(sheet.locator("thead tr.sh-hdr").count() == 1 and "Low-wage" in sheet.locator("thead tr.sh-hdr").inner_text(), "a second heading row carries each column's outcome")
        labels = [t.strip() for t in sheet.locator("tbody td.sh-lab").all_inner_texts()]
        print("       rows:", labels)
        check(labels[0] == "Import Competition" and labels[1] == "se", "the se row sits under its coefficient without a label of its own (its type in small print)")
        check("Worker FE" in labels and "Year FE" in labels and labels.index("Worker FE") < labels.index("Year FE"), "a row only a sibling has is folded in where it falls")
        check(sheet.locator("tbody tr.sh-rule").count() == 1 and "Observations" in sheet.locator("tbody tr.sh-rule").inner_text(), "the statistics rows sit under a rule")
        me_cells = sheet.locator("tbody td.sh-me .sh-val")
        check(me_cells.count() == 5 and me_cells.first.get_attribute("data-path") == "cells[0].raw_text", f"the open column's cells are editable at their own paths ({me_cells.count()})")
        blank = sheet.locator("tbody tr", has_text="Year FE").locator("td.sh-me.sh-blank")
        check(blank.count() == 1, "a row this column lacks is blank in its column")
        sib = sheet.locator("tbody td.sh-sib").first
        check(sib.count() == 1 and "rv-covered" in (sib.get_attribute("class") or ""), "a sibling's cell is linked to its own column's table citation")
        check("coefficient" in (me_cells.first.get_attribute("title") or "") and "-1.292" in (me_cells.first.get_attribute("title") or ""), "a cell's tooltip carries its type and parsed value")
        page.screenshot(path=f"{a.shot}-printed.png", full_page=True)
        # a correction of the printed text: the parsed number follows
        cell = me_cells.first
        typed = "-1.296***" if cell.inner_text().strip() != "-1.296***" else "-1.297***"     # a value other than the one there (a re-run finds the last one)
        cell.click(); page.keyboard.press("Meta+A"); page.keyboard.type(typed); page.locator(".record .rectitle").first.click()
        page.wait_for_timeout(800)                                   # the save and its follow-up leave
        paths = [(d.get("diff") or [{}])[0].get("field_path") for d in verifies]
        finals = {(d.get("diff") or [{}])[0].get("field_path"): (d.get("diff") or [{}])[0].get("final_value") for d in verifies}
        check("cells[0].raw_text" in paths and finals.get("cells[0].raw_text") == typed, f"the printed text is saved as a correction ({paths})")
        check(finals.get("cells[0].numeric_value") == float(typed.rstrip("*")), f"the parsed number follows the printed text ({finals.get('cells[0].numeric_value')})")
        check("cells[0].significance_stars" not in paths, "the star count, unchanged, is not re-saved")
        # a sibling heading opens that entry
        sheet.locator("thead th.sh-col[data-rid]").first.click()
        page.wait_for_function("() => { const h = document.querySelector('.record .sheet thead th.sh-col.sh-me'); return h && h.textContent.includes('(2)'); }", timeout=10000)
        check(True, "a sibling column's heading opens that entry, whose column is now the tinted one")
        page.wait_for_timeout(300)
        page.screenshot(path=f"{a.shot}-sibling.png", full_page=True)
        # the list view
        page.locator(".record .sh-toggle").first.click()
        page.wait_for_selector(".record .rv-table.rv-typed", timeout=10000)
        check(page.locator(".record .sheet").count() == 0 and page.locator(".record .sh-toggle").inner_text().strip().startswith("▦"), "▤ every field shows the generic typed table, with the way back")
        page.reload(); page.wait_for_selector(".record .subpanel:not(.hidden) .rv-table.rv-typed", timeout=30000)
        check(page.locator(".record .sheet").count() == 0, "the list choice sticks across a reload")
        page.screenshot(path=f"{a.shot}-list.png", full_page=True)
        page.locator(".record .sh-toggle").first.click(); page.wait_for_selector(".record .sheet table.sh-t", timeout=10000)
        check(True, "▦ as printed returns to the sheet")
        check(not errors, f"no script errors ({errors[:3]})")
        browser.close()
    print(f"{len(fails)} failure(s)" if fails else "all checks passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
