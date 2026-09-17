// Dataset overview (?id=…) — recipe + computed stats + papers. Owner actions:
// add papers (reusing the recipe), review, publish/unpublish, export, delete.
import { api } from "/static/api.js";
import { esc, renderMarkdown } from "/static/grammar.js";
import { renderGrid } from "/static/gridview.js";

const $ = (s, el = document) => el.querySelector(s);
const id = new URLSearchParams(location.search).get("id");
const body = $("#ds-body");

const fmtNum = (n) => (n || 0).toLocaleString();
function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

let OV = null, OWNER = false;

async function init() {
  if (!id) { body.innerHTML = '<p class="muted">No dataset id.</p>'; return; }
  let me;
  try { [OV, me] = await Promise.all([api.datasetOverview(id), api.me()]); }
  catch (e) { body.innerHTML = `<p class="muted">Couldn’t load this dataset: ${esc(e.message)}</p>`; return; }
  OWNER = !!(me && me.email && OV.owner_user_id && OV.owner_user_id === me.id);
  render();
}

function render() {
  const s = OV.stats, cred = OV.credibility, r = OV.recipe, vis = OV.visibility;
  const range = (s.first_extracted && s.last_extracted && s.first_extracted !== s.last_extracted)
    ? `${fmtDate(s.first_extracted)} – ${fmtDate(s.last_extracted)}` : fmtDate(s.last_extracted);

  body.innerHTML = `
    <div class="ds-head">
      <div>
        <h2 style="margin:.2em 0 4px">${esc(OV.title || "Untitled dataset")}${
          OWNER ? ` <button class="ds-rename" id="ds-rename" title="rename dataset">✎</button>` : ""}</h2>
        <div class="muted ds-sub">
          <span class="pt-vis ${esc(vis)}">${esc(vis)}</span>
          · ${s.n_papers} paper${s.n_papers === 1 ? "" : "s"} · ${s.n_records} record${s.n_records === 1 ? "" : "s"}
          ${OV.cite_as ? ` · by ${esc(OV.cite_as)}` : (OV.attribution === "anonymous" ? " · published anonymously" : "")}
          ${publishState()}
        </div>
      </div>
      <span class="badge tier-${esc(cred.tier)}" title="Computed from human verification">${esc(cred.label)}</span>
    </div>

    ${OWNER ? `<div class="ds-actions">
      <a class="btn btn-primary btn-sm" href="/extract?dataset=${esc(id)}">＋ Add papers</a>
      <a class="btn btn-ghost btn-sm" href="/workspace?project=${esc(id)}">Data review</a>
      <span class="btn btn-ghost btn-sm is-disabled" aria-disabled="true" title="Coming soon">📊 Build dashboard (soon)</span>
      <button class="btn btn-ghost btn-sm" id="ds-vis" title="${vis === "public" ? "unlist the dataset" : "opens a pull request in the datasets repository; listed once merged"}">${vis === "public" ? "Make private" : "Publish"}</button>
      <button class="btn btn-ghost btn-sm" id="ds-export">Export JSON</button>
      ${OV.publish_status === "pending" && OV.git_pr_url
        ? `<a class="btn btn-ghost btn-sm" href="${esc(OV.git_pr_url)}" target="_blank" rel="noopener">🔗 Review the pull request</a>
           <button class="btn btn-ghost btn-sm" id="ds-sync" title="check the datasets repository now (runs hourly anyway)">↻ Check GitHub</button>`
        : OV.publish_status === "published" && OV.published_url ? ""
        : `<button class="btn btn-ghost btn-sm" id="ds-github">⬆ Publish to GitHub</button>`}
      ${OWNER && (OV.stats || {}).n_records > (OV.stats || {}).n_verified ? `<button class="btn btn-ghost btn-sm" id="ds-verify-all" title="mark every unverified record verified — one verification event per record, in your name">✓ Mark all verified (${(OV.stats.n_records || 0) - (OV.stats.n_verified || 0)} left)</button>` : ""}
      ${OWNER && dupGroups().length ? `<button class="btn btn-ghost btn-sm" id="ds-dedupe" title="the same paper appears more than once; keep the newest copy of each">Remove duplicate papers (${dupGroups().reduce((n, g) => n + g.length - 1, 0)})</button>` : ""}
      <button class="btn btn-ghost btn-sm" id="ds-del">Delete dataset</button>
    </div>` : ""}

    ${publishingCard()}

    <div class="ds-card">
      <div class="ds-card-h">Extraction recipe</div>
      <div class="ds-recipe">
        <div><span class="rk">Model</span> <code>${esc(r.model || "—")}</code></div>
        <div><span class="rk">Schema</span> <code>${esc(r.schema_id || "—")}</code></div>
      </div>
      ${r.prompt
        ? `<details class="ds-prompt"><summary>Prompt</summary><pre>${esc(r.prompt)}</pre></details>`
        : `<p class="muted ds-mixed">Prompt not recorded for this dataset.</p>`}
      ${s.n_schemas > 1
        ? `<p class="muted ds-mixed">⚠ Mixed schemas — records span ${s.n_schemas} different schemas.</p>` : ""}
    </div>

    <div class="ds-stats">
      ${stat(fmtNum(s.n_papers), "papers")}
      ${stat(fmtNum(s.n_records), "records extracted")}
      ${s.n_screened ? stat(fmtNum(s.n_screened), `screened${s.n_screened_confirmed ? ` · ${s.n_screened_confirmed} confirmed` : " (no records)"}`) : ""}
      ${stat(`${s.verified_pct}%`, `verified (${s.n_verified}/${s.n_records})`)}
      ${stat(fmtNum(s.total_tokens), "tokens used")}
      ${stat(fmtDate(OV.created_at), "created", true)}
      ${stat(range, "extracted", true)}
      ${stat(fmtDate(s.last_change), "last change", true)}
    </div>

    <div class="ds-card">
      <div class="ds-card-h">Activity
        <button class="btn btn-ghost btn-sm" id="ds-act" style="margin-left:auto">Show history</button></div>
      <div class="ds-activity" id="ds-actbody"></div>
    </div>

    <div class="ds-card">
      <div class="ds-card-h">Papers <span class="muted">(${OV.documents.length})</span>
        <button class="btn btn-ghost btn-sm" id="ds-grid" style="margin-left:auto">▦ Spreadsheet</button></div>
      <div class="ds-papers">${OV.documents.map(paperRow).join("") || '<p class="muted">No papers.</p>'}</div>
    </div>`;

  wirePublishing();
  const gridBtn = $("#ds-grid"); if (gridBtn) gridBtn.onclick = showSpreadsheet;
  const actBtn = $("#ds-act"); if (actBtn) actBtn.onclick = () => toggleActivity(actBtn);
  if (OWNER) wireActions();
}

