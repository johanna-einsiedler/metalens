// A review TEMPLATE in the workspace (paperlens/templates.py): a stage's BLOCKS over UNITS — the
// paper, the entries, a child's rows, a paper table's rows, the distinct values of a column — each
// drawn as a card: the unit's title (with its marks and its page tag), the fields the block shows
// (editable into the coder's coding, read-only when the block masks numbers), then the QUESTIONS
// the coder answers (saved into their coding under the unit's key), and for the owner every
// coder's answers under them. Unit keys are the server's: "" (the paper, or the entry in its
// record), "<child>#<_rid>", "<table>#<_rid>", "<path>.<field>=<value>", "<unit>#+<id>" (added).
import { renderFields, esc, formatKey } from "/static/grammar.js";
import { entryTitle, childTitle } from "/static/spec.js";

const IDENTITY = ["title", "doi", "year", "authors", "journal"];
const empty = (v) => v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length);
const rid = (row, j) => (row && row._rid != null ? String(row._rid) : `i${j}`);

export function unitLabel(unit) { return typeof unit === "object" && unit ? String(unit.distinct || "").split(".").pop() : String(unit || "").replace("paper_metadata.", ""); }

// ("paper" | "entries" | "child" | "table" | "distinct"), the key and the field a unit names
export function unitKind(unit, vm) {
  const kids = new Set((vm.entries.children || []).map((c) => c.key));
  const tables = new Set(vm.paper.fields.filter((f) => f.type === "table").map((f) => f.name));
  if (unit === "paper") return { kind: "paper" };
  if (unit === "entries") return { kind: "entries" };
  if (typeof unit === "string" && kids.has(unit)) return { kind: "child", key: unit };
  if (typeof unit === "string" && unit.startsWith("paper_metadata.") && tables.has(unit.slice(15))) return { kind: "table", key: unit.slice(15) };
  if (unit && typeof unit === "object" && typeof unit.distinct === "string") {
    const [x, field] = unit.distinct.split(".");
    if (kids.has(x) || tables.has(x)) return { kind: "distinct", key: x, field, source: kids.has(x) ? "child" : "table" };
  }
  return null;
}

// the declarations of a unit's fields, by name
export function unitFields(uk, vm) {
  if (!uk) return new Map();
  if (uk.kind === "paper") return new Map([...IDENTITY.map((n) => [n, { name: n, type: n === "authors" ? "list" : n === "year" ? "integer" : "string", label: formatKey(n) }]), ...vm.paper.fields.map((f) => [f.name, f])]);
  if (uk.kind === "entries") return new Map(vm.entries.fields.map((f) => [f.name, f]));
  if (uk.kind === "child") { const ch = vm.entries.children.find((c) => c.key === uk.key); return new Map(((ch && ch.fields) || []).map((f) => [f.name, f])); }
  if (uk.kind === "table") { const t = vm.paper.fields.find((f) => f.name === uk.key); return new Map(((t && t.columns) || []).map((c) => [c.name, c])); }
  if (uk.kind === "distinct") {
    const src = unitFields({ kind: uk.source, key: uk.key }, vm);
    return new Map([[uk.field, src.get(uk.field) || { name: uk.field, type: "string" }], ["n", { name: "n", type: "integer", label: "Rows" }]]);
  }
  return new Map();
}

function lookup(row, key, paper) {                          // an absent value is null, as the server reads it
  const v = key.startsWith("paper_metadata.") ? (paper || {})[key.slice(15)] : key.startsWith("paper.") ? (paper || {})[key.slice(6)] : (row || {})[key];
  return v === undefined ? null : v;
}
// the small where grammar, as the server reads it (paperlens/templates.py where_match)
export function whereMatch(row, where, paper, sampled) {
  if (!where) return true;
  for (const [key, cond] of Object.entries(where)) {
    const v = lookup(row, key, paper);
    if (cond === null || typeof cond !== "object" || Array.isArray(cond)) { if (!(v === cond || (v == null && cond == null))) return false; continue; }
    for (const [op, arg] of Object.entries(cond)) {
      const isEmpty = v == null || v === "" || (Array.isArray(v) && !v.length);
      if (op === "eq" && v !== arg) return false;
      if (op === "in" && !(arg || []).includes(v)) return false;
      if (op === "not_in" && (arg || []).includes(v)) return false;
      if (op === "not_null" && isEmpty === !!arg) return false;
      if (op === "null" && isEmpty !== !!arg) return false;
      if (["gt", "gte", "lt", "lte"].includes(op)) {
        const x = Number(v), y = Number(arg);
        if (v == null || v === "" || isNaN(x) || isNaN(y)) return false;
        if ((op === "gt" && !(x > y)) || (op === "gte" && !(x >= y)) || (op === "lt" && !(x < y)) || (op === "lte" && !(x <= y))) return false;
      }
      if (op === "sampled" && sampled && !(sampled[arg] || []).includes(v)) return false;
    }
  }
  return true;
}

