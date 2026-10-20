# Changelog

All notable changes to the engine. Versions are git tags (`vX.Y.Z`); the same version is
published to PyPI as `maseminer` and deployed as the hosted Metalens / MASEMiner service.

## 2.0.0 — unreleased

The first release of the new engine. Version numbers continue from the previous MASEMiner
app (last release 1.1.0), which this engine replaces; its source is tagged `legacy-sqlite`.

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
- Without a DOI, the top of a preset card says what the viewer can do: a moderator gets "Release vN and
  mint a DOI" (to the release form, DOI ticked) or the reason minting is not possible here; a signed-in
  account that is not a moderator reads that only moderators release presets.
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
