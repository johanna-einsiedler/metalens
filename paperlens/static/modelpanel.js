// The one model picker of the proposal pages (vocabulary, pages, derived units, the dashboard
// editor): a credit when the server offers them, else the person's own API key (kept in this
// browser only, used once) or a local / self-hosted model server. Ids are prefixed per page so two
// panels on one page never clash; the page passes the note that says what the model will see.
import { esc } from "/static/grammar.js";
import { getKey, setKey } from "/static/keys.js";

const PROVIDER_LABEL = { openai: "OpenAI", google: "Google", anthropic: "Anthropic", deepseek: "DeepSeek", mistral: "Mistral" };
const $ = (s) => document.querySelector(s);

export function renderModelPanel(host, { prefix, cfg, models, note, creditsNote }) {
  const bal = cfg && cfg.credits ? cfg.credits.balance || 0 : 0;
  const credits = !!(cfg && cfg.logged_in && cfg.offered && bal > 0);
  const provs = Object.keys(models || {});
  host.innerHTML = `<div class="cmp-modes">`
    + (credits ? `<label class="cs-opt"><input type="radio" name="${prefix}-mode" value="credits" checked/> <span>Use a credit <b>(${bal} left)</b> <span class="muted">· ${esc(cfg.model || "")}${creditsNote ? ` · ${esc(creditsNote)}` : ""}</span></span></label>` : "")
    + `<label class="cs-opt"><input type="radio" name="${prefix}-mode" value="own"${credits ? "" : " checked"}/> <span>My own API key</span></label>`
    + `<label class="cs-opt"><input type="radio" name="${prefix}-mode" value="local"/> <span>A local / self-hosted model</span></label></div>`
    + `<div id="${prefix}-own" class="cmp-fields"><div class="select-wrap"><select id="${prefix}-prov">${provs.map((p) => `<option value="${esc(p)}">${esc(PROVIDER_LABEL[p] || p)}</option>`).join("")}</select></div>`
    + `<div class="select-wrap"><select id="${prefix}-mdl"></select></div><input id="${prefix}-key" type="password" placeholder="your API key (used once, never stored)" autocomplete="off"/></div>`
    + `<div id="${prefix}-local" class="cmp-fields" hidden><input id="${prefix}-url" type="url" placeholder="server URL, e.g. https://models.example.org:8000"/><input id="${prefix}-lmdl" placeholder="model name"/></div>`
    + (note ? `<p class="muted cmp-note">${esc(note)}</p>` : "");
  const fill = () => { const p = $(`#${prefix}-prov`).value; $(`#${prefix}-mdl`).innerHTML = ((models || {})[p] || []).map((m) => `<option value="${esc(m.value)}">${esc(m.label)}</option>`).join(""); $(`#${prefix}-key`).value = getKey(p); };
  if (provs.length) { $(`#${prefix}-prov`).onchange = fill; fill(); }
  $(`#${prefix}-key`).oninput = () => setKey($(`#${prefix}-prov`).value, $(`#${prefix}-key`).value.trim());
  try { $(`#${prefix}-url`).value = localStorage.getItem("metalens_local_base_url") || ""; $(`#${prefix}-lmdl`).value = localStorage.getItem("metalens_local_model") || ""; } catch { /* no storage */ }
  const sync = () => { const m = $(`input[name="${prefix}-mode"]:checked`).value; $(`#${prefix}-own`).hidden = m !== "own"; $(`#${prefix}-local`).hidden = m !== "local"; };
  document.querySelectorAll(`input[name="${prefix}-mode"]`).forEach((r) => (r.onchange = sync)); sync();
}

// The choice as the API takes it: {use_credits} | {model, api_key} | {model, base_url, api_key: ""}.
// Returns null (after a message, unless quiet) when no model was given.
export function modelChoice(prefix, { quiet } = {}) {
  const m = ($(`input[name="${prefix}-mode"]:checked`) || {}).value;
  const c = m === "credits" ? { use_credits: true }
    : m === "local" ? { model: $(`#${prefix}-lmdl`).value.trim(), base_url: $(`#${prefix}-url`).value.trim(), api_key: "" }
    : { model: $(`#${prefix}-mdl`).value, api_key: $(`#${prefix}-key`).value.trim() };
  if (c.base_url) { try { localStorage.setItem("metalens_local_base_url", c.base_url); localStorage.setItem("metalens_local_model", c.model); } catch { /* no storage */ } }
  if (!c.use_credits && !c.api_key && !c.base_url) {
    if (!quiet) alert("A proposal needs a model: enter your own API key or a local model server.");
    return null;
  }
  return c;
}
