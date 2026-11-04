// /workflow?id=<curated workflow> — the public card: introduction · steps · review policy ·
// checks · versions and citation · used by · related work. The prose comes from the workflow's
// card markdown (level-2 sections); everything else is generated from the declaration and the
// live database. Releases and DOIs are cut by moderators from this page.
import { api } from "/static/api.js";
import { esc, renderMarkdown } from "/static/grammar.js";

const $ = (s) => document.querySelector(s);
const body = $("#wc-body");
let ID = null, CARD = null, ME = null;
const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "");
const n = (k, w) => `${k} ${w}${k === 1 ? "" : "s"}`;
const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);
const name = (a) => [a.given, a.family].filter(Boolean).join(" ");
const slug = (t) => String(t).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
const testDoi = (d) => /^10\.5072\//.test(d || "");
const md = (text) => (text && text.trim() ? `<div class="md">${renderMarkdown(text)}</div>` : "");
const KIND = { extract: "⬚ extract", agent: "⬚ agent", check: "✓ check", derive: "Σ derive", vocabulary: "🏷 vocabulary", pages: "📖 pages", crosscheck: "↔ cross-check", loop: "↻ loop", review: "👁 review", release: "⛁ release" };

export async function init(id) {
  ID = id;
  let card = null;
  try { card = await api.workflowCard(id); }
  catch (e) { body.innerHTML = `<p class="muted">Couldn’t load this workflow: ${esc(e.message)}</p>`; return; }
  try { ME = await api.me(); } catch { ME = null; }
  CARD = card;
  render();
}

