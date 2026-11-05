#!/usr/bin/env python3
"""Browser check of the joint per-paper review of a workflow over linked datasets (econ: tables and
claims): one sidebar entry per paper; each table on one card — its panels stacked, every cell
editable and saved to its own column, one verdict for the whole table; under it only what the
workflow's review asks (econ: the target regressor, marked in the table and picked per column, and
the SE type — no specification); the claims citing the table under it, each with its type, the main
estimate starred and its own verdict; the claims citing no table on a pill of their own; the joint
review queue counting a paper's tables and claims together.

    python3 scripts/ui_check_joint_review.py --url http://127.0.0.1:8019 --seed seed.json [--shot /tmp/joint]

Seed: a workflow dataset whose run took one paper's tables file (Table 4 with Panel A and Panel B,
three columns each; Table 3 without panels) and claims file (S1 citing Table 4, S2 citing Table 3,
S3 citing a figure) — {"session", "workflow", "run", "tables_doc", "claims_doc"}. Needs the system
python with playwright; prints a check list and exits non-zero on a failure.
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
    ap.add_argument("--seed", required=True)
    ap.add_argument("--shot", default="/tmp/joint")
    a = ap.parse_args()
    seed = json.load(open(a.seed))
    fails: list[str] = []

    def check(cond, what):
        print(("  ok  " if cond else "  FAIL") + " " + what)
        if not cond:
            fails.append(what)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1600, "height": 1200})
        ctx.add_init_script(f"localStorage.setItem('paperlens_sid', {seed['session']!r}); localStorage.setItem('metalens_triage', 'order');"
                            " localStorage.removeItem('metalens_sheet');")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text and "Failed to load resource" not in m.text else None)
        H = {"X-Session-Id": seed["session"]}

        def recs(doc):
            return page.request.get(f"{a.url}/api/documents/{doc}/view", headers=H).json()["records"]

        page.goto(f"{a.url}/workspace?project={seed['workflow']}")
        page.wait_for_selector(".rnav-tab", timeout=30000)
        tabs = page.locator("#doctabs .doctab[data-id]")
        joint = page.locator(f"#doctabs .doctab[data-id='{seed['tables_doc']}']")
        check(page.locator(f"#doctabs .doctab[data-id='{seed['claims_doc']}']").count() == 0 and joint.count() == 1 and joint.locator(".dt-n").inner_text() == "11",
              f"the paper is one sidebar entry, its tables and claims counted together ({tabs.count()} entries)")
        check(page.locator("#docbar").is_hidden(), "no strip of the paper's documents: both sides are on this page")
        pills = [t.split("\n")[0] for t in page.locator(".rnav .rnav-group").all_inner_texts()]
        check(any(t.startswith("Table 4") for t in pills) and not any("Panel" in t for t in pills), f"one pill per table, named by its number only: {pills}")
        check(page.locator(".rnav-tab[data-sel='claims']").count() == 1, "the claims citing no table have a pill of their own")
        page.locator(".rnav .rnav-group", has_text="Table 4").click()
        page.wait_for_function("() => { const t = document.querySelector('.tcard .rectitle'); return t && t.textContent.startsWith('Table 4'); }", timeout=10000)
        card = page.locator(".tcard")
        title = card.locator(".rectitle").inner_text().splitlines()[0].strip()
        check(title == "Table 4", f"the card is titled by the table number only: {title!r}")
        panels = [t.strip() for t in card.locator("tr.sh-panel").all_inner_texts()]
        check(panels == ["Panel A. Female participants", "Panel B. Male participants"], f"the panels are stacked as printed, A above B: {panels}")
        check(card.locator("thead th.sh-col").count() == 3, "three columns, the panels' columns aligned under one heading each")
        check(card.locator(".sh-val[contenteditable]").count() == 30, f"every cell of every column is editable ({card.locator('.sh-val[contenteditable]').count()} of 30)")
        ask = card.locator(".tc-asktab")
        heads = [t.strip() for t in ask.locator("thead th").all_inner_texts()]
        check(card.locator(".tc-col").count() == 0 and card.locator(".subtab").count() == 0 and "Model" not in card.inner_text() and "Fixed effects" not in card.inner_text(),
              "no specification under the table: the review asks only what the workflow declares")
        check(heads == ["Column", "Target regressors", "SE type"] and ask.locator("tbody tr").count() == 6, f"one line per column with what it asks: {heads}, {ask.locator('tbody tr').count()} rows")
        check(card.locator(".sh-val.sh-target").count() == 6 and all(t.strip() in ("0.14", "363", "205", "1,833", "1,834", "1,102") for t in card.locator(".sh-val.sh-target").all_inner_texts()),
              "each column's target regressor cell is marked in the table")
        page.screenshot(path=f"{a.shot}-1-table.png", full_page=True)
        # a cell of Panel B, column (2): its line under the table lights up; an edit lands on that column's record
        rows_b = card.locator("tbody tr").filter(has=page.locator("td.sh-lab", has_text="Microcredit"))
        cell = rows_b.nth(1).locator("td.sh-cell").nth(1).locator(".sh-val")
        cell.click()
        page.wait_for_timeout(300)
        me = card.locator(".tc-asktab tr.tc-me th").inner_text()
        check("(2)" in me and "Panel B" in me, f"the column's line under the table follows the cell: {me!r}")
        rid = cell.get_attribute("data-rid")
        cell.evaluate("el => { el.textContent = '1,834'; }")
        page.locator(".tcard .rectitle").click()
        page.wait_for_timeout(1200)
        r = next(x for x in recs(seed["tables_doc"]) if x["id"] == rid)
        c0 = next(c for c in r["field_values"]["cells"] if c["row_label"] == "Microcredit")
        check(r["field_values"]["panel"].startswith("Panel B") and r["field_values"]["column"] == "(2)" and c0["raw_text"] == "1,834" and c0["numeric_value"] == 1834,
              f"the edit is saved to Panel B column (2), its parsed number following: {c0['raw_text']} / {c0['numeric_value']}")
        # the target regressor of Panel A column (1), picked from its coefficient rows; the SE type edited
        row = card.locator(".tc-asktab tbody tr", has_text="Panel A").first
        rid_a1 = row.get_attribute("data-rid")
        row.locator("select.tc-target").select_option(label="In-kind grant")
        page.wait_for_timeout(1200)
        r = next(x for x in recs(seed["tables_doc"]) if x["id"] == rid_a1)
        tr0 = (r["field_values"].get("target_regressors") or [{}])[0]
        check(tr0.get("name") == "In-kind grant" and tr0.get("row_index") == 2, f"picking the target saves its name and row: {tr0}")
        card = page.locator(".tcard")
        row = card.locator(".tc-asktab tbody tr", has_text="Panel A").first
        row.locator(".rv-selwrap select").first.select_option("robust")
        page.wait_for_timeout(1000)
        r = next(x for x in recs(seed["tables_doc"]) if x["id"] == rid_a1)
        check(r["field_values"].get("se_type") == "robust", f"the SE type is edited in its line: {r['field_values'].get('se_type')}")
        check("0.24***" in [t.strip() for t in card.locator(".sh-val.sh-target").all_inner_texts()], "the mark follows the new target")
        # the claims citing the table, under it; a claim's own verdict
        box = card.locator(".jc-box")
        check(box.locator(".jc-claim").count() == 1 and "S1" in box.inner_text() and "S2" not in box.inner_text(), "the claim citing Table 4 sits under it, the one citing Table 3 does not")
        check(box.locator(".jc-type").inner_text().strip().lower() == "edge" and box.locator(".jc-main").count() == 1, "the claim shows its type and its main estimate starred")
        box.locator(".jc-claim .jc-v[data-status='verified']").click()
        page.wait_for_timeout(800)
        s1 = next(x for x in recs(seed["claims_doc"]) if x["field_values"]["claim_id"] == "S1")
        check(s1["verification_status"] == "verified", "the claim's ✓ verifies the claim")
        # one verdict for the table: every column of it
        card.locator(".tc-foot .vbtn[data-status='verified']").click()
        page.wait_for_timeout(1500)
        t4 = [x for x in recs(seed["tables_doc"]) if x["field_values"]["table_number"] == "Table 4"]
        check(len(t4) == 6 and all(x["verification_status"] == "verified" for x in t4), "✓ verify table verifies all six columns of Table 4")
        t = page.locator(".tcard .rectitle").inner_text() if page.locator(".tcard").count() else ""
        check(t.startswith("Table 3"), f"the review moves on to the next table: {t.splitlines()[0] if t else '—'}")
        check("1/2 tables reviewed" in page.locator(".progress").inner_text() and "1/3 claims" in page.locator(".progress").inner_text(),
              f"the head counts tables and claims: {page.locator('.progress').inner_text()}")
        page.locator(".rnav-tab[data-sel='claims']").click()
        page.wait_for_timeout(500)
        check(page.locator(".jc-loose .jc-claim").count() == 1 and "S3" in page.locator(".jc-loose").inner_text(), "the claim citing a figure is on the claims-without-a-table pill")
        page.screenshot(path=f"{a.shot}-2-loose.png", full_page=True)
        # the joint review queue: one entry per paper, its tables' and claims' items together
        page.goto(f"{a.url}/workspace?queue={seed['run']}:joint")
        page.wait_for_selector("#queuebar:not([hidden])", timeout=30000)
        page.wait_for_selector(".rnav-tab", timeout=30000)
        ent = page.locator("#doctabs .doctab[data-id]")
        check(ent.count() == 1 and ent.first.locator(".dt-n").inner_text() == "7/11", f"the queue lists the paper once, its tables and claims together: {ent.first.locator('.dt-n').inner_text() if ent.count() else '—'}")
        page.locator(".rnav .rnav-group", has_text="Table 3").click()
        page.wait_for_selector(".tcard .jc-claim", timeout=10000)
        page.locator(".tcard .jc-claim .jc-v[data-status='verified']").click()
        page.wait_for_timeout(600)
        page.locator(".tcard .tc-foot .vbtn[data-status='verified']").click()
        page.wait_for_timeout(1500)
        bar = page.locator("#queuebar").inner_text()
        check("10 of 11 checked" in bar, f"a table's verdict counts every column, a claim's its own: {bar.splitlines()[1] if len(bar.splitlines()) > 1 else bar}")
        page.locator(".rnav-tab[data-sel='claims']").click()
        page.wait_for_selector(".jc-loose .jc-claim", timeout=10000)
        page.locator(".jc-loose .jc-v[data-status='verified']").click()
        page.wait_for_timeout(800)
        check("11 of 11 checked" in page.locator("#queuebar").inner_text() and page.locator("#queuebar a", has_text="Done").count() == 1,
              "the last claim completes the paper and the queue offers the release")
        page.screenshot(path=f"{a.shot}-3-queue.png", full_page=True)
        check(not errors, f"no script errors ({errors[:3]})")
        browser.close()
    print(("ALL OK" if not fails else f"{len(fails)} FAILED") + f" — {len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
