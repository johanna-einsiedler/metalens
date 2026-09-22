// /dashboards — every published dashboard: registered ones built elsewhere, and Metalens-built ones.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
const $ = (s) => document.querySelector(s);
const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "");
const host = (u) => { try { return new URL(u).host; } catch { return u; } };

async function init() {
  const [data, me] = await Promise.all([api.publicDashboards(), api.me()]);
  const signed = !!(me && (me.email || me.local_mode));
  $("#dbs-external").innerHTML = data.external.length ? data.external.map((x) => {
    const state = x.release_shown == null ? `<span class="badge" title="${esc(x.check_note || "")}">release unknown</span>`
      : x.latest_release && x.release_shown < x.latest_release ? `<span class="badge tier-sample_verified">release v${x.release_shown} · v${x.latest_release} available</span>`
      : `<span class="badge tier-human_verified">release v${x.release_shown}</span>`;
    // no link inside the tile link: the dataset and source links sit in a footer below it
    return `<div class="proj-tile-wrap"><a class="proj-tile" href="${esc(x.url)}" target="_blank" rel="noopener"><div class="pt-title">${esc(x.title)} ↗</div>
      <div class="pt-meta">${esc(host(x.url))} · data: ${esc(x.dataset_title || "dataset")}</div>
      <div class="pt-meta">${state}${x.checked_at ? ` <span class="muted">· checked ${esc(day(x.checked_at))}</span>` : ""}</div></a>
      <div class="pt-foot"><a href="/dataset?id=${encodeURIComponent(x.dataset_id)}">dataset</a>${x.repo_url ? ` · <a href="${esc(x.repo_url)}" target="_blank" rel="noopener">source</a>` : ""}</div></div>`;
  }).join("") : `<p class="muted">None yet.</p>`;
  $("#dbs-metalens").innerHTML = data.metalens.length ? data.metalens.map((d) =>
    `<div class="proj-tile-wrap"><a class="proj-tile" href="/dashboard?id=${encodeURIComponent(d.id)}"><div class="pt-title">📊 ${esc(d.title || "Untitled dashboard")}</div>
      <div class="pt-meta">data: ${esc(d.dataset_title || "dataset")}${d.author ? ` · by ${esc(d.author)}` : ""}</div>
      <div class="pt-meta"><span class="badge tier-human_verified">release v${d.release}</span> <span class="muted">· ${d.n_blocks} block${d.n_blocks === 1 ? "" : "s"} · published ${esc(day(d.published_at))}</span></div></a></div>`).join("")
    : `<p class="muted">None yet.</p>`;
  // registering needs a dataset the visitor owns: the form asks which
  $("#dbs-actions").innerHTML = signed ? `<button type="button" class="btn btn-primary btn-sm" id="dbs-add">＋ Register a dashboard</button>`
    : `<a class="btn btn-ghost btn-sm" href="/account?next=${encodeURIComponent("/dashboards")}" title="registering needs an account and a dataset of yours">Sign in to register a dashboard</a>`;
  const add = $("#dbs-add");
  if (add) add.onclick = async () => {
    const mine = ((await api.myDatasets()).datasets || []).filter((d) => d.visibility === "public" && me && d.owner_user_id === me.id);
    const f = $("#dbs-register");
    if (!mine.length) { f.innerHTML = `<div class="ds-card"><p class="muted" style="margin:0">A dashboard is registered on the public dataset it reads. You have no public dataset yet: publish one from its dataset page (a release → GitHub / the catalogue) first.</p></div>`; return; }
    f.innerHTML = `<form class="ds-card" id="dbs-form"><div class="ds-card-h">Register a dashboard built elsewhere</div>
      <p class="muted" style="font-size:13px;margin:0 0 8px">The page must read a release of one of your public datasets and publish <code>metalens.json</code> next to its index, naming that release (a dev-kit page does). Metalens lists it and checks which release it shows.</p>
      <label style="display:block;font-size:13px">Dataset <select id="dbs-ds" style="display:block;width:100%;margin:4px 0 8px">${mine.map((d) => `<option value="${esc(d.id)}">${esc(d.title)}</option>`).join("")}</select></label>
      <input id="dbs-title" type="text" required maxlength="200" placeholder="title"/><input id="dbs-url" type="url" required placeholder="page URL, e.g. https://name.github.io/dashboard/"/><input id="dbs-repo" type="url" placeholder="source repository (optional)"/>
      <div style="display:flex;gap:8px;margin-top:10px"><button type="submit" class="btn btn-primary btn-sm">Register</button><button type="button" class="btn btn-ghost btn-sm" id="dbs-no">Cancel</button><span class="muted" id="dbs-msg"></span></div></form>`;
    f.querySelectorAll("input").forEach((i) => { i.className = "dbs-in"; });
    $("#dbs-no").onclick = () => { f.innerHTML = ""; };
    $("#dbs-form").onsubmit = async (e) => {
      e.preventDefault();
      try { await api.addExternalDashboard($("#dbs-ds").value, { title: $("#dbs-title").value, url: $("#dbs-url").value, repo_url: $("#dbs-repo").value || null }); f.innerHTML = ""; init(); }
      catch (ex) { $("#dbs-msg").textContent = ex.message; }
    };
  };
}
init().catch((e) => { $("#dbs-external").innerHTML = `<p class="muted">Could not load: ${esc(e.message)}</p>`; });
