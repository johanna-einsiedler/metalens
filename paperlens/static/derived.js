// /derived?dataset=…[&step=<step>][&id=<version>][&run=<run>] — a dataset's derived units.
// Start view: the derive steps of the workflow with their state and a form to make the next draft
// (by hand, or to give a waiting run the key it asks for). Review view: a version as the change it
// proposes against the committed one before it — per row a computed verdict (kept / revised / new /
// removed), the cells that changed, the sources split into the NEW records that triggered the change
// and carried ones — with accept / reject per row, inline edits, and the commit.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { modelChoice, renderModelPanel } from "/static/modelpanel.js";

const $ = (s) => document.querySelector(s);
const params = new URLSearchParams(location.search);
const DATASET_ID = params.get("dataset");
let DID = params.get("id"), STEP = params.get("step"), RUN = params.get("run");
let DS = null, CFG = null, MODELS = {}, LIST = null;
const PANEL = { get prefix() { return "dv"; }, get cfg() { return CFG; }, get models() { return MODELS; }, creditsNote: "one per model call",
                note: "The model sees the step's input rows — the dataset's own values, never the PDFs — and the committed rows. It proposes; you review the change row by row." };
const fmt = (v) => v == null || v === "" ? "" : Array.isArray(v) ? v.map((x) => String(x)).join("; ") : typeof v === "object" ? JSON.stringify(v) : String(v);
const STATE = { fresh: ["tier-human_verified", "fresh"], stale: ["tier-sample_verified", "stale"], missing: ["", "not yet"], draft: ["tier-sample_verified", "draft"], blocked: ["", "blocked"] };
const chip = (state, title) => { const [cls, label] = STATE[state] || ["", state]; return `<span class="badge ${cls}" title="${esc(title || "")}">${esc(label)}</span>`; };

// Where a run continues: the workflow dataset when this dataset is one side of one, else this dataset.
const runHome = () => `/dataset?id=${encodeURIComponent((DS && DS.group_id) || DATASET_ID)}#workflow`;

