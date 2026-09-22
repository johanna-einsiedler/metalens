// /dashboard?id=<dashboard> — a saved dashboard; /dashboard?dataset=<id> — the default dashboard
// of a dataset (no model involved: built from the column roles on the server). Either way the
// data is LIVE: only the block spec is stored, the rows are read from the dataset on every load.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { loadTable, setValueLabels, setColumnLabels } from "/static/dash/table.js";
import { applyTheme, mountStylePanel } from "/static/dash/style.js";
import { createEvidence } from "/static/dash/evidence.js";
import { renderBlock } from "/static/dash/blocks.js";

const $ = (s) => document.querySelector(s);
const params = new URLSearchParams(location.search);
let DASH = null;          // the stored dashboard (null for a dataset's default view)
let SPEC = null, DATASET_ID = params.get("dataset");
const FILTERS = { verified_only: false, exclude_flagged: false };
const TABLES = new Map(), EVIDENCE = new Map();

// where the rows come from: a dashboard reads through ITS OWN data endpoints (the pinned release
// of the public page, or the owner's live draft); the default view reads the dataset directly
let SOURCE = null;
async function loadTables(source, spec, panelHost) {
  const units = [...new Set((spec.blocks || []).map((b) => b.unit).filter(Boolean))];
  await Promise.all(units.filter((u) => !TABLES.has(u)).map(async (u) => {
    const t = await loadTable(source, u);
    TABLES.set(u, t); EVIDENCE.set(u, createEvidence(t, panelHost));
  }));
}

function renderAll() {
  const grid = $("#dash-grid"); grid.innerHTML = ""; $("#dash-panel").innerHTML = "";
  const ctx = { tables: TABLES, evidence: EVIDENCE, questions: SPEC.questions, dashFilters: FILTERS, hideEmpty: true };
  let stats = null;                                      // consecutive key numbers share one row
  const hidden = [];                                     // blocks without enough data are not shown
  for (const b of SPEC.blocks) {
    let card;
    if (b.type === "stat") {
      if (!stats) { stats = document.createElement("div"); stats.className = "dash-stats"; grid.appendChild(stats); }
      card = renderBlock(stats, b, ctx);
    } else { stats = null; card = renderBlock(grid, b, ctx); }
    if (!card) hidden.push(b.title || b.template);
  }
  // only the author is told, with the way to fix it
  $("#dash-unanswered").innerHTML = hidden.length && DASH && DASH.can_edit && DASH.view !== "published"
    ? `<p class="muted" style="font-size:12.5px;margin:0 0 12px">${hidden.length} block${hidden.length === 1 ? " is" : "s are"} not shown because the data is not enough for ${hidden.length === 1 ? "it" : "them"}: ${hidden.map(esc).join(", ")}. <a href="/dashboard?id=${encodeURIComponent(DASH.id)}&edit=1">Open the editor</a> to change or remove ${hidden.length === 1 ? "it" : "them"}.</p>` : "";
}

