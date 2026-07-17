// Catalog (tool-faithful): a dataset/record browser with full-text search + a
// facet rail, powered by the cross-dataset query API. Record detail is a client
// route (/catalog/record/{id}). The URL holds the filter state (store.js).
import { api } from "/static/api.js";
import { createStore } from "/static/store.js";
import { renderValue, renderConfidence, renderEvidenceList, esc } from "/static/grammar.js";

const app = document.getElementById("app");
const PAGE = 25;
let store;

function recordRoute() {
  const m = location.pathname.match(/^\/catalog\/record\/([0-9a-f-]+)$/i);
  return m ? m[1] : null;
}

async function route() {
  const rid = recordRoute();
  if (rid) return renderDetail(rid);
  return renderBrowse();
}

// ── browse ────────────────────────────────────────────────────────────────
async function renderBrowse() {
  const f = store.get();
  app.innerHTML = `
    <div class="searchbar">
      <input id="q" type="search" placeholder="Search records, datasets &amp; prompts…" value="${esc(f.q || "")}"/>
    </div>
    <div id="filters" class="active-filters"></div>
    <div class="catalog">
      <aside class="facets" id="facets"><p class="muted">…</p></aside>
      <div id="main"><p class="muted">Loading…</p></div>
    </div>`;
  const input = document.getElementById("q");
  let t; input.oninput = () => { clearTimeout(t); t = setTimeout(() => store.set("q", input.value.trim()), 250); };

  const [res, facets, pub] = await Promise.all([
    api.search({ ...f, limit: PAGE }), api.facets(f), api.datasetsPublic(f.q),
  ]);
  renderFilters(f);
  renderFacets(facets, f);
  renderMain(res, pub, f);
}

function renderFilters(f) {
  const el = document.getElementById("filters");
  const pills = Object.entries(f).filter(([k]) => !["offset"].includes(k)).flatMap(([k, v]) =>
    (Array.isArray(v) ? v : [v]).map((x) =>
      `<span class="fpill" data-k="${k}" data-v="${esc(x)}">${esc(k)}: ${esc(x)} ✕</span>`));
  el.innerHTML = pills.join("");
  el.querySelectorAll(".fpill").forEach((p) =>
    (p.onclick = () => store.set(p.dataset.k, null)));
}

function renderFacets(facets, f) {
  const el = document.getElementById("facets");
  const dims = [["schema", "Schema"], ["verification_status", "Status"],
                ["primary_topic", "Topic"], ["jel", "JEL"], ["year", "Year"]];
  const keyOf = { schema: "schema", verification_status: "status", primary_topic: "topic", jel: "jel", year: "year" };
  el.innerHTML = dims.map(([dim, label]) => {
    const vals = (facets[dim] || []).slice(0, 12);
    if (!vals.length) return "";
    const k = keyOf[dim];
    return `<h4>${label}</h4>` + vals.map((v) => {
      const on = String(f[k]) === String(v.value);
      return `<div class="facet-val ${on ? "on" : ""}" data-k="${k}" data-v="${esc(v.value)}">`
        + `<span>${esc(v.value)}</span><span class="c">${v.count}</span></div>`;
    }).join("");
  }).join("");
  el.querySelectorAll(".facet-val").forEach((d) =>
    (d.onclick = () => store.toggle(d.dataset.k, d.dataset.v)));
}

function renderMain(res, pub, f) {
  const main = document.getElementById("main");
  const showDatasets = !f.dataset;
  const datasets = showDatasets ? `
    <div class="section-h">Datasets</div>
    ${(pub.datasets || []).map((d) => `
      <div class="dataset-card" data-ds="${d.id}">
        <div><div class="ptitle">${esc(d.title || d.slug)}</div>
          <div class="muted" style="font-size:12px">${d.credibility.n_records} records · ${esc(d.schema_id || "")}${d.cite_as ? ` · by ${esc(d.cite_as)}` : ""}</div></div>
        <span class="badge tier-${d.credibility.tier}">${esc(d.credibility.label)}</span>
      </div>`).join("") || '<p class="muted">No public datasets yet.</p>'}` : "";

  const results = `
    <div class="section-h">Records <span class="muted">(${res.total})</span></div>
    ${res.results.map((r) => `
      <div class="result" data-rid="${r.id}">
        <div class="meta">
          <span class="status ${r.verification_status}">${r.verification_status}</span>
          <code>${esc(r.schema_id || "")}</code>
          ${r.dataset ? `<span>· ${esc(r.dataset.title || r.dataset.slug)}</span>` : ""}
          ${r.paper.year ? `<span>· ${r.paper.year}</span>` : ""}
        </div>
        <div class="ptitle">${esc(r.paper.title || "(untitled paper)")}</div>
      </div>`).join("") || '<p class="muted">No matching records.</p>'}
    ${pager(res, f)}`;

  main.innerHTML = datasets + results;
  main.querySelectorAll(".dataset-card").forEach((c) =>
    (c.onclick = () => store.set("dataset", c.dataset.ds)));
  main.querySelectorAll(".result").forEach((c) =>
    (c.onclick = () => navigate(`/catalog/record/${c.dataset.rid}`)));
  main.querySelectorAll("[data-page]").forEach((b) =>
    (b.onclick = () => store.set("offset", b.dataset.page)));
}

