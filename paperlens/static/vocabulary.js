// /vocabulary?dataset=…[&id=<vocabulary>] — harmonise a column into concepts (the optional taxonomy).
// Flow: pick a text column → a model proposes concepts (draft) → review (rename, direction, reparent,
// merge, detach, place the left-out) → commit → later "N values unresolved" → propose for those → apply.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { getKey, setKey } from "/static/keys.js";

const $ = (s) => document.querySelector(s);
const params = new URLSearchParams(location.search);
const DATASET_ID = params.get("dataset");
let VID = params.get("id"), DS = null, TABLE = null, LIST = [], V = null, DRAFT = null, VALUES = [], CFG = null, MODELS = {}, RESIDUAL = null;
const PROVIDER_LABEL = { openai: "OpenAI", google: "Google", anthropic: "Anthropic", deepseek: "DeepSeek", mistral: "Mistral" };
const byIdx = () => Object.fromEntries(VALUES.map((v) => [v.idx, v]));

async function init() {
  if (!DATASET_ID) { $("#voc-app").innerHTML = `<p class="muted">Open this page from a dataset.</p>`; return; }
  $("#voc-back").href = `/dataset?id=${encodeURIComponent(DATASET_ID)}`;
  try {
    [DS, CFG, MODELS] = await Promise.all([api.dataset(DATASET_ID), api.extractionConfig().catch(() => null), api.models().then((m) => m.providers || {}).catch(() => ({}))]);
  } catch (e) { $("#voc-app").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  $("#voc-title").textContent = `· ${DS.title || ""}`;
  if (VID) await openVocabulary(VID); else await renderStart();
}

// ── start: existing vocabularies, and a new one from a column ───────────────────────────────
async function renderStart() {
  const [list, table] = await Promise.all([api.vocabularies(DATASET_ID).catch(() => ({ vocabularies: [] })), api.analysisTable(DATASET_ID).catch(() => null)]);
  LIST = list.vocabularies || []; TABLE = table;
  const units = table ? table.units : [];
  const host = $("#voc-app");
  host.innerHTML = `<section class="ws-section"><div class="ws-head"><h3>Vocabularies of this dataset</h3></div>
      ${LIST.length ? `<table class="ds-cc"><thead><tr><th>Column</th><th>Unit</th><th>Version</th><th>Status</th><th>Concepts</th><th>Left out</th><th></th></tr></thead><tbody>${LIST.map((v) => `<tr><td><code>${esc(v.column)}</code></td><td>${esc(v.unit)}</td><td>v${v.version}</td><td>${esc(v.status)}${v.unresolved ? ` · <span class="lag">${v.unresolved} unresolved</span>` : ""}</td><td>${v.n_concepts}</td><td>${v.n_left_out}</td><td><a href="/vocabulary?dataset=${encodeURIComponent(DATASET_ID)}&id=${encodeURIComponent(v.id)}">open</a></td></tr>`).join("")}</tbody></table>` : `<p class="muted">None yet.</p>`}</section>
    ${list.owner ? `<section class="ws-section"><div class="ws-head"><h3>Build one</h3></div>
      <div class="wf-field"><label>Column</label><div class="select-wrap"><select id="voc-col">${units.map((u) => (table.columns_by_unit ? [] : [])).join("")}</select></div>
        <p class="muted" style="font-size:12.5px;margin:4px 0 0">A text column whose values are phrases the papers use — a cause, an effect, an instrument, a measure name.</p></div>
      <div id="voc-model"></div>
      <div class="wf-actions"><button type="button" class="btn btn-primary" id="voc-propose">Propose concepts</button> <span class="muted" id="voc-msg"></span></div></section>` : ""}`;
  if (!list.owner) return;
  // the text columns of every unit
  const opts = [];
  for (const u of units) {
    const t = u.id === table.unit.id ? table : await api.analysisTable(DATASET_ID, u.id).catch(() => null);
    if (!t) continue;
    for (const c of t.columns) {
      if (["system", "derived", "check", "vocabulary"].includes(c.scope) || !["string", "text", "enum"].includes(c.type) || (c.distinct || 0) < 1) continue;
      if (c.scope === "entry" && u.id !== "entries") continue;        // an entry field is offered once, on the entries unit
      opts.push({ unit: u.id, column: c.name, label: `${c.label || c.name} · ${u.label} · ${c.distinct} distinct`, distinct: c.distinct || 0, prose: c.type === "text" || (c.distinct || 0) >= 0.9 * (c.n || 1) });
    }
  }
  opts.sort((a, b) => (a.prose - b.prose) || b.distinct - a.distinct);   // phrases that recur first; free text (a "why", a statement) last
  $("#voc-col").innerHTML = opts.map((o) => `<option value="${esc(o.unit)}|${esc(o.column)}">${esc(o.label)}</option>`).join("") || `<option value="">no text column</option>`;
  const pre = [params.get("unit"), params.get("column")].join("|"); if (opts.some((o) => `${o.unit}|${o.column}` === pre)) $("#voc-col").value = pre;
  renderModelPanel($("#voc-model"));
  $("#voc-propose").onclick = async () => {
    const [unit, column] = ($("#voc-col").value || "|").split("|"); if (!column) return;
    const choice = modelChoice(); if (!choice) return;
    $("#voc-propose").disabled = true; $("#voc-msg").textContent = "Asking the model to group the phrases… 20–90 seconds.";
    try {
      const r = await api.proposeVocabulary(DATASET_ID, { unit, column, ...choice });
      if (!r.ok) { $("#voc-msg").textContent = `The proposal failed: ${r.error || "unknown error"}`; $("#voc-propose").disabled = false; return; }
      history.replaceState({}, "", `/vocabulary?dataset=${encodeURIComponent(DATASET_ID)}&id=${encodeURIComponent(r.vocabulary.id)}`);
      await openVocabulary(r.vocabulary.id);
    } catch (e) { $("#voc-msg").textContent = e.message; $("#voc-propose").disabled = false; }
  };
}

function renderModelPanel(host) {
  const bal = CFG && CFG.credits ? CFG.credits.balance || 0 : 0;
  const credits = !!(CFG && CFG.logged_in && CFG.offered && bal > 0);
  const provs = Object.keys(MODELS);
  host.innerHTML = `<div class="cmp-modes">`
    + (credits ? `<label class="cs-opt"><input type="radio" name="voc-mode" value="credits" checked/> <span>Use a credit <b>(${bal} left)</b> <span class="muted">· ${esc(CFG.model || "")} · one credit per pass</span></span></label>` : "")
    + `<label class="cs-opt"><input type="radio" name="voc-mode" value="own"${credits ? "" : " checked"}/> <span>My own API key</span></label>`
    + `<label class="cs-opt"><input type="radio" name="voc-mode" value="local"/> <span>A local / self-hosted model</span></label></div>`
    + `<div id="voc-own" class="cmp-fields"><div class="select-wrap"><select id="voc-prov">${provs.map((p) => `<option value="${esc(p)}">${esc(PROVIDER_LABEL[p] || p)}</option>`).join("")}</select></div>`
    + `<div class="select-wrap"><select id="voc-mdl"></select></div><input id="voc-key" type="password" placeholder="your API key (used once, never stored)" autocomplete="off"/></div>`
    + `<div id="voc-local" class="cmp-fields" hidden><input id="voc-url" type="url" placeholder="server URL, e.g. https://models.example.org:8000"/><input id="voc-lmdl" placeholder="model name"/></div>`
    + `<p class="muted cmp-note">The model sees the column's distinct phrases, how often each occurs and one quoted sentence per phrase — never the PDFs. It proposes; you decide.</p>`;
  const fill = () => { const p = $("#voc-prov").value; $("#voc-mdl").innerHTML = (MODELS[p] || []).map((m) => `<option value="${esc(m.value)}">${esc(m.label)}</option>`).join(""); $("#voc-key").value = getKey(p); };
  if (provs.length) { $("#voc-prov").onchange = fill; fill(); }
  $("#voc-key").oninput = () => setKey($("#voc-prov").value, $("#voc-key").value.trim());
  try { $("#voc-url").value = localStorage.getItem("metalens_local_base_url") || ""; $("#voc-lmdl").value = localStorage.getItem("metalens_local_model") || ""; } catch { /* no storage */ }
  const sync = () => { const m = $('input[name="voc-mode"]:checked').value; $("#voc-own").hidden = m !== "own"; $("#voc-local").hidden = m !== "local"; };
  document.querySelectorAll('input[name="voc-mode"]').forEach((r) => (r.onchange = sync)); sync();
}
function modelChoice() {
  const m = ($('input[name="voc-mode"]:checked') || {}).value;
  const c = m === "credits" ? { use_credits: true } : m === "local" ? { model: $("#voc-lmdl").value.trim(), base_url: $("#voc-url").value.trim(), api_key: "" } : { model: $("#voc-mdl").value, api_key: $("#voc-key").value.trim() };
  if (!c.use_credits && !c.api_key && !c.base_url) { alert("A proposal needs a model: enter your own API key or a local model server."); return null; }
  return c;
}

// ── one vocabulary: review a draft, or look at / extend a committed one ─────────────────────
async function openVocabulary(vid) {
  VID = vid; RESIDUAL = null;
  try { V = await api.vocabulary(vid); } catch (e) { $("#voc-app").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  VALUES = V.values || []; DRAFT = JSON.parse(JSON.stringify(V.draft));
  renderVocabulary();
}
function conceptOptions(selected, exclude) {
  return `<option value="">—</option>` + DRAFT.concepts.filter((c) => c.id !== exclude).map((c) => `<option value="${esc(c.id)}"${c.id === selected ? " selected" : ""}>${esc(c.id)} · ${esc(c.label)}</option>`).join("");
}
function renderVocabulary() {
  const host = $("#voc-app"), idx = byIdx(), draft = V.status === "draft", owner = V.owner;
  const covered = new Set(DRAFT.concepts.flatMap((c) => c.member_values));
  const head = `<section class="ws-section"><div class="ws-head"><h3><code>${esc(V.column)}</code> · ${esc(V.unit)} · v${V.version} · ${esc(V.status)}</h3>
      <span style="margin-left:auto;display:inline-flex;gap:6px">
        ${draft && owner ? `<button type="button" class="btn btn-ghost btn-sm" id="voc-save">Save draft</button><button type="button" class="btn btn-primary btn-sm" id="voc-commit">Commit v${V.version}</button><button type="button" class="btn btn-ghost btn-sm" id="voc-del" title="drop this draft">Delete</button>` : ""}
        <a class="btn btn-ghost btn-sm" href="/vocabulary?dataset=${encodeURIComponent(DATASET_ID)}">All vocabularies</a></span></div>
      <p class="muted" style="font-size:13px;margin:0 0 6px">${VALUES.length} distinct phrases · ${DRAFT.concepts.length} concepts · ${DRAFT.left_out.length} left out${V.model ? ` · proposed by ${esc(V.model)}` : ""}${(V.repairs || []).length ? ` · <span title="${esc(V.repairs.join("\n"))}">${V.repairs.length} repairs made to the model's answer</span>` : ""}</p>
      ${V.status === "committed" && (V.unresolved || []).length ? `<div class="ds-sync off" style="font-size:13px;margin:6px 0"><span class="lag"><b>${V.unresolved.length}</b> value${V.unresolved.length === 1 ? "" : "s"} of the live column ${V.unresolved.length === 1 ? "is" : "are"} not covered</span>${owner ? ` · <button type="button" class="btn btn-ghost btn-sm" id="voc-residual">Propose for the new values</button>` : ""}</div><div id="voc-residual-box"></div>` : ""}
      <div id="voc-residual-model" hidden></div></section>`;
  const domains = [...new Set(DRAFT.concepts.map((c) => c.id.split(".")[0]))].sort();
  const tree = `<section class="ws-section"><div class="ws-head"><h3>Concepts</h3>${draft && owner ? `<button type="button" class="btn btn-ghost btn-sm" id="voc-add" style="margin-left:auto">＋ Concept</button>` : ""}</div>
    ${domains.map((dom) => `<h4 style="margin:14px 0 4px">${esc((DRAFT.domains.find((d) => d.id === dom) || {}).label || dom)} <span class="muted" style="font-weight:400">· ${esc(dom)}</span></h4>`
      + DRAFT.concepts.filter((c) => c.id.split(".")[0] === dom).sort((a, b) => a.id.localeCompare(b.id)).map((c) => `<div class="voc-c" data-cid="${esc(c.id)}" style="margin-left:${(c.id.split(".").length - 2) * 22}px">
        <div class="voc-ch"><code>${esc(c.id)}</code>
          ${draft && owner ? `<input class="voc-label" data-cid="${esc(c.id)}" value="${esc(c.label)}"/>
            <select class="voc-dir" data-cid="${esc(c.id)}"><option${c.direction === "higher_is_more" ? " selected" : ""}>higher_is_more</option><option${c.direction === "higher_is_less" ? " selected" : ""}>higher_is_less</option><option${c.direction === "not_ordered" ? " selected" : ""}>not_ordered</option></select>
            <span class="muted" style="font-size:12px">merge into</span> <select class="voc-merge" data-cid="${esc(c.id)}">${conceptOptions("", c.id)}</select>`
            : `<b>${esc(c.label)}</b> <span class="muted">· ${esc(c.direction)}</span>`}</div>
        <div class="muted" style="font-size:12.5px">${esc(c.definition || "")}${c.more_means ? ` · more = ${esc(c.more_means)}` : ""}${(c.aliases || []).length ? ` · aliases: ${esc(c.aliases.join(", "))}` : ""}</div>
        <div class="voc-members">${c.member_values.map((m) => `<span class="kw" title="${esc((idx[m] || {}).quotes ? "" : "")}${esc(m)}">${esc((idx[m] || {}).value || m)}${(c.member_polarity || {})[m] === "higher_is_less" ? " ↓" : ""} <span class="muted">×${(idx[m] || {}).n || 0}</span>${draft && owner ? ` <a href="#" class="voc-detach" data-cid="${esc(c.id)}" data-m="${esc(m)}" title="detach: back to left out">✕</a>` : ""}</span>`).join(" ") || `<span class="muted" style="font-size:12px">no phrase of its own</span>`}</div>
        ${c.rationale ? `<div class="muted" style="font-size:12px;margin-top:2px">${esc(c.rationale)}</div>` : ""}</div>`).join("")).join("")}</section>
    <section class="ws-section"><div class="ws-head"><h3>Left out <span class="muted" style="font-weight:400">· ${DRAFT.left_out.length}</span></h3></div>
      ${DRAFT.left_out.length ? DRAFT.left_out.map((lo) => `<div class="voc-lo"><span class="kw">${esc((idx[lo.value] || {}).value || lo.value)} <span class="muted">×${(idx[lo.value] || {}).n || 0}</span></span> <span class="muted" style="font-size:12.5px">${esc(lo.reason || "")}${lo.why ? ` — ${esc(lo.why)}` : ""}</span>
        ${draft && owner ? ` <select class="voc-place" data-m="${esc(lo.value)}"><option value="">place under…</option>${conceptOptions("")}</select>` : ""}</div>`).join("") : `<p class="muted">Every phrase is placed.</p>`}</section>
    ${V.status === "committed" ? `<section class="ws-section"><div class="ws-head"><h3>Assignments</h3></div><table class="ds-cc"><thead><tr><th>Phrase</th><th>Concept</th><th>Polarity</th><th>Basis</th></tr></thead><tbody>${Object.values(V.assignments || {}).sort((a, b) => String(a.value).localeCompare(String(b.value))).map((a) => `<tr><td>${esc(a.value)}</td><td><code>${esc(a.concept_id || "—")}</code></td><td>${esc(a.polarity || "")}</td><td class="muted">${esc(a.basis || "")}</td></tr>`).join("")}</tbody></table></section>` : ""}`;
  host.innerHTML = head + tree;
  if (!(draft && owner)) { const r = $("#voc-residual"); if (r) r.onclick = residual; return; }
  host.querySelectorAll(".voc-label").forEach((el) => (el.oninput = () => { const c = DRAFT.concepts.find((x) => x.id === el.dataset.cid); if (c) c.label = el.value; }));
  host.querySelectorAll(".voc-dir").forEach((el) => (el.onchange = () => { const c = DRAFT.concepts.find((x) => x.id === el.dataset.cid); if (c) c.direction = el.value; }));
  host.querySelectorAll(".voc-merge").forEach((el) => (el.onchange = () => {
    if (!el.value) return;
    const from = DRAFT.concepts.find((x) => x.id === el.dataset.cid), into = DRAFT.concepts.find((x) => x.id === el.value);
    if (!from || !into || !confirm(`Merge “${from.label}” into “${into.label}”? Its phrases and aliases move over; the concept disappears.`)) { el.value = ""; return; }
    into.member_values = [...into.member_values, ...from.member_values]; into.member_polarity = { ...into.member_polarity, ...from.member_polarity };
    into.aliases = [...new Set([...(into.aliases || []), from.label, ...(from.aliases || [])])];
    DRAFT.concepts = DRAFT.concepts.filter((x) => x !== from && !x.id.startsWith(from.id + "."));
    renderVocabulary();
  }));
  host.querySelectorAll(".voc-detach").forEach((el) => (el.onclick = (e) => {
    e.preventDefault(); const c = DRAFT.concepts.find((x) => x.id === el.dataset.cid); if (!c) return;
    c.member_values = c.member_values.filter((m) => m !== el.dataset.m); delete c.member_polarity[el.dataset.m];
    DRAFT.left_out.push({ value: el.dataset.m, reason: "detached", why: "detached by the reviewer" }); renderVocabulary();
  }));
  host.querySelectorAll(".voc-place").forEach((el) => (el.onchange = () => {
    const c = DRAFT.concepts.find((x) => x.id === el.value); if (!c) return;
    c.member_values.push(el.dataset.m); DRAFT.left_out = DRAFT.left_out.filter((lo) => lo.value !== el.dataset.m); renderVocabulary();
  }));
  $("#voc-add").onclick = () => {
    const id = prompt("Concept id (domain.leaf or domain.leaf.sub, lowercase):"); if (!id) return;
    DRAFT.concepts.push({ id: id.trim().toLowerCase(), parent: id.trim().toLowerCase().split(".").slice(0, -1).join("."), label: id.split(".").pop().replace(/_/g, " "), definition: "", direction: "higher_is_more", more_means: "", aliases: [], member_values: [], member_polarity: {}, rationale: "added by the reviewer" });
    renderVocabulary();
  };
  $("#voc-save").onclick = save;
  $("#voc-commit").onclick = async () => {
    if (!confirm(`Commit v${V.version}? The analysis table gets ${V.column}_concept and ${V.column}_polarity columns resolved with it; the next release freezes it.`)) return;
    try { await save(); await api.commitVocabulary(VID); await openVocabulary(VID); } catch (e) { alert(e.message); }
  };
  $("#voc-del").onclick = async () => { if (!confirm("Delete this draft?")) return; try { await api.deleteVocabulary(VID); location.href = `/vocabulary?dataset=${encodeURIComponent(DATASET_ID)}`; } catch (e) { alert(e.message); } };
  async function save() { const r = await api.saveVocabulary(VID, DRAFT); V = { ...V, ...r }; DRAFT = JSON.parse(JSON.stringify(r.draft)); renderVocabulary(); }
}

// ── the residual pass on a committed vocabulary ────────────────────────────────────────────
async function residual() {
  const box = $("#voc-residual-model"); box.hidden = false;
  renderModelPanel(box);
  box.insertAdjacentHTML("beforeend", `<div class="wf-actions"><button type="button" class="btn btn-primary btn-sm" id="voc-residual-go">Ask the model</button> <span class="muted" id="voc-rmsg"></span></div>`);
  $("#voc-residual-go").onclick = async () => {
    const choice = modelChoice(); if (!choice) return;
    $("#voc-residual-go").disabled = true; $("#voc-rmsg").textContent = "Asking the model… 10–60 seconds.";
    try {
      const r = await api.proposeVocabulary(DATASET_ID, { unit: V.unit, column: V.column, vocabulary_id: VID, ...choice });
      if (!r.ok) { $("#voc-rmsg").textContent = r.error || "failed"; $("#voc-residual-go").disabled = false; return; }
      RESIDUAL = r; box.hidden = true; renderResidual();
    } catch (e) { $("#voc-rmsg").textContent = e.message; $("#voc-residual-go").disabled = false; }
  };
}
function renderResidual() {
  const host = $("#voc-residual-box"), idx = Object.fromEntries((RESIDUAL.values || []).map((v) => [v.idx, v]));
  const rows = (RESIDUAL.values || []).map((v) => {
    const d = (RESIDUAL.decisions || []).find((x) => x.value === v.idx) || { value: v.idx, action: "skip", reason: "no decision from the model", why: "" };
    return { v, d };
  });
  host.innerHTML = `<table class="ds-cc"><thead><tr><th>Phrase</th><th>Decision</th><th>Concept</th><th>Why</th></tr></thead><tbody>${rows.map(({ v, d }, k) => `<tr>
      <td>${esc(v.value)} <span class="muted">×${v.n}</span></td>
      <td><select class="voc-ract" data-k="${k}"><option value="map"${d.action === "map" ? " selected" : ""}>map</option><option value="new"${d.action === "new" ? " selected" : ""}>new: ${esc(d.concept_id || "")}</option><option value="skip"${d.action === "skip" ? " selected" : ""}>skip</option></select></td>
      <td><select class="voc-rcid" data-k="${k}">${conceptOptions(d.action === "map" ? d.concept_id : "")}</select>${d.action === "new" ? ` <span class="muted" style="font-size:12px">new ${esc(d.concept_id || "")} · ${esc(d.label_text || "")}</span>` : ""}</td>
      <td class="muted" style="font-size:12.5px">${esc(d.why || d.reason || "")}</td></tr>`).join("")}</tbody></table>
    <div class="wf-actions"><button type="button" class="btn btn-primary btn-sm" id="voc-apply">Apply as v${V.version + 1}</button> <span class="muted">A new committed version; the earlier one stays with the releases cut from it.</span></div>`;
  host.querySelectorAll(".voc-rcid").forEach((el) => (el.onchange = () => { const r = rows[+el.dataset.k]; if (el.value) { r.d.action = "map"; r.d.concept_id = el.value; host.querySelector(`.voc-ract[data-k="${el.dataset.k}"]`).value = "map"; } }));
  host.querySelectorAll(".voc-ract").forEach((el) => (el.onchange = () => { const r = rows[+el.dataset.k]; r.d.action = el.value; if (el.value === "map") r.d.concept_id = host.querySelector(`.voc-rcid[data-k="${el.dataset.k}"]`).value || r.d.concept_id; }));
  $("#voc-apply").onclick = async () => {
    try { const r = await api.extendVocabulary(VID, rows.map(({ d }) => d)); history.replaceState({}, "", `/vocabulary?dataset=${encodeURIComponent(DATASET_ID)}&id=${encodeURIComponent(r.id)}`); await openVocabulary(r.id); }
    catch (e) { alert(e.message); }
  };
}

init();
