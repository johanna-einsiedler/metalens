#!/usr/bin/env python3
"""Browser check of the dataset page's Workflow panel and the release gate.

    python3 scripts/ui_check_workflow.py --url http://127.0.0.1:8019 --dataset <id> --session <X-Session-Id> [--shot /tmp/wf]

Seed: a dataset with the econ-regressions workflow declared and a finished run whose new paper's
columns await review (the scratchpad's seed_workflow.py). Checks: the panel lists the steps with
their state chips, the run's papers, the gate that holds the release with a review link; the
release form refuses while the gate holds; after the verdicts in the workspace the gate opens
and a release is created naming the run. Needs the system python with playwright; prints a check
list and exits non-zero on a failure.
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
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--shot", default="/tmp/wf")
    a = ap.parse_args()
    fails: list[str] = []

    def check(cond, what):
        print(("  ok  " if cond else "  FAIL") + " " + what)
        if not cond:
            fails.append(what)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 1100})
        ctx.add_init_script(f"localStorage.setItem('paperlens_sid', {a.session!r});")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text and "Failed to load resource" not in m.text else None)
        page.on("dialog", lambda d: d.accept())
        page.goto(f"{a.url}/dataset?id={a.dataset}")
        page.wait_for_selector("#ds-update:not([hidden]) .wf-row", timeout=30000)
        rows = page.locator("#ds-update .wf-row")
        check(rows.count() == 4, f"the panel lists the workflow's four steps ({rows.count()})")
        head = page.locator("#ds-update .ds-card-h").inner_text()
        check("Economics regression tables" in head and "econ-regressions@" in head, f"the panel names the workflow and its contract: {head.strip()[:80]}")
        chips = [c.strip() for c in page.locator("#ds-update .wf-row .badge").all_inner_texts()]
        check(chips[:4] == ["awaiting review", "fresh", "fresh", "blocked"], f"state chips: tables awaiting review, both checks fresh, the release blocked ({chips})")
        numbers = page.locator("#ds-update .wf-row", has_text="Number check")
        check(numbers.count() == 1 and "1 finding" in numbers.inner_text() and numbers.locator("a", has_text="open").count() == 1, "the number check row counts its finding and links into the review")
        check("0 of 2 checked" in page.locator("#ds-update .wf-row").first.inner_text(), "the tables row shows the gate's count")
        run = page.locator("#ds-update .wf-run")
        check(run.count() == 1 and "done" in run.inner_text() and "new-paper.pdf" in run.inner_text(), "the finished run and its paper are listed")
        gate = page.locator("#ds-update").inner_text()
        check("Release gate:" in gate and "holds" in gate and "tables awaits review" in gate, "the release gate holds on the review")
        page.screenshot(path=f"{a.shot}-panel.png", full_page=True)
        # the release form refuses while the gate holds
        page.locator("#ds-rel-btn").click()
        page.wait_for_selector("#ds-rel-go", timeout=15000)
        page.locator("#ds-rel-go").click()
        page.wait_for_function("() => (document.querySelector('#ds-rel-msg') || {}).textContent.includes('gate') || !document.querySelector('#ds-rel-go')", timeout=15000)
        msg = page.locator("#ds-rel-msg").inner_text() if page.locator("#ds-rel-msg").count() else ""
        check("gate" in msg, f"creating a release is refused while the gate holds: {msg[:80]}")
        # the finding reaches the review: the "open" link orders the entries by findings, the card carries the note
        numbers.locator("a", has_text="open").click()
        page.wait_for_selector(".record .vbtn.ok", timeout=30000)
        sel = page.locator("#triage")
        chosen = sel.input_value() if sel.count() else "(no select)"
        check(chosen == "checks", f"the review opens in the check-findings-first order (select: {chosen}; url: {page.url[-60:]})")
        note = page.locator(".record .check-note")
        check(note.count() == 1 and "regression-cells" in note.inner_text() and "refers to no coefficient" in note.inner_text(), f"the card notes the finding: {note.inner_text()[:70] if note.count() else '—'}")
        page.screenshot(path=f"{a.shot}-card.png", full_page=True)
        # verdicts in the workspace: the review link opens the dataset's review
        page.goto(f"{a.url}/dataset?id={a.dataset}")
        page.wait_for_selector("#ds-update:not([hidden]) .wf-row", timeout=30000)
        page.locator("#ds-update a", has_text="review 2").first.click()
        page.wait_for_selector(".record .vbtn.ok", timeout=30000)
        for _ in range(2):
            page.locator(".record .vbtn.ok").first.click()
            page.wait_for_timeout(600)
        page.goto(f"{a.url}/dataset?id={a.dataset}")
        page.wait_for_selector("#ds-update:not([hidden]) .wf-row", timeout=30000)
        gate = page.locator("#ds-update").inner_text()
        check("Release gate:" in gate and "open" in gate.split("Release gate:")[1][:40], "after the verdicts the release gate is open")
        page.locator("#ds-rel-btn").click()
        page.wait_for_selector("#ds-rel-go", timeout=15000)
        page.locator("#ds-rel-go").click()
        page.wait_for_function("() => document.querySelector('#ds-rellist') && /v1/.test(document.querySelector('#ds-rellist').textContent)", timeout=30000)
        check("v1" in page.locator("#ds-rellist").inner_text(), "a release is created once the gate is open")
        page.screenshot(path=f"{a.shot}-released.png", full_page=True)
        check(not errors, f"no script errors ({errors[:3]})")
        browser.close()
    print(f"{len(fails)} failure(s)" if fails else "all checks passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