function sectionsOf(mdText) {
  const out = {}; const order = []; let cur = null; let fence = 0;
  for (const line of String(mdText || "").split("\n")) {
    const f = line.match(/^\s*(`{3,})/);
    if (f) { if (!fence) fence = f[1].length; else if (f[1].length >= fence) fence = 0; if (cur) out[cur].body += line + "\n"; continue; }
    const m = !fence && line.match(/^##\s+(.+?)\s*$/);
    if (m) { cur = slug(m[1]); out[cur] = { title: m[1], body: "" }; order.push(cur); }
    else if (cur) out[cur].body += line + "\n";
  }
  return { sections: out, order };
}

function reviewText(rv) {
  if (!rv || rv.mode === "none") return "no review";
  const holds = rv.blocks === "next" ? "holds the run" : "holds the release";
  if (rv.mode === "all") return `every record · ${holds}`;
  const size = rv.n != null ? `${rv.n} records` : `a ${Math.round((rv.share || 0) * 100)}% share`;
  return `random sample of ${size}${rv.unit === "paper" ? " (whole papers)" : ""}${(rv.stratify || []).length ? `, stratified by ${rv.stratify.join(", ")}` : ""} · ${holds}`;
}

function stepsHtml(c) {
  return `<ol class="wc-steps">${(c.steps || []).map((s) => {
    const pc = s.preset_card;
    const what = s.kind === "extract" || s.kind === "agent"
      ? `${s.dataset ? `into the <b>${esc(s.dataset)}</b> dataset · ` : ""}${s.source === "supplied" ? "results made outside Metalens are uploaded into the run" : "one model call per paper on the worker"} · preset ${pc ? `<a href="${esc(pc.url)}">${esc(pc.title)}</a>` : ""} <code>${esc(s.preset || "")}</code>${pc && pc.live_schema_id && pc.live_schema_id !== s.preset ? ` <span class="muted">(the live preset is now <code>${esc(pc.live_schema_id)}</code>)</span>` : ""}${s.k > 1 ? ` · ${s.k} runs, voted` : ""}${(s.reads || []).length ? ` · reads ${s.reads.join(", ")}` : ""}`
      : s.kind === "check" ? `<code>${esc(s.check)}</code> over ${(s.over || []).join(", ")} — ${esc((s.check_info || {}).label || "")} · on a finding at or above <code>${esc(s.severity || "error")}</code>: ${esc(s.on_fail || "note")}${(s.check_info || {}).needs_companion ? " · needs the dataset's companion" : ""}`
      : s.kind === "vocabulary" ? `vocabulary of <code>${esc(s.column || "")}</code>`
      : s.kind === "pages" ? `pages of <code>${esc(s.group || "")}</code> keyed by <code>${esc(s.keyed_by || "")}</code>`
      : s.kind === "crosscheck" ? "the claims-vs-tables check against the companion dataset"
      : s.kind === "review" ? `a joint review over ${(s.over || []).join(", ")}: each paper's documents of those steps in one worklist`
      : s.kind === "release" ? `gate on ${(s.gate || []).join(", ") || "nothing"}: the release holds while a gate step is stale or awaits review`
      : esc(s.kind);
    return `<li class="ds-dashrow wc-step"><span class="wc-kind">${KIND[s.kind] || esc(s.kind)}</span> <b>${esc(s.label || s.id)}</b> <code class="muted">${esc(s.id)}</code>
      <div class="wc-what">${what}</div>${s.help ? `<div class="muted wc-help">${esc(s.help)}</div>` : ""}
      ${s.review ? `<div class="wc-review"><span class="badge ${s.review.mode === "none" ? "" : "tier-sample_verified"}">review: ${esc(reviewText(s.review))}</span></div>` : ""}</li>`;
  }).join("")}</ol>`;
}

function checksHtml(c) {
  const rows = (c.steps || []).filter((s) => s.kind === "check" && s.check_info);
  if (!rows.length) return `<p class="muted">This workflow declares no deterministic check.</p>`;
  return rows.map((s) => `<div class="ds-card"><div class="ds-card-h"><code>${esc(s.check)}</code> <span class="muted">· ${esc(s.check_info.label || "")}</span></div>
    ${Object.keys(s.check_info.codes || {}).length ? `<ul class="md-list">${Object.entries(s.check_info.codes).map(([code, sev]) => `<li><code>${esc(code)}</code> <span class="muted">${esc(sev)}</span></li>`).join("")}</ul>` : `<p class="muted" style="margin:0">Re-checks the declared contract on the stored values.</p>`}
    ${Object.keys(s.params || {}).length ? `<p class="muted" style="font-size:12.5px;margin:4px 0 0">parameters: <code>${esc(JSON.stringify(s.params))}</code></p>` : ""}</div>`).join("");
}

function badge(cr) { cr = cr || {}; return `<span class="badge tier-${esc(cr.tier || "ai_only")}" title="${esc(cr.label_long || "")}">${esc(cr.label || "")}</span>`; }

function verificationLive(c) {
  const v = c.verification || {};
  if (!v.n_datasets) return `<p class="muted">No public dataset declares this workflow yet.</p>`;
  const stat = (num, lbl) => `<div class="stat"><div class="stat-num">${esc(String(num))}</div><div class="stat-lbl">${lbl}</div></div>`;
  const smp = v.sample;
  return `<div class="ds-stats">${stat(v.n_datasets, "public datasets")}${stat(v.n_papers, "papers")}${stat(v.n_records, "records")}${stat(v.audited, "checked by a person")}${stat(pct(v.agreement), "agreed with the extraction")}
      ${smp ? stat(`${smp.drawn}/${smp.frame}`, "drawn at random") : ""}<div class="stat"><div class="stat-num small">${badge(v)}</div><div class="stat-lbl">${v.agreement_ci ? `95% CI ${pct(v.agreement_ci[0])}–${pct(v.agreement_ci[1])}` : "under the dataset badge rule"}</div></div></div>
    ${v.label_long ? `<p class="muted" style="font-size:12.5px;margin:4px 0 8px">${esc(v.label_long)}</p>` : ""}
    <table class="ds-cc"><thead><tr><th>Dataset</th><th>Papers</th><th>Records</th><th>Badge</th><th>Last run</th><th>Latest release</th></tr></thead><tbody>
    ${(c.datasets || []).map((d) => { const rel = (d.releases || [])[0]; const lr = d.last_run;
      return `<tr><td><a href="/dataset?id=${encodeURIComponent(d.id)}">${esc(d.title || d.slug || "dataset")}</a><div class="muted" style="font-size:11.5px"><code>${esc(d.contract || "")}</code></div></td><td>${d.n_papers}</td><td>${d.n_records}</td><td>${badge(d.credibility)}</td>
        <td>${lr ? `${esc(lr.status)}${lr.finished_at ? ` · ${esc(day(lr.finished_at))}` : ""}${lr.release ? ` · v${lr.release}` : ""}` : "<span class=\"muted\">none</span>"}${d.n_runs ? ` <span class="muted">· ${n(d.n_runs, "run")}</span>` : ""}</td>
        <td>${rel ? `v${rel.number}${rel.doi ? ` · <a href="https://doi.org/${encodeURIComponent(rel.doi)}" target="_blank" rel="noopener">DOI</a>` : ""}` : "<span class=\"muted\">none</span>"}</td></tr>`; }).join("")}</tbody></table>
    <p class="muted" style="font-size:12.5px;margin:6px 0 0">Live figures from the public datasets that declare this workflow on this server.</p>`;
}

function doiBadge(x) {
  if (!x.doi) return "";
  return ` <a class="badge" href="${esc(x.zenodo_url || "https://doi.org/" + encodeURIComponent(x.doi))}" target="_blank" rel="noopener" title="${testDoi(x.doi) ? "a sandbox DOI from testing: it resolves nowhere" : "the DOI of this version"}">${testDoi(x.doi) ? "test " : ""}DOI ${esc(x.doi)}</a>`;
}
function citeTop(c) {
  const latest = c.latest || {};
  const doi = c.concept_doi || latest.doi;
  const zen = doi
    ? `<a class="badge pc-zenodo" href="${esc(c.concept_doi ? "https://doi.org/" + c.concept_doi : (latest.zenodo_url || "https://doi.org/" + latest.doi))}" target="_blank" rel="noopener">${testDoi(doi) ? "test " : ""}Zenodo · DOI ${esc(doi)}</a>`
    : `<span class="muted pc-nodoi">No DOI yet</span>`;
  return `<div class="ds-cite pc-citetop"><span class="rk">How to cite</span><blockquote id="wc-cite-text">${esc(c.cite_as || "")}</blockquote>
    <div class="pc-cite-actions"><button class="btn btn-ghost btn-sm" id="wc-cite-copy" type="button">Copy</button> ${zen}</div></div>`;
}
function versions(c) {
  const admin = c.viewer && c.viewer.is_admin;
  const rows = (c.releases || []).map((x) => `<div class="ds-dashrow"><b>v${x.number}</b> <span class="muted">· ${esc(day(x.created_at))}</span>${doiBadge(x) || (admin && c.zenodo && c.zenodo.allowed ? ` <button type="button" class="btn btn-ghost btn-sm" data-wfdoi="${x.number}" title="mint a permanent DOI for this release on Zenodo${c.zenodo.sandbox ? " (sandbox: a test DOI)" : ""}">◎ DOI</button>` : ` <span class="muted">no DOI</span>`)}
      <span class="muted"> · contract <code>${esc(x.contract)}</code> · ${(x.presets || []).map((p) => `${esc(p.step)}: ${p.preset_release ? `preset v${p.preset_release}` : "unreleased preset"}`).join(", ")} · <a href="${esc(x.bundle_url)}" title="workflow.json, steps.md, card.md, CITATION.cff, README — a loadable workflow folder">⬇ files</a></span>
      ${x.notes ? `<div class="md" style="margin:2px 0 0">${renderMarkdown(x.notes)}</div>` : ""}</div>`).join("");
  const none = `<p class="muted">No numbered release yet. The contract is still identified: cite <code>${esc(c.contract)}</code> and the engine version of your run.</p>`;
  const form = admin ? `<div class="ds-relform" style="margin-top:8px"><b>Release v${((c.latest || {}).number || 0) + 1}</b>
      <p class="muted" style="font-size:12.5px;margin:4px 0">Freezes the declaration, the card and the preset releases the pins match into a bundle.${c.changed_since_release === false ? " Nothing changed since the latest release." : ""}</p>
      <textarea id="wc-rel-notes" rows="2" maxlength="4000" placeholder="Release notes (optional)"></textarea>
      <div style="display:flex;gap:8px;margin-top:6px"><button type="button" class="btn btn-primary btn-sm" id="wc-rel-go"${c.changed_since_release === false ? " disabled" : ""}>Release</button><span class="muted" id="wc-rel-msg" style="font-size:12.5px"></span></div></div>` : "";
  const cite = `<p class="muted" style="font-size:12.5px;margin:6px 0 0">The citation is at the top of this page${c.concept_doi ? "; its DOI always opens the newest version" : ""}.${c.cite_this_version && c.cite_this_version !== c.cite_as ? ` This version only: ${esc(c.cite_this_version)}` : ""}
    In a methods section, name the workflow contract and the run (the export carries both): <code>${esc(c.contract)}</code>. See <a href="https://github.com/johanna-einsiedler/metalens/blob/main/docs/WORKFLOWS.md" target="_blank" rel="noopener">WORKFLOWS.md</a>.</p>`;
  return `${rows || none}${cite}${form}`;
}

function render() {
  const c = CARD;
  const { sections, order } = sectionsOf(c.card_md);
  const known = new Set(["introduction", "steps", "review", "verification", "limitations", "versions", "changelog", "related-work"]);
  const extra = order.filter((k) => !known.has(k));
  const latest = c.latest;
  const badges = [
    latest ? `<span class="badge tier-human_verified">v${latest.number}${latest.doi ? ` · ${testDoi(latest.doi) ? "test " : ""}DOI ${esc(latest.doi)}` : ""}</span>` : `<span class="badge">unreleased</span>`,
    `<code title="the content-addressed contract of the current declaration: its steps, pins and review declarations">${esc(c.contract)}</code>`,
    c.license ? `<span class="muted">${esc(c.license)}</span>` : "",
    (c.later || []).length ? `<span class="badge" title="${esc(c.later.join("\n"))}">needs a later release</span>` : "",
  ].filter(Boolean).join(" ");
  const authors = (c.authors || []).map((a) => `<span class="pc-author">${esc(name(a))}${a.orcid ? ` <a class="pc-orcid" href="https://orcid.org/${esc(a.orcid)}" target="_blank" rel="noopener" title="ORCID ${esc(a.orcid)}">iD</a>` : ""}${a.affiliation ? ` <span class="muted">· ${esc(a.affiliation)}</span>` : ""}</span>`).join("");
  const kws = (c.keywords || []).length ? `<div class="ds-kws">${c.keywords.map((k) => `<span class="kw">${esc(k)}</span>`).join("")}</div>` : "";
  const relatedMd = (sections["related-work"] || {}).body || "";
  const toc = [["introduction", "Introduction"], ["steps", "Steps"], ["review", "Review policy"], ["checks", "Checks"], ["verification", "Verification"], ["versions", "Versions and citation"], ["used-by", "Used by"], ...(relatedMd ? [["related", "Related work"]] : [])]
    .map(([a, t]) => `<a href="#${a}">${t}</a>`).join("");
  const reviewRows = (c.steps || []).filter((s) => s.review).map((s) => `<tr><td>${esc(s.label || s.id)}</td><td>${esc(s.review.mode)}</td><td>${s.review.mode === "sample" ? (s.review.n != null ? s.review.n : `${Math.round((s.review.share || 0) * 100)}%`) : "—"}</td><td>${esc(s.review.unit || "entry")}</td><td>${(s.review.stratify || []).join(", ") || "—"}</td><td>${s.review.blocks === "next" ? "the next step" : "the release"}</td></tr>`).join("");
  body.innerHTML = `
    <div class="ds-head"><div><h1>${esc(c.title)}</h1>${c.tagline ? `<p class="lede">${esc(c.tagline)}</p>` : ""}
      ${authors ? `<p class="pc-authors">${authors}</p>` : ""}${kws}<div class="pc-badges">${badges}</div></div>
      <div class="pc-actions"><a class="btn btn-ghost btn-sm" href="/workflows">All workflows</a></div></div>
    ${citeTop(c)}
    <nav class="pc-toc">${toc}</nav>
    <section id="introduction"><h2>Introduction</h2>${md((sections.introduction || {}).body) || `<p class="md">${esc(c.description || "")}</p>`}</section>
    <section id="steps"><h2>Steps</h2>${md((sections.steps || {}).body)}${stepsHtml(c)}</section>
    <section id="review"><h2>Review policy</h2>${md((sections.review || {}).body)}${reviewRows ? `<table class="ds-cc"><thead><tr><th>Step</th><th>Mode</th><th>Size</th><th>Unit</th><th>Strata</th><th>Holds</th></tr></thead><tbody>${reviewRows}</tbody></table>` : `<p class="muted">No step declares a review.</p>`}</section>
    <section id="checks"><h2>Checks</h2>${checksHtml(c)}</section>
    <section id="verification"><h2>Verification</h2>${md((sections.verification || {}).body)}${verificationLive(c)}${sections.limitations ? `<h3 class="pc-section-h">Limitations</h3>${md(sections.limitations.body)}` : ""}</section>
    <section id="versions"><h2>Versions and citation</h2>${md((sections.versions || {}).body)}${versions(c)}${sections.changelog ? `<h3 class="pc-section-h">Changelog</h3>${md(sections.changelog.body)}` : ""}</section>
    <section id="used-by"><h2>Used by</h2>${(c.datasets || []).length ? (c.datasets || []).map((d) => `<div class="ds-dashrow"><a href="/dataset?id=${encodeURIComponent(d.id)}"><b>${esc(d.title || d.slug || "dataset")}</b></a> <span class="muted">· ${n(d.n_papers, "paper")} · ${n(d.n_records, "record")}</span> ${badge(d.credibility)}${(d.releases || []).filter((r) => r.doi).map((r) => ` <a class="badge" href="https://doi.org/${encodeURIComponent(r.doi)}" target="_blank" rel="noopener">v${r.number} DOI</a>`).join("")}</div>`).join("") : `<p class="muted">No public dataset declares this workflow yet.</p>`}</section>
    ${relatedMd ? `<section id="related"><h2>Related work</h2>${md(relatedMd)}</section>` : ""}
    ${extra.map((k) => `<section id="${esc(k)}"><h2>${esc(sections[k].title)}</h2>${md(sections[k].body)}</section>`).join("")}`;
  const copy = $("#wc-cite-copy");
  if (copy) copy.onclick = async () => { try { await navigator.clipboard.writeText($("#wc-cite-text").textContent); copy.textContent = "Copied"; } catch { /* clipboard blocked */ } };
  const go = $("#wc-rel-go");
  if (go) go.onclick = async () => {
    go.disabled = true; const msg = $("#wc-rel-msg"); msg.textContent = "Cutting the release…";
    try { const out = await api.createWorkflowRelease(ID, { notes: $("#wc-rel-notes").value }); if ((out.problems || []).length) alert(out.problems.join("\n")); await init(ID); }
    catch (e) { msg.textContent = e.message; go.disabled = false; }
  };
  body.querySelectorAll("[data-wfdoi]").forEach((b) => (b.onclick = async () => {
    if (!confirm("A DOI is permanent: it is minted under the server's Zenodo account and cannot be withdrawn. Mint it for this release?")) return;
    b.disabled = true;
    try { await api.workflowReleaseDoi(ID, +b.dataset.wfdoi); await init(ID); } catch (e) { alert(e.message); b.disabled = false; }
  }));
}

init(new URLSearchParams(location.search).get("id"));
