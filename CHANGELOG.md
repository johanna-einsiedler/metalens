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

- **The GitHub sync runs daily and costs one request when nothing changed.** It reads the datasets
  repository's newest commit and stops when that is the commit it reconciled last time (stored in
  `sync_state`); otherwise one request lists every file with its version, and only folders whose
  `results.json` changed are read. The dataset page's "Check GitHub" still checks every folder.

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
