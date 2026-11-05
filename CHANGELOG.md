# Changelog

All notable changes to the engine. Versions are git tags (`vX.Y.Z`); the same version is
published to PyPI as `maseminer` and deployed as the hosted Metalens / MASEMiner service.

## 2.0.0 — unreleased

The first release of the new engine. Version numbers continue from the previous MASEMiner
app (last release 1.1.0), which this engine replaces; its source is tagged `legacy-sqlite`.

### The joint review on one page, a regression table on one card

- A workflow's review step can say what it asks of each side (`asks`): the `econ` joint review
  asks only what the extraction validation checks — the printed cells, each column's target
  regressor (picked from its coefficient rows, its cell marked in the table) and its SE type — and
  no longer shows each column's specification. Claims under a table show their type, what a
  comparison compares, and the main estimate starred. `asks` is display: contract unchanged.
- `display.sheet.target` names the field holding the rows a column is about (a table with the
  cells' row index, or a list of row labels); its cells are marked in the table. Both tables presets
  declare it.
- Claims under a table are edited in place: the fields the workflow asks of the claims side (econ:
  type, statement, cause, effect, sign, what a comparison compares, the abstract and introduction
  quotes) as editable cells and selects, the results as an editable table — where, row, value, se,
  role — with the rows citing this table marked and each estimate checked against the table in its
  first cell. An edit is a correction of the claim's record, as in the claims document, which
  "open ↗" still reaches.
- The number check no longer flags a p-value (t) row printed as a row of its own — a joint
  significance or equality test — as belonging to no coefficient: with a label of its own that names
  no coefficient it is a test statistic. The sheet draws such a row as a row of its own, not as a
  coefficient's sub-row.
- A missing confidence rating is not counted as a structural issue of the model output (an
  import carries no ratings).
- "＋ Regression column" opens the new entry (it used to land on the paper panel). A table the
  extraction missed is added from its card: "New table" asks for the table number, panel and
  column — fields the card otherwise leaves out — and shows its cells as a table to fill row by
  row; naming it moves the card to its own tab.

- A workflow over linked datasets (econ: tables and claims) is reviewed per paper: the workspace
  lists each paper once, and the claims sit under the table their results cite (by the column an
  estimate resolved to, else by the table named), each with its own ✓ / ⚑ and its estimate against
  the table; claims citing no table of the paper have a pill of their own. The strip of the paper's
  documents is not shown there — both sides are on the page. A joint review queue counts a paper's
  tables and claims together.
- A regression table is one card, titled "Table 4": every column of the table side by side, its
  panels stacked as printed (Panel A above Panel B, the columns aligned by their label), every cell
  editable — an edit goes to the column it belongs to — and one ✓ / ⚑ for the whole table. Under the
  table, the specification of the column being edited (a heading or a cell picks it): the Column tab
  is gone (outcome and target regressors open the specification, page and notes close it; the
  identifiers are visible in the table itself) and what the model returned beyond the preset ends
  the specification instead of a tab "Other". The panel head counts tables reviewed.
- Preset grammar: `display.sheet.panel` names the field whose values stack a table's blocks;
  `display.hidden` lists entry fields the review card leaves out (they stay in the data, the grid
  and every export). Both are layout: schema ids do not change. The two tables presets use them.

### Workflow datasets: a workflow over several presets on a dataset of its own

- A workflow whose extract steps name dataset roles runs on a **workflow dataset**: the row the
  linked datasets hang from (its id is their group id). It carries the workflow, holds no records,
  and its page lists the linked datasets with their presets, the papers one row per PDF with each
  side's document, what the dataset-level steps made, the Workflow panel and one release for all
  members. Groups made before it get theirs at startup. Deleting it frees the members.
- My workspace has a **Workflows** section: my workflow datasets and my datasets running a declared
  workflow, each with its linked datasets and what its run is doing; **New workflow** makes one.
  Dataset tiles say when a dataset is one side of a workflow.
- An import no longer opens the review by itself: it lands on the dataset's page, which offers
  **Review the data** or **Add to a workflow** (the dataset joins in its preset's role). Uploads of
  a workflow run go back to the workflow dataset.
- A running workflow says which step it is on and what it is doing, with an estimate of the time
  left when it can be measured (this run's pace, else earlier runs of the same kind).
- A run that waits for its owner — uploads, a review, an API key — reads **waiting for you**, not
  "running": the panel says what each side has received ("none uploaded yet"), for how long it has
  waited, and that nothing is computed until the owner uploads, reviews or presses Uploads complete.
- Wording: supplied steps read "uploaded"; the panel's opening sentence is plain.
- **Uploads complete** shows at once that the run goes on (the button reads "Working…", the Now line
  "running the next steps"), and the checks store their findings — and a review its drawn records —
  in one batch instead of one database round trip each, which made the button slow on large datasets.
- The bundled `econ` workflow (v2) leaves out the corpus-edges step for now: after the uploads the
  checks run and the joint review holds only the release. `econ-claims` keeps the step.

### Linked datasets and a joint review: the tables and the claims of the same papers as one process

- An extract step may name its dataset role; a workflow with two such steps runs over a
  **dataset group** — two datasets sharing the workflow and a group id, linked from the dataset
  page. One run takes a new paper's tables file and claims file into their own datasets, the
  checks read across the group (the claims against the paper's own tables), every member's
  panel shows the run, and a release of one member releases the other with it.
- A `review` step is a joint review stage: one gate whose worklist walks each new paper's
  documents together — the regression columns, then the claims with their verdicts against the
  tables — in the workspace queue across both datasets. The bundled `econ` workflow is that
  process; `econ-regressions` and `econ-claims` remain the single-dataset variants.

### Pipelines and loops: k runs with a vote, follow-up steps by reference, re-runs on findings

- A live extract step may run its preset `k` times per paper and merge the runs by a `vote`
  (identity fields, per-field rules): the runs are hidden `role = run` documents, the final
  carries their quotes labelled by run, and each record its support, the candidate values the
  runs disagreed on (chips in the review, adopted as corrections), flags and a trace; a
  `contested first` triage and a runs drill-down in the workspace.
- A later extract step may read the runs instead of the paper (`input.previous`): a follow-up
  preset whose prompt says `${previous}` and whose evidence cites the runs by reference, resolved
  to their real quotes. The step it reads runs inside its pipeline; a paper costs k + 1 calls.
- A `loop` step re-runs a live extract step for the papers a check still finds problems in, with
  the findings fed into the prompt, up to `max_iterations`; a credit run pays again, an own-key
  run pauses for the key. A run keeps its PDFs until it ends.

### Derive steps: dataset-level units made by the run, reviewed as drafts

- A workflow's `derive` step makes a unit of rows over an extract step's records — a model call
  over the rows, a deterministic `group` transform, or both — with `_id` and `sources` on every
  row. The run makes the draft (transform-only steps free, model calls on the worker with credits,
  or paused as `needs_key:<step>` until the owner gives a key), and the draft is reviewed as a
  change on `/derived`: kept / revised / new / removed per row, the new records behind each change,
  accept / reject, commit. The committed unit joins the analysis units, releases freeze it and
  exports write `tables/<step>.json`; `econ-claims` gained an `edges` step.
- The model picker of the vocabulary, pages, derived and dashboard-editor pages is one module.

### Workflow cards and releases: a citable version of the process

- `/workflows` lists every curated workflow (one with a card) and `/workflow?id=` is its card:
  introduction, the steps with their pinned presets linked to the preset cards, the review policy,
  the checks with their codes, the versions and the citation, the public datasets that declare it
  with their badges and last runs. "Workflows" joins the Metalens navigation.
- Moderators cut numbered **releases** of a workflow from its card and mint a DOI per release:
  the whole document frozen next to its contract and the preset releases its pins match, deposited
  as a loadable workflow folder (`workflow.json`, `card.md`, `steps.md`, `CITATION.cff`, `README`)
  with `requires` relations to the presets' DOIs. A run records the release it matched; the daily
  DOI cap counts workflow deposits too.
- The preset card's verification roll-up now pools the random draws across its public datasets.
  `econ-claims` and `masem` gained cards.
- A server in local mode moderates itself whether or not the owner row exists yet (the preset and
  workflow release routes answered 403 under a bare `uvicorn` in local mode).

### Sampled review, the review queue, and an honest badge

- A step may declare `review: {mode: sample, n | share, seed, stratify, unit}`: when it completes,
  that many of its new records are drawn at random (proportional strata, whole papers when the
  unit is `paper`, everything when the output is smaller), the draw is stored, and the gate is
  satisfied when every drawn record has a verdict. A record is checked when its last verdict after
  the draw is verified or flagged, disagreed when a flag or a value-changing correction followed.
- The **review queue** (`/workspace?queue=<run>:<step>`, from the Workflow panel and the release
  form): the sidebar lists only the drawn papers, the panel only the drawn entries in draw order,
  a verdict moves to the next pending one across papers, and the done state resumes the run or
  opens the release.
- The **badge** says how its figures came about: "Sampled 30/412 · 93% agree" for a dataset with
  draws, the estimated error rate with its 95% interval in the long label (the chip's tooltip, the
  release form, the frozen release), the deterministic checks' findings for an AI-only dataset,
  and "audited at the reviewer's choice, not a random sample" when verdicts were ad hoc. The
  catalogue now counts exactly as the dataset page does (a withdrawn verdict is no audit, screened
  papers do not count).
- The release form lists each open gate with a link into its queue; "Release anyway" is an explicit
  tick, recorded on the release. `econ-regressions` reviews a sample of thirty columns per run.

### Checks on the review cards: regression cells and claims against tables

- Two more checks in the registry: `regression-cells` (every standard error names its coefficient,
  the parsed number is the printed one, stars agree with the level and the p-value, t with
  coefficient over se, the stated N with the printed N, columns of one table with different Ns)
  and `claims-vs-tables` (each cited estimate against the companion tables' cells, the cross-check
  as a workflow step). A workflow's `crosscheck` step runs the latter once a companion is set.
- What a check found reaches the reviewer: a note on the card naming the check and the message, a
  mark on the cell the finding names, a "check findings first" order of the entries (also a
  preset's `display.triage: checks_first`), and the Workflow panel's "open" link into it.
- Bundled: `econ-regressions` gains the number check; `econ-claims` (claims supplied, contract and
  cross-check, review all).

### Workflows: the process a dataset is built with, run incrementally

- A **workflow** (`paperlens/workflows/<id>.json`, `docs/WORKFLOWS.md`) declares which presets run,
  the deterministic checks that follow, the preset's update steps and the release gate, with human
  review declared per step (`none` or `all`, with `blocks: next | release`). Presets are pinned by
  data contract; the workflow's own contract hashes the steps, their pins and the review
  declarations, so a citation of a workflow version promises the process. A dataset without a
  declared workflow runs its preset's implicit one-step workflow, so every dataset gets the same
  Workflow panel.
- **Update** on the dataset page plans a run over the staged PDFs — per step what it would do and
  how many model calls — and starts it; credits for every planned call are spent at start and given
  back per paper that never ran. The page posts each PDF with the run and step; a pipeline uploads
  its files the same way (`source: supplied`, the only choice for an import-mode preset). The run
  advances as papers land, survives the queue forgetting a job, and is reconciled every two minutes.
- **Checks** (`paperlens/checks.py`): a registry of rules that write findings, never data or
  verdicts; the first, `schema`, re-checks the declared contract on the values as stored. A check's
  `on_fail: flag` puts the records it found in front of a reviewer.
- **The release gate**: a release holds while a gate step is stale or missing or a review gate still
  wants verdicts; the owner may release anyway, which the release records. The fingerprint now
  moves when a vocabulary or page is committed or a run finishes. Releases, exports and the dataset
  recipe carry the workflow contract and the run; a document remembers its run and step.
- Bundled: `econ-regressions` (the Robustness-at-Scale tables, supplied) and `masem` (live).

### Extraction jobs: retries, refunds and the stage they are at

- A transient provider failure (a rate limit, an overload, a 5xx, a timeout) re-queues the
  extraction job with backoff instead of ending it; any other failure, the last failed try and an
  abort from the page give the consumed credits back at once. Before, the refund waited for a third
  try that never came, so a failed credit run kept its credit.
- The model call runs in a worker thread, so the job's timeout and the page's "stop" reach a call
  in progress; a running job reports its stage, which the job status carries as `progress`.
- Credits can be spent and refunded in multiples (`n`), for runs that plan several calls.
- A paper added to a dataset inherits the dataset's preset parameters when the page posts none, so
  its provenance matches the papers before it.

### A regression column as printed: the table-shaped cells view

- **`display.sheet`** draws a table field of the entries as the printed table it transcribes, in place of
  the generic one-row-per-cell list: one row per printed row, in row order, the printed text in the cell,
  and one column per entry of the same table (the entries whose `group` fields agree — table number and
  panel) under the column labels the paper prints and the outcome each estimates. The open entry's column
  is tinted and editable; its siblings stand beside it read-only, each opening its own entry from its
  heading and showing its own evidence from a cell. A standard-error (t, p, CI) row sits under the
  coefficient it refers to, without a label of its own, as printed; statistics rows sit under a rule.
  Everything the list showed is in the cell's tooltip; "▤ every field" switches back to the list, and the
  choice is remembered.
- Correcting the printed text of a cell ("0.94***" → "0.95***") carries the parsed number and the star
  count along when they still matched the old text; an earlier independent correction of either stands.
- A click on a printed cell pinpoints its number on the row of its label in the PDF (`display.locate`
  over `cells` in both tables presets); stars and parentheses are stripped before the search.
- With a sheet declared, the entry strip above the cards and the sidebar list one pill per **table**
  (table number and panel), not one per column: a pill carries its verified-over-total column count and
  the state of its columns, and opens the first column still to review; the columns are switched from
  the sheet's headings. A paper with 45 regression columns in nine panels shows nine pills.
- A sentence written as a column's outcome (a balance table's "one regression per row") is clipped in
  the sheet's heading row, whole in its tooltip.
- `register-tables` and `econ-regression-tables` declare the sheet over `cells`.

### One paper, every side: the paper's documents across presets

- **The paper's document strip.** Above the review panel, one pill per document of the open paper
  the reviewer owns — its regression tables under one preset, its claims under another — with
  verified / total and the review shade; a click switches to that document in place (the sidebar
  keeps the dataset being walked through, Back returns). A reviewer finishes a paper on both sides
  before moving to the next, instead of all tables first and every paper again for the claims.
  `GET /api/documents/{id}/paper-documents` lists them (same PDF hash or same paper row), with the
  companion pairs among their datasets.
- **The two sides linked.** A cross-check verdict that resolves a column now names that column's
  record (`companion_record_id`): in the chain layout a result row's "column ↗" opens the regression
  column it cites in the tables document, focused; a regression column lists the claims citing it
  ("cited by S1 · R1 ✓") with links back into the claims document.
- `econ-regression-tables` is a registered companion of `register-claims` next to `register-tables`
  (`companion_presets` in the cross-check payload; the dataset page offers datasets of either).

### Preset inventory: one citable card per curated preset

- **A preset has a card.** A bundled preset may carry `meta.authors` (with ORCIDs), `keywords`, `license`,
  `related` work and a `<id>.card.md` sibling with its introduction, how it extracts, its verification
  study, limitations and related work. None of it enters the schema id. First cards: the two MASEMiner
  presets and a new import-mode preset, `econ-regression-tables`, for the Robustness-at-Scale transcription.
- **`/presets` and `/preset?id=`.** The inventory lists every documented preset with its latest release,
  DOI and what was built with it; the card page shows the prose, every field documented from the
  declaration, a live verification roll-up over the public datasets built with the preset (under the
  dataset badge rule), the public datasets (with the preset version that produced each), the dashboards
  over them, versions and citations. The personal-preset editor moved behind `?edit=1`.
- **Releases and DOIs for presets.** Moderators (`PAPERLENS_ADMINS`) cut numbered releases of a curated
  preset (`preset_release`: the frozen spec, prompt and card next to the schema row they hash to) and
  mint a DOI per release on Zenodo (`upload_type: software`, the authors as creators, relations to the
  card's papers and to the dataset releases built with it). The deposit is a loadable preset folder
  plus `fields.md`, `CITATION.cff` and a README; the same files download from the card.
- `renderMarkdown` renders links (https, doi:, anchors, site paths), emphasis, pipe tables, blockquotes,
  rules and nested fences; `GET /api/datasets/public?preset=` filters the catalogue by preset.

### Quotations that leave the server are cut to 200 characters

- **The 200-character rule** (`paperlens/quotes.py`). Everything a reader who does not own the data can
  see shows at most 200 characters of each quotation, cut around the extracted value and marked with an
  ellipsis, page and source kept: the release files (GitHub, Zenodo, the devkit), the dataset folder's
  `results.json` in the datasets repository, the dashboard
  evidence endpoint, the analysis-table evidence of a public dataset, the public record and provenance
  APIs. The full quotations stay in the database for the owner's review screen and the owner's own
  reads. Publishers' text-and-data-mining terms allow published snippets of that length with a link
  to the article, which every record carries as its DOI.
- `release.json` states the rule under `evidence_policy` and names the dataset's page (`dataset.id`,
  `dataset.url` as `/dataset?slug=`, which survives the copy to another Metalens server; the page
  redirects a slug to the id); `evidence.json` carries the same `policy` and marks cut items
  `truncated: true`. The devkit caps its display at the release's limit and, under pinned evidence,
  states the rule and links the dataset page; dashboard tooltips mark a cut quote.
- **Verbatim fields.** A field or table column may be declared `verbatim: true` when its value IS a
  quotation of the paper (the claims preset's abstract and introduction sentences and stated
  magnitudes, the tables presets' captions). Public copies cut those values to 200 characters too: the
  analysis table for non-owners (and so release files and dashboards), the published `results.json`,
  record detail, the rows API, the catalogue search, a paper's records, the dataset detail and the
  dataset download. The flag never enters the schema id, and data stored under an older version of
  the preset, or before a field was flagged, follows the live preset's flag by field name. The card marks such fields "verbatim".
- Publishing again before the pull request is merged updates that pull request instead of failing.

### Everything else

- A preset release's deposited `card.md` writes the card page's "to be published soon" links as plain
  text, and its README lists the card's actual sections and how to load the preset locally.
- Workspace tiles keep their titles clear of the rename and delete buttons.
- **`display.inline_evidence`**: the review card shows, under each field, its confidence note and the quotes
  that cite it, each with its source label ("supporting", "questioning", a table) and page, a button that jumps
  to the highlight. For presets where the reviewer judges each call against the model's reading.
- Without a DOI, the top of a preset card says what the viewer can do: a moderator gets "Release vN and
  mint a DOI" (to the release form, DOI ticked) or the reason minting is not possible here; a signed-in
  account that is not a moderator reads that only moderators release presets.
  A moderator who may not mint on this server (the Zenodo allow-list, the daily cap) sees why, in the
  version list and in the release box, instead of a bare "no DOI".
- A preset card opens with **How to cite**: the citation, a Copy button and the Zenodo link (the concept
  DOI, which opens the newest version), or "No DOI yet". The Models table names the datasets each model's
  records come from.
- Card markdown shows pictures from the site (`![alt](/static/…)`, or https) and titled folds
  (a fence opened with ```` ```fold <title> ````); a deposited card links site paths in full.
- The claims card follows one claim through the records in a figure drawn in HTML and the site's theme
  (`static/card_figures.js`, placed by a ```` ```figure claim-chain ```` fence; steps 1–3 this preset, 4–8 the
  Danish Causal Atlas), describes the two calls in the reviewed wording with illustrative examples of
  the support grades, and reproduces both prompts as run (matched to the hashes the runs recorded).
- The claims card drops its related work, names Claude Opus 5 as its verification model and reports the
  consistency of two independent runs on the Danish corpus (91.4 % of edges stated by both runs, the same
  sign in 97.9 % of those).

- **The FAQ explains the local version** step by step: install uv, `uvx metalens` (or straight from
  GitHub until the package is on PyPI), the data folder and `--data-dir`, presets from files, importing
  without a model or extracting with an own key or a local model, exporting, updating, and `maseminer`.

- **A `metalens` package and command.** The engine runs locally as Metalens too: `metalens` opens the
  Metalens brand with every bundled preset, data in `~/.metalens`, port 8775 (`maseminer` keeps
  `~/.maseminer` and 8765). The release workflow publishes two PyPI projects from one tag: `maseminer`
  (trimmed to the MASEM presets, without the `metalens` command) and `metalens` (the full engine,
  renamed by `scripts/package_as.py`).
- **Load a preset file.** *My workspace* → Presets loads a preset someone sent as a `.json` file, checks it
  (every error and warning, and the schema id it runs under) and saves it as the viewer's own preset,
  private or — signed in — for everyone on the site. A loaded preset keeps its own id, and so its
  schema id, when no built-in, retired or personal preset uses it; otherwise it gets a fresh id and
  the page says so.

- **Preset pictures and smaller tiles.** The presets page shows each curated preset's picture
  (`static/tiles/presets/<id>.png`, served as `tile_url` in the inventory and card payloads) in
  smaller tiles, three to a row; a preset without one keeps the placeholder sketch. The dataset page
  no longer explains imported records at length: its source card names the producer and the schema.

- **Locating quotes is fast again for papers with unlocatable quotes.** A quote missing from its
  cited page was searched for piece by piece on every page with PyMuPDF's page search, about 4,000
  calls for a table line printed differently from its transcription; one 63-page paper took 8.5
  minutes and timed out on the hosted site. Each page's letters and digits are now read once, and a
  page that cannot contain the piece is skipped. Same highlights on every paper compared (15 of 15),
  that paper in 17 seconds.

- One engine, two product surfaces: the brand (Metalens or MASEMiner) is chosen per request
  from the hostname (`paperlens/brands.py`, `GET /api/brand`), with its own landing page,
  page chrome and preset list (`meta.brands` on presets).
- Single-user local mode: `maseminer` starts the engine on one machine with an embedded
  PostgreSQL (`pgserver`), no accounts, no Redis, extractions inline; data in `~/.maseminer`.
- Logged-out retention: uploads of anonymous sessions are deleted after two idle hours
  (`paperlens/retention.py`); a durable free-trial counter and a per-network daily cap.
- Import page: re-importing a paper into its dataset replaces the old copy; datasets can
  remove duplicate papers; the citable dataset download can be re-imported.
- Review layout `evidence` (`display.review: {layout: "evidence", items: <entry text field>,
  paper_fields?}`): a paper is reviewed evidence first — the reader's numbered items
  (passages and tables) in order, each with a jump into the PDF and, beneath it, the study
  and entry cells it bears on and the sub-entry fields it names; what no item bears on
  follows, so nothing is hidden. Evidence rows name their item in `source` ("item 7
  (table) → result: mean"). The cells, saves and verification are the generic card's.
- Review layout `stages` (`display.review: {layout: "stages", items, stages: [{id, label, help,
  paper_fields | fields | child + child_fields, copy_from_sibling?}]}`): a paper is coded in
  stages (a→g, `]`/`[`) in three panes. The left panel holds what the coder fills in: the
  stage's instruction, its cells (paper fields; every entry as a card with "＋ add"; or each
  entry's arms / results with "＋", "✕", "copy … from <entry>" and "same as <the reference
  row>"), then "◂ · next ▸", which becomes "next stage ▸" on the stage's last item and "done" on
  the last stage. The right column (half the window by default, the splitter drags it) shows
  the reader's items that bear on the stage's fields in its upper pane, as one bare list (the
  page tag hanging at the left of the quote, or above a table) with a cursor over them (the rest
  folded at the end, the coder's marks after), and the PDF in its lower pane (half the column
  by default, a bar between the two drags it; "▸" collapses the PDF to a strip along the
  column's foot, the icon there opens it; kept per browser), which follows the cursor while
  open at the coder's zoom, scrolled so the current item's highlight starts just under its top
  edge (`→`/`←` or "◀ item k of n ▶" at the top of the pane; the cursor moves whether or not
  the pane is open). A stage's `auto_id: {field, first?, next}` fills the id of
  an added entry or row ("C", then "T1", "T2" …). A stage may name the entry's fields together
  with one child's (`fields` + `child` + `child_fields`): each entry's cells, then its rows under
  them, `auto_id` a list with one per level (the tasks with their arms); any of the three levels
  may come together, the paper's cells first. A stage with a paper table and a child may declare
  `accept_rows: {from, child, id_column, id_field, verdict_column, accepted, task_column?}`: every
  row of that table gets "＋ row" at its start, which copies its same-named columns into a new row
  of the child under the entry its task column names (the only entry, or one the coder picks),
  writes the row's id into the child's id field and sets the row's verdict to `accepted` — the
  model's estimates verified into the coder's rows, never retyped; its optional `lookup: {table,
  key_column, take_column, into_field}` carries one more value over from another paper table by
  the row's key (the coder's outcome id from the alignment table); a list of lookups is taken in
  turn, and one with `into_field: "$entry"` picks the entry the row goes under; `group_by` names a
  column whose runs open with a header naming the group and the coder's unit it was matched to. A
  stage names several children as `children: [{key, fields}]` (the arms and the outcomes under each
  task), `copy_from_sibling` then naming its child; `match: {table, columns: {column: {from, value,
  label}}}` turns the named columns of the paper table into selects fed by the paper's own units (the
  entries, or a child's rows), lists the coder's units over the table and the units no row points to
  under it; `link: [{child, field, value, label, when?, skip_when?}]` turns a child field that
  names a sibling row into a select of the sibling rows (the reference arm a treatment arm is
  compared with), a dash on the rows `skip_when` marks; an auto id's `first_values` fills the
  first row's other cells (the reference arm's label); `accept_rows.skip_fields` keeps named
  columns out of the accepted row. A paper table row with evidence of its own carries a page tag
  and jumps to it on a click; an entry's child table whose rows cite nothing drops its citation
  column; child-table headers carry their help as a tooltip. The foot of a stage moves between stages ("next ▸", "◂"; the PDF pane's control and
  ← → move the items), and on the last stage reads "✓ done with this paper" where the preset
  verifies per paper. An editable list field is typed as comma-separated items. `display.review.verify:
  "paper"` drops the per-entry status, verify and flag (the coder built the entries) for one
  "✓ done with this paper"; the tables in a stage's fields block are set tight. The staged head shows the
  paper's title alone (the sidebar switches papers, the stage bar counts) and keeps Raw, Save and
  JSON only — no Fields or Grid toggle, no ＋ entry (the stage adds), no CSV; the paper strip at
  the left narrows to one PDF icon per paper (its name on hover, the count in the corner); the
  evidence list is set a size smaller. The items table may sit on the paper, so a blind import with no entries opens on the
  stages and the coder adds the first entry; adding an entry retires the "screened — no records"
  sentinel. Verification stays per entry; "verify all" finishes a paper. The Fields view is the
  two-column one, unchanged (the panel and the PDF).
- `allow_other` on `multi` fields and table columns: the widget takes a term of the paper's own
  after the declared boxes (Enter adds it), stores codes and terms in one list, draws a stored
  off-list value as a removable chip (never dropped unseen), and the validator no longer flags
  such values.
- The generic card adds and removes sub-entries (an arm, a result: "＋", "✕"), logged as
  corrections like a table row; a cell inside a paper-level table is saved with its table.
- A stored schema row follows its preset file: when a file with the SAME schema id is loaded
  (at start-up, at each listing of the presets, at an import), the row's wording — labels,
  help, titles, option help, stage help — its version and its whole `display` block are
  replaced by the file's, the contract keys untouched (the id is the contract, so the data
  shape cannot change); tooltips, auto ids and layout changes thus reach papers already
  imported. A row of another contract is left alone; the workspace then says "preset file
  now vN (hash)" next to the paper's own schema id.
- A coder's highlighter, "mark only, no cell link": in marking mode (`✎ mark` at the top of the
  PDF column, or `h`) a click on a page marks the sentence under it — the server finds the word
  at the point in the page's text layer and expands it to its sentence (boundaries at `. ! ?`
  before a capital or a digit, abbreviations spared; the paragraph, or the clicked line of a
  long one, when there is none) — painted violet apart from the model's evidence, listed under
  "Your marks" at the end of the stage's evidence list (page and sentence, click to jump, ✕ to
  remove; a click on a mark in marking mode removes it too), kept with the document
  (`coder_highlight`: `GET`/`POST /api/documents/{id}/highlights`, `DELETE /api/highlights/{id}`,
  the paper's owner only) and exported: `highlights` per paper in the dataset export and in the
  workspace's JSON download (not yet in release files).
- Definitions on hover: an option of an enum or multi field (or table column) may carry `help`
  (`{value, label, help}`) — the review shows it as the title of the checkbox label, of each
  `<option>` and of the select itself (the chosen option's definition, else the field's help);
  a field with help gets a dotted underline and a small ⓘ on its label. Wording only: it never
  re-versions a dataset. The help shows at once in one custom tooltip (`[data-tip]`, shown on
  hover or focus, below or above the element within the window, hidden on leaving or Escape),
  not through the browser's slow native title: on the field label and its ⓘ, on each checkbox
  label, on the select (the chosen option's definition) and on a ⓘ after the select listing
  every option as "label — definition" under the field's help. While the tip is up the native
  title of the element and of its ancestors is held back (and put back after), so the text no
  longer appears twice, the browser's bubble over the tip.
- Review: empty cells are editable; identical highlight rectangles are painted once; a
  click on a number lands on the citation that contains it.
- Declarative preset format (format 2) with generated evidence / confidence sections,
  content-addressed schema ids, saved setups, the human–AI collaboration preset.
- Exports carry the engine version and commit and, per paper, the prompt hash, resolved
  model, parameters and extraction time.
- A cell edit in the review screen now sticks. Each save names only its cell and the server
  sets it on the record as stored (`patch` on `POST /api/records/{id}/verify`, a row lock for
  the write); the page applies the edit to its own copy at once, keeps two edits of one cell in
  order, and a paper switch waits for saves still in flight. Before, every save sent the whole
  record as the page knew it and the server replaced it, so two edits within one round trip
  raced and the later one restored the earlier cell — coders saw values "come back" after
  moving between columns or papers, sporadically, more on a slow connection.
- The MASEMiner distribution ships the MASEMiner presets only: `scripts/masem_only.py` trims a
  checkout to them (and to what is about them) before the wheel, the image and the mirror
  repository are built from it; the engine repository keeps every preset.

## 1.1.0 and earlier

- The previous MASEMiner application (FastAPI + SQLite), now retired.
