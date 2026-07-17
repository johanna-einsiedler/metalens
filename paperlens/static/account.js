// Account settings: citation name (for public-dataset attribution), API keys
// (browser-only), change password, delete account.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { PROVIDERS, getKey, setKey } from "/static/keys.js";

const $ = (s) => document.querySelector(s);
const PLABEL = { openai: "OpenAI", google: "Google Gemini", anthropic: "Anthropic",
                 deepseek: "DeepSeek", mistral: "Mistral" };

async function init() {
  const body = $("#acct-body");
  const me = await api.me();
  if (!me || !me.email) {
    body.innerHTML = '<p class="muted">Please sign in (top-right) to manage your account.</p>';
    return;
  }
  body.innerHTML = `
    <section class="acct-sec">
      <h3>Profile</h3>
      <p class="muted" style="font-size:13px">Signed in as <b>${esc(me.email)}</b>.</p>
      <div class="wf-field"><label>Citation name</label>
        <input id="ac-cite" placeholder="e.g. J. Einsiedler" value="${esc(me.citation_name || "")}"/>
        <div class="sf-help">Used to attribute your <b>public</b> datasets &amp; dashboards in citations.</div></div>
      <button class="btn btn-primary" id="ac-savep">Save</button> <span id="ac-pmsg" class="muted"></span>
    </section>

    <section class="acct-sec">
      <h3>API keys</h3>
      <p class="muted" style="font-size:13px">Stored <b>only in this browser</b> and auto-filled on the extract page — never sent to or stored on our servers. Clear a field to remove it.</p>
      <div id="ac-keys"></div>
    </section>

    <section class="acct-sec">
      <h3>Metalens credits</h3>
      <p class="muted" style="font-size:13px">Run extractions without your own API key — Metalens supplies the model &amp; key, and each run uses one credit.</p>
      <div id="ac-credits"><p class="muted">Loading…</p></div>
    </section>

    <section class="acct-sec">
      <h3>Change password</h3>
      <form id="ac-pwform" class="acct-form">
        <input id="ac-old" type="password" placeholder="current password" autocomplete="current-password"/>
        <input id="ac-new" type="password" placeholder="new password" autocomplete="new-password"/>
        <div class="wf-actions" style="justify-content:flex-start">
          <button class="btn btn-primary" type="submit">Change password</button>
          <span id="ac-pwmsg" class="muted" style="align-self:center"></span>
        </div>
      </form>
    </section>

    <section class="acct-sec danger">
      <h3>Delete account</h3>
      <p class="muted" style="font-size:13px">Permanently deletes your account, your extracted documents (and their stored PDFs + page images), and your datasets. This cannot be undone.</p>
      <button class="btn btn-ghost" id="ac-del">Delete my account…</button> <span id="ac-delmsg" class="muted"></span>
    </section>`;

  $("#ac-savep").onclick = async () => {
    try { await api.updateProfile({ citation_name: $("#ac-cite").value }); $("#ac-pmsg").textContent = "✓ saved"; }
    catch (e) { $("#ac-pmsg").textContent = "✗ " + e.message; }
  };

  renderKeys();
  renderCredits();

  $("#ac-pwform").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api.changePassword({ old_password: $("#ac-old").value, new_password: $("#ac-new").value });
      $("#ac-pwmsg").textContent = "✓ changed"; $("#ac-old").value = ""; $("#ac-new").value = "";
    } catch (ex) { $("#ac-pwmsg").textContent = "✗ " + ex.message; }
  };

  $("#ac-del").onclick = async () => {
    const typed = prompt(`Type your email (${me.email}) to confirm account deletion:`);
    if (typed !== me.email) { if (typed !== null) alert("Email didn't match — not deleted."); return; }
    try { await api.deleteAccount(); alert("Account deleted."); location.href = "/"; }
    catch (e) { $("#ac-delmsg").textContent = "✗ " + e.message; }
  };
}

function renderKeys() {
  const box = $("#ac-keys");
  box.innerHTML = PROVIDERS.map((p) =>
    `<div class="wf-field"><label>${esc(PLABEL[p] || p)}</label>`
    + `<input class="ac-key" data-p="${p}" type="password" autocomplete="off"`
    + ` placeholder="paste your ${esc(PLABEL[p] || p)} API key" value="${esc(getKey(p))}"/></div>`).join("");
  box.querySelectorAll(".ac-key").forEach((inp) =>
    (inp.onchange = () => setKey(inp.dataset.p, inp.value.trim())));
}

async function renderCredits() {
  const box = $("#ac-credits"); if (!box) return;
  let c;
  try { c = await api.credits(); } catch { box.innerHTML = '<p class="muted">Unavailable.</p>'; return; }
  const head = `<p><b style="font-size:20px">${c.balance}</b> credit${c.balance === 1 ? "" : "s"} left `
    + `<span class="muted">· ${c.used} used of ${c.granted} granted</span></p>`
    + (c.offered
        ? `<p class="muted" style="font-size:12px">Keyless extraction is available${c.model ? ` (model: ${esc(c.model)})` : ""}. Choose “Metalens credits” on the extract page.</p>`
        : `<p class="muted" style="font-size:12px">Keyless extraction isn’t configured on this server right now.</p>`);
  const led = (c.ledger || []);
  const rows = led.length
    ? `<table class="cred-ledger"><thead><tr><th></th><th>Change</th><th>Reason</th><th>Model</th></tr></thead><tbody>`
      + led.map((e) => {
          const d = e.created_at ? new Date(e.created_at).toLocaleString() : "";
          const sign = e.delta > 0 ? `+${e.delta}` : `${e.delta}`;
          return `<tr><td class="muted">${esc(d)}</td><td>${esc(sign)}</td><td>${esc(e.reason || "")}</td><td class="muted">${esc(e.model || "")}</td></tr>`;
        }).join("") + `</tbody></table>`
    : '<p class="muted" style="font-size:12px">No credit activity yet.</p>';
  box.innerHTML = head + rows;
}

init();
