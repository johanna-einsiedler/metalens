// /preset?id=<curated preset> — the public card: introduction · how it extracts · fields ·
// verification · versions and citation · used by · related work. The prose comes from the
// preset's card markdown (level-2 sections), everything else is generated from the declaration
// and the live database. Moderators (PAPERLENS_ADMINS) see the release and DOI controls.
import { api } from "/static/api.js";
import { esc, renderMarkdown } from "/static/grammar.js";

const $ = (s) => document.querySelector(s);
const body = $("#pe-body");
let ID = null, CARD = null, ME = null;
const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "");
const n = (k, w) => `${k} ${w}${k === 1 ? "" : "s"}`;
const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);
const name = (a) => [a.given, a.family].filter(Boolean).join(" ");
const slug = (t) => String(t).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
const testDoi = (d) => /^10\.5072\//.test(d || "");

export async function init(id) {
  ID = id;
  let card = null;
  try { card = await api.presetCard(id); }
  catch (e) {
    if (!/^404/.test(e.message)) { body.innerHTML = `<p class="muted">Couldn’t load this preset: ${esc(e.message)}</p>`; return; }
  }
  ME = await api.me();
  if (!card) return renderPlain(id);
  CARD = card;
  render();
}

// A preset without a card (a personal preset, or a built-in nobody documented yet): the detail
// the editor also reads — title, tagline, description, prompt.
async function renderPlain(id) {
  let d;
  try { d = await api.presetDetail(id); }
  catch (e) { body.innerHTML = `<p class="muted">Couldn’t load this preset: ${esc(e.message)}</p>`; return; }
  const mine = ME && d.source === "personal" && (d.owner_user_id === ME.id);
  const vis = d.source === "personal" ? `<span class="badge">${d.visibility === "public" ? "public" : "private"} personal preset</span>` : `<span class="badge">built-in · no card yet</span>`;
  body.innerHTML = `<div class="ds-head"><div><h1>${esc(d.title || id)}</h1>${d.tagline ? `<p class="lede">${esc(d.tagline)}</p>` : ""}<div class="pc-badges">${vis} <code>${esc(d.schema_id || "")}</code></div></div>
    <div class="pc-actions">${mine ? `<a class="btn btn-ghost btn-sm" href="/preset?id=${encodeURIComponent(id)}&edit=1">Edit</a>` : ""}${ME && ME.email ? `<a class="btn btn-ghost btn-sm" href="/preset?id=${encodeURIComponent(id)}&edit=1">Duplicate as a personal preset</a>` : ""}</div></div>
    ${d.description ? `<p class="md">${esc(d.description)}</p>` : ""}
    ${d.prompt ? `<details class="ds-prompt pc-foldable"><summary>The full prompt</summary><div class="md-preview">${renderMarkdown(d.prompt)}</div></details>` : ""}`;
}

