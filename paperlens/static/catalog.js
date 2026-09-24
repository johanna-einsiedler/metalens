// Data Catalogue: the published datasets, one card each, with a search over their title,
// description and recipe, and the "is this paper included?" check. Records are read inside a
// dataset (its page), not here.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";

const app = document.getElementById("app");

async function render() {
  const q = new URLSearchParams(location.search).get("q") || "";
  app.innerHTML = `
    <div class="searchbar">
      <input id="q" type="search" placeholder="Search datasets…" value="${esc(q)}"/>
    </div>
    <div class="paper-check">
      <input id="pq" type="search" placeholder="Is a paper included? Paste a DOI or words of the title…"/>
      <div id="pq-out" class="muted"></div>
    </div>
    <div id="main" class="catalog-list"><p class="muted">Loading…</p></div>`;
  const input = document.getElementById("q");
  let t; input.oninput = () => { clearTimeout(t); t = setTimeout(() => setQuery(input.value.trim()), 250); };
  const pq = document.getElementById("pq"), pqOut = document.getElementById("pq-out");
  let pt; pq.oninput = () => { clearTimeout(pt); pt = setTimeout(() => paperCheck(pq.value.trim(), pqOut), 300); };
  renderDatasets(await api.datasetsPublic(q || undefined));
}

function setQuery(q) {
  const u = new URL(location.href);
  if (q) u.searchParams.set("q", q); else u.searchParams.delete("q");
  history.replaceState({}, "", u);
  api.datasetsPublic(q || undefined).then(renderDatasets);
}

// the coverage check: which published datasets contain this paper (DOI or title words)
async function paperCheck(q, out) {
  if (!q) { out.innerHTML = ""; return; }
  let hits = [];
  try { hits = (await api.paperCoverage(q)).papers || []; } catch { out.textContent = "lookup failed"; return; }
  if (!hits.length) { out.innerHTML = `No paper matching “${esc(q)}” is in a published dataset.`; return; }
  out.innerHTML = hits.slice(0, 8).map((p) => {
    const ref = `${esc((p.authors || []).slice(0, 3).join(", ") || "")}${(p.authors || []).length > 3 ? " et al." : ""} (${esc(String(p.year || "n.d."))}). ${esc(p.title || "")}`;
    const where = p.datasets.length
      ? "in " + p.datasets.map((d) => `<a href="/dataset?id=${esc(d.id)}">${esc(d.title || d.slug)}</a> (${d.n_records} record${d.n_records === 1 ? "" : "s"})`).join(", ")
      : "known here, but not in a published dataset";
    return `<div class="pq-hit">${ref}<br/><span class="muted">${where}</span></div>`;
  }).join("");
}

// record count, preset, author, GitHub — only the parts this dataset actually has, so a missing
// preset never shows up as a gap between two separators
function metaLine(d) {
  const bits = [`${d.credibility.n_records} record${d.credibility.n_records === 1 ? "" : "s"}`];
  if (d.schema_id) bits.push(esc(d.schema_id));
  if (d.cite_as) bits.push(`by ${esc(d.cite_as)}`);
  if (d.published_url) bits.push(`<a href="${esc(d.published_url)}" target="_blank" rel="noopener">GitHub ↗</a>`);
  return bits.join(" · ");
}

function renderDatasets(pub) {
  const main = document.getElementById("main");
  main.innerHTML = `<div class="section-h">Datasets <span class="muted">(${(pub.datasets || []).length})</span></div>`
    + ((pub.datasets || []).map((d) => `
      <div class="dataset-card" data-href="/dataset?id=${esc(d.id)}" role="link" tabindex="0">
        <div><div class="ptitle">${esc(d.title || d.slug)}</div>
          ${d.description ? `<div class="ds-desc">${esc(d.description)}</div>` : ""}
          <div class="ds-meta">${metaLine(d)}</div>
          ${(d.keywords || []).length ? `<div class="ds-kws">${(d.keywords || []).map((k) => `<span class="kw">${esc(k)}</span>`).join(" ")}</div>` : ""}</div>
        <span class="badge tier-${d.credibility.tier}">${esc(d.credibility.label)}</span>
      </div>`).join("") || '<p class="muted">No public datasets yet.</p>');
  // the card is the link (a GitHub link sits inside it, so it cannot be an <a> itself)
  main.querySelectorAll(".dataset-card").forEach((c) => {
    c.onclick = (e) => { if (!e.target.closest("a")) location.href = c.dataset.href; };
    c.onkeydown = (e) => { if (e.key === "Enter") location.href = c.dataset.href; };
  });
}

render();
