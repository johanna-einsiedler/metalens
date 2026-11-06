#!/usr/bin/env python3
"""Browser check of display.sheet: a regression table on one card, laid out as printed.

    python3 scripts/ui_check_sheet.py --url http://127.0.0.1:8019 --doc <doc id> --session <X-Session-Id> [--shot /tmp/sheet]

Seed: one paper under econ-regression-tables (or register-tables) with three columns of Table 3 (a
Year FE row only columns (2) and (3) print, an outcome written as a sentence, an undeclared key on
column (1)) and one column of Table 4, in one anonymous session. Checks: one pill per table, named by
its number; the card is titled "Table 3" and shows every column of the table; rows are aligned by
label (an se row under its coefficient, label hidden); every cell is editable and a correction of the
printed text carries the parsed number along; a heading opens that column's fields under the table
(no Column or Other tab; the undeclared key at the end of the specification); one ✓ verifies every
column; "▤ every field" shows the generic list and the choice sticks; ＋ adds a table the extraction
missed (a card asking for its name, then its cells); no script error. Needs the
system python with playwright installed; prints a check list and exits non-zero on a failure. Writes
a screenshot per view.
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
        page.wait_for_selector(".tcard .sheet table.sh-t", timeout=30000)
        # the strip and the sidebar list the tables, not every column, each by its number
        pills = page.locator(".rnav .rnav-tab.rnav-group")
        names = [t.split("\n")[0].strip() for t in pills.all_inner_texts()]
        check(pills.count() == 2 and names[0].startswith("Table 3") and names[1].startswith("Table 4"), f"the strip lists the paper's tables ({names})")
        subs = page.locator(".docsub .docsub-item")
        check(subs.count() == 3 and "Table 4" in subs.nth(2).inner_text(), f"the sidebar lists Paper and the two tables ({subs.count()} items)")
        page.locator(".rnav .rnav-group", has_text="Table 3").click()
        page.wait_for_function("() => { const t = document.querySelector('.tcard .rectitle'); return t && t.textContent.startsWith('Table 3'); }", timeout=10000)
        card = page.locator(".tcard")
        check(card.locator(".rectitle").inner_text().splitlines()[0].strip() == "Table 3", "the card is titled by the table number only")
        sheet = card.locator(".sheet")
        heads = sheet.locator("thead tr:first-child th.sh-col")
        check(heads.count() == 3, f"one column per entry of the table ({heads.count()}; the other table's column is not among them)")
        check(sheet.locator("thead tr.sh-hdr").count() == 1 and "Low-wage" in sheet.locator("thead tr.sh-hdr").inner_text(), "a second heading row carries each column's outcome")
        long_th = sheet.locator("thead tr.sh-hdr th").nth(3)
        check("High school" in (long_th.get_attribute("title") or "") and long_th.locator(".sh-colname[data-col=header]").count() == 1 and long_th.bounding_box()["height"] < 60,
              "a sentence written as the outcome is shown whole in its tooltip, clamped in the heading, and typed there to correct it")
        labels = [t.strip() for t in sheet.locator("tbody td.sh-lab").all_inner_texts()]
        print("       rows:", labels)
        check(labels[0] == "Import Competition" and labels[1] == "se", "the se row sits under its coefficient without a label of its own (its type in small print)")
        check("Worker FE" in labels and "Year FE" in labels and labels.index("Worker FE") < labels.index("Year FE"), "a row only some columns print is folded in where it falls")
        check(sheet.locator("tbody tr.sh-rule").count() == 1 and "Observations" in sheet.locator("tbody tr.sh-rule").inner_text(), "the statistics rows sit under a rule")
        cells = sheet.locator("tbody .sh-val[contenteditable]")
        check(cells.count() == 14, f"every cell of every column is editable ({cells.count()} of 14)")
        check(sheet.locator("tbody tr", has_text="Year FE").locator("td.sh-blank").count() == 1, "a row a column lacks is blank in that column")
        first = sheet.locator("tbody tr").first.locator(".sh-val").first
        check(first.get_attribute("data-path") == "cells[0].raw_text" and "rv-covered" in (first.get_attribute("class") or ""), "a cell sits at its own column's path, linked to that column's table citation")
        check("coefficient" in (first.get_attribute("title") or "") and "-1.292" in (first.get_attribute("title") or ""), "a cell's tooltip carries its type and parsed value")
        subtabs = [t.strip() for t in card.locator(".subtab").all_inner_texts()]
        check("Column" not in subtabs and "Other" not in subtabs, f"no Column or Other tab ({subtabs or 'one section'})")
        page.screenshot(path=f"{a.shot}-printed.png", full_page=True)
        # a correction of the printed text in column (2): the parsed number follows, on that column's record
        cell = sheet.locator("tbody tr").first.locator(".sh-val").nth(1)
        typed = "-0.875**" if cell.inner_text().strip() != "-0.875**" else "-0.876**"
        cell.click(); page.keyboard.press("Meta+A"); page.keyboard.type(typed); card.locator(".rectitle").click()
        page.wait_for_timeout(900)
        finals = {(d.get("diff") or [{}])[0].get("field_path"): (d.get("diff") or [{}])[0].get("final_value") for d in verifies}
        paths = list(finals)
        check(finals.get("cells[0].raw_text") == typed, f"the printed text is saved as a correction ({paths})")
        check(finals.get("cells[0].numeric_value") == float(typed.rstrip("*")), f"the parsed number follows the printed text ({finals.get('cells[0].numeric_value')})")
        check("cells[0].significance_stars" not in paths, "the star count, unchanged, is not re-saved")
        check("Column (2)" in card.locator(".tc-colhead").inner_text(), "the column being edited is the one whose fields sit under the table")
        # a heading opens that column's fields; the undeclared key ends its specification
        sheet.locator("thead th.sh-col").first.click()
        page.wait_for_timeout(300)
        col = card.locator(".tc-col")
        check("Column (1)" in col.locator(".tc-colhead").inner_text() and sheet.locator("thead th.sh-col.sh-me").inner_text().strip() == "(1)", "a heading opens that column: its fields below, its column tinted")
        check("Returned by the model but not declared" in col.inner_text() and "headline_criteria" in col.inner_text().replace(" ", "_").lower()
              or "Headline criteria" in col.inner_text(), "what the preset does not declare ends the specification")
        check(page.locator(".rnav .rnav-group.active").inner_text().startswith("Table 3"), "the table's pill stays active while its columns are switched")
        page.screenshot(path=f"{a.shot}-column.png", full_page=True)
        # one verdict for the table
        n0 = len(verifies)
        card.locator(".tc-foot .vbtn[data-status='verified']").click()
        page.wait_for_timeout(1200)
        given = [d for d in verifies[n0:] if d.get("status") == "verified"]
        check(len(given) == 3, f"✓ verify table gives the verdict to all three columns ({len(given)})")
        page.locator(".rnav .rnav-group", has_text="Table 3").click()
        page.wait_for_selector(".tcard .rectitle", timeout=10000)
        check("verified" in page.locator(".tcard .rectitle .status").inner_text(), "the table reads verified")
        # the list view
        page.locator(".tcard .sh-toggle").first.click()
        page.wait_for_selector(".tcard .rv-table.rv-typed", timeout=10000)
        check(page.locator(".tcard .sheet").count() == 0 and page.locator(".tcard .sh-toggle").inner_text().strip().startswith("▦"), "▤ every field shows the generic typed table, with the way back")
        page.reload(); page.wait_for_selector(".tcard .rv-table.rv-typed", timeout=30000)
        check(page.locator(".tcard .sheet").count() == 0, "the list choice sticks across a reload")
        page.screenshot(path=f"{a.shot}-list.png", full_page=True)
        page.locator(".tcard .sh-toggle").first.click(); page.wait_for_selector(".tcard .sheet table.sh-t", timeout=10000)
        check(True, "▦ as printed returns to the sheet")
        # a table the extraction missed: ＋ opens a new card that asks for the table's name, with an empty cells table
        page.locator("#addfinding").click()
        page.wait_for_selector(".tcard .tc-ident", timeout=20000)
        card = page.locator(".tcard")
        check(card.locator(".rectitle").inner_text().startswith("New table") and card.locator(".tc-ident .rv-row[data-field='table_number']").count() == 1
              and card.locator(".tc-list .rv-addrow").count() == 1, "＋ opens the new entry: a card asking for the table's name, its cells to add row by row")
        tn = card.locator(".tc-ident .rv-row[data-field='table_number'] .rv-editable")
        tn.click(); page.keyboard.type("Table 7"); card.locator(".rectitle").click()
        page.wait_for_timeout(1000)
        check(page.locator(".rnav .rnav-group", has_text="Table 7").count() == 1 and page.locator(".tcard .rectitle").inner_text().startswith("Table 7"),
              "naming it moves the card and its pill to that table")
        # rows and names: ＋ row adds a printed row to every column, a row's name is typed, ✕ removes it from every column
        def t3():
            rs = page.request.get(f"{a.url}/api/documents/{a.doc}/view", headers={"X-Session-Id": a.session}).json()["records"]
            return [r for r in rs if r["field_values"].get("table_number") == "Table 3"]
        page.locator(".rnav .rnav-group", has_text="Table 3").click(); page.wait_for_selector(".tcard .sheet table.sh-t", timeout=10000)
        page.once("dialog", lambda d: d.accept("Added row"))
        page.locator(".tcard .sh-addrow").click(); page.wait_for_timeout(1500)
        row = page.locator(".tcard .sheet tbody tr", has_text="Added row")
        check(row.count() == 1 and row.locator("td.sh-cell .sh-val").count() == 3, "＋ row adds a row with a cell in every column")
        lab = row.locator(".sh-labtxt"); lab.click(); page.keyboard.press("Meta+A"); page.keyboard.type("Added row (renamed)"); page.locator(".tcard .rectitle").first.click()
        page.wait_for_timeout(1500)
        check(all(any(c["row_label"] == "Added row (renamed)" for c in r["field_values"]["cells"]) for r in t3()), "a row's name is saved on every column")
        row = page.locator(".tcard .sheet tbody tr", has_text="Added row (renamed)")
        row.hover(); page.once("dialog", lambda d: d.accept())
        row.locator(".sh-rowdel").click(); page.wait_for_timeout(1500)
        recs3 = t3()
        check(page.locator(".tcard .sheet tbody tr", has_text="Added row").count() == 0
              and all(not any(str(c["row_label"]).startswith("Added row") for c in r["field_values"]["cells"]) and [c["row_index"] for c in r["field_values"]["cells"]] == list(range(len(r["field_values"]["cells"]))) for r in recs3),
              "✕ removes the row from every column; row indexes stay contiguous")
        head = page.locator(".tcard .sheet thead th.sh-col .sh-colname").first
        head.click(); page.keyboard.press("Meta+A"); page.keyboard.type("(1a)"); page.locator(".tcard .rectitle").first.click(); page.wait_for_timeout(1200)
        check(any(r["field_values"].get("column") == "(1a)" for r in t3()), "a column's name is typed on its heading and saved")
        page.screenshot(path=f"{a.shot}-rows.png", full_page=True)
        check(not errors, f"no script errors ({errors[:3]})")
        browser.close()
    print(f"{len(fails)} failure(s)" if fails else "all checks passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
