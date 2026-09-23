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

let OV = null, OWNER = false, ANON = false, AUDIT = null, ZEN = { configured: false, sandbox: false, allowed: false };   // ZEN: may this account mint a DOI here?
let FINALIZED = new URLSearchParams(location.search).get("finalized") === "1";   // arrived from "Finalize" in the review

async function init() {
  if (!id) { body.innerHTML = '<p class="muted">No dataset id.</p>'; return; }
  let me;
  try { [OV, me] = await Promise.all([api.datasetOverview(id), api.me()]); }
  catch (e) { body.innerHTML = `<p class="muted">Couldn’t load this dataset: ${esc(e.message)}</p>`; return; }
  // an anonymous browser session can own a dataset too (Finalize without an account)
  OWNER = !!OV.viewer_is_owner || !!(me && me.email && OV.owner_user_id && OV.owner_user_id === me.id);
  ANON = !!OV.viewer_is_anonymous;
  render();
  loadAudit();
}

function render() {
  const s = OV.stats, cred = OV.credibility, r = OV.recipe, vis = OV.visibility;
  const range = (s.first_extracted && s.last_extracted && s.first_extracted !== s.last_extracted)
    ? `${fmtDate(s.first_extracted)} – ${fmtDate(s.last_extracted)}` : fmtDate(s.last_extracted);

  body.innerHTML = `
    ${OWNER && ANON ? `<div class="ds-keep">
      <div><b>Save this dataset.</b> You are not signed in, so it stays private and is kept for two hours after your last
        activity, then deleted. Create a free account to save it, or download the results and the audit report now.</div>
      <a class="btn btn-primary btn-sm" href="/account?next=${encodeURIComponent(location.pathname + "?id=" + id + "&finalized=1")}">Create a free account to save</a>
    </div>` : ""}
    ${OWNER && !ANON && FINALIZED ? `<form class="ds-keep" id="ds-saveform">
      <div style="flex:1"><b>Save this dataset.</b> It is in your workspace as a private dataset. Give it a name to find it again; you can publish it later.
        <input id="ds-savename" type="text" value="${esc(OV.title || "")}" placeholder="dataset name" required style="display:block;width:100%;margin-top:8px"/></div>
      <button class="btn btn-primary btn-sm" type="submit">Save</button>
    </form>` : ""}
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
      <button class="btn btn-ghost btn-sm" id="ds-rel-btn" hidden title="freeze the dataset as it is now; published dashboards pin a release">Create release</button>
      ${OV.publish_status === "pending" ? `<button class="btn btn-ghost btn-sm" id="ds-sync" title="check the datasets repository now (runs hourly anyway)">↻ Check GitHub</button>` : ""}
      <button class="btn btn-ghost btn-sm" id="ds-export">Export JSON</button>
      ${OWNER && (OV.stats || {}).n_records > (OV.stats || {}).n_verified ? `<button class="btn btn-ghost btn-sm" id="ds-verify-all" title="mark every unverified record verified — one verification event per record, in your name">✓ Mark all verified (${(OV.stats.n_records || 0) - (OV.stats.n_verified || 0)} left)</button>` : ""}
      ${OWNER && dupGroups().length ? `<button class="btn btn-ghost btn-sm" id="ds-dedupe" title="the same paper appears more than once; keep the newest copy of each">Remove duplicate papers (${dupGroups().reduce((n, g) => n + g.length - 1, 0)})</button>` : ""}
      <button class="btn btn-ghost btn-sm" id="ds-del">Delete dataset</button>
    </div>` : ""}

    <!-- 1 · the dataset itself: numbers, recipe, papers, history -->
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

    <!-- releases sit right under the numbers; the publishing details belong to releasing, so they show up with it -->
    <div id="ds-rel-new"></div>
    <div class="ds-card" id="ds-rel"><div class="ds-card-h">Release history
        <span class="muted" id="ds-rel-state" style="margin-left:auto;font-size:12.5px;font-weight:400"></span></div>
      <div id="ds-rel-sync" style="font-size:13px;margin:0 0 8px"></div>
      <p class="muted" style="font-size:13px;margin:0 0 8px">A release freezes the dataset as it is now: papers, values, review status and evidence. Published dashboards show one release and are updated on purpose, so adding or editing papers never changes a public page by itself.</p>
      <div id="ds-rellist" class="muted" style="font-size:13px">…</div></div>

    <div id="ds-pubwrap" hidden>${OWNER && ANON ? "" : publishingCard()}</div>

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

    <div class="ds-card">
      <div class="ds-card-h">Papers <span class="muted">(${dupGroups().length ? `${s.n_papers} paper${s.n_papers === 1 ? "" : "s"} in ${OV.documents.length} extractions` : OV.documents.length})</span>
        <button class="btn btn-ghost btn-sm" id="ds-grid" style="margin-left:auto">▦ Spreadsheet</button></div>
      ${dupGroups().length ? `<p class="dash-dupnote">The same paper is in this dataset more than once (${dupGroups().map((g) => esc(g[0].title || g[0].filename || "untitled")).slice(0, 3).join("; ")}): for instance imported from a file AND extracted from its PDF. Every copy's rows count in dashboards and releases.${OWNER ? " Use “Remove duplicate papers” above to keep the newest copy of each." : ""}</p>` : ""}
      <div class="ds-papers">${OV.documents.map(paperRow).join("") || '<p class="muted">No papers.</p>'}</div>
    </div>

    <div class="ds-card">
      <div class="ds-card-h">Activity
        <button class="btn btn-ghost btn-sm" id="ds-act" style="margin-left:auto">Show history</button></div>
      <div class="ds-activity" id="ds-actbody"></div>
    </div>

    <!-- 3 · how the extraction held up in review -->
    <div class="ds-card" id="ds-audit"><div class="ds-card-h">Audit report</div><p class="muted" style="margin:0">Comparing the model output with the reviewed data…</p></div>

    <!-- 4 · dashboards over the dataset -->
    <div class="ds-card" id="ds-dash"><div class="ds-card-h">Dashboards
        <span style="margin-left:auto;display:inline-flex;gap:6px">
          <a class="btn btn-ghost btn-sm" href="/dashboard?dataset=${esc(id)}" title="built from the column types, no model involved">Default view</a>
          ${ANON ? `<a class="btn btn-primary btn-sm" href="/account?next=${encodeURIComponent(`/dashboard?dataset=${id}&edit=1`)}" title="dashboards are kept, updated and published from an account">Sign in to build a dashboard</a>`
            : `<a class="btn btn-primary btn-sm" href="/dashboard?dataset=${esc(id)}&edit=1">＋ Build a dashboard</a>`}</span></div>
      <p class="muted" style="font-size:13px;margin:0 0 8px">Interactive figures, tables and key numbers over this dataset. Every point traces back to its paper, its verification status and the quoted evidence.</p>
      <div id="ds-dashlist" class="muted" style="font-size:13px">…</div>
      <div class="ds-card-h" style="margin-top:18px">Dashboards built elsewhere
        ${OWNER && !ANON ? `<button type="button" class="btn btn-ghost btn-sm" id="ds-ext-add" style="margin-left:auto">＋ Register a dashboard</button>` : ""}</div>
      <p class="muted" style="font-size:13px;margin:0 0 8px">Pages in their authors’ own code over a release of this dataset, hosted on their own sites (e.g. GitHub Pages). Metalens lists them and checks which release each one shows; the release files they read are in the datasets repository.</p>
      <div id="ds-ext-new"></div>
      <div id="ds-extlist" class="muted" style="font-size:13px">…</div></div>

    <!-- 5 · the other side of the same papers (only for presets with a registered check) -->
    <div class="ds-card" id="ds-crosscheck" hidden></div>`;

  if (!(OWNER && ANON)) wirePublishing();
  if (AUDIT) renderAudit();
  loadReleases();
  loadDashboards();
  loadExternal();
  loadCrosscheck();
  const sf = $("#ds-saveform");
  if (sf) sf.onsubmit = async (e) => {
    e.preventDefault();
    const title = $("#ds-savename").value.trim(); if (!title) return;
    try {
      if (title !== OV.title) OV.title = (await api.renameDataset(id, title)).title;
      FINALIZED = false;
      const u = new URL(location.href); u.searchParams.delete("finalized"); history.replaceState(null, "", u);
      ACT = null; render();
    } catch (ex) { alert("save failed: " + ex.message); }
  };
  const gridBtn = $("#ds-grid"); if (gridBtn) gridBtn.onclick = showSpreadsheet;
  const actBtn = $("#ds-act"); if (actBtn) actBtn.onclick = () => toggleActivity(actBtn);
  if (OWNER) wireActions();
}

// ── releases: what changed since the last one, and cutting the next ──────────
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
function changesInWords(c) {
  if (!c) return "";
  if (c.first) return `${plural(c.n_papers, "paper")}, ${plural(c.n_records, "entry", "entries")}`;
  const bits = [];
  if (c.papers_added.length) bits.push(`+${plural(c.papers_added.length, "paper")} (${c.papers_added.slice(0, 3).map((p) => p.study || p.title).join(", ")}${c.papers_added.length > 3 ? ", …" : ""})`);
  if (c.papers_removed.length) bits.push(`−${plural(c.papers_removed.length, "paper")} (${c.papers_removed.slice(0, 3).map((p) => p.study || p.title).join(", ")})`);
  if (c.records.changed) bits.push(`${plural(c.records.changed, "entry", "entries")} edited`);
  if (c.status.newly_verified) bits.push(`${c.status.newly_verified} newly verified`);
  if (c.status.newly_flagged) bits.push(`${c.status.newly_flagged} newly flagged`);
  if (c.preset_changed) bits.push("preset settings changed");
  return bits.join(" · ") || "review status or metadata changed";
}
function showPublishing(on) {
  const w = $("#ds-pubwrap"); if (w) w.hidden = !on;
}
async function loadReleases() {
  const host = $("#ds-rellist"); if (!host) return;
  let r; try { r = await api.releases(id); } catch { host.textContent = ""; return; }
  const list = r.releases || [];
  ZEN = r.zenodo || ZEN;
  renderSync(r.sync, list);
  const gh = OV.published_url || (OV.git_pr_url ? OV.git_pr_url.replace(/\/pull\/\d+$/, "") : "");
  const pubState = (x, k) => x.published_at ? (k === 0 && OV.publish_status === "pending" ? `<span class="badge">pull request open</span>` : `<a class="badge" href="${esc(gh ? gh + "/releases/v" + x.number : "#")}" target="_blank" rel="noopener" title="on GitHub since ${esc(fmtDate(x.published_at))}">on GitHub ↗</a>`)
    : (k === 0 && OWNER && !ANON ? `<button type="button" class="btn btn-ghost btn-sm" data-relpub="${x.number}" title="send this release to the datasets repository">⬆ Publish</button>` : `<span class="muted">not published</span>`);
  const testDoi = (x) => (x.doi || "").startsWith("10.5072/");
  const doiState = (x) => x.doi && (testDoi(x) === ZEN.sandbox || !ZEN.configured) ? ` <a class="badge" href="${esc(testDoi(x) ? x.zenodo_url || "#" : "https://doi.org/" + encodeURIComponent(x.doi))}" target="_blank" rel="noopener" title="${testDoi(x) ? "a sandbox DOI from testing: it resolves nowhere" : "the DOI of this release" + (x.zenodo_url ? " · " + esc(x.zenodo_url) : "")}">${testDoi(x) ? "test " : ""}DOI ${esc(x.doi)}</a>`
    + (OWNER && !ANON && ZEN.allowed && !testDoi(x) && (x.published_at || OV.published_url) ? ` <button type="button" class="btn btn-ghost btn-sm" data-doigh="${x.number}" title="send this DOI to the GitHub copy of the release (a small pull request), if it does not have it yet">DOI → GitHub</button>` : "")
    : (OWNER && !ANON && ZEN.allowed ? ` <button type="button" class="btn btn-ghost btn-sm" data-reldoi="${x.number}" title="mint a permanent DOI for this release on Zenodo${ZEN.sandbox ? " (sandbox: a test DOI)" : ""}">◎ DOI</button>` : "");
  host.innerHTML = list.length ? list.map((x, k) => `<div class="ds-dashrow"><b>v${x.number}</b> ${pubState(x, k)}${doiState(x)} <span class="muted">· ${esc(fmtDate(x.created_at))} · ${esc(changesInWords(x.changes))}`
    + ` · <span class="badge tier-${esc((x.credibility || {}).tier || "ai_only")}">${esc((x.credibility || {}).label || "")}</span>`
    + ` · <span title="sha256 of the release's content">${esc((x.content_sha || "").slice(0, 8))}</span>`
    + ` · <a href="#" data-relzip="${x.number}" title="this release as static files (tables, evidence, metadata): what a dashboard you write yourself reads">⬇ files</a></span>${x.notes ? `<div class="muted" style="margin:2px 0 0 0">${esc(x.notes)}</div>` : ""}</div>`).join("")
    : "No release yet. Dashboards you publish will pin one.";
  // A dataset nobody released is just a working artifact in its owner's account: the publishing
  // details (description, keywords, citation, where to publish) come with the first release.
  showPublishing(list.length > 0 || ["published", "pending"].includes(OV.publish_status) || !OWNER);
  host.querySelectorAll("[data-relpub]").forEach((b) => (b.onclick = async () => {
    const target = confirm("Publish to GitHub AND list it in the Metalens catalogue? (Cancel = GitHub only)") ? "github+metalens" : "github";
    b.disabled = true; b.textContent = "Publishing…";
    try { await api.publishRelease(id, +b.dataset.relpub, target); OV = await api.datasetOverview(id); } catch (e) { alert(`Publishing failed: ${e.message}`); }
    loadReleases();
  }));
  host.querySelectorAll("[data-doigh]").forEach((b) => (b.onclick = async () => {
    b.disabled = true; b.textContent = "Sending…";
    try { const r = await api.releaseDoi(id, +b.dataset.doigh); alert(r.github && r.github.pr_url ? `Pull request opened: ${r.github.pr_url}\nMerge it and the dashboards following this dataset pick the DOI up.` : (r.github && r.github.error) || "Nothing to send."); }
    catch (e) { alert(e.message); }
    loadReleases();
  }));
  host.querySelectorAll("[data-reldoi]").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Mint a DOI for release v${b.dataset.reldoi} on Zenodo${ZEN.sandbox ? " (sandbox: a test DOI that resolves nowhere)" : ""}? A DOI is permanent: the release's files are deposited under it and cannot be withdrawn.`)) return;
    b.disabled = true; b.textContent = "Minting…";
    try { await api.releaseDoi(id, +b.dataset.reldoi); } catch (e) { alert(`Minting the DOI failed: ${e.message}`); }
    loadReleases();
  }));
  host.querySelectorAll("[data-relzip]").forEach((a) => (a.onclick = async (e) => {
    e.preventDefault(); const was = a.textContent; a.textContent = "preparing…";
    try {
      const { blob, name } = await api.releaseExport(id, +a.dataset.relzip);
      const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = name; link.click(); URL.revokeObjectURL(link.href);
    } catch (ex) { alert("download failed: " + ex.message); }
    a.textContent = was;
  }));
  const btn = $("#ds-rel-btn"), state = $("#ds-rel-state");
  if (!OWNER || !r.head) { if (btn) btn.hidden = true; return; }
  const next = list.length ? list[0].number + 1 : (OV.version || 1);
  if (btn) { btn.hidden = !r.head.changed; btn.textContent = `Create release v${next}`; btn.classList.toggle("btn-primary", r.head.changed); btn.classList.toggle("btn-ghost", !r.head.changed); btn.onclick = openReleaseForm; }
  if (state) state.textContent = list.length ? (r.head.changed ? `the dataset changed since v${list[0].number}` : `up to date with v${list[0].number}`) : "";
}
// The three copies of the dataset must tell the same story; Zenodo is the ground truth once a DOI
// exists. Out of step → say which copy lags and offer the one action that catches it up.
function renderSync(sync, list) {
  const host = $("#ds-rel-sync"); if (!host || !sync || !list.length) { if (host) host.innerHTML = ""; return; }
  const at = (n) => (n == null ? "—" : `v${n}`);
  const cells = [[`Zenodo`, sync.zenodo == null ? "no DOI" : at(sync.zenodo), sync.behind.includes("zenodo")],
                 [`GitHub`, sync.github == null ? "not published" : at(sync.github) + (sync.pending ? " (pull request open)" : ""), sync.behind.includes("github")],
                 [`Catalogue`, sync.catalogue ? "listed" : "not listed", sync.behind.includes("catalogue")]];
  host.innerHTML = `<span class="ds-sync${sync.in_sync ? "" : " off"}">${cells.map(([k, v, lag]) => `<span${lag ? ' class="lag"' : ""}><b>${k}</b> ${esc(v)}</span>`).join(" · ")}</span>`
    + (!sync.in_sync && OWNER && !ANON ? ` <button type="button" class="btn btn-ghost btn-sm" id="ds-sync-go" title="bring every copy to release v${sync.latest}${sync.zenodo != null && sync.behind.includes("zenodo") ? ": a new version on Zenodo (new DOI), then the GitHub copy and the catalogue" : ": the GitHub copy and the catalogue"}">Sync to v${sync.latest}</button>` : "");
  const go = $("#ds-sync-go");
  if (go) go.onclick = async () => {
    go.disabled = true; go.textContent = "Syncing…";
    const problems = [];
    try {
      if (sync.zenodo != null && sync.behind.includes("zenodo")) { try { await api.releaseDoi(id, sync.latest); } catch (e) { problems.push(`Zenodo: ${e.message}`); } }
      if (sync.behind.includes("github") || sync.behind.includes("catalogue")) { try { await api.publishRelease(id, sync.latest, "github+metalens"); } catch (e) { problems.push(`GitHub: ${e.message}`); } }
      OV = await api.datasetOverview(id);
    } finally { if (problems.length) alert(problems.join("\n")); loadReleases(); }
  };
}
// (a) the dataset has a DOI → publish as a new version everywhere (Zenodo, then GitHub, then the
// catalogue) or keep the release here; (b) no DOI yet → publish with a DOI, without, or keep it here.
function publishChoices(p) {
  const hasDoi = p.has_doi, published = OV.publish_status === "published" || OV.publish_status === "pending";
  const opt = (v, label, note, checked) => `<label><input type="radio" name="ds-relpub" value="${v}"${checked ? " checked" : ""}/> ${label}${note ? ` <span class="muted">· ${note}</span>` : ""}</label>`;
  let rows;
  if (hasDoi) {
    rows = [opt("doi", `Publish v${p.next_number} as a new version everywhere`, `new DOI on Zenodo (same concept DOI), the GitHub copy, the Metalens catalogue`, true),
            opt("none", "Keep it in Metalens only", "not published; Zenodo and GitHub stay at their release until you sync")];
  } else {
    rows = [ZEN.allowed ? opt("doi", "Publish with a DOI", `Zenodo${ZEN.sandbox ? " (sandbox: a test DOI)" : ""} first, then GitHub and the Metalens catalogue · a DOI is permanent`, false) : "",
            opt("github+metalens", "Publish without a DOI", "GitHub and the Metalens catalogue", published),
            opt("none", "Keep it in Metalens only", "you can publish it later from the release history", !published)];
  }
  return `<div class="ds-relpub"><b>Publish it?</b> <span class="muted">Only releases are published. ${hasDoi ? "This dataset has a DOI, so Zenodo is the reference copy: a published release is a new version there, and GitHub and the catalogue follow." : "The release's files go to the datasets repository on GitHub as a pull request; the dataset is listed in the catalogue once it is merged."}</span>${rows.join("")}</div>`;
}
async function openReleaseForm() {
  const host = $("#ds-rel-new"); host.innerHTML = '<p class="muted" style="font-size:13px">checking what changed…</p>';
  host.scrollIntoView({ behavior: "smooth", block: "center" });
  let p; try { p = await api.pendingRelease(id); } catch (e) { host.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  const w = p.warnings, warn = [];
  if (w.empty) warn.push("The dataset has no entries yet.");
  if (w.unverified) warn.push(`${plural(w.unverified, "entry is", "entries are")} not yet verified${w.unverified_in_new_papers ? ` (${w.unverified_in_new_papers} of them in papers added since v${p.latest.number})` : ""}: they enter the release as unverified.`);
  if (w.flagged) warn.push(`${plural(w.flagged, "entry is", "entries are")} flagged.`);
  if (w.crosscheck_mismatch) warn.push(`${plural(w.crosscheck_mismatch, "point estimate disagrees", "point estimates disagree")} with the companion dataset's table cells: the release freezes that verdict.`);
  if (w.companion_moved) warn.push("The companion dataset changed since the last release; the cross-check is redone against its latest release.");
  host.innerHTML = `<div class="ds-relform"><b>Release v${p.next_number}</b> <span class="muted">· ${esc(changesInWords(p.changes))}</span>`
    + `<div style="margin:6px 0">Badge that will be frozen with it: <span class="badge tier-${esc(p.credibility.tier || "ai_only")}">${esc(p.credibility.label || "")}</span></div>`
    + (warn.length ? `<ul class="ds-relwarn">${warn.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "")
    + `<textarea id="ds-rel-notes" rows="2" maxlength="4000" placeholder="Release notes (optional): what is new in this release?"></textarea>`
    + (ANON ? "" : publishChoices(p))
    + `<div id="ds-rel-dash"></div>`
    + `<div style="display:flex;gap:8px;margin-top:8px"><button type="button" class="btn btn-primary btn-sm" id="ds-rel-go">Create release v${p.next_number}</button>`
    + `<button type="button" class="btn btn-ghost btn-sm" id="ds-rel-no">Cancel</button><span class="muted" id="ds-rel-msg" style="font-size:12.5px"></span></div></div>`;
  showPublishing(true);                                     // releasing is when these details matter
  $("#ds-rel-no").onclick = () => { host.innerHTML = ""; loadReleases(); };
  // Published dashboards of this dataset stay on their release until they are updated: ask now.
  // Each one says what the new data would do to it; one that would break is left unticked.
  let linked = [];
  try { linked = ((await api.dashboards(id)).dashboards || []).filter((d) => d.mine && d.published); } catch { /* none */ }
  if (linked.length) {
    const previews = await Promise.all(linked.map((d) => api.dashboardUpdatePreview(d.id, "head", "published").catch(() => null)));
    $("#ds-rel-dash").innerHTML = `<div class="ds-reldash"><b>Update ${linked.length === 1 ? "the published dashboard" : "published dashboards"} to this release?</b>`
      + linked.map((d, k) => {
        const sm = previews[k] && previews[k].summary, risky = !sm || sm.broken || sm.attention;
        const what = !sm ? "could not be checked" : [sm.changed ? `${plural(sm.changed, "block")} change` : "", sm.unchanged ? `${sm.unchanged} unchanged` : "",
          sm.attention ? `${sm.attention} need${sm.attention === 1 ? "s" : ""} attention` : "", sm.broken ? `${sm.broken} cannot be drawn any more` : ""].filter(Boolean).join(", ");
        return `<label><input type="checkbox" class="ds-rel-upd" value="${esc(d.id)}"${risky ? "" : " checked"}/> <span><a href="/dashboard?id=${encodeURIComponent(d.id)}" target="_blank" rel="noopener">${esc(d.title || "Untitled dashboard")}</a>`
          + ` <span class="muted">· now on v${d.release} · ${esc(what)}${risky ? " — better reviewed on the dashboard (“Update to v" + p.next_number + "” shows the details)" : ""}</span></span></label>`;
      }).join("") + `<div class="muted">Unticked dashboards keep showing v${linked[0].release}; you can update them later from the dashboard.</div></div>`;
  }
  $("#ds-rel-go").onclick = async () => {
    $("#ds-rel-go").disabled = true;
    const msg = $("#ds-rel-msg"), chosen = [...document.querySelectorAll(".ds-rel-upd:checked")].map((x) => x.value);
    try {
      const choice = (document.querySelector('input[name="ds-relpub"]:checked') || {}).value || "none";
      msg.textContent = choice === "none" ? "Creating the release…" : choice === "doi" ? "Creating the release, minting its DOI, publishing…" : "Creating the release and publishing…";
      const rel = await api.createRelease(id, { notes: $("#ds-rel-notes").value, doi: choice === "doi", publish: choice === "none" ? null : choice === "doi" ? "github+metalens" : choice });
      if ((rel.problems || []).length) alert(`Release v${rel.number} was created, but:\n${rel.problems.join("\n")}`);
      if (choice !== "none") OV = await api.datasetOverview(id);
      const done = [];
      for (const did of chosen) {
        msg.textContent = `Release v${rel.number} created. Updating dashboards…`;
        try { const d = await api.dashboard(did); await api.publishDashboard(did, { rev: d.rev, release: rel.number, source: "published" }); done.push(did); }
        catch (e) { alert(`The release was created, but a dashboard could not be updated: ${e.message}`); }
      }
      if (done.length === 1) { location.href = `/dashboard?id=${encodeURIComponent(done[0])}&view=published`; return; }   // go and look at it
      host.innerHTML = ""; await loadReleases(); loadDashboards();
    } catch (e) { msg.textContent = e.message; $("#ds-rel-go").disabled = false; }
  };
}

// ── cross-check against a companion dataset (claims ↔ tables): one verdict per row ─────────────
// Shown only for presets with a registered check; the companion is chosen under "Advanced".
async function loadCrosscheck() {
  const host = $("#ds-crosscheck"); if (!host) return;
  let r; try { r = await api.crosscheck(id); } catch { host.hidden = true; return; }
  if (!r.applies) { host.hidden = true; return; }
  host.hidden = false;
  const STATUS = { exact: "exact", mismatch: "mismatch", unlinked: "unlinked", no_estimate: "no estimate", figure: "figure" };
  const sm = r.summary || {};
  const order = { mismatch: 0, unlinked: 1, no_estimate: 2, exact: 3, figure: 4 };
  const rows = (r.rows || []).slice().sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9)).slice(0, 80);
  host.innerHTML = `<div class="ds-card-h">Cross-check${r.companion ? ` against <a href="/dataset?id=${esc(r.companion.id)}" style="margin-left:4px">${esc(r.companion.title || "companion dataset")}</a>` : ""}
      ${r.frozen ? `<span class="badge" title="as frozen in this release">release</span>` : ""}</div>
    <p class="muted" style="font-size:13px;margin:0 0 8px">${r.companion
      ? `Every table result a claim cites is looked up in the companion’s regression columns (transcribed cell by cell, independently) and the printed coefficient must agree.${r.companion.release != null ? ` Checked against its release v${r.companion.release}.` : " The companion has no release yet: checked against its live data."}`
      : `Claims can be checked row by row against a <b>${esc(r.companion_preset || "companion")}</b> dataset of the same papers: each cited table result must reappear as a transcribed coefficient cell. No companion is set${OWNER && !ANON ? " — choose one under Advanced." : "."}`}</p>
    ${r.companion && r.summary ? `<div class="ds-sync${sm.mismatch ? " off" : ""}" style="font-size:13px;margin:0 0 8px"><b>${sm.exact || 0}</b> exact · <span${sm.mismatch ? ' class="lag"' : ""}><b>${sm.mismatch || 0}</b> mismatch</span> · <b>${sm.unlinked || 0}</b> unlinked · <b>${sm.no_estimate || 0}</b> without estimate · <b>${sm.figure || 0}</b> figures</div>
      ${rows.length ? `<table class="ds-cc"><thead><tr><th>Claim</th><th>Result</th><th>Exhibit</th><th>Estimate</th><th>Cells</th><th>Status</th></tr></thead><tbody>${rows.map((x) => `<tr class="cc-${esc(x.status)}"><td>${esc(x.claim_id || "")}</td><td>${esc(x.result_id || "")}</td><td>${esc([x.source_table, x.panel, x.column].filter(Boolean).join(" "))}</td><td>${x.point_estimate == null ? "—" : esc(String(x.point_estimate))}</td><td class="muted">${esc((x.table_coefficients || []).map(String).join(", "))}</td><td title="${esc(x.detail || "")}">${esc(STATUS[x.status] || x.status)}</td></tr>`).join("")}</tbody></table>${(r.rows || []).length > rows.length ? `<p class="muted" style="font-size:12px">first ${rows.length} of ${r.rows.length} rows; every row is in the analysis table as <code>_crosscheck</code></p>` : ""}` : ""}` : ""}
    ${OWNER && !ANON ? `<details class="ds-adv" style="margin-top:10px"><summary class="muted" style="font-size:13px;cursor:pointer">Advanced</summary>
      <div style="margin:8px 0 0;font-size:13px">Companion dataset (a <b>${esc(r.companion_preset || "")}</b> dataset of yours over the same papers): <select id="ds-companion"><option value="">— none —</option></select> <button type="button" class="btn btn-ghost btn-sm" id="ds-companion-set">Set</button> <span class="muted" id="ds-companion-msg"></span></div></details>` : ""}`;
  const sel = $("#ds-companion");
  if (sel) {
    try {
      const mine = ((await api.myDatasets()).datasets || []).filter((d) => d.id !== id && String(d.schema_id || "").startsWith(`${r.companion_preset}@`));
      sel.innerHTML = `<option value="">— none —</option>` + mine.map((d) => `<option value="${esc(d.id)}"${r.companion && r.companion.id === d.id ? " selected" : ""}>${esc(d.title || d.slug || d.id)}</option>`).join("");
    } catch { /* keep the empty select */ }
    $("#ds-companion-set").onclick = async () => {
      const msg = $("#ds-companion-msg"); msg.textContent = "saving…";
      try { await api.setCompanion(id, sel.value); msg.textContent = ""; loadCrosscheck(); loadReleases(); } catch (e) { msg.textContent = e.message; }
    };
  }
}

// ── dashboards built elsewhere: URL, the release they show, last check ─────────────────────────
async function loadExternal() {
  const host = $("#ds-extlist"); if (!host) return;
  let r; try { r = await api.externalDashboards(id); } catch { host.textContent = ""; return; }
  const list = r.dashboards || [], latest = r.latest_release;
  const state = (x) => x.release_shown == null ? `<span class="badge" title="${esc(x.check_note || "")}">release unknown</span>`
    : latest && x.release_shown < latest ? `<span class="badge tier-sample_verified" title="the page shows an older release than the latest">shows v${x.release_shown} · v${latest} available</span>`
    : `<span class="badge tier-human_verified">shows v${x.release_shown}${latest ? " · current" : ""}</span>`;
  host.innerHTML = list.length ? list.map((x) => `<div class="ds-dashrow"><a href="${esc(x.url)}" target="_blank" rel="noopener"><b>${esc(x.title)}</b> ↗</a> ${state(x)}`
    + ` <span class="muted">${x.repo_url ? `· <a href="${esc(x.repo_url)}" target="_blank" rel="noopener">source</a> ` : ""}· ${x.checked_at ? `checked ${esc(fmtDate(x.checked_at))}` : "not checked yet"}${x.check_note ? ` · ${esc(x.check_note)}` : ""}</span>`
    + (OWNER && !ANON ? ` <a class="muted" href="#" data-extcheck="${esc(x.id)}">check now</a> · <a class="muted" href="#" data-extdel="${esc(x.id)}">remove</a>` : "") + `</div>`).join("")
    : "None registered yet.";
  host.querySelectorAll("[data-extcheck]").forEach((a) => (a.onclick = async (e) => { e.preventDefault(); a.textContent = "checking…"; try { await api.checkExternalDashboard(a.dataset.extcheck); } catch (ex) { alert(ex.message); } loadExternal(); }));
  host.querySelectorAll("[data-extdel]").forEach((a) => (a.onclick = async (e) => {
    e.preventDefault(); if (!confirm("Remove this dashboard from the list? The page itself is not affected.")) return;
    try { await api.deleteExternalDashboard(a.dataset.extdel); } catch (ex) { alert(ex.message); } loadExternal();
  }));
  const add = $("#ds-ext-add");
  if (add) add.onclick = () => {
    const f = $("#ds-ext-new");
    f.innerHTML = `<form class="ds-relform" id="ds-ext-form"><b>Register a dashboard</b>
      <input id="ds-ext-title" type="text" required maxlength="200" placeholder="title, e.g. Humans & GenAI in Decision Tasks"/>
      <input id="ds-ext-url" type="url" required placeholder="page URL, e.g. https://name.github.io/dashboard/"/>
      <input id="ds-ext-repo" type="url" placeholder="source repository (optional)"/>
      <p class="muted" style="margin:6px 0 0">To be checked, the page publishes <code>metalens.json</code> next to its index (a dev-kit page does) naming the release it shows.</p>
      <div style="display:flex;gap:8px;margin-top:8px"><button type="submit" class="btn btn-primary btn-sm">Register</button><button type="button" class="btn btn-ghost btn-sm" id="ds-ext-no">Cancel</button><span class="muted" id="ds-ext-msg"></span></div></form>`;
    f.querySelectorAll("input").forEach((i) => { i.style.cssText = "display:block;width:100%;margin-top:6px"; });
    $("#ds-ext-no").onclick = () => { f.innerHTML = ""; };
    $("#ds-ext-form").onsubmit = async (e) => {
      e.preventDefault();
      try { await api.addExternalDashboard(id, { title: $("#ds-ext-title").value, url: $("#ds-ext-url").value, repo_url: $("#ds-ext-repo").value || null }); f.innerHTML = ""; loadExternal(); }
      catch (ex) { $("#ds-ext-msg").textContent = ex.message; }
    };
  };
}

async function loadDashboards() {
  const host = $("#ds-dashlist"); if (!host) return;
  let list = [];
  try { list = (await api.dashboards(id)).dashboards || []; } catch { host.textContent = ""; return; }
  host.innerHTML = list.length ? list.map((d) => `<div class="ds-dashrow"><a href="/dashboard?id=${encodeURIComponent(d.id)}"><b>${esc(d.title || "Untitled dashboard")}</b></a>`
    + ` <span class="muted">· ${d.n_blocks} block${d.n_blocks === 1 ? "" : "s"} · ${d.published ? `published over release v${d.release}` : "private draft"}${d.mine ? "" : " · by another user"} · updated ${esc(fmtDate(d.updated_at))}</span>`
    + (d.update_available ? ` <a class="badge tier-sample_verified" href="/dashboard?id=${encodeURIComponent(d.id)}" title="a newer release of this dataset exists; open the dashboard to update its public page">update to v${d.update_available} available</a>` : "")
    + (d.mine ? ` <a class="muted" href="/dashboard?id=${encodeURIComponent(d.id)}&edit=1">edit</a> · <a class="muted" href="#" data-deldash="${esc(d.id)}">delete</a>` : "") + `</div>`).join("")
    : "No saved dashboards yet.";
  host.querySelectorAll("[data-deldash]").forEach((a) => (a.onclick = async (e) => {
    e.preventDefault();
    if (!confirm("Delete this dashboard? The dataset is not affected.")) return;
    try { await api.deleteDashboard(a.dataset.deldash); loadDashboards(); } catch (ex) { alert("delete failed: " + ex.message); }
  }));
}

// ── audit report: an APA-style table of what the model extracted and what the review changed ──
async function loadAudit() {
  try { AUDIT = await api.datasetAudit(id); } catch { AUDIT = { error: true }; }
  renderAudit();
}
const fmtIdx = (v) => (v == null ? "—" : v >= 1 ? "1.00" : v.toFixed(2).replace(/^0/, ""));   // APA: no leading zero
const AUDIT_COLS = [["extracted", "Extracted"], ["reviewed", "Reviewed"], ["unchanged", "Unchanged"], ["changed", "Changed"],
                    ["removed", "Removed"], ["added", "Added"]];
function auditNote(a) {
  const parts = [
    `Extracted = non-null values in the model output (${a.n_papers - a.n_papers_without_original} paper${a.n_papers - a.n_papers_without_original === 1 ? "" : "s"}, ${a.n_entries} entr${a.n_entries === 1 ? "y" : "ies"}).`,
    `Reviewed = extracted values in entries a human verified or removed (${a.n_entries_reviewed} of ${a.n_entries} entries).`,
    "Changed = value replaced by the reviewer, counted as a false positive and a false negative; Removed = value, row or entry deleted (false positive); Added = value supplied by the reviewer (false negative).",
    "SEN = sensitivity, TP / (TP + FN); PRE = precision, TP / (TP + FP); JAC = Jaccard index, TP / (TP + FN + FP), where TP = Unchanged and the human-reviewed data are the reference. A dash marks an index that is not defined.",
  ];
  if (a.edits_in_unreviewed_entries) parts.push(`${a.edits_in_unreviewed_entries} further edit${a.edits_in_unreviewed_entries === 1 ? " lies" : "s lie"} in entries not yet verified and ${a.edits_in_unreviewed_entries === 1 ? "is" : "are"} not included.`);
  if (a.n_papers_without_original) parts.push(`${a.n_papers_without_original} paper${a.n_papers_without_original === 1 ? "" : "s"} without a stored model response ${a.n_papers_without_original === 1 ? "is" : "are"} not included.`);
  if (a.model) parts.push(`Extraction model: ${a.model}.`);
  parts.push(`Generated ${String(a.generated_at || "").slice(0, 10)}.`);
  return parts.join(" ");
}
// On the page: the platform's own table style. The APA layout is for the downloads.
function auditPageHtml(a) {
  let group = null;
  const idx = (v) => `<td class="au-idx">${fmtIdx(v)}</td>`;
  const rows = a.rows.map((r) => {
    const head = (r.group && r.group !== group) ? `<tr class="au-group"><td colspan="10">${esc(r.group)}</td></tr>` : "";
    group = r.group || group;
    return head + `<tr><td class="au-var">${esc(r.label)}</td>`
      + AUDIT_COLS.map(([k]) => `<td class="${(k === "changed" || k === "removed" || k === "added") && r[k] ? "au-hit" : ""}">${r[k]}</td>`).join("")
      + idx(r.sen) + idx(r.pre) + idx(r.jac) + `</tr>`;
  }).join("");
  const t = a.total;
  return `<table class="au-table"><thead><tr><th class="au-var">Variable</th>`
    + AUDIT_COLS.map(([k, l]) => `<th title="${esc(AUDIT_HELP[k])}">${l}</th>`).join("")
    + `<th title="sensitivity: TP / (TP + FN)">SEN</th><th title="precision: TP / (TP + FP)">PRE</th><th title="Jaccard: TP / (TP + FN + FP)">JAC</th></tr></thead>`
    + `<tbody>${rows}<tr class="au-total"><td class="au-var">Total</td>` + AUDIT_COLS.map(([k]) => `<td>${t[k]}</td>`).join("")
    + idx(t.sen) + idx(t.pre) + idx(t.jac) + `</tr></tbody></table>`;
}
const AUDIT_HELP = {
  extracted: "non-null values in the model output", reviewed: "extracted values in entries a human verified or removed",
  unchanged: "reviewed values that stand as extracted (true positives)", changed: "values the reviewer replaced (false positive and false negative)",
  removed: "values, rows or entries the reviewer deleted (false positives)", added: "values the reviewer supplied (false negatives)",
};
// LaTeX (booktabs, APA layout): \usepackage{booktabs} and, for the note, threeparttable.
function auditTex(a) {
  const TEX = { "\\": "\\textbackslash{}", "&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#", "_": "\\_", "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}" };
  const tx = (s) => String(s == null ? "" : s).replace(/[\\&%$#_{}~^]/g, (ch) => TEX[ch]);   // one pass, so inserted braces are not escaped again
  const line = (label, r, indent) => `${indent ? "\\hspace{1em}" : ""}${tx(label)} & ` + AUDIT_COLS.map(([k]) => r[k]).join(" & ")
    + ` & ${[r.sen, r.pre, r.jac].map((v) => fmtIdx(v).replace("—", "---")).join(" & ")} \\\\`;
  let group = null; const body = [];
  a.rows.forEach((r) => {
    if (r.group && r.group !== group) { body.push(`\\textit{${tx(r.group)}} \\\\`); group = r.group; }
    body.push(line(r.label, r, !!r.group));
  });
  return [
    "% requires \\usepackage{booktabs,threeparttable}",
    "\\begin{table}[htbp]", "\\begin{threeparttable}",
    `\\caption{${tx(AUDIT_TITLE)}}`, "\\label{tab:extraction-audit}",
    "\\begin{tabular}{l" + "r".repeat(AUDIT_COLS.length + 3) + "}", "\\toprule",
    "Variable & " + AUDIT_COLS.map(([, l]) => l).join(" & ") + " & SEN & PRE & JAC \\\\", "\\midrule",
    ...body, "\\midrule", line("Total", a.total, false), "\\bottomrule", "\\end{tabular}",
    "\\begin{tablenotes}[flushleft]\\small", `\\item \\textit{Note.} ${tx(auditNote(a)).replace(/—/g, "---")}`, "\\end{tablenotes}",
    "\\end{threeparttable}", "\\end{table}", "",
  ].join("\n");
}
function auditTableHtml(a) {
  let group = null;
  const bodyRows = a.rows.map((r) => {
    const head = (r.group && r.group !== group) ? `<tr class="apa-group"><td colspan="10"><i>${esc(r.group)}</i></td></tr>` : "";
    group = r.group || group;
    return head + `<tr><td class="apa-var${r.group ? " apa-indent" : ""}">${esc(r.label)}</td>`
      + AUDIT_COLS.map(([k]) => `<td>${r[k]}</td>`).join("")
      + `<td>${fmtIdx(r.sen)}</td><td>${fmtIdx(r.pre)}</td><td>${fmtIdx(r.jac)}</td></tr>`;
  }).join("");
  const t = a.total;
  return `<table class="apa"><thead><tr><th class="apa-var">Variable</th>`
    + AUDIT_COLS.map(([, l]) => `<th>${l}</th>`).join("") + `<th>SEN</th><th>PRE</th><th>JAC</th></tr></thead>`
    + `<tbody>${bodyRows}<tr class="apa-total"><td class="apa-var">Total</td>`
    + AUDIT_COLS.map(([k]) => `<td>${t[k]}</td>`).join("")
    + `<td>${fmtIdx(t.sen)}</td><td>${fmtIdx(t.pre)}</td><td>${fmtIdx(t.jac)}</td></tr></tbody></table>`;
}
const AUDIT_TITLE = "Audit of the AI-Assisted Extraction: Extracted Values, Human Revisions, and Agreement With the Reviewed Data";
function renderAudit() {
  const box = $("#ds-audit"); if (!box || !AUDIT) return;
  const head = `<div class="ds-card-h">Audit report`;
  if (AUDIT.error) { box.innerHTML = `${head}</div><p class="muted" style="margin:0">The audit report could not be computed.</p>`; return; }
  if (!AUDIT.rows.length) {
    box.innerHTML = `${head}</div><p class="muted" style="margin:0">${AUDIT.n_papers_without_original
      ? "No stored model response for these papers, so there is nothing to compare the reviewed data with."
      : "Nothing extracted yet."}</p>`;
    return;
  }
  box.innerHTML = `${head}
      <span style="margin-left:auto;display:inline-flex;gap:6px">
        <span class="muted" style="font-weight:400;font-size:12.5px;align-self:center">APA-style table:</span>
        <button class="btn btn-ghost btn-sm" id="au-csv">⬇ CSV</button>
        <button class="btn btn-ghost btn-sm" id="au-doc" title="APA-style table with note; opens in Word, prints to PDF">⬇ DOC</button>
        <button class="btn btn-ghost btn-sm" id="au-tex" title="APA-style LaTeX table (booktabs, threeparttable)">⬇ TeX</button></span></div>
    <p class="muted" style="font-size:13px;margin:0 0 10px">What the model extracted and what the review changed, per extraction target. Hover a column for its definition.</p>
    <div class="au-wrap">${auditPageHtml(AUDIT)}</div>
    <details class="au-note"><summary>How this is computed</summary><p>${esc(auditNote(AUDIT))}</p></details>
    ${AUDIT.n_entries_reviewed < AUDIT.n_entries ? `<p class="muted" style="font-size:12.5px;margin:10px 0 0">Only ${AUDIT.n_entries_reviewed} of ${AUDIT.n_entries} entries are verified so far. The indices describe the verified part; verify the rest in <a href="/workspace?project=${esc(id)}">Data review</a> to complete the report.</p>` : ""}`;
  const slug = (OV.slug || "dataset");
  $("#au-csv").onclick = () => {
    const q = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
    const lines = [["group", "variable", ...AUDIT_COLS.map(([k]) => k), "sen", "pre", "jac"].join(",")]
      .concat([...AUDIT.rows, { group: "", label: "Total", ...AUDIT.total }].map((r) =>
        [q(r.group || ""), q(r.label), ...AUDIT_COLS.map(([k]) => r[k]), r.sen ?? "", r.pre ?? "", r.jac ?? ""].join(",")));
    saveBlob(`${slug}-audit.csv`, lines.join("\n") + "\n", "text/csv");
  };
  $("#au-tex").onclick = () => saveBlob(`${slug}-audit.tex`, auditTex(AUDIT), "application/x-tex");
  $("#au-doc").onclick = () => {
    const css = "body{font-family:'Times New Roman',serif;font-size:12pt;line-height:1.5;margin:1in}table{border-collapse:collapse;width:100%}"
      + "th,td{padding:3pt 6pt;text-align:center;border:none}th{font-weight:normal;border-top:1.5pt solid #000;border-bottom:.75pt solid #000}"
      + ".apa-var{text-align:left}.apa-indent{padding-left:14pt}.apa-group td{text-align:left}.apa-total td{border-top:.75pt solid #000;border-bottom:1.5pt solid #000}p{margin:6pt 0}";
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>${esc(OV.title || "Audit report")}</title><style>${css}</style></head><body>`
      + `<p><b>Table 1</b></p><p><i>${esc(AUDIT_TITLE)}</i></p>${auditTableHtml(AUDIT)}<p><i>Note.</i> ${esc(auditNote(AUDIT))}</p>`
      + `<p style="font-size:10pt">Dataset: ${esc(OV.title || "")}${OV.published_url ? ` (${esc(OV.published_url)})` : ""}. Schema: ${esc(AUDIT.schema_id || "")}.</p></body></html>`;
    saveBlob(`${slug}-audit.doc`, html, "application/msword");
  };
}
function saveBlob(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
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
  if (OV.publish_status === "published" && OV.published_url) return ` · <a href="${esc(OV.published_url)}" target="_blank" rel="noopener">published on GitHub ↗</a>${OV.catalogue ? "" : " · not listed here"}`;
  if (OV.publish_status === "pending") return ` · pending review on GitHub${OV.git_pr_url ? ` (<a href="${esc(OV.git_pr_url)}" target="_blank" rel="noopener">pull request ↗</a>)` : ""}${OV.catalogue ? "" : " · GitHub only"}`;
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
    try {
      const r = await api.verifyAllDataset(id); OV = await api.datasetOverview(id); render();
      alert(`${r.verified} record${r.verified === 1 ? "" : "s"} marked verified · ${r.credibility.label}`);
      // the published copy carries the old badge: the next release (and its publication) carries the new one
      if (OV.publish_status === "published" && r.verified > 0) openReleaseForm();
    }
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
  const rn = $("#ds-rename"); if (rn) rn.onclick = doRename;
  $("#ds-export").onclick = doExport;

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
// One Publish button, three destinations. GitHub is the source of truth: a pull request in
// the datasets repository, listed in this catalogue once merged (or never, for "GitHub
// only"). "Here only" exists for servers without GitHub configured.
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
