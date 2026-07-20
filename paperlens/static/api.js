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

async function req(path, opts = {}) {
  const r = await fetch(path, {
    credentials: "same-origin",
    headers: { "X-Session-Id": sid(), ...(opts.headers || {}) },
    ...opts,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* ignore */ }
    throw new Error(`${r.status} ${msg}`);
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
  myDatasets: () => req(`/api/datasets`),
  createDataset: (body) => req(`/api/datasets`, json(body)),
  addToDataset: (id, body) => req(`/api/datasets/${id}/add`, json(body)),
  deleteDocument: (id) => req(`/api/documents/${id}`, { method: "DELETE" }),
  deleteDataset: (id) => req(`/api/datasets/${id}`, { method: "DELETE" }),
  setDatasetVisibility: (id, visibility) =>
    req(`/api/datasets/${id}`, { method: "PATCH", headers: { "content-type": "application/json" },
                                 body: JSON.stringify({ visibility }) }),
  publishDataset: (id) => req(`/api/datasets/${id}/publish`, { method: "POST" }),
  record: (id) => req(`/api/records/${id}`),
  recordsProvenance: (ids) => req(`/api/records/provenance`, json({ ids })),
  deleteRecord: (id) => req(`/api/records/${id}`, { method: "DELETE" }),
  addRecord: (docId, body) => req(`/api/documents/${docId}/records`, json(body)),
  documents: (f) => req(`/api/documents${qs(f)}`),
  checkDuplicates: (hashes, schemaId) => req(`/api/documents/check-duplicates`, json({ hashes, schema_id: schemaId })),
  setDocumentField: (docId, key, value) => req(`/api/documents/${docId}/set-field`, json({ key, value })),
  updatePaper: (docId, fields) => req(`/api/documents/${docId}/paper`, { method: "PATCH",
    headers: { "content-type": "application/json" }, body: JSON.stringify(fields) }),
  documentView: (id) => req(`/api/documents/${id}/view`),
  recordEvents: (id) => req(`/api/records/${id}/events`),
  locateValue: (id, value, page) =>
    req(`/api/documents/${id}/locate?value=${encodeURIComponent(value)}&page=${page}`),
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
  myPresets: () => req(`/api/presets/mine`),
  presetPrompt: (id) => req(`/api/presets/${id}/prompt`),
  presetDetail: (id) => req(`/api/presets/${encodeURIComponent(id)}/detail`),
  createPreset: (body) => req(`/api/presets`, json(body)),
  updatePreset: (id, body) => req(`/api/presets/${encodeURIComponent(id)}`,
    { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  deletePreset: (id) => req(`/api/presets/${encodeURIComponent(id)}`, { method: "DELETE" }),
  buildPresetPrompt: (body) => req(`/api/build-preset-prompt`, json(body)),
  models: () => req(`/static/models.json`),
  testKey: (body) => req(`/api/providers/test`, json(body)),
  designPrompt: (body) => req(`/api/design-prompt`, json(body)),
  extract: (formData) => req(`/api/extract`, { method: "POST", body: formData }),
  ingest: (body) => req(`/api/ingest`, json(body)),
  job: (id) => req(`/api/jobs/${id}`),
  me: () => req(`/api/auth/me`).catch(() => null),
  credits: () => req(`/api/credits`),
  login: (email, password) => req(`/api/auth/login`, json({ email, password })),
  register: (email, password) => req(`/api/auth/register`, json({ email, password })),
  logout: () => req(`/api/auth/logout`, { method: "POST" }),
  updateProfile: (body) => req(`/api/auth/me`, { method: "PATCH",
    headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  changePassword: (body) => req(`/api/auth/password`, json(body)),
  deleteAccount: () => req(`/api/auth/me`, { method: "DELETE" }),
};