// The units a block has in the open document, in order — the server's unit_rows, so the keys agree.
export function unitRows(block, data, vm, sampled = null) {
  const uk = unitKind(block.unit, vm); if (!uk) return [];
  const pm = data.paper_metadata || {}, ekey = vm.entries.key;
  const recs = (data.records || []).filter((r) => !r.screened_empty);
  const out = [];
  if (uk.kind === "paper") {
    const row = { ...Object.fromEntries(IDENTITY.map((k) => [k, (data.paper || {})[k]])), ...pm };
    if (whereMatch(row, block.where, pm, sampled)) out.push({ key: "", record_id: null, index: null, path: "paper_metadata", row, scope: "paper", base: "" });
  } else if (uk.kind === "entries") {
    for (const r of recs) { const fv = r.field_values || {}; if (whereMatch(fv, block.where, pm, sampled)) out.push({ key: "", record_id: r.id, rec: r, index: null, path: `${ekey}[${r.entry_index}]`, row: fv, scope: "record", base: "" }); }
  } else if (uk.kind === "child") {
    for (const r of recs) {
      const rows = (r.field_values || {})[uk.key];
      (Array.isArray(rows) ? rows : []).forEach((row, j) => {
        if (!row || typeof row !== "object" || !whereMatch(row, block.where, pm, sampled)) return;
        out.push({ key: `${uk.key}#${rid(row, j)}`, record_id: r.id, rec: r, index: j, path: `${ekey}[${r.entry_index}].${uk.key}[${j}]`, row, scope: "record", base: `${uk.key}[${j}]` });
      });
    }
  } else if (uk.kind === "table") {
    const rows = pm[uk.key];
    (Array.isArray(rows) ? rows : []).forEach((row, j) => {
      if (!row || typeof row !== "object" || !whereMatch(row, block.where, pm, sampled)) return;
      out.push({ key: `${uk.key}#${rid(row, j)}`, record_id: null, index: j, path: `paper_metadata.${uk.key}[${j}]`, row, scope: "paper", base: `${uk.key}[${j}]` });
    });
  } else if (uk.kind === "distinct") {
    let src = [];
    if (uk.source === "child") for (const r of recs) { const rows = (r.field_values || {})[uk.key]; src = src.concat((Array.isArray(rows) ? rows : []).filter((x) => x && typeof x === "object")); }
    else { const rows = pm[uk.key]; src = (Array.isArray(rows) ? rows : []).filter((x) => x && typeof x === "object"); }
    const seen = new Map();
    for (const x of src) {
      const v = x[uk.field] === undefined ? null : x[uk.field];
      if (!whereMatch({ [uk.field]: v }, block.where, pm, sampled)) continue;
      const s = v == null ? "" : String(v);
      seen.set(s, (seen.get(s) || 0) + 1);
    }
    let k = 0;
    for (const [s, n] of seen) out.push({ key: `${uk.key}.${uk.field}=${s}`, record_id: null, index: k++, path: null, row: { [uk.field]: s, n }, scope: "none", base: "" });
  }
  if (block.only_sampled && sampled && sampled._keys) return out.filter((u) => sampled._keys.includes(u.key));
  return out;
}

// the units the coder added to a block: their answers under "<unit>#+<id>" keys
export function addedUnits(block, answers, vm) {
  const uk = unitKind(block.unit, vm); if (!uk || !answers) return [];
  const name = unitLabel(block.unit), out = [];
  const take = (recId, units) => { for (const [key, vals] of Object.entries(units || {})) if (key.startsWith(`${name}#+`)) out.push({ key, record_id: recId, index: null, path: null, row: { ...(vals || {}) }, added: true, scope: "none", base: "" }); };
  if (uk.kind === "child" || uk.kind === "entries") for (const [recId, units] of Object.entries(answers.records || {})) take(recId, units);
  else take(null, answers.paper || {});
  return out;
}

export function answersAt(answers, recordId, key) {
  if (!answers) return {};
  return (recordId ? ((answers.records || {})[recordId] || {})[key] : (answers.paper || {})[key]) || {};
}

