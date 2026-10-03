// /pages?dataset=…[&id=<page>] — a dataset's living pages, reviewed as CHANGES.
// A draft is never shown alone: each section carries a computed verdict against the committed
// version (kept / revised / new / removed), a word-level diff, and its sources split into the
// NEW records that triggered the change (highlighted) and carried ones (muted). A revision no
// new record explains is flagged — the model rewrote something the data didn't move.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { getKey, setKey } from "/static/keys.js";

const $ = (s) => document.querySelector(s);
const params = new URLSearchParams(location.search);
const DATASET_ID = params.get("dataset");
let PID = params.get("id"), DS = null, CFG = null, MODELS = {}, STEPS = [], PAGES = [];
const PROVIDER_LABEL = { openai: "OpenAI", google: "Google", anthropic: "Anthropic", deepseek: "DeepSeek", mistral: "Mistral" };

async function init() {
  if (!DATASET_ID) { $("#pg-app").innerHTML = `<p class="muted">Open this page from a dataset.</p>`; return; }
  $("#pg-back").href = `/dataset?id=${encodeURIComponent(DATASET_ID)}`;
  try {
    [DS, CFG, MODELS] = await Promise.all([api.dataset(DATASET_ID), api.extractionConfig().catch(() => null),
                                           api.models().then((m) => m.providers || {}).catch(() => ({}))]);
  } catch (e) { $("#pg-app").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  $("#pg-title").textContent = `· ${DS.title || ""}`;
  if (PID) await openPage(PID); else await renderStart();
}

// ── start: the pages that exist, and a new/updated one ────────────────────────────────────
async function renderStart() {
  let r; try { r = await api.pagesList(DATASET_ID); } catch (e) { $("#pg-app").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  STEPS = (r.steps || []).filter((s) => s.type === "pages");
  PAGES = r.pages || [];
  const latest = {};
  for (const p of PAGES) {
    const k = `${p.group}|${p.key}`;
    if (!latest[k] || p.version > latest[k].version) latest[k] = p;
  }
  const host = $("#pg-app");
  const rows = Object.values(latest).sort((a, b) => (a.group + a.key).localeCompare(b.group + b.key));
  host.innerHTML = `<section class="ws-section"><div class="ws-head"><h3>Pages</h3></div>
    ${rows.length ? rows.map((p) => `<div class="ds-dashrow"><a href="/pages?dataset=${encodeURIComponent(DATASET_ID)}&id=${esc(p.id)}">
        <b>${esc(p.title || p.key)}</b></a> <span class="muted">${esc(p.group)} · ${esc(p.key)} · v${p.version}</span>
        <span class="badge ${p.status === "committed" ? "tier-human_verified" : ""}">${esc(p.status)}</span></div>`).join("")
      : `<p class="muted">No pages yet.</p>`}</section>
    <section class="ws-section"><div class="ws-head"><h3>Write or update a page</h3></div>
      <div class="ds-relform">
        ${STEPS.length ? `<div class="select-wrap"><select id="pg-group">${STEPS.map((s) =>
            `<option value="${esc(s.group)}" data-keyedby="${esc(s.keyed_by)}">${esc(s.label || s.group)} (keyed by ${esc(s.keyed_by)})</option>`).join("")}</select></div>`
          : `<input id="pg-group-free" placeholder="group, e.g. design"/> <input id="pg-keyedby-free" placeholder="keyed_by column, e.g. design"/>`}
        <input id="pg-key" placeholder="key — a concept id or value, e.g. diff_in_diff" list="pg-keys" style="display:block;width:100%;margin-top:6px"/>
        <datalist id="pg-keys">${rows.map((p) => `<option value="${esc(p.key)}"></option>`).join("")}</datalist>
        <div id="pg-model" style="margin-top:8px"></div>
        <div style="display:flex;gap:8px;margin-top:8px"><button type="button" class="btn btn-primary btn-sm" id="pg-propose">Propose</button>
        <span class="muted" id="pg-msg"></span></div>
      </div>
      <p class="muted" style="font-size:12.5px">An update receives the committed page and the rows — including the ones
      added since it was written — and comes back as a draft you review change by change.</p></section>`;
  renderModelPanel($("#pg-model"));
  $("#pg-propose").onclick = async () => {
    const choice = modelChoice(); if (!choice) return;
    const sel = $("#pg-group");
    const group = sel ? sel.value : ($("#pg-group-free").value || "").trim();
    const keyed_by = sel ? sel.selectedOptions[0].dataset.keyedby : ($("#pg-keyedby-free").value || "").trim();
    const key = ($("#pg-key").value || "").trim();
    if (!group || !keyed_by || !key) { $("#pg-msg").textContent = "Group, keyed_by and key are all needed."; return; }
    $("#pg-propose").disabled = true; $("#pg-msg").textContent = "asking the model…";
    try {
      const r = await api.pagePropose(DATASET_ID, { group, key, keyed_by, ...choice });
      if (!r.ok) { $("#pg-msg").textContent = `The proposal failed: ${r.error || "unknown error"}`; $("#pg-propose").disabled = false; return; }
      history.replaceState({}, "", `/pages?dataset=${encodeURIComponent(DATASET_ID)}&id=${encodeURIComponent(r.page.id)}`);
      await openPage(r.page.id);
    } catch (e) { $("#pg-msg").textContent = e.message; $("#pg-propose").disabled = false; }
  };
}

// ── one page, as the change it proposes ───────────────────────────────────────────────────
async function openPage(pid) {
  PID = pid;
  let r; try { r = await api.pageReview(pid); } catch (e) { $("#pg-app").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  const pg = r.page, labels = r.labels || {}, draft = pg.status === "draft";
  const host = $("#pg-app");
  const chip = (id, trigger) => `<span class="pg-chip${trigger ? " trigger" : ""}" title="${esc(id)}">${esc(labels[id] || id.slice(0, 8))}</span>`;
  const verdictHelp = { kept: "unchanged from the committed page", revised: "changed — the highlighted records moved it",
                        new: "a section the committed page did not have", removed: "a committed section this draft drops" };
  host.innerHTML = `<div class="ws-head" style="align-items:center;gap:10px">
      <h3 style="margin:0">${esc(pg.title || pg.key)} <span class="muted">· ${esc(pg.group)} · ${esc(pg.key)} · v${pg.version}</span></h3>
      <span class="badge ${pg.status === "committed" ? "tier-human_verified" : ""}">${esc(pg.status)}</span>
      <span style="margin-left:auto"></span>
      ${draft ? `<button type="button" class="btn btn-primary btn-sm" id="pg-commit">Commit v${pg.version}</button>
                 <button type="button" class="btn btn-ghost btn-sm" id="pg-del">Delete draft</button>` : ""}
      <a class="btn btn-ghost btn-sm" href="/pages?dataset=${encodeURIComponent(DATASET_ID)}">All pages</a></div>
    ${(r.repairs || []).length ? `<div class="ds-sync off" style="font-size:13px;margin:8px 0">${r.repairs.map((x) => `<div>⚠ ${esc(x)}</div>`).join("")}</div>` : ""}
    ${pg.curated_md ? `<div class="pg-lede">${esc(pg.curated_md)}</div>` : ""}
    ${r.committed && draft ? `<p class="muted" style="font-size:12.5px">Reviewed against committed v${r.committed.version}: verdicts and
      word diffs are computed, and the highlighted chips are the records added since that version — the evidence that triggered each change.</p>` : ""}
    <div id="pg-secs">${(r.diff || []).map((d, i) => `
      <div class="pg-sec${d.verdict === "removed" ? " removed" : ""}" data-i="${i}">
        <span class="pg-verdict ${d.verdict}" title="${esc(verdictHelp[d.verdict] || "")}">${esc(d.verdict)}</span>
        ${d.unexplained ? `<span class="pg-verdict unexplained" title="revised or added, but no record new since the committed version is among its sources">no new evidence cited</span>` : ""}
        ${draft && d.verdict !== "removed" ? `<button type="button" class="btn btn-ghost btn-sm pg-edit" style="float:right">✎ edit</button>` : ""}
        <div class="pg-text">${d.verdict === "revised" && d.diff.length
          ? d.diff.map((x) => x.op === "delete" ? `<del>${esc(x.text)}</del>` : x.op === "insert" ? `<ins>${esc(x.text)}</ins>` : esc(x.text)).join(" ")
          : esc(d.text)}</div>
        <div class="pg-src">${(d.sources_new || []).map((x) => chip(x, true)).join("")}${(d.sources_carried || []).map((x) => chip(x, false)).join("")}</div>
      </div>`).join("")}</div>`;
  if (!draft) return;
  host.querySelectorAll(".pg-edit").forEach((b) => (b.onclick = () => {
    const box = b.closest(".pg-sec"), i = Number(box.dataset.i);
    const cur = ((r.diff || [])[i] || {}).text || "";
    box.querySelector(".pg-text").innerHTML = `<textarea>${esc(cur)}</textarea>
      <div style="margin-top:6px"><button type="button" class="btn btn-primary btn-sm pg-save">Save draft</button></div>`;
    box.querySelector(".pg-save").onclick = async () => {
      const texts = [];
      host.querySelectorAll(".pg-sec").forEach((sec) => {
        const j = Number(sec.dataset.i), d = (r.diff || [])[j];
        if (d.verdict === "removed") return;
        const ta = sec.querySelector("textarea");
        texts.push({ text: ta ? ta.value : d.text, sources: [...(d.sources_new || []), ...(d.sources_carried || [])] });
      });
      try { await api.savePage(pid, { sections: texts }); await openPage(pid); }
      catch (e) { alert("save failed: " + e.message); }
    };
  }));
  $("#pg-commit").onclick = async () => {
    try { await api.commitPage(pid); await openPage(pid); } catch (e) { alert(e.message); }
  };
  $("#pg-del").onclick = async () => {
    if (!confirm("Delete this draft? Committed versions stay.")) return;
    try { await api.deletePage(pid); location.href = `/pages?dataset=${encodeURIComponent(DATASET_ID)}`; }
    catch (e) { alert(e.message); }
  };
}

// ── model picker (the vocabulary page's, with pg- ids) ────────────────────────────────────
function renderModelPanel(host) {
  const bal = CFG && CFG.credits ? CFG.credits.balance || 0 : 0;
  const credits = !!(CFG && CFG.logged_in && CFG.offered && bal > 0);
  const provs = Object.keys(MODELS);
  host.innerHTML = `<div class="cmp-modes">`
    + (credits ? `<label class="cs-opt"><input type="radio" name="pg-mode" value="credits" checked/> <span>Use a credit <b>(${bal} left)</b> <span class="muted">· ${esc(CFG.model || "")} · one per page</span></span></label>` : "")
    + `<label class="cs-opt"><input type="radio" name="pg-mode" value="own"${credits ? "" : " checked"}/> <span>My own API key</span></label>`
    + `<label class="cs-opt"><input type="radio" name="pg-mode" value="local"/> <span>A local / self-hosted model</span></label></div>`
    + `<div id="pg-own" class="cmp-fields"><div class="select-wrap"><select id="pg-prov">${provs.map((p) => `<option value="${esc(p)}">${esc(PROVIDER_LABEL[p] || p)}</option>`).join("")}</select></div>`
    + `<div class="select-wrap"><select id="pg-mdl"></select></div><input id="pg-apikey" type="password" placeholder="your API key (used once, never stored)" autocomplete="off"/></div>`
    + `<div id="pg-local" class="cmp-fields" hidden><input id="pg-url" type="url" placeholder="server URL"/><input id="pg-lmdl" placeholder="model name"/></div>`
    + `<p class="muted cmp-note">The model sees the matching rows and the papers' extracted summaries — never the PDFs. It proposes; you review the change.</p>`;
  const fill = () => { const p = $("#pg-prov").value; $("#pg-mdl").innerHTML = (MODELS[p] || []).map((m) => `<option value="${esc(m.value)}">${esc(m.label)}</option>`).join(""); $("#pg-apikey").value = getKey(p); };
  if (provs.length) { $("#pg-prov").onchange = fill; fill(); }
  $("#pg-apikey").oninput = () => setKey($("#pg-prov").value, $("#pg-apikey").value.trim());
  try { $("#pg-url").value = localStorage.getItem("metalens_local_base_url") || ""; $("#pg-lmdl").value = localStorage.getItem("metalens_local_model") || ""; } catch { /* no storage */ }
  const sync = () => { const m = $('input[name="pg-mode"]:checked').value; $("#pg-own").hidden = m !== "own"; $("#pg-local").hidden = m !== "local"; };
  document.querySelectorAll('input[name="pg-mode"]').forEach((x) => (x.onchange = sync)); sync();
}
function modelChoice() {
  const m = ($('input[name="pg-mode"]:checked') || {}).value;
  const c = m === "credits" ? { use_credits: true } : m === "local" ? { model: $("#pg-lmdl").value.trim(), base_url: $("#pg-url").value.trim(), api_key: "" } : { model: $("#pg-mdl").value, api_key: $("#pg-apikey").value.trim() };
  if (!c.use_credits && !c.api_key && !c.base_url) { alert("A proposal needs a model: enter your own API key or a local model server."); return null; }
  return c;
}

init();
