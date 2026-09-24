// /dashboards — every published dashboard: registered ones built elsewhere, and Metalens-built ones.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { iconSvg } from "/static/dash/icons.js";
const $ = (s) => document.querySelector(s);
const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "");
const host = (u) => { try { return new URL(u).host; } catch { return u; } };

// The tile picture: the image the owner supplied if there is one, else whatever the page's own
// manifest names. An owner's image is stable, so only the manifest's is cache-busted by the check.
function tileImg(x) {
  const own = x.preview_override, url = own || x.tile_url || x.preview_url;
  if (!url) return `<div class="tile-ph">${iconSvg("forest", {})}</div>`;
  const src = own ? url : url + (url.includes("?") ? "&" : "?") + "v=" + encodeURIComponent(x.checked_at || "");
  return `<img src="${esc(src)}" alt="" loading="lazy"/>`;
}

async function init() {
  const [data, me] = await Promise.all([api.publicDashboards(), api.me()]);
  const signed = !!(me && (me.email || me.local_mode));
  const papers = (n) => (n == null ? "" : ` · ${n} paper${n === 1 ? "" : "s"}`);
  const kicker = (x) => x.release_shown == null ? `<span class="tile-k" title="${esc(x.check_note || "")}">Dashboard · release unknown</span>`
    : `<span class="tile-k">Dashboard · release v${x.release_shown}${papers(x.n_papers)}${x.latest_release && x.release_shown < x.latest_release ? ` <span class="tile-new">v${x.latest_release} available</span>` : ""}</span>`;
  const kws = (list) => ((list || []).length ? `<div class="tile-kws">${list.slice(0, 7).map((k) => `<span class="kw">${esc(k)}</span>`).join("")}</div>` : "");
  $("#dbs-external").innerHTML = data.external.length ? data.external.map((x) =>
    `<article class="tile"><a class="tile-a" href="${esc(x.url)}" target="_blank" rel="noopener">
      <div class="tile-img">${tileImg(x)}</div>
      <div class="tile-body">${kicker(x)}<h3 class="tile-h">${esc(x.title)}</h3>${kws(x.keywords)}
      ${x.description ? `<p class="tile-p">${esc(x.description)}</p>` : ""}
      ${x.authors ? `<p class="tile-by">${esc(x.authors)}</p>` : ""}</div></a>
      <div class="tile-foot"><span>${esc(host(x.url))}</span> · <a href="/dataset?id=${encodeURIComponent(x.dataset_id)}">${esc(x.dataset_title || "dataset")}</a>${x.repo_url ? ` · <a href="${esc(x.repo_url)}" target="_blank" rel="noopener">source</a>` : ""}${x.checked_at ? ` · checked ${esc(day(x.checked_at))}` : ""}</div></article>`).join("")
    : `<p class="muted">None yet.</p>`;
  $("#dbs-metalens").innerHTML = data.metalens.length ? data.metalens.map((d) =>
    `<article class="tile"><a class="tile-a" href="/dashboard?id=${encodeURIComponent(d.id)}">
      <div class="tile-img tile-sketch">${iconSvg((d.preview || {}).icon || "rows_table", { x: (d.preview || {}).title || "" })}</div>
      <div class="tile-body"><span class="tile-k">Dashboard · release v${d.release}${papers(d.n_papers)} · ${d.n_blocks} block${d.n_blocks === 1 ? "" : "s"}</span><h3 class="tile-h">${esc(d.title || "Untitled dashboard")}</h3>${kws(d.keywords)}
      ${d.description ? `<p class="tile-p">${esc(d.description)}</p>` : ""}${d.author ? `<p class="tile-by">${esc(d.author)}</p>` : ""}</div></a>
      <div class="tile-foot"><span>Metalens</span> · <a href="/dataset?id=${encodeURIComponent(d.dataset_id)}">${esc(d.dataset_title || "dataset")}</a> · published ${esc(day(d.published_at))}</div></article>`).join("")
    : `<p class="muted">None yet.</p>`;
  if (me && me.is_admin) renderQueue();
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

// ── the moderation queue, for the accounts named in PAPERLENS_ADMINS ──────────────────────────
// A registration points at someone else's page, and listing it here is Metalens vouching for it,
// so nothing reaches the public page until one of them says so.
async function renderQueue() {
  let pending = [];
  try { pending = (await api.pendingExternalDashboards()).dashboards || []; } catch { return; }
  const box = $("#dbs-queue");
  if (!box) return;
  box.hidden = false;
  box.innerHTML = `<div class="section-h">Awaiting approval <span class="muted">(${pending.length})</span></div>`
    + (pending.length ? pending.map((x) => `<div class="q-row">
        <div><a href="${esc(x.url)}" target="_blank" rel="noopener"><b>${esc(x.title)}</b> ↗</a>
          <div class="muted">${esc(host(x.url))} · <a href="/dataset?id=${encodeURIComponent(x.dataset_id)}">the dataset</a>
            · registered ${esc(day(x.created_at))}${x.check_note ? ` · ${esc(x.check_note)}` : ""}</div></div>
        <button type="button" class="btn btn-primary btn-sm" data-approve="${esc(x.id)}">List it</button>
      </div>`).join("") : `<p class="muted">Nothing waiting.</p>`);
  box.querySelectorAll("[data-approve]").forEach((b) => (b.onclick = async () => {
    b.disabled = true; b.textContent = "listing…";
    try { await api.approveExternalDashboard(b.dataset.approve, true); location.reload(); }
    catch (ex) { b.disabled = false; b.textContent = "List it"; alert(ex.message); }
  }));
}