const fill = (t, row) => String(t).replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, n) => (row[n] == null ? "" : String(row[n]))).trim();
export function unitTitle(block, u, vm) {
  const row = u.row || {};
  if (u.added) { const f = ((block.add || {}).fields || [])[0]; return (f && row[f.name]) ? String(row[f.name]) : `new ${unitLabel(block.unit).replace(/s$/, "")}`; }
  if (block.title) return fill(block.title, row) || u.key;
  const uk = unitKind(block.unit, vm);
  if (uk && uk.kind === "entries" && u.rec) return entryTitle(vm, u.rec, u.rec.entry_index);
  if (uk && uk.kind === "child") { const ch = vm.entries.children.find((c) => c.key === uk.key); if (ch && ch.title) return childTitle(ch, row, u.index); }
  if (uk && uk.kind === "paper") return block.label || "This paper";
  for (const n of block.show || []) if (!empty(row[n])) return String(row[n]);
  if (uk && uk.kind === "distinct") return String(row[uk.field] ?? "");
  return u.key;
}

// the block's required questions still without an answer, over its units and the coder's added ones
export function completeness(tpl, data, answers, vm, sampled = null) {
  const missing = []; let required = 0;
  const pm = data.paper_metadata || {};
  for (const st of tpl.stages || []) (st.blocks || []).forEach((bl, bi) => {
    const qs = (bl.questions || []).filter((q) => q.required || q.required_when);
    if (!qs.length) return;
    for (const u of [...unitRows(bl, data, vm, sampled), ...addedUnits(bl, answers, vm)]) {
      const a = answersAt(answers, u.record_id, u.key), ctx = { ...(u.row || {}), ...a };
      for (const q of qs) {
        if (q.required_when && !whereMatch(ctx, q.required_when, pm, sampled)) continue;
        required += 1;
        if (empty(a[q.name])) missing.push({ stage: st.id, block: bi, unit: u.key, record_id: u.record_id, question: q.name, label: q.label || q.name });
      }
    }
  });
  return { required, missing, complete: !missing.length };
}

// `sections` and `inline` over a block's fields (shown ones and questions alike), as a stage's
function sectioned(block, defs, values, basePath, opts) {
  const secs = Array.isArray(block.sections) ? block.sections : [];
  const byName = new Map(defs.map((f) => [f.name, f])), placed = new Set();
  let html = "";
  for (const sc of secs) {
    const own = (sc.fields || []).map((n) => byName.get(n)).filter((f) => f && !placed.has(f.name));
    if (!own.length) continue;
    own.forEach((f) => placed.add(f.name));
    html += `<div class="stg-section"><div class="stg-section-h">${esc(sc.label || "")}</div>${sc.help ? `<div class="stg-section-help muted">${esc(sc.help)}</div>` : ""}${renderFields(own, values, basePath, opts, block.inline)}</div>`;
  }
  const rest = defs.filter((f) => !placed.has(f.name));
  return html + (rest.length ? renderFields(rest, values, basePath, opts, block.inline) : "");
}

function citeTag(u, evidence) {
  if (!u.path || !Array.isArray(evidence)) return "";
  const ids = evidence.map((ev, i) => ({ ev, i })).filter(({ ev }) => ev.field_path === u.path || (ev.field_path || "").startsWith(u.path + ".")).map(({ i }) => i);
  if (!ids.length) return "";
  const ev = evidence[ids[0]];
  return `<button type="button" class="ev-cite" data-eids="${ids.join(",")}" data-page="${ev.page || 1}" title="${esc(`open the passage, p. ${ev.page || "?"}${ev.snippet ? "\n" + ev.snippet : ""}`)}">p.${ev.page || "?"}</button>`;
}

function marksHtml(block, row) {
  return (block.marks || []).filter((m) => (m.when ? row[m.when] === true : !empty(row[m.when_nonempty])))
    .map((m) => `<span class="tq-mark">${esc(fill(m.text, { ...row, ...Object.fromEntries(Object.entries(row).map(([k, v]) => [k, Array.isArray(v) ? v.join(", ") : v])) }))}</span>`).join(" ");
}

