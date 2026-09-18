// PaperLens API client — the only module that knows endpoint URLs. Sends the
// anonymous X-Session-Id (browser-minted) and surfaces clean errors.
const SID_KEY = "paperlens_sid";

function sid() {
  let s = localStorage.getItem(SID_KEY);
  if (!s) {
    s = (crypto.randomUUID && crypto.randomUUID()) || String(Math.random()).slice(2);
    localStorage.setItem(SID_KEY, s);
  }
  return s;
}

// Logged-out retention is an idle timeout: every API request refreshes the session's
// last-seen clock server-side (see paperlens/retention.py); nothing to do here.

async function req(path, opts = {}) {
  // opts first: a caller's own headers (content-type) MERGE with the session header
  // rather than replacing it, otherwise every JSON POST would arrive anonymous.
  const r = await fetch(path, {
    credentials: "same-origin",
    ...opts,
    headers: { "X-Session-Id": sid(), ...(opts.headers || {}) },
  });
  if (!r.ok) {
    let msg = r.statusText, detail = null;
    try {
      detail = (await r.json()).detail;
      // a structured detail ({message, errors}) keeps its shape on the error for callers
      // that can show it (the preset editor lists every validation problem)
      msg = (typeof detail === "string" ? detail : detail && detail.message) || msg;
    } catch { /* ignore */ }
    const err = new Error(`${r.status} ${msg}`);
    err.detail = detail;
    throw err;
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

function qs(params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v == null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => u.append(k, x));
    else u.set(k, v);
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

const json = (body) => ({ method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  search: (f) => req(`/api/search${qs(f)}`),
  facets: (f) => req(`/api/facets${qs(f)}`),
  papersSearch: (f) => req(`/api/papers/search${qs(f)}`),
  datasetsPublic: (q) => req(`/api/datasets/public${qs({ q })}`),
  dataset: (id) => req(`/api/datasets/${id}`),
  datasetOverview: (id) => req(`/api/datasets/${id}/overview`),
  datasetActivity: (id) => req(`/api/datasets/${id}/activity`),
  datasetExport: (id) => req(`/api/datasets/${id}/export`),
  myDatasets: () => req(`/api/datasets`),
  createDataset: (body) => req(`/api/datasets`, json(body)),
  addToDataset: (id, body) => req(`/api/datasets/${id}/add`, json(body)),
  deleteDocument: (id) => req(`/api/documents/${id}`, { method: "DELETE" }),
  forgetSession: () => req(`/api/session/forget`, { method: "POST" }),
  brand: () => req(`/api/brand`),
  datasetDuplicates: (id) => req(`/api/datasets/${id}/duplicates`),
  verifyAllDataset: (id) => req(`/api/datasets/${id}/verify-all`, { method: "POST" }),
  dedupeDataset: (id) => req(`/api/datasets/${id}/dedupe`, { method: "POST" }),
  deleteDataset: (id) => req(`/api/datasets/${id}`, { method: "DELETE" }),
  setDatasetVisibility: (id, visibility) =>
    req(`/api/datasets/${id}`, { method: "PATCH", headers: { "content-type": "application/json" },
                                 body: JSON.stringify({ visibility }) }),
  updateDatasetMeta: (id, body) =>
    req(`/api/datasets/${id}`, { method: "PATCH", headers: { "content-type": "application/json" },
                                 body: JSON.stringify(body) }),
  renameDataset: (id, title) =>
    req(`/api/datasets/${id}`, { method: "PATCH", headers: { "content-type": "application/json" },
                                 body: JSON.stringify({ title }) }),
  publishDataset: (id, body) => req(`/api/datasets/${id}/publish`, json(body || { target: "github+metalens" })),
  paperCoverage: (q) => req(`/api/papers/coverage${qs({ q })}`),
  githubSync: () => req(`/api/github/sync`, { method: "POST" }),
  record: (id) => req(`/api/records/${id}`),
  recordsProvenance: (ids) => req(`/api/records/provenance`, json({ ids })),
  deleteRecord: (id) => req(`/api/records/${id}`, { method: "DELETE" }),
  addRecord: (docId, body) => req(`/api/documents/${docId}/records`, json(body)),
  documents: (f) => req(`/api/documents${qs(f)}`),
  myPapers: () => req(`/api/papers/mine`),
  deletePaper: (sha) => req(`/api/papers/mine/${encodeURIComponent(sha)}`, { method: "DELETE" }),
  checkDuplicates: (hashes, schemaId, presetId) => req(`/api/documents/check-duplicates`,
    json({ hashes, schema_id: schemaId, preset_id: presetId || null })),
  setDocumentField: (docId, key, value) => req(`/api/documents/${docId}/set-field`, json({ key, value })),
  updatePaper: (docId, fields) => req(`/api/documents/${docId}/paper`, { method: "PATCH",
    headers: { "content-type": "application/json" }, body: JSON.stringify(fields) }),
  documentView: (id) => req(`/api/documents/${id}/view`),
  recordEvents: (id) => req(`/api/records/${id}/events`),
  // `bands` ("y0:y1,…", image pixels of `page`) restricts the search to the cited table row(s)
  locateValue: (id, value, page, bands) =>
    req(`/api/documents/${id}/locate?value=${encodeURIComponent(value)}&page=${page}${bands ? `&bands=${encodeURIComponent(bands)}` : ""}`),
  // the model's verbatim response as stored at extraction time; null when none was kept
  rawResponse: async (id) => {
    const r = await fetch(`/api/documents/${id}/raw`, { credentials: "same-origin", headers: { "X-Session-Id": sid() } });
    return r.ok ? r.text() : null;
  },
  documentText: (id, page) =>
    req(`/api/documents/${id}/text${page != null ? `?page=${page}` : ""}`),
  aggregate: (body) => req(`/api/aggregate`, json(body)),
  verify: (id, body) => req(`/api/records/${id}/verify`, json(body)),
  views: () => req(`/api/views`),
  viewData: (id) => req(`/api/views/${id}/data`),
  viewGet: (id) => req(`/api/views/${id}`),
  createView: (body) => req(`/api/views`, json(body)),
  datasetRows: (datasetIds) => req(`/api/datasets/rows${qs({ dataset: datasetIds })}`),
  schema: (id) => req(`/api/schemas/${encodeURIComponent(id)}`),
  analysisRows: (viewId) => req(`/api/analyses/${viewId}/rows`),
  proposeFigures: (body) => req(`/api/analyses/propose-figures`, json(body)),
  presets: () => req(`/api/presets`),
  ingest: (body) => req(`/api/ingest`, json(body)),                    // JSON only, no PDF
  schema: (id) => req(`/api/schemas/${encodeURIComponent(id)}`),
  myPresets: () => req(`/api/presets/mine`),
  presetPrompt: (id) => req(`/api/presets/${id}/prompt`),
  presetDetail: (id) => req(`/api/presets/${encodeURIComponent(id)}/detail`),
  createPreset: (body) => req(`/api/presets`, json(body)),
  validatePreset: (spec) => req(`/api/presets/validate`, json({ spec })),
  renderPreset: (id, params) => req(`/api/presets/${encodeURIComponent(id)}/render`, json({ params: params || {} })),
  updatePreset: (id, body) => req(`/api/presets/${encodeURIComponent(id)}`,
    { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  deletePreset: (id) => req(`/api/presets/${encodeURIComponent(id)}`, { method: "DELETE" }),
  buildPresetPrompt: (body) => req(`/api/build-preset-prompt`, json(body)),
  models: () => req(`/static/models.json`),
  testKey: (body) => req(`/api/providers/test`, json(body)),
  extract: (formData) => req(`/api/extract`, { method: "POST", body: formData }),
  ingestPdf: (formData) => req(`/api/ingest-pdf`, { method: "POST", body: formData }),
  ingest: (body) => req(`/api/ingest`, json(body)),
  job: (id) => req(`/api/jobs/${id}`),
  retryJob: (id) => req(`/api/jobs/${id}/retry`, { method: "POST" }),
  cancelJob: (id) => req(`/api/jobs/${id}/cancel`, { method: "POST" }),
  me: () => req(`/api/auth/me`).catch(() => null),
  credits: () => req(`/api/credits`),
  extractionConfig: () => req(`/api/extraction-config`),
  login: (email, password) => req(`/api/auth/login`, json({ email, password })),
  register: (email, password) => req(`/api/auth/register`, json({ email, password })),
  logout: () => req(`/api/auth/logout`, { method: "POST" }),
  updateProfile: (body) => req(`/api/auth/me`, { method: "PATCH",
    headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  changePassword: (body) => req(`/api/auth/password`, json(body)),
  deleteAccount: () => req(`/api/auth/me`, { method: "DELETE" }),
};
