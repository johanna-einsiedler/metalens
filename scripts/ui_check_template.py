#!/usr/bin/env python3
"""Browser check of a review TEMPLATE (paperlens/templates.py, static/template.js): a coder works a
task's queue through the template's stages, the owner reads every coder's answers.

    python3 scripts/ui_check_template.py --url http://127.0.0.1:8031 --run <run id> --step judgment --doc <document id>
                                         --owner EMAIL --coder EMAIL [--password pw123456] [--shots DIR]

--run/--step: a review step with `template: genai-work-judgment` whose gate assigned --doc to --coder
(the scratchpad's seed_template.py makes one). Checks: the coder's queue opens the document through
the template — three stages; the design stage shows no evidence items and its questions as selects
in labelled sections, nothing of the model's blind values (the summary) anywhere; a select saved
survives a reload; the units stage shows the task's description with its digits masked and its
cells read-only, the arms grouped under their task with the reference arm marked, a category
select of prefixed options, "＋" adds a unit card and ✕ removes it; the done button is disabled
while required answers are missing and says how many; the headline stage shows the abstract's
sentence masked; once every required answer is in, done marks the paper verified and the queue
bar counts it; the owner sees the same stages unblinded with the coder's answers under each unit
and no answer controls of their own. Needs the system python with playwright
(``python3 -m playwright install chromium``)."""
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
    ap.add_argument("--url", default="http://127.0.0.1:8031")
    ap.add_argument("--run", required=True)
    ap.add_argument("--step", default="judgment")
    ap.add_argument("--doc", required=True)
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
            page.on("dialog", lambda d: d.accept())
            return ctx, page

        def shot(page, name):
            if a.shots:
                os.makedirs(a.shots, exist_ok=True); page.screenshot(path=os.path.join(a.shots, name), full_page=False)

        queue = f"{a.url}/workspace?queue={a.run}:{a.step}&doc={a.doc}"
        # ── the coder ──
        ctx, p = signed_in(a.coder)
        p.goto(queue); p.wait_for_selector(".stg-bar .stg-tab", timeout=25000)
        tabs = p.locator(".stg-bar .stg-tab").all_inner_texts()
        check(len(tabs) == 3 and "Study design" in tabs[0] and "Units" in tabs[1] and "Headline" in tabs[2], f"the task opens on the template's three stages: {tabs}")
        check(p.evaluate("() => document.body.classList.contains('stg-noev')"), "the design stage lists no evidence items (evidence: none)")
        check(p.locator(".tq-cells .stg-section").count() >= 4, f"the design questions sit in labelled sections ({p.locator('.tq-cells .stg-section').count()})")
        check(p.locator(".tq-q select").count() >= 10, f"the design stage's questions are selects ({p.locator('.tq-q select').count()})")
        html = p.content()
        check("SECRET-SUMMARY" not in html, "nothing of the model's blind paper values reaches the page")
        owe = p.locator("#stg-owe").inner_text()
        check("required answer" in owe and "missing" in owe, f"the bar says what is owed: {owe!r}")
        check(p.locator("#stg-verifyall").is_disabled(), "the done button is disabled while answers are missing")
        scale = p.locator(".tq-q .rv-selwrap[data-path='scale'] select").first
        scale.select_option(index=1); p.wait_for_timeout(700)
        chosen = scale.input_value()
        p.reload(); p.wait_for_selector(".tq-q select", timeout=25000)
        check(p.locator(".tq-q .rv-selwrap[data-path='scale'] select").first.input_value() == chosen, f"a design answer survives a reload ({chosen})")
        # every required design question
        for name in ("screen_ai_treatment", "screen_work_outcome", "screen_design"):
            p.locator(f".tq-q .rv-selwrap[data-path='{name}'] select").first.select_option(index=1); p.wait_for_timeout(300)
        shot(p, "tpl-design.png")
        # ── units ──
        p.locator(".stg-bar .stg-tab").nth(1).click(); p.wait_for_selector(".tq-block[data-unit='entries']", timeout=15000)
        desc = p.locator(".tq-block[data-unit='entries'] .tq-shown .rv-val [data-path='task_description']").first
        check(desc.count() == 1 and "#" in desc.inner_text() and "3" not in desc.inner_text(), f"the task's description is shown with its digits masked: {desc.inner_text()!r}")
        check(desc.get_attribute("contenteditable") in (None, "false"), "…and read-only")
        check(p.locator(".tq-block[data-unit='arms'] .tq-group-h").count() == 1, "the arms are grouped under their task")
        check(p.locator(".tq-block[data-unit='arms'] .tq-mark", has_text="reference arm").count() == 1, "the reference arm is marked")
        opts = p.locator(".tq-block[data-unit='arms'] .tq-unit").first.locator(".tq-q .rv-selwrap[data-path='category'] option").all_inner_texts()
        check(any(o.startswith("reference arm") for o in opts) and any(o.startswith("treatment arm") for o in opts), "the arm category offers prefixed options from both fields")
        # answer the units: the task, both arms, the outcome, the subgroup
        for unit in p.locator(".tq-block[data-unit='entries'] .tq-unit, .tq-block[data-unit='arms'] .tq-unit, .tq-block[data-unit='outcomes'] .tq-unit").all():
            unit.locator(".tq-q .rv-selwrap[data-path='category'] select").first.select_option(index=1); p.wait_for_timeout(250)
            unit.locator(".tq-q .rv-selwrap[data-path='verdict'] select").first.select_option(index=1); p.wait_for_timeout(250)
        sub = p.locator(".tq-block[data-unit='subgroup'] .tq-unit")
        check(sub.count() == 1 and "novices" in sub.first.inner_text(), f"the distinct subgroups of the estimands are units (full_sample left out): {sub.count()}")
        sub.first.locator(".tq-q .rv-selwrap[data-path='verdict'] select").first.select_option(index=1); p.wait_for_timeout(300)
        added_before = p.locator(".tq-block[data-unit='entries'] .tq-unit.tq-added").count()
        p.locator(".tq-block[data-unit='entries'] .tq-add").click(); p.wait_for_timeout(900)
        check(p.locator(".tq-block[data-unit='entries'] .tq-unit.tq-added").count() == added_before + 1, "＋ adds a unit card of the coder's own")
        p.locator(".tq-block[data-unit='entries'] .tq-unit.tq-added .tq-del").first.click(); p.wait_for_timeout(900)
        check(p.locator(".tq-block[data-unit='entries'] .tq-unit.tq-added").count() == added_before, "…and ✕ removes it with its answers")
        for _ in range(added_before):                                                     # leftovers of an earlier run
            p.locator(".tq-block[data-unit='entries'] .tq-unit.tq-added .tq-del").first.click(); p.wait_for_timeout(700)
        shot(p, "tpl-units.png")
        # ── headline ──
        p.locator(".stg-bar .stg-tab").nth(2).click(); p.wait_for_selector(".tq-block[data-unit='claims']", timeout=15000)
        claim = p.locator(".tq-block[data-unit='claims'] .tq-unit")
        check(claim.count() == 1 and "##" in claim.first.locator(".tq-name").inner_text(), f"the abstract's sentence is a unit, masked: {claim.first.locator('.tq-name').inner_text()!r}")
        claim.first.locator(".tq-q .rv-selwrap[data-path='verdict'] select").first.select_option(index=1); p.wait_for_timeout(300)
        p.locator(".tq-block .tq-q .rv-selwrap[data-path='full_review'] select").first.select_option(index=1); p.wait_for_timeout(600)
        owe = p.locator("#stg-owe").inner_text()
        check("missing" not in owe, f"every required answer in: {owe!r}")
        check(not p.locator("#stg-verifyall").is_disabled(), "the done button is enabled")
        p.locator("#stg-verifyall").click(); p.wait_for_timeout(1500)
        bar = p.locator("#queuebar").inner_text()
        check("1 of 1 checked" in bar, f"the queue counts the paper as checked: {bar!r}")
        shot(p, "tpl-done.png")
        # ── the owner ──
        ctx_o, po = signed_in(a.owner)
        po.goto(queue); po.wait_for_selector(".stg-bar .stg-tab", timeout=25000)
        po.locator(".stg-bar .stg-tab").nth(1).click(); po.wait_for_selector(".tq-block[data-unit='entries']", timeout=15000)
        check("SECRET-SUMMARY" in po.content() or po.locator(".tq-block[data-unit='entries'] .tq-shown .rv-val [data-path='task_description']").first.inner_text().find("3") >= 0,
              "the owner sees the document unblinded and unmasked")
        by = po.locator(".tq-block[data-unit='arms'] .ans-by")
        check(by.count() >= 2 and "coder_1" in by.first.inner_text(), f"the owner sees each coder's answers under the unit ({by.count()} lines)")
        check(po.locator(".tq-q select").count() == 0, "…and no answer controls of their own")
        check(po.locator("#stg-owe").inner_text().strip() == "" and "Verdict: ok" in by.first.inner_text(), "…the bar counts nothing owed by the owner; an answer shows by its option")
        shot(po, "tpl-owner.png")
        check(not errors, f"no script errors ({errors[:2]})")
        browser.close()
    print("ALL OK" if not fails else f"{len(fails)} FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
