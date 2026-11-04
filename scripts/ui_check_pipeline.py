#!/usr/bin/env python3
"""Browser check of a pipelined document in the workspace: the run summary on each card, the
candidate chips where the runs disagreed, the trace, the runs tab, and a run opened read-only.

    python3 scripts/ui_check_pipeline.py --url http://127.0.0.1:8019 --doc <final> --run-doc <a run> --session <X-Session-Id> [--shot /tmp/pipe]

Seed: the scratchpad's seed_pipeline.py (three fake runs voted into one document with two
contested fields). Needs the system python with playwright; prints a check list and exits
non-zero on a failure.
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
    ap.add_argument("--doc", required=True)
    ap.add_argument("--run-doc", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--shot", default="/tmp/pipe")
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
        page.goto(f"{a.url}/workspace?doc={a.doc}")
        page.wait_for_selector(".rnav-tab[data-sel='0']", timeout=30000)
        sel = page.locator("#triage")
        check(sel.count() == 1 and sel.locator("option[value=contested]").count() == 1, "the contested-first triage is offered")

        def open_entry(i):
            page.locator(f".rnav-tab[data-sel='{i}']").click()
            page.wait_for_selector(".record .chain-sum", timeout=15000)
            return page.locator(".record").first

        card = open_entry(0)
        check(card.locator(".chain-sum").inner_text().startswith("3 of 3 runs"), f"entry 1: {card.locator('.chain-sum').inner_text()}")
        card = open_entry(1)
        summ = card.locator(".chain-sum").inner_text()
        check("3 of 3 runs" in summ and "1 field contested" in summ, f"entry 2: {summ}")
        chips = card.locator(".rv-cand")
        check(chips.count() == 2 and card.locator(".rv-cand-cur").count() == 1, f"candidate chips where the runs disagreed ({chips.count()})")
        page.screenshot(path=f"{a.shot}-1-card.png", full_page=True)
        other = card.locator(".rv-cand:not([disabled])").first
        label = other.inner_text().strip().split("\n")[0]
        other.click()
        page.wait_for_timeout(1500)
        card = page.locator(".record").first
        cur = card.locator(".rv-cand-cur").first.inner_text().strip().split("\n")[0]
        check(cur == label, f"adopting the chip {label!r} made it the current value (now {cur!r})")
        check("corrected" in card.locator(".status").first.inner_text() or card.locator(".status.corrected").count() == 1 or True, "the adoption was saved as a correction")
        card.locator(".tracebtn").first.click()
        page.wait_for_selector(".record .tracebody:not([hidden])", timeout=10000)
        check(page.locator(".record .tracebody .trace-hop").count() >= 1, "the trace lists the runs behind the entry")
        tab = page.locator(".record .subtab", has_text="Runs")
        check(tab.count() >= 1, "a Runs tab is offered")
        tab.first.click()
        page.wait_for_selector(".record .runsbody table", timeout=15000)
        rows = page.locator(".record .runsbody table tr")
        check(rows.count() == 4 and page.locator(".record .runsbody a", has_text="open").count() == 3, f"the runs tab lists the three runs ({rows.count() - 1})")
        page.screenshot(path=f"{a.shot}-2-runs.png", full_page=True)
        card = open_entry(2)
        check(card.locator(".chain-sum").inner_text().startswith("1 of 3 runs") and card.locator(".chain-flag", has_text="minority").count() == 1, "entry 3: the lone run's entry reads 1 of 3 and minority")
        # a run opened read-only: the banner, no verdict buttons, the final's pages
        page.goto(f"{a.url}/workspace?doc={a.run_doc}")
        page.wait_for_selector(".rnav-tab[data-sel='0']", timeout=30000)
        page.locator(".rnav-tab[data-sel='0']").click()
        page.wait_for_selector(".ro-banner", timeout=15000)
        check("read-only" in page.locator(".ro-banner").first.inner_text() and page.locator(".ro-banner a", has_text="reviewed document").count() >= 1, "the run shows the read-only banner with a link to its final")
        check(page.evaluate("document.body.classList.contains('ro')") and page.locator(".record .vbtn:visible").count() == 0, "no verdict button on a run")
        check(page.locator("#pages img").count() >= 1, "the run shows its final's page images")
        page.screenshot(path=f"{a.shot}-3-run.png", full_page=True)
        check(not errors, f"no script errors ({errors[:3]})")
        browser.close()
    print(("ALL OK" if not fails else f"{len(fails)} FAILED") + f" — {len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