function pager(res, f) {
  const off = +(f.offset || 0);
  const prev = off > 0 ? `<button class="btn btn-ghost" data-page="${Math.max(0, off - PAGE)}">← Prev</button>` : "";
  const next = off + PAGE < res.total ? `<button class="btn btn-ghost" data-page="${off + PAGE}">Next →</button>` : "";
  return (prev || next) ? `<div class="pager">${prev}${next}<span class="muted">${off + 1}–${Math.min(off + PAGE, res.total)} of ${res.total}</span></div>` : "";
}

// ── record detail ───────────────────────────────────────────────────────────
async function renderDetail(rid) {
  app.innerHTML = '<p class="muted">Loading record…</p>';
  let d;
  try { d = await api.record(rid); } catch (e) { app.innerHTML = `<p class="muted">${e.message}</p>`; return; }
  const p = d.paper || {};
  const prov = (p.provenance || []).slice(0, 24).map((x) =>
    `<span class="ev-tag">${esc(x.field)}: ${esc(x.source)}/${esc(x.method)}</span>`).join("");
  app.innerHTML = `
    <p style="margin:16px 0"><a href="/catalog" id="back">← Catalog</a></p>
    <div class="card">
      <div class="meta" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
        <span class="status ${d.record.verification_status}">${d.record.verification_status}</span>
        <code>${esc(d.record.schema_id || "")}</code>
        ${d.dataset ? `<span class="muted">· ${esc(d.dataset.title || d.dataset.slug)}</span>` : ""}
      </div>
      <h2 style="margin:.2em 0">${esc(p.title || "(untitled paper)")}</h2>
      <p class="muted" style="margin:.2em 0 14px">${[p.journal, p.year, p.doi].filter(Boolean).map(esc).join(" · ")}
        ${p.oa_pdf_url ? `· <a href="${esc(p.oa_pdf_url)}" target="_blank" rel="noopener">OA PDF</a>` : ""}</p>
      <div class="verify"><button class="vbtn ok" data-status="verified">✓ verify</button>
        <button class="vbtn flag" data-status="flagged">⚑ flag</button></div>
      ${renderConfidence(d.record.field_values && d.record.field_values.extraction_confidence)}
      ${renderValue(d.record.field_values)}
      <div class="section-h">Evidence</div>
      ${renderEvidenceList(d.evidence) || '<p class="muted">No evidence spans.</p>'}
      ${prov ? `<div class="section-h">Provenance</div><div class="ev-tags">${prov}</div>` : ""}
    </div>`;
  document.getElementById("back").onclick = (e) => { e.preventDefault(); navigate("/catalog"); };
  app.querySelectorAll(".vbtn").forEach((b) => (b.onclick = async () => {
    app.querySelectorAll(".vbtn").forEach((x) => (x.disabled = true));
    try { await api.verify(rid, { status: b.dataset.status }); app.querySelector(".status").textContent = b.dataset.status; app.querySelector(".status").className = `status ${b.dataset.status}`; }
    catch (e) { alert(e.message); }
    finally { app.querySelectorAll(".vbtn").forEach((x) => (x.disabled = false)); }
  }));
}

// ── client routing ──────────────────────────────────────────────────────────
function navigate(path) { history.pushState({}, "", path); route(); }
window.addEventListener("popstate", route);

store = createStore(() => { if (!recordRoute()) renderBrowse(); });
route();
