// PaperLens observatory — a saved view rendered as a chart. The view re-queries
// records on every load, so adding/verifying papers recomputes it (no per-view code).
// Vanilla, no build step.

const $ = (s, el = document) => el.querySelector(s);
const api = (p) => fetch(p).then((r) => {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
});

let CURRENT = null;

async function init() {
  const picker = $("#viewpicker");
  try {
    const { views } = await api("/api/views");
    if (!views.length) {
      picker.innerHTML = "<option>(no saved views)</option>";
      $("#summary").innerHTML =
        'No saved views yet. Create one, e.g.:<pre class="fv">curl -X POST ' +
        'localhost:8000/api/views -H "content-type: application/json" -d \'' +
        '{"title":"Studies by design","dataset_ids":["&lt;id&gt;"],' +
        '"viz_config":{"kind":"bar","group_by":"design","measure":"count"}}\'</pre>';
      return;
    }
    picker.innerHTML = views.map((v) =>
      `<option value="${v.id}">${escapeHtml(v.title)} · ${v.view_type}</option>`).join("");
    picker.onchange = () => load(picker.value);
    $("#refresh").onclick = () => CURRENT && load(CURRENT);
    // honor a ?view=<id> deep-link (e.g. an Analysis tile from My Workspace)
    const want = new URLSearchParams(location.search).get("view");
    const start = views.some((v) => v.id === want) ? want : views[0].id;
    picker.value = start;
    load(start);
  } catch (e) {
    picker.innerHTML = `<option>error: ${e.message}</option>`;
  }
}

async function load(id) {
  CURRENT = id;
  const d = await api(`/api/views/${id}/data`);
  const of = d.measure === "mean" && d.value_field
    ? ` of <code>${escapeHtml(d.value_field)}</code>` : "";
  $("#summary").innerHTML =
    `<b>${escapeHtml(d.title)}</b> — ${d.measure} by <code>${escapeHtml(d.group_by || "—")}</code>${of}` +
    ` · ${d.total_records} records · ${d.series.length} groups`;
  renderChart(d);
  renderTable(d);
  $("#attrib").textContent = "Data: PaperLens · recomputed live from records.";
}

function renderChart(d) {
  const s = d.series, chart = $("#chart");
  if (!s.length) { chart.innerHTML = '<p class="muted">No matching records.</p>'; return; }
  const max = Math.max(...s.map((x) => x.value || 0), 1);
  const rowH = 28, labelW = 180, valW = 64, barMax = 460, h = s.length * rowH + 10;
  const rows = s.map((x, i) => {
    const bw = Math.max(2, ((x.value || 0) / max) * barMax);
    const y = i * rowH + 5;
    return `<text x="${labelW - 8}" y="${y + 18}" text-anchor="end" class="blabel">` +
             `${escapeHtml(trunc(x.group, 26))}</text>` +
           `<rect x="${labelW}" y="${y + 4}" width="${bw}" height="18" class="bar"></rect>` +
           `<text x="${labelW + bw + 6}" y="${y + 18}" class="bval">${x.value}</text>`;
  }).join("");
  chart.innerHTML = `<svg width="${labelW + barMax + valW}" height="${h}">${rows}</svg>`;
}

function renderTable(d) {
  const t = $("#tbl");
  if (!d.series.length) { t.innerHTML = ""; return; }
  t.innerHTML =
    `<thead><tr><th>${escapeHtml(d.group_by || "group")}</th>` +
    `<th>${escapeHtml(d.measure)}</th><th>n</th></tr></thead>` +
    `<tbody>${d.series.map((x) =>
      `<tr><td>${escapeHtml(x.group)}</td><td>${x.value}</td><td>${x.n}</td></tr>`).join("")}</tbody>`;
}

function trunc(s, n) { s = String(s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

init();
