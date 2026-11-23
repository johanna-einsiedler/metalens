// A review TEMPLATE in the workspace (paperlens/templates.py): a stage's BLOCKS over UNITS — the
// paper, the entries, a child's rows, a paper table's rows, the distinct values of a column — each
// drawn as a card: the unit's title (with its marks and its page tag), the fields the block shows
// (editable into the coder's coding, read-only when the block masks numbers), then the QUESTIONS
// the coder answers (saved into their coding under the unit's key), and for the owner every
// coder's answers under them. Unit keys are the server's: "" (the paper, or the entry in its
// record), "<child>#<_rid>", "<table>#<_rid>", "<path>.<field>=<value>", "<unit>#+<id>" (added).
import { renderFields, renderFieldControl, esc, formatKey, parseTableMarkdown, captionNames, placeCells } from "/static/grammar.js";
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
// a title placeholder whose field the projection dropped (a blind field is popped from the row; a
// null value stays): the head would print nothing — fall through to the shown fields instead
const dropped = (t, row) => [...String(t || "").matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)].some((m) => m[1] !== "#" && !(m[1] in (row || {})));

export function unitTitle(block, u, vm) {
  const row = u.row || {};
  if (u.added) { const f = ((block.add || {}).fields || [])[0]; return (f && row[f.name]) ? String(row[f.name]) : `new ${unitLabel(block.unit).replace(/s$/, "")}`; }
  if (block.title) return fill(block.title, row) || u.key;
  const uk = unitKind(block.unit, vm);
  if (uk && uk.kind === "entries" && u.rec) return entryTitle(vm, u.rec, u.rec.entry_index);
  if (uk && uk.kind === "child") { const ch = vm.entries.children.find((c) => c.key === uk.key); if (ch && ch.title && !dropped(ch.title, row)) return childTitle(ch, row, u.index); }
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

// ── a block's `grid`: the exhibit its units sit in, as printed, the units marked ─────────────
// The rows of the template's items table (`items`), on the paper and on every record.
export function itemRows(data, items) {
  if (!items) return [];
  const pm = (data && data.paper_metadata) || {};
  const out = Array.isArray(pm[items]) ? pm[items].slice() : [];
  for (const r of (data && data.records) || []) { const rows = (r.field_values || {})[items]; if (Array.isArray(rows)) out.push(...rows); }
  return out.filter((x) => x && typeof x === "object");
}
// the items-table row that transcribes an exhibit: its caption line names the id
export function transcriptionFor(id, rows) {
  return (rows || []).find((r) => r && r.kind !== "passage" && captionNames(r.text, id)) || null;
}
// The exhibits a stage's grid blocks draw (the `grid.of` values of their units): what the pane
// opens on.
export function gridIds(stage, data, vm, sampled = null) {
  const ids = [];
  for (const bl of (stage && stage.blocks) || []) {
    if (!bl || !bl.grid || !bl.grid.of) continue;
    for (const u of unitRows(bl, data, vm, sampled)) { const v = (u.row || {})[bl.grid.of]; if (v != null && v !== "" && !ids.includes(v)) ids.push(v); }
  }
  return ids;
}

// ── layout "flags": the coder works on the table ──────────────────────────────────────────
// A unit's answers to the block's flag questions (grid.flag, grid.mapping): flagged when given
// and not what `confirm` would give (the "correct" answer; without confirm: the flag answer).
function flagQuestions(block) {
  const g = block.grid || {};
  return [g.flag && g.flag.question, g.mapping && g.mapping.question].filter(Boolean);
}
const given = (v) => !(v === null || v === undefined || v === "");
export function isFlagged(block, a) {
  const ok = (block.confirm || {}).answers || {}, g = block.grid || {};
  return flagQuestions(block).some((q) => given(a[q]) && (q in ok ? String(a[q]) !== String(ok[q]) : String(a[q]) === String(((g.flag || {}).question === q ? g.flag : g.mapping).answer)));
}
// every question `confirm` answers that applies to the unit (its `required_when` holds) is
// answered as confirm would answer it
function isConfirmed(block, a, row = {}) {
  const ok = (block.confirm || {}).answers || {};
  const qs = new Map((block.questions || []).map((q) => [q.name, q]));
  const apply = Object.keys(ok).filter((n) => { const q = qs.get(n); return !(q && q.required_when) || whereMatch({ ...row, ...a }, q.required_when, {}, null); });
  return apply.length > 0 && apply.every((n) => String(a[n]) === String(ok[n]));
}

// A mapping part's flag: every cell it covers gets the mapping's flag answer, and — when the
// grid names a note question (`grid.mapping.note`) — "wrong: <part>" in its note, so the record
// says which part was wrong (and the part shows flagged, not every part of those cells). Taking
// it back removes that line; the flag answer stays while another part of the cell is still named.
const wrongTag = (text) => `wrong: ${text}`;
export function partFlagItems(block, pairs, answers, text, off) {
  const m = (block.grid || {}).mapping || {};
  const tag = wrongTag(text);
  return pairs.map(([rid, unit]) => {
    const a = answersAt(answers, rid || null, unit);
    if (!m.note) return { record_id: rid || null, unit, answers: { [m.question]: off ? null : m.answer } };
    const lines = String(a[m.note] || "").split("\n").map((x) => x.trim()).filter(Boolean);
    const rest = lines.filter((x) => x !== tag);
    const note = off ? rest : (lines.includes(tag) ? lines : [...lines, tag]);
    const still = note.some((x) => x.startsWith("wrong: "));
    return { record_id: rid || null, unit, answers: { [m.question]: off && !still ? null : m.answer, [m.note]: note.length ? note.join("\n") : null } };
  });
}
export function partFlagged(block, a, text) {
  const m = (block.grid || {}).mapping || {};
  if (!m.question || String(a[m.question]) !== String(m.answer)) return false;
  return !m.note || String(a[m.note] || "").split("\n").map((x) => x.trim()).includes(wrongTag(text));
}

// What "everything else correct" sends: every unit's `confirm.answers` for the questions it has not
// answered — the coder's added units too (a question whose `required_when` does not hold for the
// unit is left alone) — and `confirm.paper` on the paper where still open.
export function confirmItems(block, units, answers, data, sampled = null) {
  const conf = block.confirm || {};
  const qs = new Map((block.questions || []).map((q) => [q.name, q]));
  const pm = (data && data.paper_metadata) || {};
  const items = [];
  for (const u of units) {
    const a = answersAt(answers, u.record_id, u.key), set = {};
    for (const [qn, v] of Object.entries(conf.answers || {})) {
      if (given(a[qn])) continue;
      const q = qs.get(qn);
      if (q && q.required_when && !whereMatch({ ...(u.row || {}), ...a }, q.required_when, pm, sampled)) continue;
      set[qn] = v;
    }
    if (Object.keys(set).length) items.push({ record_id: u.record_id || null, unit: u.key, answers: set });
  }
  const pa = answersAt(answers, null, ""), pset = {};
  for (const [qn, v] of Object.entries(conf.paper || {})) if (!given(pa[qn])) pset[qn] = v;
  if (Object.keys(pset).length) items.push({ record_id: null, unit: "", answers: pset });
  return items;
}
// a block's units, drawn and added, as blockHtml sees them
export function blockUnits(block, data, vm, answers, sampled = null) {
  return [...unitRows(block, data, vm, sampled), ...addedUnits(block, answers, vm)];
}

// The mapping consolidated onto the table: each placed cell's mapping text split into its parts
// (`sep`), and each part shown once where it holds — over every cited cell, a panel's, a column's
// or a row's (a column wins a tie unless only its rows hold more than one cell) — else in the cell.
// cells [{key, pi, ri, ci, text}] → {global, panel: {pi}, column: {"pi:ci"}, row: {"pi:ri"}, cell:
// {key}}, each a list of {text, keys}.
export function consolidateMapping(cells, sep) {
  const out = { global: [], panel: {}, column: {}, row: {}, cell: {} };
  const parts = cells.map((c) => String(c.text == null ? "" : c.text).split(sep).map((x) => x.trim()));
  const K = Math.max(0, ...parts.map((p) => p.length));
  const groupBy = (f) => { const m = new Map(); cells.forEach((c, i) => { const k = f(c); if (!m.has(k)) m.set(k, []); m.get(k).push(i); }); return m; };
  const panels = groupBy((c) => `${c.pi}`), cols = groupBy((c) => `${c.pi}:${c.ci}`), rows = groupBy((c) => `${c.pi}:${c.ri}`);
  const shares = (groups, k) => [...groups.values()].every((ix) => ix.every((i) => (parts[i][k] || "") === (parts[ix[0]][k] || "")));
  const busy = (groups) => [...groups.values()].some((ix) => ix.length > 1);
  const put = (bucket, key, k, ix) => { const t = parts[ix[0]][k] || ""; if (!t) return; (bucket[key] = bucket[key] || []).push({ text: t, keys: ix.map((i) => cells[i].key) }); };
  for (let k = 0; k < K; k++) {
    const all = cells.map((_, i) => i);
    if (cells.length && shares(new Map([["*", all]]), k)) { const t = parts[0][k] || ""; if (t) out.global.push({ text: t, keys: cells.map((c) => c.key) }); continue; }
    if (panels.size > 1 && shares(panels, k)) { for (const [key, ix] of panels) put(out.panel, key, k, ix); continue; }
    const colOK = shares(cols, k), rowOK = shares(rows, k);
    if (colOK && (!rowOK || !(busy(rows) && !busy(cols)))) { for (const [key, ix] of cols) put(out.column, key, k, ix); continue; }
    if (rowOK) { for (const [key, ix] of rows) put(out.row, key, k, ix); continue; }
    cells.forEach((c, i) => put(out.cell, c.key, k, [i]));
  }
  return out;
}

// The grid above a block's rows: per exhibit its transcription laid out as printed (a header row
// per panel, the row labels down the side), each placed unit's cell marked with its row number,
// its "(SE)" line beneath lightly marked; a unit the transcription has no cell for is named under
// the grid. Nothing for an exhibit without a pipe table (a figure). Layout "rows": a click goes to
// the unit's row. Layout "flags" (the coder's): a cited cell's click flags its value, a mapping
// part's (`grid.mapping`, consolidated) flags every cell it covers, a click on any other cell adds
// it as a missed unit (its panel, row, column and value filled in); a cell shows ✓ once confirmed.
// Returns {html, placed: Set of keys drawn in a grid}.
function gridHtml(block, units, num, ctx) {
  const g = block.grid || {};
  const flags = block.layout === "flags", live = flags && ctx.editable;
  const byId = new Map();
  for (const u of units) {
    if (u.added) continue;
    const id = (u.row || {})[g.of];
    if (id == null || id === "") continue;
    if (!byId.has(id)) byId.set(id, []);
    byId.get(id).push(u);
  }
  const added = units.filter((u) => u.added);
  const ans = (u) => answersAt(ctx.answers, u.record_id, u.key);
  const placedAll = new Set();
  let html = "";
  for (const [id, us] of byId) {
    const tr = transcriptionFor(id, ctx.items);
    if (!tr) continue;
    const t = parseTableMarkdown(tr.text);
    if (!t.panels.some((p) => p.header && p.rows.length)) continue;
    const coords = (u) => ({ key: u.key, panel: g.panel ? (u.row || {})[g.panel] : null, row: (u.row || {})[g.row], column: (u.row || {})[g.column] });
    const { at, se, unplaced } = placeCells(t, us.map(coords));
    const missedAt = flags ? placeCells(t, added.map(coords)).at : {};
    const byKey = new Map(us.map((u) => [u.key, u]));
    const pos = new Map(Object.entries(at).map(([spot, k]) => { const [pi, ri, ci] = spot.split(":").map(Number); return [k, { pi, ri, ci }]; }));
    pos.forEach((_, k) => placedAll.add(k));
    const mp = flags && g.mapping ? consolidateMapping([...pos].map(([k, p]) => ({ key: k, ...p, text: (byKey.get(k).row || {})[g.mapping.field] })).filter((c) => given(c.text)), g.mapping.sep) : null;
    const mq = g.mapping && g.mapping.question, mBad = g.mapping && g.mapping.answer;
    const chip = (part) => {
      const bad = !!mq && part.keys.every((k) => partFlagged(block, ans(byKey.get(k)), part.text));     // every cell it covers flagged for this part
      const n = part.keys.length;
      if (!live) return `<span class="tq-part${bad ? " tq-part-bad" : ""}">${esc(part.text)}</span>`;
      const units_ = JSON.stringify(part.keys.map((k) => [byKey.get(k).record_id || "", k]));
      return `<button type="button" class="tq-part${bad ? " tq-part-bad" : ""}" data-units="${esc(units_)}" data-part="${esc(part.text)}" title="${esc(bad ? `flagged as a wrong mapping for ${n} cell${n === 1 ? "" : "s"} — click to take the flag back` : `click if this mapping is wrong — flags the ${n} cell${n === 1 ? "" : "s"} it covers`)}">${esc(part.text)}</button>`;
    };
    const chips = (list) => (list || []).map(chip).join("");
    const width = Math.max(...t.panels.map((p) => Math.max(p.header ? p.header.length : 0, ...p.rows.map((r) => r.length))));
    const many = t.panels.length > 1;
    const body = t.panels.map((p, pi) => {
      const rowMap = mp ? p.rows.map((_, ri) => mp.row[`${pi}:${ri}`]) : [];
      const hasRowMap = rowMap.some((x) => x && x.length);
      const colMap = mp ? (p.header || []).map((_, ci) => mp.column[`${pi}:${ci}`]) : [];
      const hasColMap = colMap.some((x) => x && x.length);
      const w = width + (hasRowMap ? 1 : 0);
      const label = p.label || `Panel ${pi + 1}`;
      const pchips = mp ? chips(mp.panel[`${pi}`]) : "";
      let out = many || (p.label && p.label !== t.caption) || pchips ? `<tr class="tq-gp"><td colspan="${w}">${esc(label)}${pchips ? ` <span class="tq-gp-map">→ ${pchips}</span>` : ""}</td></tr>` : "";
      if (p.header) out += `<tr class="tq-gh">${p.header.map((h, ci) => `<th${ci ? "" : ` class="tq-grl"`}>${esc(h)}</th>${!ci && hasRowMap ? `<th class="tq-grmap-h">mapped to</th>` : ""}`).join("")}</tr>`;
      if (hasColMap) out += `<tr class="tq-gmaprow">${(p.header || []).map((_, ci) => ci ? `<td class="tq-gmap">${chips(colMap[ci])}</td>` : `<th class="tq-grl tq-gmap-k">mapped to</th>${hasRowMap ? "<td></td>" : ""}`).join("")}</tr>`;
      const panelName = p.labels.length ? p.labels[p.labels.length - 1] : "";
      out += p.rows.map((r, ri) => `<tr>${r.map((c, ci) => {
        if (!ci) return `<th class="tq-grl">${esc(c)}</th>${hasRowMap ? `<td class="tq-grmap">${chips(rowMap[ri])}</td>` : ""}`;
        const spot = `${pi}:${ri}:${ci}`, k = at[spot], ks = se[spot];
        if (k) {
          const u = byKey.get(k), a = ans(u), tip = g.tip ? fill(g.tip, u.row || {}) : "";
          const st = flags ? (isFlagged(block, a) ? " tq-bad" : isConfirmed(block, a, u.row || {}) ? " tq-ok" : "") : "";
          const cparts = mp ? chips(mp.cell[k]) : "";
          const what = live ? (st === " tq-bad" ? "flagged — click: its row below" : "click if this value is wrong") : "click: its answers";
          return `<td class="tq-gc tq-cited${st}" data-unit="${esc(k)}"${live ? ` data-act="value"${u.record_id ? ` data-rid="${esc(u.record_id)}"` : ""}` : ""} title="${esc(`row ${num.get(k)}${tip ? ` · ${tip}` : ""} — ${what}`)}">${esc(c)}<sup>${num.get(k)}</sup>${cparts ? `<div class="tq-cparts">${cparts}</div>` : ""}</td>`;
        }
        if (ks) return `<td class="tq-gc tq-cited-se" data-unit="${esc(ks)}"${live ? ` data-act="value"` : ""}>${esc(c)}</td>`;
        const mk = missedAt[spot];
        if (mk) return `<td class="tq-gc tq-missed" data-unit="${esc(mk)}" title="${esc(`added as a cell the extraction missed (row ${num.get(mk)})`)}">${esc(c)}<sup>${num.get(mk)}</sup></td>`;
        if (live && c !== "" && !/\(se\)\s*$/i.test(r[0] || ""))
          return `<td class="tq-gc tq-free" data-act="missed" data-panel="${esc(panelName)}" data-row="${esc(r[0])}" data-col="${esc((p.header || [])[ci] || "")}" data-val="${esc(c)}" title="click if the extraction missed this cell (an estimate it should have read)">${esc(c)}</td>`;
        return `<td class="tq-gc">${esc(c)}</td>`;
      }).join("")}</tr>`).join("");
      return out;
    }).join("");
    const lost = unplaced.map((k) => num.get(k)).filter(Boolean);
    const lead = flags ? (live ? "click a wrong value, a wrong mapping or a cell the extraction missed" : "as transcribed, the cited cells numbered") : "as transcribed, the cited cells numbered as their rows below";
    html += `<div class="tq-grid" data-exhibit="${esc(id)}"><div class="tq-grid-cap">${esc(t.caption || id)} <span class="muted">· ${lead}</span></div>`
      + (mp && mp.global.length ? `<div class="tq-map-all"><span class="muted">every cited cell is mapped to</span> ${chips(mp.global)}</div>` : "")
      + `<div class="tq-gridwrap"><table class="tq-gridt"><tbody>${body}</tbody></table></div>`
      + (lost.length ? `<div class="tq-grid-note muted">row${lost.length === 1 ? "" : "s"} ${lost.join(", ")} not found in the transcription's grid (check ${lost.length === 1 ? "it" : "them"} against the PDF${flags ? ", below" : ""})</div>` : "")
      + `</div>`;
  }
  return { html, placed: placedAll };
}

// layout "flags": the grid, then "everything else correct" with the tally, then as rows only what
// needs words — the flagged units, the ones the coder added, any the grid could not place. The
// owner (and a figure, which has no grid) gets the grid and every unit as a row.
function flagsHtml(block, units, num, ctx, rowsArgs) {
  const grid = gridHtml(block, units, num, ctx);
  if (!ctx.editable || !grid.placed.size) return grid.html + rowsHtml(block, units, num, ctx, ...rowsArgs);
  const own = units.filter((u) => !u.added);
  const flagged = own.filter((u) => isFlagged(block, answersAt(ctx.answers, u.record_id, u.key)));
  const done = own.filter((u) => isConfirmed(block, answersAt(ctx.answers, u.record_id, u.key), u.row || {}));
  const added = units.filter((u) => u.added);
  const listed = units.filter((u) => u.added || !grid.placed.has(u.key) || flagged.includes(u));
  const open = own.length - done.length - flagged.length;
  const conf = block.confirm;
  const bar = `<div class="tq-flagbar">`
    + (conf ? `<button type="button" class="btn tq-confirm"${open > 0 || (conf.paper && confirmItems(block, [], ctx.answers, ctx.data).length) ? "" : " disabled"} title="every cell you have not flagged: value and mapping right${conf.paper ? "; and the paper needs no full review, unless you said otherwise" : ""}">✓ ${esc(conf.label || "Everything else correct")}</button>` : "")
    + `<span class="tq-flagsum muted">${own.length} cited · ${done.length} confirmed · ${flagged.length} flagged · ${added.length} missed${open > 0 ? ` · <b>${open} open</b>` : ""}</span></div>`;
  const rows = listed.length ? `<div class="tq-flag-h">Flagged and added <span class="muted">— say what is wrong; a value: type it as printed</span></div>` + rowsHtml(block, listed, num, { ...ctx, unflag: true }, ...rowsArgs)
    : `<div class="tq-flag-none muted">Nothing flagged. Click a wrong value or mapping in the table, or a cell the extraction missed; then ✓ ${esc((conf || {}).label || "Everything else correct")}.</div>`;
  return grid.html + bar + rows;
}

// The block's `columns` as [{label, fields, wide}]: a name, a list of names (stacked in one cell),
// or {label, fields, wide} (`wide`: a long text that needs room to read); without `columns`, one
// column per shown field.
function rowColumns(block, fields) {
  const lab = (n) => { const f = fields.get(n); return (f && f.label) || formatKey(n); };
  const cols = Array.isArray(block.columns) && block.columns.length ? block.columns : (block.show || []);
  return cols.map((c) => {
    const names = typeof c === "string" ? [c] : Array.isArray(c) ? c : (c && Array.isArray(c.fields) ? c.fields : []);
    const obj = c && typeof c === "object" && !Array.isArray(c);
    return { label: obj && c.label ? c.label : names.map(lab).join(" · "), fields: names, wide: !!(obj && c.wide) };
  }).filter((c) => c.fields.length);
}

// layout "rows": the units as one table — a row per unit (its number and page tag, the shown
// fields in the block's columns, a control per question; the owner gets every coder's answers in
// a last column); an added unit's own fields sit in the columns of the same name.
function rowsHtml(block, units, num, ctx, fields, showDefs, qdefs, addDefs) {
  const cols = rowColumns(block, fields);
  const byName = new Map(showDefs.map((f) => [f.name, f]));
  const addBy = new Map(addDefs.map((f) => [f.name, f]));
  const placedAdd = new Set(cols.flatMap((c) => c.fields));
  const strayAdd = addDefs.filter((f) => !placedAdd.has(f.name));   // an add field no column names: in the first column
  const tip = (f) => (f && f.help ? ` title="${esc(f.help)}"` : "");
  const head = `<tr><th class="tq-n">#</th>${cols.map((c) => `<th${c.wide ? ` class="tq-wide"` : ""}>${esc(c.label)}</th>`).join("")}`
    + qdefs.map((q) => `<th class="tq-qh"${tip(q)}>${esc(q.label || formatKey(q.name))}</th>`).join("")
    + (ctx.owner && qdefs.length ? `<th>Answers</th>` : "") + `</tr>`;
  const body = units.map((u) => {
    const a = { ...Object.fromEntries(qdefs.map((q) => [q.name, null])), ...answersAt(ctx.answers, u.record_id, u.key) };
    const shownEditable = ctx.editable && !block.mask_numbers && u.scope !== "none" && !u.added;
    const cell = (c, ci) => {
      if (u.added) {
        const defs = [...c.fields.map((n) => addBy.get(n)).filter(Boolean), ...(ci === 0 ? strayAdd : [])];
        const row = u.row || {};
        return `<td class="tq-shown rv-root${c.wide ? " tq-wide" : ""}" data-scope="answers">${defs.map((f, k) => `<div class="${k ? "tq-sub" : ""}"${tip(f)}>${renderFieldControl(f, row[f.name] === undefined ? null : row[f.name], f.name, { editable: ctx.editable })}</div>`).join("")}</td>`;
      }
      const defs = c.fields.map((n) => byName.get(n)).filter(Boolean);
      return `<td class="tq-shown rv-root${c.wide ? " tq-wide" : ""}" data-scope="${u.scope}"${u.rec ? ` data-rid="${esc(u.rec.id)}"` : ""}>`
        + defs.map((f, k) => `<div class="${k ? "tq-sub" : ""}">${renderFieldControl(f, (u.row || {})[f.name], u.base ? `${u.base}.${f.name}` : f.name, { editable: shownEditable })}</div>`).join("") + `</td>`;
    };
    return `<tr class="tq-unit tq-row${u.added ? " tq-added" : ""}" data-unit="${esc(u.key)}"${u.record_id ? ` data-rid="${esc(u.record_id)}"` : ""}>`
      + `<td class="tq-n">${num.get(u.key) || ""} ${citeTag(u, ctx.evidence)}${u.added && ctx.editable ? `<button type="button" class="tq-del" title="remove this added unit and its answers">✕</button>` : ""}`
      + `${ctx.unflag && !u.added && ctx.editable && isFlagged(block, a) ? `<button type="button" class="tq-unflag" title="not wrong after all: take the flags back">✕</button>` : ""}</td>`
      + cols.map(cell).join("")
      + qdefs.map((q) => `<td class="tq-q rv-root">${renderFieldControl(q, a[q.name], q.name, { editable: ctx.editable })}</td>`).join("")
      + (ctx.owner && qdefs.length ? `<td class="tq-by">${byCoderHtml(qdefs, ctx.byCoder, u.record_id, u.key)}</td>` : "")
      + `</tr>`;
  }).join("");
  return `<div class="tq-rowswrap"><table class="tq-rows"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

// One block: its label and help, then a card per unit (layout "rows": a table, a row per unit;
// `grid`: the exhibit as printed above it; layout "flags": the grid is where the coder works). ctx: {data, vm, answers, byCoder, editable, owner,
// evidence, sampled, items (the items table's rows, for a grid)}
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
  if ((block.layout === "rows" || block.layout === "flags") && units.length) {
    const num = new Map(units.map((u, i) => [u.key, i + 1]));
    const rowsArgs = [fields, showDefs, qdefs, addDefs];
    html += block.layout === "flags" ? flagsHtml(block, units, num, ctx, rowsArgs)
      : (block.grid ? gridHtml(block, units, num, ctx).html : "") + rowsHtml(block, units, num, ctx, ...rowsArgs);
    if (block.add && ctx.editable) html += `<div class="rv-tableadd"><button type="button" class="tq-add" data-unit="${esc(unitLabel(block.unit))}" title="a unit the paper has and the list lacks — its answers are yours">＋ ${esc(block.add.label || unitLabel(block.unit).replace(/s$/, ""))}</button></div>`;
    return html + `</div>`;
  }
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
