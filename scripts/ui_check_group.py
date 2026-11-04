#!/usr/bin/env python3
"""Browser check of a dataset group's joint review: the Workflow panel names the linked datasets
and the joint stage; the queue walks the paper's tables then its claims across both datasets;
verdicts move the count; the claims dataset shows the same run.

    python3 scripts/ui_check_group.py --url http://127.0.0.1:8019 --tables <id> --claims <id> --run <id> --session <X-Session-Id> [--shot /tmp/grp]
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
    ap.add_argument("--tables", required=True)
    ap.add_argument("--claims", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--shot", default="/tmp/grp")
    a = ap.parse_args()
    fails: list[str] = []

    def check(cond, what):
        print(("  ok  " if cond else "  FAIL") + " " + what)
        if not cond:
            fails.append(what)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1500, "height": 1100})
        ctx.add_init_script(f"localStorage.setItem('paperlens_sid', {a.session!r});")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text and "Failed to load resource" not in m.text else None)
        page.on("dialog", lambda d: d.accept())
        page.goto(f"{a.url}/dataset?id={a.tables}")
        page.wait_for_selector("#ds-update:not([hidden]) .wf-row", timeout=30000)
        grp = page.locator("#ds-update .wf-group")
        check(grp.count() == 1 and "tables" in grp.inner_text() and "claims" in grp.inner_text() and "this dataset" in grp.inner_text(), f"the panel names the linked datasets: {grp.inner_text()[:100]}")
        check(grp.locator("a[href^='/dataset?id=']").count() == 1, "the other dataset is linked")
        joint = page.locator("#ds-update .wf-row", has_text="Joint review")
        check(joint.count() == 1 and "tables → claims" in joint.inner_text() and "0 of 4 checked" in joint.inner_text(), f"the joint stage row: {joint.inner_text()[:120]}")
        link = joint.locator("a[href*='queue=']")
        check(link.count() == 1, "the joint row links to its queue")
        check("holds" in page.locator("#ds-update").inner_text() and "joint" in page.locator("#ds-update").inner_text(), "the release gate holds on the joint review")
        page.screenshot(path=f"{a.shot}-1-panel.png", full_page=True)
        link.click()
        page.wait_for_selector("#queuebar:not([hidden])", timeout=30000)
        bar = page.locator("#queuebar").inner_text()
        check("joint" in bar and "0 of 1 paper" in bar, f"the queue bar: {bar[:120]}")
        tabs = page.locator(".doctab[data-id]")
        check(tabs.count() == 2, f"the sidebar lists the paper's two documents ({tabs.count()})")
        page.wait_for_function("document.querySelectorAll('.docbar .docbar-doc').length >= 2", timeout=15000)   # the paper strip loads (and redraws) after the panel
        strip = page.locator(".docbar")
        check(strip.locator(".docbar-doc").count() == 2 and "0/" in strip.inner_text(), f"the paper strip shows both sides: {strip.inner_text()[:80]}")
        page.screenshot(path=f"{a.shot}-2-queue.png", full_page=True)
        # verdicts on the first document walk the queue into the second (the claims)
        first_doc = page.evaluate("document.querySelector('.doctab.active') ? document.querySelector('.doctab.active').dataset.id || '' : ''")
        n = 0
        for _ in range(12):
            btn = page.locator(".record .vbtn.ok:not(.on)").first
            if btn.count() == 0:
                break
            btn.click(); n += 1
            page.wait_for_timeout(700)
            if page.locator(".record .vbtn.ok:not(.on)").count() == 0 and page.locator(".rnav-tab[data-sel]:not(.active)").count():
                nxt = page.locator(".rnav-tab[data-sel]:not(.active)").first
                if nxt.count():
                    nxt.click(); page.wait_for_timeout(500)
        bar = page.locator("#queuebar").inner_text()
        check(n >= 1 and "checked" in bar, f"verdicts moved the count ({n} given): {bar[:120]}")
        page.screenshot(path=f"{a.shot}-3-verdicts.png", full_page=True)
        page.goto(f"{a.url}/dataset?id={a.claims}")
        page.wait_for_selector("#ds-update:not([hidden]) .wf-row", timeout=30000)
        check(page.locator("#ds-update .wf-run").count() == 1 and "Econ tables" in page.locator("#ds-update .wf-group").inner_text(), "the claims dataset shows the same run and its sibling")
        # a second run with nothing new to upload: "Uploads complete" completes both supplied steps and the checks run
        r = page.request.post(f"{a.url}/api/datasets/{a.tables}/workflow/runs", data={}, headers={"X-Session-Id": a.session})
        check(r.ok, f"a second run starts ({r.status})")
        page.goto(f"{a.url}/dataset?id={a.tables}")
        page.wait_for_selector("#wf-complete", timeout=30000)
        check(page.locator("#ds-update a", has_text="Upload the tables files").count() == 1 and page.locator("#ds-update a", has_text="Upload the claims files").count() == 1,
              "one upload button per dataset role")
        page.locator("#wf-complete").click()
        page.wait_for_function("!document.querySelector('#wf-complete')", timeout=30000)
        run_txt = page.locator("#ds-update .wf-run").inner_text()
        check("edges needs an API key" in run_txt, f"Uploads complete moved the run to the edges step: {run_txt[:90]}")
        chips = {t: page.locator("#ds-update .wf-row", has_text=t).locator(".badge").first.inner_text().strip()
                 for t in ("Contract check (tables)", "Number check", "Contract check (claims)", "Estimates against the tables")}
        check(all(v == "fresh" for v in chips.values()), f"the checks ran: {chips}")
        check(not errors, f"no script errors ({errors[:3]})")
        browser.close()
    print(("ALL OK" if not fails else f"{len(fails)} FAILED") + f" — {len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