async function init() {
  if (!DATASET_ID) { $("#dv-app").innerHTML = `<p class="muted">Open this page from a dataset.</p>`; return; }
  $("#dv-back").href = `/dataset?id=${encodeURIComponent(DATASET_ID)}`;
  try {
    [DS, CFG, MODELS] = await Promise.all([api.dataset(DATASET_ID), api.extractionConfig().catch(() => null), api.models().then((m) => m.providers || {}).catch(() => ({}))]);
  } catch (e) { $("#dv-app").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  $("#dv-title").textContent = `· ${DS.title || ""}`;
  if (DID) await openVersion(DID); else await renderStart();
}

// ── start: the steps, their versions, and the next draft ──────────────────────────────────
async function renderStart() {
  try { LIST = await api.derivedList(DATASET_ID); } catch (e) { $("#dv-app").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  const host = $("#dv-app"), mine = LIST.owner;
  const steps = LIST.steps || [];
  if (!steps.length) { host.innerHTML = `<p class="muted">The dataset's workflow declares no derive step.${mine ? " Declare one in the workflow (docs/WORKFLOWS.md) and it appears here." : ""}</p>`; return; }
  const run = LIST.active_run, needsKey = run && run.status.startsWith("needs_key:") ? run.status.split(":")[1] : null;
  const versions = (s) => (LIST.versions || []).filter((v) => v.step === s.id).sort((a, b) => b.version - a.version);
  host.innerHTML = steps.map((s) => `<section class="ws-section"><div class="ws-head" style="align-items:center;gap:8px"><h3 style="margin:0">${esc(s.label || s.id)} <code class="muted">${esc(s.id)}</code></h3>
      ${chip(s.state, (s.because || []).join("; "))}${needsKey === s.id ? `<span class="badge tier-sample_verified">the run waits for a key</span>` : ""}</div>
    ${s.help ? `<p class="muted" style="margin:4px 0">${esc(s.help)}</p>` : ""}
    <p class="muted" style="font-size:12.5px;margin:2px 0 6px">over <code>${esc(s.over)}</code> (${esc((s.input || {}).unit || "entries")}) · identity ${(s.identity || []).map((x) => `<code>${esc(x)}</code>`).join(", ")} ·
      ${(s.calls || []).map((c) => c.kind === "llm" ? `model call <code>${esc(c.id || "")}</code>` : `${esc(c.op || "transform")} by ${(c.by || []).join(", ")}`).join(" → ")}
      · review ${esc((s.review || {}).mode || "all")}${(s.review || {}).blocks === "next" ? ", holds the run" : ""}${s.because && s.because.length ? ` — ${esc(s.because.join("; "))}` : ""}</p>
    ${versions(s).length ? versions(s).map((v) => `<div class="ds-dashrow"><a href="/derived?dataset=${encodeURIComponent(DATASET_ID)}&step=${encodeURIComponent(s.id)}&id=${esc(v.id)}"><b>v${v.version}</b></a>
        <span class="badge ${v.status === "committed" ? "tier-human_verified" : "tier-sample_verified"}">${esc(v.status)}</span>
        <span class="muted">· ${v.n_rows} row${v.n_rows === 1 ? "" : "s"}${v.model ? ` · ${esc(v.model)}` : " · deterministic"} · ${esc((v.committed_at || v.created_at || "").slice(0, 10))}${v.run_id ? " · from a run" : ""}</span></div>`).join("")
      : `<p class="muted">No version yet.</p>`}
    ${mine ? `<div class="ds-relform" style="margin-top:8px" data-step="${esc(s.id)}">
        <div class="muted dv-plan" style="font-size:12.5px">planning…</div>
        <div class="dv-model" style="margin-top:6px"${s.llm_calls ? "" : " hidden"}></div>
        <div style="display:flex;gap:8px;margin-top:8px;align-items:center"><button type="button" class="btn btn-primary btn-sm dv-go">${needsKey === s.id ? "Continue the run with this key" : s.state === "draft" ? "Make a new draft" : "Propose"}</button>
        <span class="muted dv-msg" style="font-size:12.5px"></span></div></div>` : ""}</section>`).join("");
  if (!mine) return;
  for (const box of host.querySelectorAll(".ds-relform[data-step]")) {
    const sid = box.dataset.step, s = steps.find((x) => x.id === sid);
    if (s.llm_calls) renderModelPanel(box.querySelector(".dv-model"), { ...PANEL, prefix: `dv-${sid}` });
    api.derivedPlan(DATASET_ID, sid).then((p) => {
      box.querySelector(".dv-plan").textContent = `${p.n_rows} input row${p.n_rows === 1 ? "" : "s"}${p.n_new ? ` (${p.n_new} new)` : ""} · ${p.llm_calls ? `${p.n_calls} model call${p.n_calls === 1 ? "" : "s"} in ${p.n_chunks} chunk${p.n_chunks === 1 ? "" : "s"}` : "deterministic, free"}${p.over_budget ? " — over the limit: narrow the input" : ""}`;
    }).catch((e) => { box.querySelector(".dv-plan").textContent = e.message; });
    box.querySelector(".dv-go").onclick = async () => {
      const msg = box.querySelector(".dv-msg"), go = box.querySelector(".dv-go");
      const choice = s.llm_calls ? modelChoice(`dv-${sid}`) : {};
      if (choice === null) return;
      go.disabled = true; msg.textContent = needsKey === sid ? "running the step…" : "making the draft…";
      try {
        if (needsKey === sid) {
          await api.workflowResume(run.id, { step_id: sid, ...choice });
          location.href = runHome();
          return;
        }
        const r = await api.derivedPropose(DATASET_ID, sid, choice);
        if (!r.ok) { msg.textContent = `The proposal failed: ${r.error || "unknown error"}`; go.disabled = false; return; }
        location.href = `/derived?dataset=${encodeURIComponent(DATASET_ID)}&step=${encodeURIComponent(sid)}&id=${encodeURIComponent(r.derived.derived.id)}`;
      } catch (e) { msg.textContent = e.message; go.disabled = false; }
    };
  }
}

// ── one version, as the change it proposes ────────────────────────────────────────────────
async function openVersion(did) {
  DID = did;
  let r; try { r = await api.derivedReview(did); } catch (e) { $("#dv-app").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  const d = r.derived, st = r.step || {}, draft = d.status === "draft", mine = r.owner, labels = r.labels || {};
  const fields = ((st.output || {}).fields || []).map((f) => f.name);
  const decisions = { ...(d.decisions || {}) };
  const host = $("#dv-app");
  const sum = r.summary || {};
  const verdictHelp = { kept: "unchanged from the committed version", revised: "changed — the highlighted records moved it", new: "a row the committed version did not have", removed: "a committed row this draft drops" };
  const srcChip = (id, trigger) => { const l = labels[id] || {}; return `<a class="pg-chip${trigger ? " trigger" : ""}" title="${esc(id)}" href="/workspace?project=${encodeURIComponent(DATASET_ID)}${l.document_id ? `&doc=${esc(l.document_id)}` : ""}">${esc(l.label || id.slice(0, 8))}</a>`; };
  const cell = (x, f) => {
    const v = x.row ? x.row[f] : null, o = x.old ? x.old[f] : null, changed = x.verdict === "revised" && x.changed.includes(f);
    if (x.verdict === "revised" && changed) return `<td class="dv-cell changed" data-f="${esc(f)}"><del>${esc(fmt(o))}</del> <ins>${esc(fmt(v))}</ins></td>`;
    return `<td class="dv-cell" data-f="${esc(f)}">${esc(fmt(x.verdict === "removed" ? o : v))}</td>`;
  };
  host.innerHTML = `<div class="ws-head" style="align-items:center;gap:10px">
      <h3 style="margin:0">${esc(st.label || d.step)} <span class="muted">· <code>${esc(d.step)}</code> · v${d.version}</span></h3>
      <span class="badge ${d.status === "committed" ? "tier-human_verified" : "tier-sample_verified"}">${esc(d.status)}</span>
      <span style="margin-left:auto"></span>
      ${draft && mine ? `<button type="button" class="btn btn-primary btn-sm" id="dv-commit">Commit v${d.version}</button>
                         <button type="button" class="btn btn-ghost btn-sm" id="dv-del">Delete draft</button>` : ""}
      <a class="btn btn-ghost btn-sm" href="/derived?dataset=${encodeURIComponent(DATASET_ID)}">All derived units</a></div>
    <p class="muted" style="font-size:13px;margin:4px 0">${d.rows.length} row${d.rows.length === 1 ? "" : "s"}${d.model ? ` · proposed by ${esc(d.model)}` : " · deterministic"}${d.run_id ? " · made by a workflow run" : ""}${(d.built_from || {}).n_new ? ` · ${d.built_from.n_new} record${d.built_from.n_new === 1 ? "" : "s"} new since v${d.built_from.derived_version_from}` : ""}${(r.repairs || []).length ? ` · <span title="${esc(r.repairs.join("\n"))}">${r.repairs.length} repair${r.repairs.length === 1 ? "" : "s"} made to the answer</span>` : ""}</p>
    ${r.committed && draft ? `<p class="muted" style="font-size:12.5px">Reviewed against committed v${r.committed.version}: verdicts and the changed cells are computed; the highlighted chips are the records added since that version — the evidence that triggered each change.</p>` : ""}
    <div class="dv-filter" style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0">
      ${["kept", "revised", "new", "removed"].map((k) => `<span class="badge pg-verdict ${k} on" data-filter="${k}">${k} ${sum[k] || 0}</span>`).join("")}
      ${sum.unexplained ? `<span class="badge pg-verdict unexplained on" data-filter="unexplained">no new evidence cited ${sum.unexplained}</span>` : ""}
      ${draft && mine ? `<span style="margin-left:auto"></span><button type="button" class="btn btn-ghost btn-sm" id="dv-accept-shown">Accept all shown</button>` : ""}</div>
    <div style="overflow-x:auto"><table class="dv-table"><thead><tr><th>Verdict</th>${fields.map((f) => `<th>${esc(f)}</th>`).join("")}<th>Sources</th>${draft && mine ? "<th></th>" : ""}</tr></thead>
    <tbody>${(r.diff || []).map((x, i) => `<tr class="dv-row ${esc(x.verdict)}${decisions[x._id] === "reject" ? " rejected" : ""}" data-i="${i}" data-id="${esc(x._id || "")}" data-verdict="${esc(x.verdict)}" data-unexplained="${x.unexplained ? 1 : 0}">
        <td><span class="pg-verdict ${esc(x.verdict)}" title="${esc(verdictHelp[x.verdict] || "")}">${esc(x.verdict)}</span>${x.unexplained ? `<br><span class="pg-verdict unexplained" title="revised or added, but no record new since the committed version is among its sources">no new evidence</span>` : ""}</td>
        ${fields.map((f) => cell(x, f)).join("")}
        <td><div class="dv-src">${(x.sources_new || []).map((s) => srcChip(s, true)).join("")}${(x.sources_carried || []).map((s) => srcChip(s, false)).join("")}</div></td>
        ${draft && mine ? `<td class="dv-decide">${x.verdict === "removed" ? "" : `<button type="button" data-accept="${esc(x._id)}" class="btn btn-ghost btn-sm${decisions[x._id] === "accept" ? " on" : ""}">✓</button> <button type="button" data-reject="${esc(x._id)}" class="btn btn-ghost btn-sm${decisions[x._id] === "reject" ? " on" : ""}">✗</button> <button type="button" data-edit="${i}" class="btn btn-ghost btn-sm">✎</button>`}</td>` : ""}
      </tr>`).join("")}</tbody></table></div>`;
  // filters
  const applyFilter = () => {
    const on = new Set([...host.querySelectorAll("[data-filter].on")].map((b) => b.dataset.filter));
    host.querySelectorAll(".dv-row").forEach((tr) => { tr.classList.toggle("hidden", !(on.has(tr.dataset.verdict) || (on.has("unexplained") && tr.dataset.unexplained === "1"))); });
  };
  host.querySelectorAll("[data-filter]").forEach((b) => (b.onclick = () => { b.classList.toggle("on"); applyFilter(); }));
  if (!draft || !mine) return;
  const saveDecisions = async () => { try { await api.saveDerived(did, { decisions }); } catch (e) { alert("save failed: " + e.message); } };
  host.querySelectorAll("[data-accept]").forEach((b) => (b.onclick = async () => { decisions[b.dataset.accept] = "accept"; await saveDecisions(); openVersion(did); }));
  host.querySelectorAll("[data-reject]").forEach((b) => (b.onclick = async () => { decisions[b.dataset.reject] = "reject"; await saveDecisions(); openVersion(did); }));
  const acceptShown = $("#dv-accept-shown");
  if (acceptShown) acceptShown.onclick = async () => {
    host.querySelectorAll(".dv-row:not(.hidden)").forEach((tr) => { if (tr.dataset.verdict !== "removed" && tr.dataset.id) decisions[tr.dataset.id] = "accept"; });
    await saveDecisions(); openVersion(did);
  };
  host.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => {
    const tr = b.closest("tr"), x = r.diff[+b.dataset.edit];
    tr.querySelectorAll(".dv-cell").forEach((td) => { const f = td.dataset.f; td.innerHTML = `<input value="${esc(fmt(x.row[f]))}" data-f="${esc(f)}"/>`; });
    b.textContent = "save"; b.onclick = async () => {
      const rows = d.rows.map((row) => row._id === x._id ? { ...row, ...Object.fromEntries([...tr.querySelectorAll("input[data-f]")].map((inp) => [inp.dataset.f, inp.value])) } : row);
      try { await api.saveDerived(did, { rows }); openVersion(did); } catch (e) { alert("save failed: " + e.message); }
    };
  }));
  $("#dv-commit").onclick = async () => {
    const rejected = Object.values(decisions).filter((v) => v === "reject").length;
    const unexplained = (r.diff || []).filter((x) => x.unexplained && decisions[x._id] !== "reject").length;
    if (unexplained && !confirm(`${unexplained} row${unexplained === 1 ? "" : "s"} cite no new evidence for their change and are not rejected. Commit anyway?`)) return;
    try { await api.commitDerived(did); if (RUN) location.href = runHome(); else openVersion(did); } catch (e) { alert(e.message); }
    void rejected;
  };
  $("#dv-del").onclick = async () => {
    if (!confirm("Delete this draft? Committed versions stay.")) return;
    try { await api.deleteDerived(did); location.href = `/derived?dataset=${encodeURIComponent(DATASET_ID)}`; } catch (e) { alert(e.message); }
  };
}

init();
