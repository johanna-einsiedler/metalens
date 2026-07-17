// Save-to-workspace flow: ensure the user is signed in (claims their anonymous
// work), then name a project (= a dataset) and choose Public/Private. Used from
// the extraction results step and the workspace. No page reload — modals resolve
// promises so the caller can continue. Reuses the .modal CSS from auth.js.
import { api } from "/static/api.js";
import { mountAccount } from "/static/auth.js";

function makeModal(innerHTML) {
  const ov = document.createElement("div");
  ov.className = "modal-overlay";
  ov.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${innerHTML}</div>`;
  document.body.appendChild(ov);
  const close = () => ov.remove();
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
  return { ov, close };
}

// Sign in / create account inline; resolves the user object, or null if cancelled.
function authModal() {
  return new Promise((resolve) => {
    const { ov, close } = makeModal(`
      <h3 id="sv-atitle"></h3>
      <p class="muted">Saving keeps your PDF + results in your workspace. Anonymous work is claimed on sign-in.</p>
      <form id="sv-aform">
        <input id="sv-email" type="text" placeholder="email or username" autocomplete="username" required/>
        <input id="sv-pw" type="password" placeholder="password" autocomplete="current-password" required/>
        <div id="sv-aerr" class="pl-err"></div>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" id="sv-acancel">Cancel</button>
          <button type="submit" class="btn btn-primary" id="sv-asubmit"></button>
        </div>
      </form>
      <p class="muted pl-toggle" id="sv-atoggle"></p>`);
    let mode = "login";
    const render = () => {
      const reg = mode === "register";
      ov.querySelector("#sv-atitle").textContent = reg ? "Create account to save" : "Sign in to save";
      ov.querySelector("#sv-asubmit").textContent = reg ? "Create account" : "Sign in";
      ov.querySelector("#sv-atoggle").innerHTML = reg
        ? 'Have an account? <a href="#" id="sv-aswitch">Sign in</a>'
        : 'No account? <a href="#" id="sv-aswitch">Create one</a>';
      ov.querySelector("#sv-aswitch").onclick = (e) => { e.preventDefault(); mode = reg ? "login" : "register"; render(); };
    };
    render();
    ov.querySelector("#sv-acancel").onclick = () => { close(); resolve(null); };
    ov.querySelector("#sv-aform").onsubmit = async (e) => {
      e.preventDefault();
      const email = ov.querySelector("#sv-email").value.trim();
      const pw = ov.querySelector("#sv-pw").value;
      const err = ov.querySelector("#sv-aerr"), btn = ov.querySelector("#sv-asubmit");
      btn.disabled = true; err.textContent = "";
      try {
        await (mode === "register" ? api.register(email, pw) : api.login(email, pw));
        close(); resolve(await api.me());
      } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
    };
    setTimeout(() => ov.querySelector("#sv-email").focus(), 0);
  });
}

// Name the dataset + pick visibility; resolves {name, visibility} or null.
function projectModal(defaultName) {
  return new Promise((resolve) => {
    const safe = (defaultName || "").replace(/"/g, "&quot;");
    const { ov, close } = makeModal(`
      <h3>Save to your workspace</h3>
      <p class="muted">Name this dataset and choose who can see the results.</p>
      <form id="sv-pform">
        <input id="sv-name" type="text" placeholder="dataset name" value="${safe}" required/>
        <div class="radio-row" style="margin:10px 0">
          <label class="radio"><input type="radio" name="sv-vis" value="private" checked/> Private</label>
          <label class="radio"><input type="radio" name="sv-vis" value="public"/> Public</label>
        </div>
        <p class="muted" style="font-size:12px;margin:0">Public shares the <b>results</b> (never your PDFs) in the Data Catalogue. You can change this later.</p>
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
  let me = await api.me();
  if (!me || !me.email) {
    me = await authModal();
    if (!me) return null;
    try { await mountAccount(); } catch { /* topbar refresh is best-effort */ }
  }
  const project = await projectModal(defaultName);
  if (!project) return null;
  const ds = await api.createDataset({
    title: project.name, visibility: project.visibility,
    prompt: recipe.prompt || null, model: recipe.model || null, schema_id: recipe.schema_id || null,
  });
  for (const id of documentIds) await api.addToDataset(ds.id, { document_id: id });
  return { ...ds, visibility: project.visibility };
}
