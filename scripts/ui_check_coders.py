#!/usr/bin/env python3
"""Browser check of coders (paperlens/coders.py): two coder accounts on one owner's dataset, each
coding independently; the owner's view of every coding.

    python3 scripts/ui_check_coders.py --url http://127.0.0.1:8021 --dataset <id> --run <run id> --doc <document id>
                                       --owner EMAIL --coder-a EMAIL --coder-b EMAIL --a-id <user id> [--password pw123456] [--shots DIR]

--dataset: a dataset of the owner's with a finished run whose gate says `coders: 2`, both coders
granted (scripts in the scratchpad seed one). Checks: a coder's workspace lists the dataset under
"Coding for" and offers "Code the papers"; in the review the coder is told they code as coder_1,
sees no delete / add buttons, edits a cell and verifies, and both survive a reload; the second
coder sees the original value and no verdict; the owner sees the record as stored, a chip per
coding, the "view as" switch, and one coder's coding read-only; the coder's queue bar counts
their own share; the owner's dataset page lists the coders with their tallies. Needs the
system python with playwright (``python3 -m playwright install chromium``)."""
from __future__ import annotations

import argparse
import os
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("  SKIP  playwright is not installed for this python (python3 -m pip install playwright; python3 -m playwright install chromium)")
    sys.exit(0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8021")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--doc", required=True)
    ap.add_argument("--owner", required=True)
    ap.add_argument("--coder-a", required=True)
    ap.add_argument("--coder-b", required=True)
    ap.add_argument("--a-id", required=True, help="coder A's user id (the owner's ?coder= switch)")
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
                os.makedirs(a.shots, exist_ok=True); page.screenshot(path=os.path.join(a.shots, name), full_page=False)

        # ── coder A ──
        ctx_a, pa = signed_in(a.coder_a)
        pa.goto(f"{a.url}/projects"); pa.wait_for_selector("#coded-section:not([hidden])", timeout=15000)
        check(pa.locator("#coded .proj-tile").count() >= 1, "the coder's workspace lists the dataset under “Coding for”")
        pa.goto(f"{a.url}/dataset?id={a.dataset}"); pa.wait_for_selector(".ds-actions", timeout=15000)
        check(pa.locator(".ds-actions a", has_text="Code the papers").count() == 1, "the dataset page offers “Code the papers”")
        check(pa.locator("#ds-del").count() == 0 and pa.locator("#ds-coders").count() == 0, "…and none of the owner's controls")
        pa.goto(f"{a.url}/workspace?project={a.dataset}&doc={a.doc}"); pa.wait_for_selector(".record .vbtn.ok", timeout=20000)
        check("coding as coder_1" in (pa.locator(".coding-head").inner_text() if pa.locator(".coding-head").count() else ""), "the review says whose coding it is (coder_1)")
        check(pa.locator("#deldoc").count() == 0 and pa.locator("#addfinding").count() == 0 and pa.locator(".record .recdel:visible").count() == 0,
              "no delete / add-entry controls for a coder")
        check(pa.locator("#doctabs .doctab").count() >= 2, "the sidebar lists the owner's papers")
        cell = pa.locator(".record .rv-editable[contenteditable='plaintext-only'][data-path]").first
        path = cell.get_attribute("data-path")
        orig = cell.inner_text()
        cell.click(); pa.keyboard.press("Meta+A"); pa.keyboard.type("Coded by A"); pa.keyboard.press("Tab"); pa.wait_for_timeout(800)
        card_of = lambda page: page.locator(".record", has=page.locator(f"[data-path='{path}']")).first   # the card holding the edited cell  # noqa: E731
        card_of(pa).locator(".vbtn.ok").first.click(); pa.wait_for_timeout(800)
        pa.reload(); pa.wait_for_selector(".record .vbtn.ok", timeout=20000)
        again = pa.locator(f".record [data-path='{path}']").first
        check(again.inner_text().strip() == "Coded by A", f"the coder's edit survives a reload ({path}: {orig!r} → 'Coded by A')")
        check(card_of(pa).locator(".status.verified").count() >= 1, "…and so does the verdict (verified)")
        shot(pa, "coder-a.png")
        pa.goto(f"{a.url}/workspace?queue={a.run}:tables"); pa.wait_for_selector("#queuebar:not([hidden])", timeout=20000)
        bar = pa.locator("#queuebar").inner_text()
        q = ctx_a.request.get(f"{a.url}/api/workflow-runs/{a.run}/queue/tables").json()
        check("your share" in bar and f"{q['progress']['checked']}" in bar and q["viewer_coder"], f"the queue bar counts the coder's own share ({q['progress']['checked']} of {q['progress']['drawn']})")
        # ── coder B ──
        ctx_b, pb = signed_in(a.coder_b)
        pb.goto(f"{a.url}/workspace?project={a.dataset}&doc={a.doc}"); pb.wait_for_selector(".record .vbtn.ok", timeout=20000)
        check("coding as coder_2" in (pb.locator(".coding-head").inner_text() if pb.locator(".coding-head").count() else ""), "the second coder codes as coder_2")
        bcell = pb.locator(f".record [data-path='{path}']").first
        check(bcell.inner_text().strip() == orig.strip(), "…and sees the original value, not coder_1's")
        check(card_of(pb).locator(".status.verified").count() == 0 and card_of(pb).locator(".status.unverified").count() >= 1, "…and no verdict")
        # ── the owner ──
        ctx_o, po = signed_in(a.owner)
        po.goto(f"{a.url}/workspace?project={a.dataset}&doc={a.doc}"); po.wait_for_selector(".record .vbtn.ok", timeout=20000)
        ocell = po.locator(f".record [data-path='{path}']").first
        check(ocell.inner_text().strip() == orig.strip(), "the owner sees the record as stored")
        chips = card_of(po).locator(".coding-chip")
        texts = [chips.nth(i).inner_text() for i in range(chips.count())]
        check(chips.count() >= 1 and any("coder_1" in t and "✓" in t and "✎" in t for t in texts), f"…with a chip for coder_1's coding (verified, changed a value): {texts}")
        opts = po.locator("#viewas option").all_inner_texts() if po.locator("#viewas").count() else []
        check(any("coder_1" in o for o in opts) and any("coder_2" in o for o in opts), f"…and a “view as” switch over the coders ({opts})")
        check(po.locator("#deldoc").count() == 1, "the owner keeps the delete control")
        shot(po, "owner.png")
        po.goto(f"{a.url}/workspace?project={a.dataset}&doc={a.doc}&coder={a.a_id}"); po.wait_for_selector(".record", timeout=20000)
        check(po.locator(".ro-banner").count() >= 1 and "coder_1" in po.locator(".ro-banner").first.inner_text() and po.evaluate("() => document.body.classList.contains('ro')"),
              "view as coder_1: a read-only banner")
        check(po.locator(f".record [data-path='{path}']").first.inner_text().strip() == "Coded by A", "…showing coder_1's value")
        po.goto(f"{a.url}/dataset?id={a.dataset}"); po.wait_for_selector("#ds-coderlist .ds-dashrow", timeout=20000)
        rows = po.locator("#ds-coderlist .ds-dashrow").all_inner_texts()
        check(len(rows) == 2 and "coder_1" in rows[0] and a.coder_a in rows[0] and "coder_2" in rows[1], f"the Coders card lists both coders: {rows}")
        po.wait_for_selector(".wf-row", timeout=20000)
        wf = po.locator("#ds-update").inner_text()
        check("2 coders per paper" in wf and "by coder" in wf and "coder_1" in wf, "the workflow card says two coders per paper and tallies each coder")
        shot(po, "owner-dataset.png")
        check(not errors, f"no script errors ({errors[:2]})")
        browser.close()
    print(("ALL OK" if not fails else f"{len(fails)} FAILED"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