// The card markdown split on its level-2 headings (fences respected, so a prompt quoted on a
// card never splits it); keyed by slug.
function sectionsOf(md) {
  const out = {}; const order = []; let cur = null; let fence = 0;
  for (const line of String(md || "").split("\n")) {
    const f = line.match(/^\s*(`{3,})/);
    if (f) { if (!fence) fence = f[1].length; else if (f[1].length >= fence) fence = 0; if (cur) out[cur].body += line + "\n"; continue; }
    const m = !fence && line.match(/^##\s+(.+?)\s*$/);
    if (m) { cur = slug(m[1]); out[cur] = { title: m[1], body: "" }; order.push(cur); }
    else if (cur) out[cur].body += line + "\n";
  }
  return { sections: out, order };
}
const md = (text) => (text && text.trim() ? `<div class="md">${renderMarkdown(text)}</div>` : "");

function render() {
  const c = CARD;
  const { sections, order } = sectionsOf(c.card_md);
  const known = new Set(["introduction", "how-it-extracts", "verification", "limitations", "changelog", "related-work"]);
  const extra = order.filter((k) => !known.has(k));
  const latest = c.latest;
  const badges = [
    latest ? `<span class="badge tier-human_verified">v${latest.number}${latest.doi ? ` · ${testDoi(latest.doi) ? "test " : ""}DOI ${esc(latest.doi)}` : ""}</span>` : `<span class="badge">unreleased</span>`,
    c.mode === "import" ? `<span class="badge">import schema</span>` : "",
    c.hidden ? `<span class="badge" title="selected inside the MASEMiner builder, not from the extract picker">builder-only</span>` : "",
    `<code title="the content-addressed id of the current declaration">${esc(c.schema_id)}</code>`,
    c.license ? `<span class="muted">${esc(c.license)}</span>` : "",
  ].filter(Boolean).join(" ");
  const authors = (c.authors || []).map((a) => `<span class="pc-author">${esc(name(a))}${a.orcid ? ` <a class="pc-orcid" href="https://orcid.org/${esc(a.orcid)}" target="_blank" rel="noopener" title="ORCID ${esc(a.orcid)}">iD</a>` : ""}${a.affiliation ? ` <span class="muted">· ${esc(a.affiliation)}</span>` : ""}</span>`).join("");
  const kws = (c.keywords || []).length ? `<div class="ds-kws">${c.keywords.map((k) => `<span class="kw">${esc(k)}</span>`).join("")}</div>` : "";
  const useHref = c.mode === "import" ? "/import" : `/extract?preset=${encodeURIComponent(c.hidden && c.id === "masem-indirect" ? "masem-direct" : c.id)}`;
  const actions = [
    c.mode === "import" ? `<a class="btn btn-primary btn-sm" href="${useHref}">Import into this schema</a>` : `<a class="btn btn-primary btn-sm" href="${useHref}">Use this preset</a>`,
    ME && ME.email ? `<a class="btn btn-ghost btn-sm" href="/preset?id=${encodeURIComponent(c.id)}&edit=1">Duplicate as a personal preset</a>` : "",
  ].filter(Boolean).join("");
  const toc = [["introduction", "Introduction"], ["how", "How it extracts"], ["fields", "Fields"], ["verification", "Verification"],
               ["versions", "Versions and citation"], ["used-by", "Used by"], ["related", "Related work"]]
    .map(([a, t]) => `<a href="#${a}">${t}</a>`).join("");

  body.innerHTML = `
    <div class="ds-head"><div><h1>${esc(c.title)}</h1>${c.tagline ? `<p class="lede">${esc(c.tagline)}</p>` : ""}
      ${authors ? `<p class="pc-authors">${authors}</p>` : ""}${kws}<div class="pc-badges">${badges}</div></div>
      <div class="pc-actions">${actions}</div></div>
    <nav class="pc-toc">${toc}</nav>
    <section id="introduction"><h2>Introduction</h2>${md((sections.introduction || {}).body) || `<p class="md">${esc(c.description || "")}</p>`}</section>
    <section id="how"><h2>How it extracts</h2>${md((sections["how-it-extracts"] || {}).body)}${howGenerated(c)}</section>
    <section id="fields"><h2>Fields</h2><p class="muted" style="margin:0 0 8px">Generated from the declaration (<code>${esc(c.schema_id)}</code>): every value this preset extracts, with its type, whether it is required, the evidence policy and the confidence group.</p>${renderFieldDocs(c.fields_doc)}</section>
    <section id="verification"><h2>Verification</h2>${md((sections.verification || {}).body)}${verificationLive(c)}${sections.limitations ? `<h3 class="pc-section-h">Limitations</h3>${md(sections.limitations.body)}` : ""}</section>
    <section id="versions"><h2>Versions and citation</h2>${versions(c)}${sections.changelog ? `<h3 class="pc-section-h">Changelog</h3>${md(sections.changelog.body)}` : ""}</section>
    <section id="used-by"><h2>Used by</h2>${usedBy(c)}</section>
    <section id="related"><h2>Related work</h2>${md((sections["related-work"] || {}).body)}${relatedRest(c, (sections["related-work"] || {}).body || "")}</section>
    ${extra.map((k) => `<section id="${esc(k)}"><h2>${esc(sections[k].title)}</h2>${md(sections[k].body)}</section>`).join("")}`;
  // long code blocks (a quoted pipeline prompt) fold
  body.querySelectorAll("pre.md-code").forEach((pre) => {
    const lines = pre.textContent.split("\n").length;
    if (lines <= 30) return;
    const d = document.createElement("details"); d.className = "pc-foldable md-fold";
    d.innerHTML = `<summary>Show all ${lines} lines</summary>`;
    pre.parentNode.insertBefore(d, pre); d.appendChild(pre);
  });
  wireAdmin();
  const copy = $("#pc-cite-copy");
  if (copy) copy.onclick = async () => { try { await navigator.clipboard.writeText($("#pc-cite-text").textContent); copy.textContent = "Copied"; } catch { /* clipboard blocked */ } };
}

function howGenerated(c) {
  const doc = c.fields_doc || {};
  const params = (doc.params || []).length ? `<h3 class="pc-section-h">Parameters</h3><p class="muted" style="margin:0 0 6px">Set per run; the defaults are what this card documents.</p>
    <table class="ds-cc fd-table"><thead><tr><th>Parameter</th><th>Type</th><th>Default</th><th>Used by</th><th>Meaning</th></tr></thead><tbody>
    ${doc.params.map((p) => `<tr><td><code>${esc(p.name)}</code></td><td>${esc(p.type || "")}</td><td>${p.default_text ? `<pre class="pc-default">${esc(p.default_text)}</pre>` : `<span class="muted">none${p.empty_text ? ` (${esc(p.empty_text)})` : ""}</span>`}</td><td>${(p.used_by || []).map((u) => `<code>${esc(u)}</code>`).join(" ") || "—"}</td><td>${esc(p.label || "")}${p.help ? ` <span class="muted">— ${esc(p.help)}</span>` : ""}</td></tr>`).join("")}</tbody></table>` : "";
  if (c.mode === "import") {
    return `${params}<p class="muted ds-mixed">The records are <b>not extracted in Metalens</b>: a pipeline that runs elsewhere produces them, and Metalens imports them into this schema, anchors the evidence, and is where a person reviews and corrects them. There is no Metalens prompt behind them.</p>`;
  }
  return `${params}${c.prompt ? `<details class="ds-prompt pc-foldable"><summary>The full prompt, as rendered with the default parameters</summary><div class="md-preview">${renderMarkdown(c.prompt)}</div></details>` : ""}`;
}

// ── fields: the structure the API documented, drawn as tables ─────────────────────────────
function optionsHtml(d) {
  const o = d.options; if (!o) return "";
  const item = (x) => (x.label ? `${esc(String(x.value))} = ${esc(x.label)}` : esc(String(x.value)));
  if (o.kind === "param") {
    const dflt = (o.items || []).length ? ` · default: ${o.items.map(item).join("; ")}` : (o.empty ? ` · no default (${esc(o.empty)})` : "");
    return ` <span class="muted">· codes from the <b>${esc(o.param_label)}</b> parameter, set per run${dflt}</span>`;
  }
  if ((o.items || []).length <= 8) return ` <span class="muted">· ${o.items.map(item).join("; ")}</span>`;
  return `<ul class="md-list fd-opts">${o.items.map((x) => `<li>${item(x)}</li>`).join("")}</ul>`;
}
function fieldRows(fields, { columns = false } = {}) {
  const head = columns ? ["Column", "Type", "Required", "Meaning"] : ["Field", "Type", "Required", "Evidence", "Confidence", "Meaning"];
  const rows = (fields || []).map((d) => {
    const meaning = `${esc(d.label || "")}${d.help ? ` <span class="muted">— ${esc(d.help)}</span>` : ""}${optionsHtml(d)}${d.allow_other ? ` <span class="muted">· or the paper's own term</span>` : ""}`;
    const cells = [`<code>${esc(d.name)}</code>`, esc(d.type_text || d.type || ""), d.required ? `<span class="badge">required</span>` : ""];
    if (!columns) cells.push(d.evidence_text ? `<span title="${esc(d.evidence_text)}">${esc(d.evidence_text.split(" — ")[0])}</span>` : "",
                             d.confidence_label ? `<a href="#conf-${esc(d.confidence)}">${esc(d.confidence_label)}</a>` : "");
    cells.push(meaning);
    const sub = d.columns ? `<tr class="fd-sub"><td colspan="${head.length}"><div class="fd-subh">Columns of ${esc(d.label || d.name)} (<code>${esc(d.name)}</code>)</div>${fieldRows(d.columns, { columns: true })}</td></tr>` : "";
    return `<tr>${cells.map((x) => `<td>${x}</td>`).join("")}</tr>${sub}`;
  }).join("");
  return `<table class="ds-cc fd-table"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>`;
}
function renderFieldDocs(doc) {
  if (!doc) return "";
  const e = doc.entries;
  const parts = [`<h3 class="pc-section-h">Identity fields</h3><p class="muted" style="margin:0 0 4px">Always extracted, whatever the preset declares (<code>paper_metadata</code>):</p><ul class="md-list">${(doc.identity || []).map((i) => `<li><code>${esc(i.name)}</code> <span class="muted">(${esc(i.type)}) — ${esc(i.help)}</span></li>`).join("")}</ul>`];
  if ((doc.paper || []).length) parts.push(`<h3 class="pc-section-h">Paper-level fields</h3>${fieldRows(doc.paper)}`);
  if (e) {
    const lead = `${e.cardinality === "one" ? `Exactly one ${esc((e.label || "").toLowerCase())}` : `One <code>${esc(e.key)}</code> element per ${esc((e.label || "").toLowerCase())}`}${e.help ? ` <span class="muted">— ${esc(e.help)}</span>` : ""}.${e.id_field ? ` Identified by <code>${esc(e.id_field)}</code>.` : ""}${e.evidence_text ? ` <span class="muted">Evidence: ${esc(e.evidence_text)}.</span>` : ""}`;
    parts.push(`<h3 class="pc-section-h">${esc(e.label || "Entries")} <code class="muted">${esc(e.key)}</code></h3><p style="margin:0 0 6px">${lead}</p>${fieldRows(e.fields)}`);
    for (const ch of doc.children || []) {
      parts.push(`<h3 class="pc-section-h">${esc(ch.label || ch.key)} <code class="muted">${esc(ch.key)}</code> <span class="muted" style="font-weight:400">inside each ${esc((e.label || "").toLowerCase())}</span></h3>${ch.help ? `<p class="muted" style="margin:0 0 6px">${esc(ch.help)}${ch.evidence_text ? ` Evidence: ${esc(ch.evidence_text)}.` : ""}</p>` : ""}${fieldRows(ch.fields)}`);
    }
  }
  const conf = doc.confidence || {};
  if ((conf.groups || []).length) {
    parts.push(`<h3 class="pc-section-h">Confidence ratings</h3><p class="muted" style="margin:0 0 4px">The model rates its confidence per group on the levels ${esc((conf.levels || []).join(", "))}${conf.notes ? ", with a note" : ""}:</p><ul class="md-list">${conf.groups.map((g) => `<li id="conf-${esc(g.id)}"><b>${esc(g.label)}</b> <span class="muted">(<code>${esc(g.id)}</code>, per ${esc(g.scope)})${g.help ? ` — ${esc(g.help)}` : ""}</span> · covers ${(g.covers || []).map((x) => `<code>${esc(x)}</code>`).join(", ") || "—"}</li>`).join("")}</ul>`);
  }
  parts.push(`<h3 class="pc-section-h">Evidence policy</h3><ul class="md-list">${Object.entries(doc.evidence_legend || {}).map(([k, v]) => `<li><b>${esc(k)}</b> <span class="muted">— ${esc(v)}</span></li>`).join("")}</ul>`);
  return parts.join("");
}

// ── verification: the live roll-up over the public datasets built with the preset ──────────
const badge = (cred) => `<span class="badge tier-${esc((cred || {}).tier || "ai_only")}">${esc((cred || {}).label || "")}</span>`;
function verificationLive(c) {
  const v = c.verification || {};
  const stat = (num, lbl) => `<div class="stat"><div class="stat-num">${esc(String(num))}</div><div class="stat-lbl">${lbl}</div></div>`;
  if (!v.n_datasets) return `<h3 class="pc-section-h">On this server</h3><p class="muted">No public dataset uses this preset yet.</p>`;
  return `<h3 class="pc-section-h">On this server</h3>
    <div class="ds-stats">${stat(v.n_datasets, n(v.n_datasets, "public dataset").replace(/^\d+ /, ""))}${stat(v.n_papers, "papers")}${stat(v.n_records, "records")}${stat(v.audited, "checked by a person")}${stat(pct(v.agreement), "agreed with the model")}
      <div class="stat"><div class="stat-num small">${badge(v)}</div><div class="stat-lbl">${v.agreement_ci ? `95% CI ${pct(v.agreement_ci[0])}–${pct(v.agreement_ci[1])}` : "under the dataset badge rule"}</div></div></div>
    <table class="ds-cc"><thead><tr><th>Dataset</th><th>Papers</th><th>Records</th><th>Checked</th><th>Agreed</th><th>Badge</th><th>Latest release</th></tr></thead><tbody>
    ${(c.datasets || []).map((d) => { const cr = d.credibility || {}; const rel = (d.releases || [])[0];
      return `<tr><td><a href="/dataset?id=${encodeURIComponent(d.id)}">${esc(d.title || d.slug || "dataset")}</a></td><td>${d.n_papers}</td><td>${d.n_records}</td><td>${cr.audited || 0}</td><td>${cr.agreed != null ? cr.agreed : "—"}</td><td>${badge(cr)}</td><td>${rel ? `v${rel.number}${rel.doi ? ` · <a href="https://doi.org/${encodeURIComponent(rel.doi)}" target="_blank" rel="noopener">DOI</a>` : ""}` : "<span class=\"muted\">none</span>"}</td></tr>`; }).join("")}</tbody></table>
    <p class="muted" style="font-size:12.5px;margin:6px 0 0">Live figures from the public datasets built with this preset on this server; they change as coders verify. Datasets synced from GitHub show the badge they were published with.</p>`;
}

// ── versions and citation ───────────────────────────────────────────────────────────────
function doiBadge(x) {
  if (!x.doi) return "";
  return ` <a class="badge" href="${esc(x.zenodo_url || "https://doi.org/" + encodeURIComponent(x.doi))}" target="_blank" rel="noopener" title="${testDoi(x.doi) ? "a sandbox DOI from testing: it resolves nowhere" : "the DOI of this version"}">${testDoi(x.doi) ? "test " : ""}DOI ${esc(x.doi)}</a>`;
}
function versions(c) {
  const admin = CARD.viewer && CARD.viewer.is_admin;
  const zen = c.zenodo || {};
  const rows = (c.releases || []).map((x) => `<div class="ds-dashrow"><b>v${x.number}</b> <span class="muted">· ${esc(day(x.created_at))}</span>${doiBadge(x) || (admin && zen.allowed ? ` <button type="button" class="btn btn-ghost btn-sm" data-reldoi="${x.number}" title="mint a permanent DOI for this version on Zenodo${zen.sandbox ? " (sandbox: a test DOI)" : ""}">◎ DOI</button>` : ` <span class="muted">no DOI</span>`)}
      <span class="muted"> · schema <code>${esc(x.schema_id)}</code> · <a href="${esc(x.bundle_url)}" title="preset.json, prompt.md, card.md, fields.md, CITATION.cff, README — a loadable preset folder">⬇ files</a></span>
      ${x.notes ? `<div class="md" style="margin:2px 0 0">${renderMarkdown(x.notes)}</div>` : ""}</div>`).join("");
  const cite = `<div class="ds-cite"><span class="rk">How to cite</span><blockquote id="pc-cite-text">${esc(c.cite_as || "")}</blockquote><button class="btn btn-ghost btn-sm" id="pc-cite-copy">Copy</button>
    ${c.cite_this_version && c.cite_this_version !== c.cite_as ? `<p class="muted" style="font-size:12.5px;margin:6px 0 0">This version only: ${esc(c.cite_this_version)}</p>` : ""}
    <p class="muted" style="font-size:12.5px;margin:6px 0 0">In a methods section, name the schema id and the model id of your run (the export carries both): <code>${esc(c.schema_id)}</code>. See <a href="https://github.com/johanna-einsiedler/metalens/blob/main/docs/REPRODUCIBILITY.md" target="_blank" rel="noopener">REPRODUCIBILITY.md</a>.</p></div>`;
  const none = `<p class="muted">No numbered release yet. The data contract is still identified: cite the schema id <code>${esc(c.schema_id)}</code> and the engine version of your run.</p>`;
  const form = admin ? `<div class="ds-relform" id="pc-relform"><b>Release v${((c.latest || {}).number || 0) + 1}</b> <span class="muted">— freezes the current declaration, prompt and card text into a numbered, downloadable bundle${zen.configured ? ", which can then get a DOI" : ""}.</span>
      ${c.changed_since_release === false ? `<p class="muted" style="margin:6px 0 0">Nothing changed since v${c.latest.number}.</p>` : `<textarea id="pc-rel-notes" rows="2" placeholder="release notes (markdown): what changed and why"></textarea>
      <div style="display:flex;gap:8px;align-items:center;margin-top:6px"><button type="button" class="btn btn-primary btn-sm" id="pc-rel-go">Release v${((c.latest || {}).number || 0) + 1}</button>${zen.allowed ? `<label class="muted" style="font-size:12.5px"><input type="checkbox" id="pc-rel-doi"/> and mint its DOI${zen.sandbox ? " (sandbox)" : ""}</label>` : (zen.configured ? `<span class="muted" style="font-size:12.5px">${esc(zen.why_not || "")}</span>` : "")}<span class="muted" id="pc-rel-msg"></span></div>`}</div>` : "";
  return `${form}${rows || none}${cite}`;
}

// ── used by: datasets, dashboards, papers ──────────────────────────────────────────────
function basisText(v) {
  if (v.basis === "exact") return `made with v${v.release}`;
  if (v.basis === "contract") return v.release ? `same fields as v${v.release}` : `same fields as v${(v.candidates || []).join(" / ")}`;
  if (v.basis === "pre-release") return "an earlier declaration (pre-release)";
  return "the current declaration (unreleased)";
}
function usedBy(c) {
  const ds = c.datasets || [];
  const dash = c.dashboards || { internal: [], external: [] };
  const papers = (c.papers || {}).related || [];
  const dsRows = ds.length ? ds.map((d) => `<div class="ds-dashrow"><a href="/dataset?id=${encodeURIComponent(d.id)}"><b>${esc(d.title || d.slug || "dataset")}</b></a> <span class="muted">· ${n(d.n_papers, "paper")} · ${n(d.n_records, "record")}</span> ${badge(d.credibility)}
      ${(d.releases || []).filter((r) => r.doi).map((r) => ` <a class="badge" href="https://doi.org/${encodeURIComponent(r.doi)}" target="_blank" rel="noopener">v${r.number} DOI</a>`).join("")}${d.published_url ? ` <a href="${esc(d.published_url)}" target="_blank" rel="noopener">GitHub ↗</a>` : ""}${d.n_dashboards ? ` <span class="muted">· ${n(d.n_dashboards, "dashboard")}</span>` : ""}
      <div class="muted" style="font-size:12.5px">${(d.versions || []).map((v) => `<code>${esc(v.schema_id)}</code> ${esc(basisText(v))} · ${n(v.n_records, "record")}`).join(" · ")}</div></div>`).join("")
    : `<p class="muted" style="margin:0">No public dataset uses this preset yet.</p>`;
  const dashRows = [...dash.internal.map((x) => `<div class="ds-dashrow"><a href="${esc(x.url)}"><b>${esc(x.title || "Untitled dashboard")}</b></a> <span class="muted">· Metalens · on <a href="/dataset?id=${encodeURIComponent(x.dataset_id)}">${esc(x.dataset_title || "dataset")}</a>${x.release ? ` · shows v${x.release}` : ""}${x.author ? ` · ${esc(x.author)}` : ""}</span></div>`),
                    ...dash.external.map((x) => `<div class="ds-dashrow"><a href="${esc(x.url)}" target="_blank" rel="noopener"><b>${esc(x.title)}</b></a> <span class="muted">· ${esc((() => { try { return new URL(x.url).host; } catch { return x.url; } })())} · on <a href="/dataset?id=${encodeURIComponent(x.dataset_id)}">${esc(x.dataset_title || "dataset")}</a>${x.release ? ` · shows v${x.release}` : ""}${x.author ? ` · ${esc(x.author)}` : ""}</span></div>`)];
  const paperRows = papers.length ? papers.map((r) => `<div class="ds-dashrow">${esc(r.title)}${r.doi ? ` <a href="https://doi.org/${encodeURIComponent(r.doi)}" target="_blank" rel="noopener">doi:${esc(r.doi)}</a>` : (r.url ? ` <a href="${esc(r.url)}" target="_blank" rel="noopener">↗</a>` : "")}${r.role ? ` <span class="muted">— ${esc(r.role)}</span>` : ""}</div>`).join("") : `<p class="muted" style="margin:0">No paper named yet.</p>`;
  const primary = (c.papers || {}).n_primary ? `<p class="muted" style="font-size:12.5px;margin:6px 0 0">${n(c.papers.n_primary, "primary study")} coded with this preset sit in the <a href="${esc(c.papers.catalogue_url)}">data catalogue</a>.</p>` : "";
  return `<div class="ds-card"><div class="ds-card-h">Datasets</div>${dsRows}</div>
    <div class="ds-card"><div class="ds-card-h">Dashboards</div>${dashRows.length ? dashRows.join("") : `<p class="muted" style="margin:0">No dashboard over these datasets yet.</p>`}</div>
    <div class="ds-card"><div class="ds-card-h">Papers</div>${paperRows}${primary}</div>`;
}
function relatedRest(c, written) {
  // an entry the prose already names (by DOI, URL, companion id, title or a project number) is not repeated
  const named = (r) => (r.doi && written.includes(r.doi)) || (r.url && written.includes(r.url)) || (r.id && written.includes(`id=${r.id}`))
    || (r.title && written.includes(r.title)) || (r.title || "").split(/\D+/).some((num) => num.length >= 5 && written.includes(num));
  const rest = (c.related || []).filter((r) => !named(r));
  if (!rest.length) return "";
  const line = (r) => r.kind === "preset" ? `<a href="/preset?id=${encodeURIComponent(r.id)}">${esc(r.title)}</a>` : `${esc(r.title)}${r.doi ? ` <a href="https://doi.org/${encodeURIComponent(r.doi)}" target="_blank" rel="noopener">doi:${esc(r.doi)}</a>` : (r.url ? ` <a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.url.replace(/^https?:\/\//, ""))}</a>` : "")}`;
  return `<ul class="md-list">${rest.map((r) => `<li><span class="muted">${esc(r.kind)}:</span> ${line(r)}${r.role ? ` <span class="muted">— ${esc(r.role)}</span>` : ""}</li>`).join("")}</ul>`;
}

// ── moderator controls ─────────────────────────────────────────────────────────────────
function wireAdmin() {
  const go = $("#pc-rel-go");
  if (go) go.onclick = async () => {
    go.disabled = true; $("#pc-rel-msg").textContent = "Releasing…";
    try {
      const r = await api.createPresetRelease(ID, { notes: $("#pc-rel-notes").value, doi: !!($("#pc-rel-doi") && $("#pc-rel-doi").checked) });
      if ((r.problems || []).length) alert(r.problems.join("\n"));
      await init(ID);
    } catch (e) { $("#pc-rel-msg").textContent = "✗ " + e.message; go.disabled = false; }
  };
  body.querySelectorAll("[data-reldoi]").forEach((b) => (b.onclick = async () => {
    const zen = CARD.zenodo || {};
    if (!confirm(`Mint a DOI for version v${b.dataset.reldoi} of this preset on Zenodo${zen.sandbox ? " (sandbox: a test DOI that resolves nowhere)" : ""}? A DOI is permanent: the version's files are deposited under it and cannot be withdrawn.`)) return;
    b.disabled = true; b.textContent = "Minting…";
    try { await api.presetReleaseDoi(ID, +b.dataset.reldoi); } catch (e) { alert(`Minting the DOI failed: ${e.message}`); }
    await init(ID);
  }));
}
