// Save-to-workspace flow: name a project (= a dataset) and, when signed in, choose
// Public/Private. No account is needed: a logged-out dataset is private to the browser
// session and claimed by the account created later. Used from
// the extraction results step and the workspace. No page reload — modals resolve
// promises so the caller can continue. Reuses the .modal CSS from auth.js.
import { api } from "/static/api.js";

function makeModal(innerHTML) {
  const ov = document.createElement("div");
  ov.className = "modal-overlay";
  ov.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${innerHTML}</div>`;
  document.body.appendChild(ov);
  const close = () => ov.remove();
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
  return { ov, close };
}

// Name the dataset + pick visibility; resolves {name, visibility} or null.
function projectModal(defaultName, { anonymous = false } = {}) {
  return new Promise((resolve) => {
    const safe = (defaultName || "").replace(/"/g, "&quot;");
    const { ov, close } = makeModal(`
      <h3>${anonymous ? "Save as a dataset" : "Save to your workspace"}</h3>
      <p class="muted">${anonymous ? "Name this dataset. You are not signed in, so it stays private and is kept for two hours after your last activity; create an account on the next page to keep it." : "Name this dataset and choose who can see the results."}</p>
      <form id="sv-pform">
        <input id="sv-name" type="text" placeholder="dataset name" value="${safe}" required/>
        <div class="radio-row" style="margin:10px 0"${anonymous ? " hidden" : ""}>
          <label class="radio"><input type="radio" name="sv-vis" value="private" checked/> Private</label>
          <label class="radio"><input type="radio" name="sv-vis" value="public"/> Public</label>
        </div>
        <p class="muted" style="font-size:12px;margin:0"${anonymous ? " hidden" : ""}>Public shares the <b>results</b> (never your PDFs) in the Data Catalogue. You can change this later.</p>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" id="sv-pcancel">Cancel</button>
          <button type="submit" class="btn btn-primary" id="sv-psubmit">Save dataset</button>
        </div>
      </form>`);
    ov.querySelector("#sv-pcancel").onclick = () => { close(); resolve(null); };
    ov.querySelector("#sv-pform").onsubmit = (e) => {
      e.preventDefault();
      const name = ov.querySelector("#sv-name").value.trim();
      if (!name) return;
      const visibility = (ov.querySelector("input[name='sv-vis']:checked") || {}).value || "private";
      close(); resolve({ name, visibility });
    };
    setTimeout(() => ov.querySelector("#sv-name").focus(), 0);
  });
}

// Orchestrates the whole flow. Returns the created dataset (with .visibility), or
// null if the user cancelled at any step. `recipe` (prompt/model/schema_id) is
// stored on the new dataset so re-opening it can add papers with the same settings.
export async function saveToWorkspace(documentIds, { defaultName = "", recipe = {} } = {}) {
  // No account needed to save: a logged-out dataset is private, belongs to this browser
  // session and is deleted after the idle timeout unless an account claims it (the dataset
  // page says so). Signing in is offered there, not forced here.
  const me = await api.me();
  const anonymous = !me || !me.email;
  const project = await projectModal(defaultName, { anonymous });
  if (!project) return null;
  const ds = await api.createDataset({
    title: project.name, visibility: project.visibility,
    prompt: recipe.prompt || null, model: recipe.model || null, schema_id: recipe.schema_id || null,
  });
  // Add each paper independently — one failure must not drop the rest of the batch.
  let saved = 0; const failed = [];
  for (const id of documentIds) {
    try { await api.addToDataset(ds.id, { document_id: id }); saved++; }
    catch { failed.push(id); }
  }
  if (failed.length) console.warn("saveToWorkspace: some papers failed to add to dataset", failed);
  return { ...ds, visibility: project.visibility, saved, failed: failed.length };
}
