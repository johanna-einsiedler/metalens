// Load a preset someone sent as a .json file: check it (every error and warning, the schema id it
// runs under), choose who may use it, and save it as the viewer's own preset. The file keeps its
// own id — and so its schema id — when no other preset uses it (POST /api/presets keep_id).
//
//   mountPresetFile({ button, input, panel, signedIn, onSaved })
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";

const errList = (xs) => `<ul class="pr-errs">${xs.map((x) => `<li>${esc(typeof x === "string" ? x : JSON.stringify(x))}</li>`).join("")}</ul>`;

export function mountPresetFile({ button, input, panel, signedIn = false, onSaved = null }) {
  button.onclick = () => input.click();
  input.onchange = (ev) => { const f = ev.target.files && ev.target.files[0]; ev.target.value = ""; if (f) load(f); };
  const show = (html) => {
    panel.hidden = false;
    panel.innerHTML = `<button class="pr-x" type="button" aria-label="close">✕</button>${html}`;
    panel.querySelector(".pr-x").onclick = () => { panel.hidden = true; };
  };
  async function load(file) {
    let spec;
    try { spec = JSON.parse(await file.text()); }
    catch (e) { show(`<p><b>${esc(file.name)}</b> is not a JSON file: ${esc(e.message)}</p>`); return; }
    if (!spec || typeof spec !== "object" || Array.isArray(spec)) { show(`<p><b>${esc(file.name)}</b> does not hold a preset (a JSON object with <code>meta</code>, <code>entries</code>, …).</p>`); return; }
    let v;
    try { v = await api.validatePreset(spec); } catch (e) { show(`<p>Could not check <b>${esc(file.name)}</b>: ${esc(e.message)}</p>`); return; }
    const meta = spec.meta || {};
    const head = `<div class="ds-card-h">${esc(meta.title || file.name)}</div>${meta.tagline ? `<p class="muted pr-tag">${esc(meta.tagline)}</p>` : ""}`;
    if (!v.ok) {
      show(`${head}<p>This preset cannot be saved yet: fix ${v.errors.length === 1 ? "this problem" : `these ${v.errors.length} problems`} in the file and load it again.</p>${errList(v.errors)}`);
      return;
    }
    show(`${head}
      <p class="pr-meta">Schema <code>${esc(v.schema_id || "")}</code>${meta.mode === "import" ? " · import schema (for results made elsewhere)" : ""}</p>
      ${(v.warnings || []).length ? `<details class="pr-warn"><summary>${v.warnings.length} warning${v.warnings.length === 1 ? "" : "s"}</summary>${errList(v.warnings)}</details>` : ""}
      <fieldset class="pr-vis"><legend>Who can use it</legend>
        <label><input type="radio" name="pr-vis" value="private" checked> Only me</label>
        <label><input type="radio" name="pr-vis" value="public"${signedIn ? "" : " disabled"}> Everyone on this site (listed in the extract and import pickers)${signedIn ? "" : " · sign in first"}</label></fieldset>
      <p class="muted pr-note">You can change this later in the preset's editor.</p>
      <div class="pr-btns"><button class="btn btn-primary pr-save" type="button">Save as my preset</button> <span class="muted pr-status"></span></div>`);
    const save = panel.querySelector(".pr-save"), status = panel.querySelector(".pr-status");
    save.onclick = async () => {
      const vis = (panel.querySelector('input[name="pr-vis"]:checked') || {}).value || "private";
      save.disabled = true; status.textContent = "saving…";
      let r;
      try { r = await api.createPreset({ spec, visibility: vis, keep_id: true }); }
      catch (e) { save.disabled = false; status.textContent = `Could not save: ${e.message}`; return; }
      const id = r.id || r.preset_id;
      const moved = r.id_wanted && !r.id_kept
        ? `<p class="pr-note">The id <code>${esc(r.id_wanted)}</code> is already in use here, so it was saved as <code>${esc(id)}</code> and runs under the schema <code>${esc(r.schema_id || "")}</code>.</p>` : "";
      const renamed = r.title_wanted && r.title && r.title !== r.title_wanted
        ? `<p class="pr-note">You already have a preset called “${esc(r.title_wanted)}”, so this one is named <b>${esc(r.title)}</b> to tell them apart.</p>` : "";
      const use = r.mode === "import"
        ? `<a class="btn btn-primary" href="/import">Import papers with it</a>`
        : `<a class="btn btn-primary" href="/extract?preset=${encodeURIComponent(id)}">Run it on papers</a> <a class="btn btn-ghost" href="/import">Import results</a>`;
      show(`${head}<p>Saved as your preset <code>${esc(id)}</code>${vis === "public" ? ", usable by everyone on this site" : ", visible only to you"}. It appears under <i>Review as</i> on the Import page and in the extract picker.</p>${renamed}${moved}
        <div class="pr-btns">${use} <a class="btn btn-ghost" href="/preset?id=${encodeURIComponent(id)}&edit=1">Open in the editor</a></div>`);
      if (onSaved) onSaved(r);
    };
  }
}
