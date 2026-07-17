// Render a saved dashboard analysis (?view=<id>): the saved figures over live rows.
// Recomputes on every load (rows are fetched fresh), so adding papers updates it.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { renderDashboard } from "/static/figures.js";
import { createTrace } from "/static/trace.js";
import { renderComingSoon } from "/static/coming-soon.js";

const $ = (s) => document.querySelector(s);

// Saved dashboards are disabled in the beta; render a placeholder so old
// /analysis?view=… deep-links don't error. Renderer kept for later re-enable.
async function init() {
  renderComingSoon(document.querySelector("main"), {
    title: "Dashboards are coming soon",
    note: "Saved analyses aren’t viewable in the beta yet. The extracted data behind them is still available under Data review.",
  });
}
// eslint-disable-next-line no-unused-vars
async function _initAnalysis() {
  const id = new URLSearchParams(location.search).get("view");
  if (!id) { $("#dash").innerHTML = '<p class="muted">No analysis id.</p>'; return; }
  let view, rowsResp;
  try {
    [view, rowsResp] = await Promise.all([api.viewGet(id), api.analysisRows(id)]);
  } catch (e) { $("#dash").innerHTML = `<p class="muted">Couldn’t load this analysis: ${esc(e.message)}</p>`; return; }
  const cfg = view.viz_config || {};
  // apply the saved "vibe" palette, if any
  if (cfg.theme && cfg.theme.vibe) document.body.setAttribute("data-vibe", cfg.theme.vibe);

  const raw = cfg.proposal && cfg.proposal.raw;
  let pretty = raw;
  try { pretty = JSON.stringify(JSON.parse(raw), null, 2); } catch { /* as-is */ }
  $("#ahead").innerHTML = `<h2 style="margin:.2em 0 4px">${esc(view.title || "Analysis")}</h2>`
    + (cfg.goals ? `<p class="muted" style="margin:0 0 10px">${esc(cfg.goals)}</p>` : "")
    + (raw ? `<details class="raw-details"><summary>AI proposal (raw JSON)`
        + `${cfg.proposal.model ? ` · ${esc(cfg.proposal.model)}` : ""}</summary>`
        + `<pre class="raw-json">${esc(pretty)}</pre></details>` : "");
  const rows = (rowsResp && rowsResp.rows) || [];
  // traceability: hover → provenance tooltip, click → data review, ⊞ Data → the table
  let panel = $("#trace-panel");
  if (!panel) { panel = document.createElement("div"); panel.id = "trace-panel"; panel.className = "trace-panel"; panel.hidden = true; $("#dash").after(panel); }
  const trace = createTrace(rows, panel, { navigate: true });
  renderDashboard($("#dash"), cfg, rows, trace.opts);
}

init();
