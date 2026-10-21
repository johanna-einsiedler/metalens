// "＋ Add papers" on a dataset page and in the review sidebar: extract new PDFs with the dataset's
// recipe, or import results made elsewhere — a choice, not a jump. A dataset of an import-mode
// preset has no prompt to run, so it goes straight to Import.
export function addPapersMenu(id, importOnly, cls) {
  const imp = `/import?dataset=${encodeURIComponent(id)}`, ext = `/extract?dataset=${encodeURIComponent(id)}`;
  if (importOnly) return `<a class="${cls}" href="${imp}" title="this dataset holds results made elsewhere: add more the same way">＋ Add papers</a>`;
  return `<details class="addmenu"><summary class="${cls}" title="extract new papers, or import results made elsewhere">＋ Add papers</summary>
    <div class="addmenu-pop">
      <a href="${ext}"><b>Extract with a model</b><span class="muted">upload PDFs; this dataset's recipe runs on them</span></a>
      <a href="${imp}"><b>Import results</b><span class="muted">JSON made elsewhere, with the PDFs, reviewed under this dataset's preset</span></a>
    </div></details>`;
}
