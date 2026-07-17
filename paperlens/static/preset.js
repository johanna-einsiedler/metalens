// Personal-preset editor: create (no ?id), edit an owned preset (?id=&lt;personal&gt;), or
// duplicate a built-in preset into a personal one (?id=&lt;file preset&gt;). A preset is a
// title + tagline + description + the extraction prompt + optional review-tab grammar
// (sub_views) + visibility. Uses the shared api client; owner-gated server-side.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";

const $ = (s) => document.querySelector(s);
const id = new URLSearchParams(location.search).get("id");
const body = $("#pe-body");
let LOADED = null;                 // the preset being edited/duplicated (or null for new)

async function init() {
  const me = await api.me();
  if (!me || !me.email) {
    body.innerHTML = '<p class="muted">Please <a href="/">sign in</a> to create presets.</p>';
    return;
  }
  if (id) {
    try { LOADED = await api.presetDetail(id); }
    catch (e) { body.innerHTML = `<p class="muted">Couldn’t load preset: ${esc(e.message)}</p>`; return; }
  }
  render();
}

// A loaded preset is editable in place only if it's one of the user's DB presets.
const isOwned = () => !!(LOADED && LOADED.source === "personal");

function render() {
  const p = LOADED || {};
  const owned = isOwned();
  const sv = p.sub_views ? JSON.stringify(p.sub_views, null, 2) : "";
  const heading = !LOADED ? "New personal preset" : owned ? "Edit preset" : "Duplicate as a personal preset";
  const dupNote = LOADED && !owned
    ? '<p class="muted" style="margin:-8px 0 14px">This is a built-in preset — saving creates your own editable copy.</p>' : "";
  body.innerHTML = `
    <h2 style="margin:0 0 4px">${esc(heading)}</h2>
    <p class="muted" style="margin:0 0 6px">A personal extraction preset — your prompt + review tabs. Pick it on the extract page; publish a dataset built with it to share it with everyone.</p>
    ${dupNote}
    <div class="wf-field"><label>Title</label><input id="pe-title" value="${esc(p.title || "")}"/></div>
    <div class="wf-field"><label>Tagline</label><input id="pe-tagline" value="${esc(p.tagline || "")}"/></div>
    <div class="wf-field"><label>Description</label><textarea id="pe-desc" rows="2">${esc(p.description || "")}</textarea></div>
    <div class="wf-field"><label>Extraction prompt</label>
      <div class="sf-help">The exact prompt sent to the model. It must ask for ONE JSON object with a core array (<code>records</code>/<code>samples</code>) and an <code>evidence</code> array whose <code>field</code> is <code>records[i].&lt;Column&gt;</code>.</div>
      <textarea id="pe-prompt" rows="16" class="sf-ta">${esc(p.prompt || "")}</textarea></div>
    <div class="wf-field"><label>Review tabs <span class="muted">(sub_views JSON — optional, advanced)</span></label>
      <div class="sf-help">Groups columns into tabs in Data review. A JSON array of <code>{"id","label","include_keys":[…]}</code>. Leave blank for one flat view.</div>
      <textarea id="pe-subviews" rows="7" class="sf-ta" placeholder='[{"id":"main","label":"Main","include_keys":["col1","col2"]}]'>${esc(sv)}</textarea></div>
    <div class="wf-field"><label>Visibility</label>
      <label class="radio"><input type="radio" name="pe-vis" value="private" ${p.visibility !== "public" ? "checked" : ""}/> Private (only you)</label>
      <label class="radio"><input type="radio" name="pe-vis" value="public" ${p.visibility === "public" ? "checked" : ""}/> Public (anyone can use)</label></div>
    <div class="wf-actions" style="justify-content:flex-start;flex-wrap:wrap">
      <button class="btn btn-primary" id="pe-save">${owned ? "Save changes" : "Create preset"}</button>
      ${owned ? '<button class="btn btn-ghost" id="pe-del">Delete</button>' : ""}
      <a class="btn btn-ghost" href="/projects">Cancel</a>
      <span id="pe-msg" class="muted" style="align-self:center"></span>
    </div>`;
  $("#pe-save").onclick = save;
  const del = $("#pe-del"); if (del) del.onclick = doDelete;
}

function parseSubViews() {
  const raw = $("#pe-subviews").value.trim();
  if (!raw) return null;
  const v = JSON.parse(raw);                          // throws → caught by save()
  if (!Array.isArray(v)) throw new Error("must be a JSON array");
  return v;
}

async function save() {
  const msg = $("#pe-msg"); msg.textContent = "";
  let sub_views;
  try { sub_views = parseSubViews(); }
  catch (e) { msg.textContent = "✗ Review tabs JSON: " + e.message; return; }
  const payload = {
    title: $("#pe-title").value.trim(),
    tagline: $("#pe-tagline").value.trim() || null,
    description: $("#pe-desc").value.trim() || null,
    prompt: $("#pe-prompt").value,
    sub_views,
    visibility: document.querySelector('input[name="pe-vis"]:checked').value,
  };
  if (!payload.title || !payload.prompt.trim()) { msg.textContent = "✗ Title and prompt are required."; return; }
  $("#pe-save").disabled = true;
  try {
    if (isOwned()) await api.updatePreset(id, payload);
    else await api.createPreset(payload);            // new, or a duplicate of a built-in
    location.href = "/projects";
  } catch (e) { msg.textContent = "✗ " + e.message; $("#pe-save").disabled = false; }
}

async function doDelete() {
  if (!confirm("Delete this preset? Datasets already built with it keep working.")) return;
  try { await api.deletePreset(id); location.href = "/projects"; }
  catch (e) { $("#pe-msg").textContent = "✗ " + e.message; }
}

init();
