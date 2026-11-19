#!/usr/bin/env python3
"""Browser check of a PROJECT (paperlens/projects.py): the owner's project page and a coder's workspace.

    python3 scripts/ui_check_project.py --url http://127.0.0.1:8032 --project <id> --run <run id> --step coding
                                        --owner EMAIL --coder EMAIL [--password pw123456] [--shots DIR]

--project: a project with two linked datasets, two people and a finished run whose review step
--step (coders: 1) assigned its paper to --coder (the scratchpad's seed_project.py makes one).
Checks: /dataset?id=<project> redirects to /project?id=; the page says Project, lists both
datasets, the people by label and the review task with its progress and, for the owner, the
assignment matrix with a "move to…" select; the coder's workspace lists the task under "My
review tasks" with their share and the Projects section is named so; the task's tile opens the
queue; no script errors. Needs the system python with playwright (``python3 -m playwright
install chromium``)."""
from __future__ import annotations

import argparse
import os
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("  SKIP  playwright is not installed for this python")
    sys.exit(0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8032")
    ap.add_argument("--project", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--step", default="coding")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--coder", required=True)
    ap.add_argument("--password", default="pw123456")
    ap.add_argument("--shots", default=None)
    a = ap.parse_args()
    fails: list[str] = []
    errors: list[str] = []

    def check(cond, what):
        print(f"  {'ok  ' if cond else 'FAIL'} {what}")
        if not cond:
            fails.append(what)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        def signed_in(email: str):
            ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
            r = ctx.request.post(f"{a.url}/api/auth/login", data={"email": email, "password": a.password})
            assert r.ok, f"login failed for {email}: {r.status} {r.text()}"
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            return ctx, page

        def shot(page, name):
            if a.shots:
                os.makedirs(a.shots, exist_ok=True); page.screenshot(path=os.path.join(a.shots, name), full_page=True)

        # ── the owner ──
        ctx, p = signed_in(a.owner)
        p.goto(f"{a.url}/dataset?id={a.project}"); p.wait_for_selector("#wd-members", timeout=25000)
        check(p.url.startswith(f"{a.url}/project?id="), f"a project opened as a dataset lands on /project ({p.url.split(a.url)[1]})")
        head = p.locator(".ds-head").inner_text()
        check("project" in head.lower() and "Round 1" in head, "the page says Project and its title")
        check(p.locator("#wd-members .wd-member").count() == 2, "both datasets are listed")
        p.wait_for_selector("#ds-coderlist .ds-dashrow", timeout=20000)
        people = p.locator("#ds-coderlist .ds-dashrow").all_inner_texts()
        check(len(people) == 2 and "coder_1" in people[0] and "coder_2" in people[1], f"the People card lists both by label: {[x[:30] for x in people]}")
        p.wait_for_selector("#wd-tasklist .wd-task", timeout=20000)
        task = p.locator("#wd-tasklist .wd-task").first.inner_text()
        check("Code the tables" in task and "1 coder per paper" in task and "0 of 2 checked" in task, f"the review task with its progress: {task[:120]!r}")
        p.locator("#wd-tasklist details summary").first.click(); p.wait_for_timeout(300)
        check(p.locator("#wd-tasklist .wd-matrix tbody tr").count() == 1 and p.locator("#wd-tasklist .wd-move").count() == 1, "the assignment matrix: one paper, a move-to select")
        shot(p, "project-owner.png")
        # ── the coder ──
        ctx_c, pc = signed_in(a.coder)
        pc.goto(f"{a.url}/projects"); pc.wait_for_selector("#tasks-section:not([hidden])", timeout=25000)
        tile = pc.locator("#tasks .proj-tile").first
        check(tile.count() == 1 and "Code the tables" in tile.inner_text() and "2" in tile.inner_text() and "still to check" in tile.inner_text(), f"My review tasks lists the task with the share: {tile.inner_text()[:100]!r}")
        check(pc.locator("#wf-section h3").inner_text().strip() == "Projects", "the workflows section is named Projects")
        shot(pc, "workspace-coder.png")
        tile.click(); pc.wait_for_selector("#queuebar:not([hidden])", timeout=25000)
        check(f"queue={a.run}:{a.step}" in pc.url and "your share" in pc.locator("#queuebar").inner_text(), "the tile opens the coder's queue")
        pc.goto(f"{a.url}/project?id={a.project}"); pc.wait_for_selector("#wd-tasklist .wd-task", timeout=25000)
        check(pc.locator("#ds-coders").count() == 0 and "code your 2" in pc.locator("#wd-tasklist").inner_text(), "the coder's project page: no People card, a link to code their 2")
        check(not errors, f"no script errors ({errors[:2]})")
        browser.close()
    print("ALL OK" if not fails else f"{len(fails)} FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
