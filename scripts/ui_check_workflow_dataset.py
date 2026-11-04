#!/usr/bin/env python3
"""Browser check of workflow datasets: the after-import card (review the data, or add to a
workflow — which makes a workflow dataset and opens it), the workflow dataset's page (linked
datasets, papers with each side, the run saying what it is doing now), a linked dataset pointing
to its workflow dataset, My workspace's Workflows section, and the review of a workflow
dataset's papers in the workspace.

    python3 scripts/ui_check_workflow_dataset.py --url http://127.0.0.1:8019 --workflow <id> --tables <id> --fresh <id> --session <X-Session-Id> [--shot /tmp/wfd]
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
    ap.add_argument("--url", default="http://127.0.0.1:8019")
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--tables", required=True)
    ap.add_argument("--fresh", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--shot", default="/tmp/wfd")
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

        # 1 · the workflow dataset's own page
        page.goto(f"{a.url}/dataset?id={a.workflow}")
        page.wait_for_selector("#wd-members", timeout=30000)
        head = page.locator(".ds-head").inner_text()
        check("workflow dataset" in head.lower() and "Econ papers" in head and "Economics articles" in head, f"the header names the workflow: {head[:140]!r}")
        mem = page.locator("#wd-members .wd-member")
        check(mem.count() == 2 and "Econ tables" in mem.nth(0).inner_text() and "Econ claims" in mem.nth(1).inner_text(), "both linked datasets are listed with their datasets")
        check(mem.nth(0).locator("a[href^='/preset?id=']").count() == 1, "each side names its preset")
        rows = page.locator("#wd-papers tbody tr")
        heads = page.locator("#wd-papers thead th").all_inner_texts()
        check(rows.count() == 2 and [h.lower() for h in heads[1:3]] == ["regression tables", "claims and their results"], f"papers table: {rows.count()} rows, columns {heads}")
        check("missing" in rows.nth(1).inner_text() and rows.nth(1).locator("a[href*='doc=']").count() == 1, "the new paper has its tables and misses its claims")
        page.wait_for_selector("#ds-update:not([hidden]) .wf-row", timeout=30000)
        card = page.locator("#ds-update").inner_text()
        check("Each time papers are added, these steps run in this order." in card and "every check and review is declared" not in card, "the panel's sentence is in plain words")
        check("made elsewhere" not in card and "uploaded" in card, "supplied steps say 'uploaded'")
        now = page.locator("#ds-update .wf-nowline")
        check(now.count() == 1 and "Now:" in now.inner_text() and "Claims and their results" in now.inner_text() and "waiting for uploads" in now.inner_text() and "0 of 1 arrived" in now.inner_text(),
              f"the run says what it is doing: {now.inner_text() if now.count() else None!r}")
        cur = page.locator("#ds-update .wf-row.wf-current")
        check(cur.count() == 1 and "Claims and their results" in cur.inner_text() and "waiting" in cur.inner_text(), "the current step's row is marked")
        ups = page.locator("#ds-update a[href^='/import?']")
        check(ups.count() == 2 and all(f"back={a.workflow}" in (ups.nth(i).get_attribute("href") or "") for i in range(2)), "the upload buttons come back to the workflow dataset")
        check(page.locator("#ds-update .wf-group").count() == 0, "the panel leaves the linked datasets to the card above")
        page.screenshot(path=f"{a.shot}-1-workflow-dataset.png", full_page=True)

        # 2 · a linked dataset points to its workflow dataset
        page.goto(f"{a.url}/dataset?id={a.tables}")
        page.wait_for_selector("#ds-update:not([hidden])", timeout=30000)
        card = page.locator("#ds-update").inner_text()
        check("side of the workflow dataset" in card and "Econ papers" in card and page.locator("#ds-update .wf-row").count() == 0, f"the member card points up: {card[:160]!r}")
        check(page.locator(f"#ds-update a[href='/dataset?id={a.workflow}#workflow']").count() >= 1, "it links to the workflow")
        check("waiting for uploads" in card, "it shows what the run is doing")
        check("Regression tables side of Econ papers" in page.locator("#ds-wfpill").inner_text(), "the header says which side it is")
        page.screenshot(path=f"{a.shot}-2-member.png", full_page=True)

        # 3 · My workspace → Workflows
        page.goto(f"{a.url}/projects")
        page.wait_for_selector("#workflows .proj-tile, #workflows p", timeout=30000)
        tile = page.locator(f"#workflows a.proj-tile[href='/dataset?id={a.workflow}']")
        check(tile.count() == 1 and "Econ papers" in tile.inner_text() and "waiting for uploads" in tile.inner_text() and "Econ claims" in tile.inner_text(), f"the workflow tile: {tile.inner_text()[:160] if tile.count() else None!r}")
        listed = page.evaluate(f"fetch('/api/datasets', {{headers: {{'X-Session-Id': {a.session!r}}}}}).then(r => r.json()).then(j => j.datasets.find(d => d.id === {a.tables!r}))")
        check(bool(listed) and listed.get("group_role") == "tables" and listed.get("workflow_dataset_id") == a.workflow, "the dataset list says which workflow the tables dataset is a side of")
        side = page.locator(f"#datasets a.proj-tile[href='/dataset?id={a.tables}']")   # listed only when a signed-in account owns it
        check(side.count() == 0 or "tables side" in side.inner_text(), "a member's tile says it is a side of a workflow")
        check(page.locator(f"#datasets a.proj-tile[href='/dataset?id={a.workflow}']").count() == 0, "the workflow dataset is not listed among the datasets")
        page.click("#wf-new")
        page.wait_for_selector("#wf-new-pick option", state="attached", timeout=10000)
        opts = page.locator("#wf-new-pick option").all_inner_texts()
        check(any("Economics articles" in o for o in opts), f"the new-workflow form lists the workflows: {opts}")
        page.select_option("#wf-new-pick", label="Economics articles: tables and claims")
        check("one dataset per preset" in page.locator("#wf-new-help").inner_text(), "it explains that the workflow gets a dataset of its own")
        page.screenshot(path=f"{a.shot}-3-workspace.png", full_page=True)

        # 4 · reviewing the workflow dataset's papers: both linked datasets' documents, no "add papers"
        page.goto(f"{a.url}/workspace?project={a.workflow}")
        page.wait_for_selector(".doctab[data-id]", timeout=30000)
        check(page.locator(".doctab[data-id]").count() == 2, f"the workspace lists the papers of the linked datasets ({page.locator('.doctab[data-id]').count()})")
        check(page.locator(".doctab-add").count() == 0 and page.locator("#dsdl").count() == 0, "no 'add papers' and no dataset export on a workflow dataset (each linked dataset has its own)")
        check("Econ papers" in page.locator(".doctabs-label").inner_text(), "the label names the workflow dataset")

        # 5 · after an import: the overview with the two next steps; adding to the workflow makes a workflow dataset
        page.goto(f"{a.url}/dataset?id={a.fresh}&imported=3")
        page.wait_for_selector("#ds-next .ds-next-opt", timeout=30000)
        nxt = page.locator("#ds-next")
        check("3 papers imported" in nxt.inner_text() and nxt.locator(".ds-next-opt").count() == 2, "the card offers two next steps")
        check(nxt.locator(f"a[href='/workspace?project={a.fresh}']").count() == 1, "review the data opens the workspace for this dataset")
        btn = nxt.locator("button[data-join]", has_text="Economics articles")
        check(btn.count() == 1 and "Regression tables side" in nxt.inner_text(), "add to the econ workflow names this dataset's side")
        page.screenshot(path=f"{a.shot}-5-next.png", full_page=True)
        btn.click()
        page.wait_for_selector("#wd-members", timeout=30000)
        check("Fresh import" in page.locator("#wd-members").inner_text() and "not linked yet" in page.locator("#wd-members").inner_text(), "the new workflow dataset opens with the fresh dataset on its side")
        page.screenshot(path=f"{a.shot}-6-new-workflow-dataset.png", full_page=True)

        check(not errors, f"no script errors: {errors[:3]}")
        browser.close()
    print(f"\n{'all checks passed' if not fails else f'{len(fails)} check(s) failed'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