function renderHead() {
  const t = TABLES.values().next().value, d = t.dataset;
  const canEdit = DASH ? DASH.can_edit : t.viewer.owner;
  const title = DASH ? DASH.title : `${d.title || "Dataset"}: overview`;
  // the fixed line under the title, the same on every dashboard: how far the data is verified,
  // how many papers, when it last changed, who published, where the data and the preset live
  const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "");
  const updated = [DASH && DASH.updated_at, d.data_updated_at].filter(Boolean).sort().pop();
  const rel = d.release;                                  // set when this page shows a frozen release
  const author = DASH ? DASH.author : d.author;
  const meta = [
    `<span data-tip="Verification status: how many entries of the dataset a person has checked against the paper, and how often they agreed with the extraction." tabindex="0"><span class="badge tier-${esc(d.credibility.tier || "ai_only")}">${esc(d.credibility.label || "")}</span></span>`,
    (() => {
      const lo = d.left_out && d.left_out.rows ? d.left_out : null, all = d.n_papers + (lo ? lo.papers : 0);
      const tip = lo ? `${lo.papers ? `${lo.papers} of the ${all} papers in the dataset ${lo.papers === 1 ? "has" : "have"} no row that can be analysed, so ${lo.papers === 1 ? "it does" : "they do"} not appear here. ` : ""}A row needs ${lo.needs}; ${lo.rows} of ${lo.of} rows lack one of them. They stay in the dataset.`
        : `papers in the dataset (v${String(d.version || 1)})`;
      return `<span data-tip="${esc(tip)}" tabindex="0"><span class="tip-u"><b>${d.n_papers}</b>${lo && lo.papers ? ` of ${all}` : ""} paper${(lo && lo.papers ? all : d.n_papers) === 1 ? "" : "s"}</span></span>`;
    })(),
    rel ? `<span data-tip="This page shows release v${rel.number} of the dataset, frozen on ${esc(day(rel.created_at))} (sha256 ${esc((rel.content_sha || "").slice(0, 12))}…). It changes only when its author publishes an update." tabindex="0"><span class="tip-u">Data: <b>release v${rel.number}</b> · ${esc(day(rel.created_at))}</span></span>`
      : updated ? `<span title="data last changed ${esc(day(d.data_updated_at))}${DASH ? `; dashboard last edited ${esc(day(DASH.updated_at))}` : ""}. The figures are drawn from the live data.">${DASH && DASH.view === "draft" ? "Draft · live data, updated" : "Updated"} ${esc(day(updated))}</span>` : "",
    author ? `<span>By ${esc(author)}</span>` : "",
    d.published_url ? `<a href="${esc(d.published_url)}" target="_blank" rel="noopener" title="the published copy of the dataset">Data on GitHub ↗</a>`
      : `<span class="muted" title="the dataset has not been published to the public catalogue">Data not on GitHub yet</span>`,
  ].filter(Boolean);
  const reachable = canEdit || t.viewer.owner || d.visibility === "public";      // a published dashboard may sit over a private dataset
  $("#dash-head").innerHTML = `<div>${reachable ? `<a class="muted" href="/dataset?id=${encodeURIComponent(d.id)}">← ${esc(d.title || "Dataset")}</a>` : `<span class="muted">${esc(d.title || "Dataset")}</span>`}`
    + `<h2 style="margin:.2em 0 6px">${esc(title)}</h2>`
    + `<div class="dash-meta">${meta.map((m) => `<span class="dm">${m}</span>`).join("")}</div>`
    + `${DASH ? "" : `<div class="muted ds-sub">Default view, built from the column types (no model involved).</div>`}</div>`
    + `<div class="dash-toolbar">`
    + (DASH && canEdit && DASH.view !== "published" ? `<button type="button" class="btn btn-ghost btn-sm" id="dash-style-btn" title="look, font and colours of this dashboard">🎨 Style</button>`
      + `<a class="btn btn-ghost btn-sm" href="/dashboard?id=${encodeURIComponent(DASH.id)}&edit=1">✎ Edit</a>` : "")
    + (!DASH ? `<a class="btn btn-primary btn-sm" href="/dashboard?dataset=${encodeURIComponent(d.id)}&edit=1">＋ Build your own</a>` : "") + `</div>`;   // signed-out visitors are asked to sign in on that page
  document.title = `${title} · dashboard`;
  const styleBtn = $("#dash-style-btn");
  if (styleBtn) styleBtn.onclick = async () => {
    if ($("#dash-style").innerHTML) return;
    const registry = await api.analysisTemplates();
    mountStylePanel($("#dash-style"), { registry, theme: SPEC.theme, onSave: async (theme) => {
      const d = await api.updateDashboard(DASH.id, { rev: DASH.rev, spec: { ...SPEC, theme } });
      DASH = { ...DASH, ...d, can_edit: true }; SPEC = DASH.spec; applyTheme(SPEC.theme);
    } });
  };
  $("#dash-controls").innerHTML = `<label class="dash-ctl" data-tip="Show only values a person has checked against the paper and confirmed (or corrected). Everything else was extracted by a model and not reviewed yet; without this filter those values are drawn as hollow marks."><input type="checkbox" id="f-ver"${FILTERS.verified_only ? " checked" : ""}/> <span>verified rows only</span></label>`
    + `<label class="dash-ctl" data-tip="Leave out entries a reviewer has flagged: marked as doubtful or wrong during review (for example the value could not be found in the paper). Flagged entries stay in the dataset until someone corrects or removes them."><input type="checkbox" id="f-flag"${FILTERS.exclude_flagged ? " checked" : ""}/> <span>exclude flagged</span></label>`
    + `<span class="muted dash-hint">Hover a mark for its source; click to pin the evidence. Hollow marks are not yet verified.</span>`;
  $("#f-ver").onchange = (e) => { FILTERS.verified_only = e.target.checked; renderAll(); };
  $("#f-flag").onchange = (e) => { FILTERS.exclude_flagged = e.target.checked; renderAll(); };
  renderOwnerBar();
  const year = rel ? new Date(rel.created_at).getFullYear() : new Date().getFullYear();
  $("#dash-foot").innerHTML = (DASH && rel ? `<b>Cite this dashboard:</b> ${esc(author || "[author]")} (${year}). ${esc(title)} [Dashboard]. Metalens. Data: ${esc(d.title || "dataset")}, release v${rel.number} (sha256:${esc((rel.content_sha || "").slice(0, 8))}). ${esc(location.origin)}/dashboard?id=${esc(DASH.id)}<br/>` : "")
    + (d.citation ? `<b>Cite the data:</b> ${esc(d.citation)}<br/>` : "")
    + `Every value is extracted from the cited paper; computed values state their formula in the tooltip.`;
}