// Precise timestamp for the history — a date alone can't order same-day edits.
function fmtWhen(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
    + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

const ACT_LABEL = {
  paper_added: ["＋", "added"], edited: ["✎", "edited"], verified: ["✓", "verified"],
  flagged: ["⚑", "flagged"], unverified: ["↺", "reset to unverified"],
  corrected: ["✎", "corrected"],
};

function activityRow(e) {
  const [icon, verb] = ACT_LABEL[e.kind] || ["·", esc(e.kind)];
  const what = e.kind === "paper_added"
    ? esc(e.paper || "a paper")
    : `entry ${e.entry_index} of ${esc(e.paper || "a paper")}`;
  // Show the actual before→after for value edits; that IS the "what changed".
  const changes = (e.changes || []).map((c) =>
    `<div class="act-diff"><code>${esc(c.field_path || "?")}</code> `
    + `<span class="act-from">${esc(JSON.stringify(c.from))}</span> → `
    + `<span class="act-to">${esc(JSON.stringify(c.to))}</span></div>`).join("");
  return `<div class="act-row">
    <span class="act-icon">${icon}</span>
    <div class="act-main">
      <div>${verb} ${what}${e.actor ? ` · <span class="muted">${esc(e.actor)}</span>` : ""}</div>
      ${changes}${e.notes ? `<div class="muted act-note">${esc(e.notes)}</div>` : ""}
    </div>
    <span class="muted act-when">${esc(fmtWhen(e.at))}</span>
  </div>`;
}

let ACT = null;
async function toggleActivity(btn) {
  const host = $("#ds-actbody");
  if (host.innerHTML) { host.innerHTML = ""; btn.textContent = "Show history"; return; }
  btn.disabled = true; host.innerHTML = '<p class="muted">Loading…</p>';
  try {
    ACT = ACT || await api.datasetActivity(id);
    const rows = (ACT.events || []).map(activityRow).join("");
    host.innerHTML =
      `<div class="muted act-head">Created ${esc(fmtWhen(ACT.created_at))}`
      + `${ACT.updated_at ? ` · dataset last edited ${esc(fmtWhen(ACT.updated_at))}` : ""}</div>`
      + (rows || '<p class="muted">No activity recorded yet.</p>');
    btn.textContent = "Hide history";
  } catch (ex) {
    host.innerHTML = `<p class="muted">Couldn’t load the history: ${esc(ex.message)}</p>`;
  } finally { btn.disabled = false; }
}

function stat(num, label, small) {
  return `<div class="stat"><div class="stat-num${small ? " small" : ""}">${esc(String(num))}</div>`
    + `<div class="stat-lbl">${esc(label)}</div></div>`;
}

// where the dataset stands on its way to the datasets repository (the source of truth)
function publishState() {
  if (OV.github_source) return ` · <a href="${esc(OV.published_url)}" target="_blank" rel="noopener">imported from GitHub ↗</a> (read-only)`;
  if (OV.publish_status === "published" && OV.published_url) return ` · <a href="${esc(OV.published_url)}" target="_blank" rel="noopener">published on GitHub ↗</a>`;
  if (OV.publish_status === "pending") return ` · <span title="listed in the catalogue once the pull request is merged">pending review on GitHub</span>`;
  if (OV.visibility === "public") return " · public here only (not on GitHub yet)";
  return "";
}

// ── publishing details: description, README, keywords, attribution, citation ─────────
// Owners edit them here; everyone else sees them rendered. They travel into the catalogue
// card, the JSON export (metadata) and the README of the GitHub publication.
const chips = (kws) => (kws || []).map((k) => `<span class="kw">${esc(k)}</span>`).join(" ");

function publishingCard() {
  const kws = OV.keywords || [];
  if (!OWNER) {
    if (!OV.description && !OV.readme && !kws.length && !OV.citation) return "";
    return `<div class="ds-card">
      <div class="ds-card-h">About this dataset</div>
      ${OV.description ? `<p class="ds-desc">${esc(OV.description)}</p>` : ""}
      ${kws.length ? `<div class="ds-kws">${chips(kws)}</div>` : ""}
      ${OV.readme ? `<div class="ds-readme md">${renderMarkdown(OV.readme)}</div>` : ""}
      ${OV.citation ? `<div class="ds-cite"><span class="rk">How to cite</span><blockquote id="ds-cite-text">${esc(OV.citation)}</blockquote>
        <button class="btn btn-ghost btn-sm" id="ds-cite-copy">Copy</button></div>` : ""}
    </div>`;
  }
  const named = OV.attribution !== "anonymous";
  return `<div class="ds-card" id="ds-pub">
    <div class="ds-card-h">Publishing details <span class="muted" style="font-weight:400">— shown in the catalogue, the export and the GitHub README</span></div>
    <div class="ds-form">
      <label>One-line description<input id="pd-desc" type="text" maxlength="300" value="${esc(OV.description || "")}" placeholder="What the dataset contains, in one sentence"/></label>
      <label>Keywords <span class="muted">(comma-separated)</span><input id="pd-kw" type="text" value="${esc(kws.join(", "))}" placeholder="meta-analysis, human–AI collaboration, decision tasks"/></label>
      <div class="pd-row"><span class="rk">Published as</span>
        <label class="radio"><input type="radio" name="pd-attr" value="named" ${named ? "checked" : ""}/> ${OV.owner_citation_name ? esc(OV.owner_citation_name) : 'your name <span class="muted">(set a citation name under <a href="/account">Account</a>)</span>'}</label>
        <label class="radio"><input type="radio" name="pd-attr" value="anonymous" ${named ? "" : "checked"}/> Anonymous</label>
      </div>
      <label>Suggested citation <span class="muted">(edit freely; <a href="#" id="pd-cite-reset">reset to the suggested one</a>)</span>
        <textarea id="pd-cite" rows="2">${esc(OV.citation || "")}</textarea></label>
      <label>README <span class="muted">(Markdown: what was extracted, how, caveats, how to use it)</span>
        <textarea id="pd-readme" rows="8" placeholder="## What is in here&#10;…">${esc(OV.readme || "")}</textarea></label>
      <div class="pd-row"><button class="btn btn-primary btn-sm" id="pd-save">Save details</button>
        <button class="btn btn-ghost btn-sm" id="pd-preview">Preview README</button><span class="muted" id="pd-status"></span></div>
      <div class="ds-readme md" id="pd-previewbox" hidden></div>
    </div>
  </div>`;
}

function wirePublishing() {
  const copy = $("#ds-cite-copy");
  if (copy) copy.onclick = async () => { try { await navigator.clipboard.writeText($("#ds-cite-text").textContent); copy.textContent = "Copied"; } catch { /* clipboard blocked */ } };
  const save = $("#pd-save"); if (!save) return;
  $("#pd-cite-reset").onclick = (e) => { e.preventDefault(); $("#pd-cite").value = OV.citation_suggested || ""; };
  $("#pd-preview").onclick = () => { const b = $("#pd-previewbox"); b.hidden = !b.hidden; if (!b.hidden) b.innerHTML = renderMarkdown($("#pd-readme").value || "*nothing yet*"); };
  document.querySelectorAll('input[name="pd-attr"]').forEach((r) => (r.onchange = () => {
    // the suggested citation depends on the attribution: refresh it unless hand-edited
    if (!OV.citation_custom) $("#pd-cite").value = (OV.citation_suggested || "").replace(/^(Anonymous|[^(]+)\s\(/, (r.value === "anonymous" ? "Anonymous" : (OV.owner_citation_name || "[author]")) + " (");
  }));
  save.onclick = async () => {
    save.disabled = true; $("#pd-status").textContent = "saving…";
    const cite = $("#pd-cite").value.trim();
    const body = {
      description: $("#pd-desc").value.trim(),
      keywords: $("#pd-kw").value.split(",").map((k) => k.trim()).filter(Boolean),
      attribution: document.querySelector('input[name="pd-attr"]:checked').value,
      readme: $("#pd-readme").value,
      citation: cite === (OV.citation_suggested || "") ? "" : cite,   // "" = keep the suggested one
    };
    try { await api.updateDatasetMeta(id, body); OV = await api.datasetOverview(id); render(); }
    catch (ex) { $("#pd-status").textContent = "save failed: " + ex.message; save.disabled = false; }
  };
}

function paperRow(d) {
  const name = d.filename || d.title || "(untitled)";
  const meta = d.screened
    ? `screened — no applicable records${d.doi ? " · " + esc(d.doi) : ""}`
    : `${d.n_records} record${d.n_records === 1 ? "" : "s"}`
      + (d.n_verified ? ` · ${d.n_verified} verified` : "")
      + (d.doi ? ` · ${esc(d.doi)}` : "");
  return `<div class="paper-row" data-doc="${esc(d.document_id)}">
    <div class="pr-main">
      <div class="pr-name">${esc(name)}</div>
      <div class="pr-meta muted">${meta}</div>
    </div>
    <div class="pr-actions">
      <a class="btn btn-ghost btn-sm" href="/workspace?doc=${esc(d.document_id)}">Review</a>
      ${OWNER ? `<button class="pr-del" data-doc="${esc(d.document_id)}" title="remove paper">🗑</button>` : ""}
    </div></div>`;
}

// Papers listed more than once (same DOI, else title, else filename) — mirrors the server rule.
function dupGroups() {
  const key = (d) => {
    const doi = String(d.doi || "").trim().toLowerCase().replace(/^https?:\/\/(dx\.)?doi\.org\//, "");
    if (doi) return "doi:" + doi;
    const t = String(d.title || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    if (t) return "title:" + t;
    const f = String(d.filename || "").toLowerCase().replace(/\.(pdf|json)$/, "");
    return f ? "file:" + f : null;
  };
  const groups = {};
  for (const d of OV.documents || []) { const k = key(d); if (k) (groups[k] ||= []).push(d); }
  return Object.values(groups).filter((g) => g.length > 1);
}

function wireActions() {
  const va = $("#ds-verify-all");
  if (va) va.onclick = async () => {
    const left = (OV.stats.n_records || 0) - (OV.stats.n_verified || 0);
    if (!confirm(`Mark ${left} record${left === 1 ? "" : "s"} as verified in your name? Flagged records stay flagged.`)) return;
    va.disabled = true;
    try { const r = await api.verifyAllDataset(id); OV = await api.datasetOverview(id); render(); alert(`${r.verified} record${r.verified === 1 ? "" : "s"} marked verified · ${r.credibility.label}`); }
    catch (ex) { alert("failed: " + ex.message); va.disabled = false; }
  };
  const dd = $("#ds-dedupe");
  if (dd) dd.onclick = async () => {
    const n = dupGroups().reduce((k, g) => k + g.length - 1, 0);
    if (!confirm(`Remove ${n} older duplicate cop${n === 1 ? "y" : "ies"}? The newest copy of each paper stays; the older documents and their records are deleted.`)) return;
    dd.disabled = true;
    try { const r = await api.dedupeDataset(id); OV = await api.datasetOverview(id); render(); if (!r.n_removed) alert("Nothing to remove."); }
    catch (ex) { alert("cleanup failed: " + ex.message); dd.disabled = false; }
  };
  $("#ds-vis").onclick = async (e) => {
    const next = OV.visibility === "public" ? "private" : "public";
    e.target.disabled = true;
    try {
      const r = await api.setDatasetVisibility(id, next);
      if (r && r.github_error) alert("The dataset is public here, but publishing to GitHub failed: " + r.github_error);
      OV = await api.datasetOverview(id); render();
    }
    catch (ex) { alert("update failed: " + ex.message); e.target.disabled = false; }
  };
  const rn = $("#ds-rename"); if (rn) rn.onclick = doRename;
  $("#ds-export").onclick = doExport;
  const gh = $("#ds-github"); if (gh) gh.onclick = () => publishToGithub(gh);
  const sy = $("#ds-sync"); if (sy) sy.onclick = async () => {
    sy.disabled = true;
    try { const r = await api.githubSync(); OV = await api.datasetOverview(id); render(); if (OV.publish_status !== "published") alert(`Not merged yet (repository checked: ${r.seen} dataset${r.seen === 1 ? "" : "s"} on the main branch).`); }
    catch (ex) { alert(ex.message); sy.disabled = false; }
  };
  $("#ds-del").onclick = async () => {
    if (!confirm("Delete this dataset? Its records become private again (the underlying documents are kept).")) return;
    try { await api.deleteDataset(id); location.href = "/projects"; }
    catch (ex) { alert("delete failed: " + ex.message); }
  };
  body.querySelectorAll(".pr-del").forEach((b) => (b.onclick = async () => {
    if (!confirm("Delete this paper — its records, PDF, and page images? This cannot be undone.")) return;
    try { await api.deleteDocument(b.dataset.doc); OV = await api.datasetOverview(id); render(); }
    catch (ex) { alert("delete failed: " + ex.message); }
  }));
}

// Rename the dataset. The slug stays as it was — it is the published address
// (datasets/<slug>/ in the GitHub repo), so a retitle must not move it.
async function doRename() {
  const next = prompt("Rename this dataset:", OV.title || "");
  if (next === null) return;                      // cancelled
  const title = next.trim();
  if (!title || title === OV.title) return;       // unchanged / empty → nothing to do
  try {
    const r = await api.renameDataset(id, title);
    OV.title = r.title;
    ACT = null;                 // its header carries updated_at, which just moved
    render();
  } catch (ex) { alert("rename failed: " + ex.message); }
}

// Publish the dataset to the metalens-datasets GitHub repo as a PR (owner-only).
// The endpoint enqueues when Redis is up (poll the job) or runs synchronously.
async function publishToGithub(btn) {
  btn.disabled = true; const label = btn.textContent; btn.textContent = "Publishing…";
  try {
    let res = await api.publishDataset(id);
    if (res.queued) {
      for (let i = 0; i < 60 && res.queued; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const j = await api.job(res.job_id);
        if (j.status === "complete") { res = j.result || {}; break; }
        if (j.status === "failed") throw new Error(j.error || "publish failed");
      }
    }
    if (res.pr_url) { OV.git_pr_url = res.pr_url; render(); }
    else { btn.textContent = "Published ✓"; }
  } catch (e) {
    alert("Publish failed: " + e.message);
    btn.disabled = false; btn.textContent = label;
  }
}

// Grouped by paper: every row carries its record id and the paper it came from.
// The old flat `records: [field_values]` shape dropped both, so a row could not be
// traced back to its source — unusable once a dataset spans more than a paper or two.
async function doExport() {
  try {
    const out = await api.datasetExport(id);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 2)], { type: "application/json" }));
    a.download = `${(OV.slug || "dataset")}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  } catch (e) { alert("export failed: " + e.message); }
}

// Cross-document spreadsheet of every record in the dataset. Cells don't inline the
// PDF (many documents) — a row/cell click deep-links into Data review for that record,
// where the evidence + highlight live.
async function showSpreadsheet() {
  body.innerHTML = '<p class="muted">Loading spreadsheet…</p>';
  let rows = [], sub = [];
  try {
    const rowsResp = await api.datasetRows([id]);
    rows = (rowsResp && rowsResp.rows) || [];
    const schemaId = (OV.recipe && OV.recipe.schema_id) || (rows[0] && rows[0].schema_id);
    if (schemaId) {
      try { const s = await api.schema(schemaId); sub = (s.field_defs && s.field_defs.sub_views) || []; }
      catch { /* schema optional — grid falls back to raw column order */ }
    }
  } catch (e) { body.innerHTML = `<p class="muted">Couldn’t load rows: ${esc(e.message)}</p>`; return; }

  const records = rows.map((r, i) => ({
    id: r.record_id, entry_index: i + 1, field_values: r.field_values,
    verification_status: r.verification_status, document_id: r.document_id,
  }));
  body.innerHTML = `<div class="ds-head"><h2 style="margin:0">${esc(OV.title || "Dataset")} — spreadsheet</h2>`
    + `<button class="btn btn-ghost btn-sm" id="ds-back" style="margin-left:auto">← Back to dataset</button></div>`
    + `<div id="ds-grid-host"></div>`;
  $("#ds-back").onclick = render;
  renderGrid($("#ds-grid-host"), {
    records, subViews: sub,
    onRowClick: (rec) => {
      location.href = `/workspace?doc=${encodeURIComponent(rec.document_id || "")}&rec=${encodeURIComponent(rec.id || "")}`;
    },
  });
}

init();