// the owner's view of every coder's answers on a unit: one line per coder, a question the coders
// disagree on marked
function byCoderHtml(qdefs, byCoder, recordId, key) {
  const rows = Object.entries(byCoder || {}).map(([label, a]) => [label, answersAt(a, recordId, key)]).filter(([, a]) => Object.keys(a).length);
  if (!rows.length) return `<div class="ans-by muted">no answers yet</div>`;
  const diff = new Set(qdefs.filter((q) => new Set(rows.map(([, a]) => JSON.stringify(a[q.name] ?? null))).size > 1).map((q) => q.name));
  const show = (q, v) => { if (empty(v)) return "—"; const o = (q.options || []).find((x) => String(x.value) === String(v)); return o ? (o.label || String(o.value)) : Array.isArray(v) ? v.join(", ") : String(v); };
  return rows.map(([label, a]) => `<div class="ans-by"><b>${esc(label)}</b> · ${qdefs.filter((q) => a[q.name] !== undefined).map((q) => `<span class="${diff.has(q.name) ? "ans-diff" : ""}">${esc(q.label || q.name)}: ${esc(show(q, a[q.name]))}</span>`).join(" · ") || "—"}</div>`).join("");
}

// One block: its label and help, then a card per unit. ctx: {data, vm, answers, byCoder, editable, owner, evidence}
export function blockHtml(block, ctx) {
  const { data, vm } = ctx;
  const uk = unitKind(block.unit, vm);
  const fields = unitFields(uk, vm);
  const units = [...unitRows(block, data, vm, ctx.sampled), ...addedUnits(block, ctx.answers, vm)];
  const showDefs = (block.show || []).map((n) => fields.get(n)).filter(Boolean);
  const qdefs = block.questions || [];
  const addDefs = ((block.add || {}).fields || []);
  const label = block.label || (uk && uk.kind === "paper" ? "" : formatKey(unitLabel(block.unit)));
  let html = `<div class="tq-block" data-unit="${esc(unitLabel(block.unit))}">${label ? `<div class="tq-block-h">${esc(label)}${units.length > 1 ? ` <span class="muted">(${units.length})</span>` : ""}</div>` : ""}${block.help ? `<div class="tq-block-help">${esc(block.help)}</div>` : ""}`;
  if (!units.length) html += `<div class="tq-empty">none in this paper</div>`;
  let lastEntry = null;
  for (const u of units) {
    if (block.group_by_entry && u.rec && u.record_id !== lastEntry) { lastEntry = u.record_id; html += `<div class="tq-group-h">${esc(entryTitle(vm, u.rec, u.rec.entry_index))}</div>`; }
    // an unanswered question is null, not absent: the control renders blank, not as "—" (not returned)
    const a = { ...Object.fromEntries(qdefs.map((q) => [q.name, null])), ...answersAt(ctx.answers, u.record_id, u.key) };
    const addRow = u.added ? { ...Object.fromEntries(addDefs.map((f) => [f.name, null])), ...u.row } : null;
    const shownEditable = ctx.editable && !block.mask_numbers && u.scope !== "none" && !u.added;
    const shown = u.added
      ? `<div class="tq-shown${block.stack ? " tq-stack" : ""} rv-root" data-scope="answers">${renderFields(addDefs, addRow, "", { editable: ctx.editable })}</div>`
      : showDefs.length ? `<div class="tq-shown${block.stack ? " tq-stack" : ""} rv-root" data-scope="${u.scope}"${u.rec ? ` data-rid="${esc(u.rec.id)}"` : ""}>${sectioned(block, showDefs, u.row, u.base, { editable: shownEditable })}</div>` : "";
    const qs = qdefs.length ? `<div class="tq-q rv-root">${sectioned(block, qdefs, a, "", { editable: ctx.editable })}</div>` : "";
    const by = ctx.owner && qdefs.length ? byCoderHtml(qdefs, ctx.byCoder, u.record_id, u.key) : "";
    const title = uk && uk.kind === "paper" && !block.title ? "" : `<div class="tq-title">${citeTag(u, ctx.evidence)} <span class="tq-name">${esc(unitTitle(block, u, vm))}</span>${marksHtml(block, u.row || {})}${u.added && ctx.editable ? `<button type="button" class="tq-del" title="remove this added unit and its answers">✕</button>` : ""}</div>`;
    html += `<div class="tq-unit${u.added ? " tq-added" : ""}" data-unit="${esc(u.key)}"${u.record_id ? ` data-rid="${esc(u.record_id)}"` : ""}>${title}${shown}${qs}${by}</div>`;
  }
  if (block.add && ctx.editable) html += `<div class="rv-tableadd"><button type="button" class="tq-add" data-unit="${esc(unitLabel(block.unit))}" title="a unit the paper has and the list lacks — its answers are yours">＋ ${esc(block.add.label || unitLabel(block.unit).replace(/s$/, ""))}</button></div>`;
  return html + `</div>`;
}