// ── the owner's bar: draft vs public page, publish, update to a newer release ──
const dayShort = (iso) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "");
function renderOwnerBar() {
  const host = $("#dash-owner"); if (!host) return;
  if (!DASH || !DASH.can_edit) { host.innerHTML = ""; return; }
  const u = DASH.update, here = `/dashboard?id=${encodeURIComponent(DASH.id)}`;
  let html;
  if (DASH.view === "published") {
    html = `<span>You are looking at the <b>public page</b> (release v${DASH.release.number}), as everyone sees it.</span><a class="btn btn-ghost btn-sm" href="${here}">Back to my draft</a>`;
  } else if (!DASH.published) {
    html = `<span><b>Private draft</b> over the live data. Only you can see it.</span><button type="button" class="btn btn-primary btn-sm" data-pub="first">Publish…</button><button type="button" class="btn btn-ghost btn-sm" data-pub="delete">Delete</button>`;
  } else {
    html = `<span><b>Your draft</b> (live data). Public page: release v${u.pinned}, published ${esc(dayShort(DASH.published_at))}.</span>`
      + `<a class="btn btn-ghost btn-sm" href="${here}&view=published">View public page</a>`
      + (u.available ? `<button type="button" class="btn btn-primary btn-sm" data-pub="update" title="move the public page to the newer release">Update to v${u.latest}</button>` : "")
      + (!u.available && u.head_changed ? `<button type="button" class="btn btn-ghost btn-sm" data-pub="current" title="the dataset changed since release v${u.latest}: create the next release and move the public page to it">Update to current data</button>` : "")
      + (DASH.draft_differs ? `<button type="button" class="btn btn-ghost btn-sm" data-pub="draft" title="make your draft edits public, over the same release">Publish draft edits</button>` : "")
      + `<button type="button" class="btn btn-ghost btn-sm" data-pub="unpublish">Unpublish</button><button type="button" class="btn btn-ghost btn-sm" data-pub="delete">Delete</button>`;
  }
  host.innerHTML = `<div class="dash-ownerbar">${html}<span class="muted" id="dash-owner-msg"></span></div><div id="dash-pubform"></div>`;
  host.querySelectorAll("[data-pub]").forEach((b) => (b.onclick = () => onPublishAction(b.dataset.pub)));
}
async function doPublish(body, done) {
  const msg = $("#dash-owner-msg"); msg.textContent = "Publishing…";
  try {
    const r = await api.publishDashboard(DASH.id, { rev: DASH.rev, ...body });
    if ((r.hidden_blocks || []).length) alert(`Published over release v${r.release}. Not shown on the public page (not enough data in that release): ${r.hidden_blocks.join(", ")}.`);
    location.href = done || `/dashboard?id=${encodeURIComponent(DASH.id)}&view=published`;
  } catch (e) { msg.textContent = /^401/.test(e.message) ? "Sign in to publish a dashboard." : `Could not publish: ${e.message}`; }
}
// Before the public page moves to other data: what changes, block by block. Nothing is
// published until the owner confirms; "also publish my draft edits" is off by default.
const STATUS_WORD = { unchanged: "unchanged", changed: "changed", attention: "needs attention", broken: "cannot be drawn" };
async function showUpdatePreview(target) {
  const host = $("#dash-pubform"); host.innerHTML = `<div class="ds-card dash-pubform"><p class="muted">Comparing the public page with the new data…</p></div>`;
  let pv = { published: null, draft: null }, source = "published";
  const load = async (src) => (pv[src] = pv[src] || await api.dashboardUpdatePreview(DASH.id, target, src));
  try { await load("published"); } catch (e) { host.innerHTML = `<div class="ds-card dash-pubform"><p>${esc(e.message)}</p></div>`; return; }
  const draw = () => {
    const p = pv[source], c = p.changes || {}, toLabel = p.to ? `release v${p.to}` : "the dataset as it is now (creates the next release)";
    const head = [c.papers_added && c.papers_added.length ? `+${c.papers_added.length} paper${c.papers_added.length === 1 ? "" : "s"} (${c.papers_added.slice(0, 4).map((x) => esc(x.study || x.title)).join(", ")}${c.papers_added.length > 4 ? ", …" : ""})` : "",
      c.papers_removed && c.papers_removed.length ? `−${c.papers_removed.length} paper${c.papers_removed.length === 1 ? "" : "s"} (${c.papers_removed.slice(0, 4).map((x) => esc(x.study || x.title)).join(", ")})` : "",
      c.records && c.records.changed ? `${c.records.changed} entr${c.records.changed === 1 ? "y" : "ies"} edited` : "",
      c.status && c.status.newly_verified ? `${c.status.newly_verified} newly verified` : "", c.preset_changed ? "preset settings changed" : "",
      (p.credibility[0] || {}).label !== (p.credibility[1] || {}).label ? `badge: ${esc((p.credibility[0] || {}).label || "")} → ${esc((p.credibility[1] || {}).label || "")}` : "badge unchanged"].filter(Boolean).join(" · ");
    const cell = (x) => (x ? `${x.rows} row${x.rows === 1 ? "" : "s"} · ${x.studies} stud${x.studies === 1 ? "y" : "ies"}` : "—");
    const broken = p.blocks.filter((b) => b.status === "broken");
    host.innerHTML = `<div class="ds-card dash-pubform"><div class="ds-card-h">Update the public page: release v${p.from} → ${esc(toLabel)}</div>`
      + `<p>${head}</p>`
      + `<table class="tp-table dash-upd"><thead><tr><th>Block</th><th>Now (v${p.from})</th><th>After</th><th></th></tr></thead><tbody>`
      + p.blocks.map((b) => `<tr class="upd-${esc(b.status)}"><td>${esc(b.title || b.template)}</td><td>${cell(b.before)}</td><td>${cell(b.after)}</td>`
        + `<td><span class="upd-chip">${esc(STATUS_WORD[b.status])}</span>${b.reasons.length ? `<div class="muted">${b.reasons.map(esc).join("<br/>")}</div>` : ""}</td></tr>`).join("") + `</tbody></table>`
      + ((p.questions.newly_unanswered || []).length ? `<p class="dash-warn">No longer answered: ${p.questions.newly_unanswered.map(esc).join("; ")}</p>` : "")
      + (p.columns_removed.length ? `<p class="dash-warn">Columns that no longer exist: ${p.columns_removed.map(esc).join(", ")}</p>` : "")
      + (DASH.draft_differs ? `<label class="dash-ctl" style="margin-top:8px"><input type="checkbox" id="upd-draft"${source === "draft" ? " checked" : ""}/> also publish my draft edits (otherwise the public page keeps its current layout)</label>` : "")
      + `<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;align-items:center"><button type="button" class="btn btn-primary btn-sm" id="upd-go">${p.to ? `Update to v${p.to}` : "Create the release and update"}</button>`
      + `<button type="button" class="btn btn-ghost btn-sm" id="upd-no">Cancel</button>`
      + (p.summary.attention || p.summary.broken ? `<a class="btn btn-ghost btn-sm" href="/dashboard?id=${encodeURIComponent(DASH.id)}&edit=1" title="fix labels, filters or bindings in your draft, then publish it with the update">Fix in the editor</a>` : "")
      + (broken.length ? `<span class="muted">${broken.length} block${broken.length === 1 ? "" : "s"} will be left off the public page.</span>` : "") + `</div></div>`;
    $("#upd-no").onclick = () => { host.innerHTML = ""; };
    const cb = $("#upd-draft");
    if (cb) cb.onchange = async () => { source = cb.checked ? "draft" : "published"; cb.disabled = true; await load(source); draw(); };
    $("#upd-go").onclick = () => {
      if (broken.length && !confirm(`${broken.length} block${broken.length === 1 ? "" : "s"} cannot be drawn from the new data and will be left off the public page. Update anyway?`)) return;
      doPublish({ release: p.to || "new", source });
    };
  };
  draw(); host.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function onPublishAction(kind) {
  if (kind === "delete") {
    if (!confirm(DASH.published ? "Delete this dashboard? Its public link stops working for everyone. The dataset and its releases are not affected." : "Delete this dashboard? The dataset is not affected.")) return;
    try { await api.deleteDashboard(DASH.id); location.href = `/dataset?id=${encodeURIComponent(DATASET_ID)}`; } catch (e) { $("#dash-owner-msg").textContent = `Could not delete: ${e.message}`; }
    return;
  }
  if (kind === "unpublish") {
    if (!confirm("Unpublish this dashboard? Its link stops working for everyone but you. Your draft stays.")) return;
    await api.unpublishDashboard(DASH.id); location.reload(); return;
  }
  if (kind === "draft") { if (confirm(`Make your draft edits public? The data stays release v${DASH.update.pinned}.`)) doPublish({ release: DASH.update.pinned, source: "draft" }); return; }
  let rel = { releases: [], head: { changed: true } };
  try { rel = await api.releases(DATASET_ID); } catch { /* not the dataset's owner: only existing releases can be used */ }
  const latest = rel.releases[0], headChanged = !rel.head || rel.head.changed;
  if (kind === "update" || kind === "current") { await showUpdatePreview(kind === "update" ? "latest" : "head"); return; }
  // first publication: say what becomes public, and over which data
  const next = latest ? latest.number + 1 : (TABLES.values().next().value.dataset.version || 1);
  const options = [latest && !headChanged ? { v: String(latest.number), t: `Release v${latest.number} (the dataset has not changed since)` } : null,
    latest && headChanged ? { v: String(latest.number), t: `Release v${latest.number} (${esc(dayShort(latest.created_at))}) — without what changed since` } : null,
    headChanged ? { v: "new", t: `The dataset as it is now — creates release v${next}` } : null].filter(Boolean);
  $("#dash-pubform").innerHTML = `<div class="ds-card dash-pubform"><div class="ds-card-h">Publish this dashboard</div>`
    + `<p>Anyone with the link will be able to read this page.</p>`
    + `<div class="dash-pubopts"${options.length < 2 ? " hidden" : ""}>${options.map((o, k) => `<label><input type="radio" name="pub-rel" value="${o.v}"${k === options.length - 1 ? " checked" : ""}/> ${o.t}</label>`).join("")}</div>`
    + `<div style="display:flex;gap:8px;margin-top:10px"><button type="button" class="btn btn-primary btn-sm" id="pub-go">Publish</button><button type="button" class="btn btn-ghost btn-sm" id="pub-no">Cancel</button></div></div>`;
  $("#pub-no").onclick = () => { $("#dash-pubform").innerHTML = ""; };
  $("#pub-go").onclick = () => { const v = document.querySelector('input[name="pub-rel"]:checked').value; doPublish({ release: v === "new" ? "new" : +v, source: "draft" }); };
}

async function init() {
  // ?edit=1: the editor takes the page over (questions, the live grid, the inspector). Its module
  // is loaded only then, so readers never download it.
  if (params.get("edit") === "1") {
    const me = await api.me().catch(() => null);
    if (!(me && (me.email || me.local_mode))) {          // building is account work: kept, updated, published
      $("#dash-head").innerHTML = `<h2 style="margin:.2em 0 8px">Sign in to build a dashboard</h2><p class="muted">Dashboards are saved to an account, so they can be updated when the dataset grows and published with a stable link. <a class="btn btn-primary btn-sm" href="/account?next=${encodeURIComponent(location.pathname + location.search)}">Sign in or create a free account</a></p>`;
      return;
    }
    const { mountEditor } = await import("/static/dash/editor.js"); await mountEditor(); return;
  }
  try {
    if (params.get("id")) {
      DASH = await api.dashboard(params.get("id"), params.get("view") || undefined);
      SPEC = DASH.spec; DATASET_ID = DASH.dataset_id;
      SOURCE = { dashboard: DASH.id, view: DASH.view === "live" ? undefined : DASH.view };
    } else if (DATASET_ID) {
      SPEC = (await api.validateDashboard(DATASET_ID, null)).spec;
    } else { $("#dash-head").innerHTML = '<p class="muted">No dashboard or dataset given.</p>'; return; }
    Object.assign(FILTERS, SPEC.filters || {});
    if (!SPEC.blocks.length) { $("#dash-head").innerHTML = '<p class="muted">This dataset has no rows to show yet.</p>'; return; }
    applyTheme(SPEC.theme);
    await loadTables(SOURCE || DATASET_ID, SPEC, $("#dash-panel"));
    setValueLabels(TABLES, SPEC.value_labels); setColumnLabels(TABLES, SPEC.column_labels);
    renderHead(); renderAll();
  } catch (e) { $("#dash-head").innerHTML = `<p class="muted">Couldn’t load this dashboard: ${esc(e.message)}</p>`; }
}
init();
