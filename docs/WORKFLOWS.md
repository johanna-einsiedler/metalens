# Workflows

A preset says what one model call per paper extracts. A **workflow** says which presets run, in
what order, what deterministic checks and dataset-level steps follow, and where a person looks
before a release: every record, a random sample, or none. The dataset keeps the state (records,
drafts, runs); the workflow declares the process, and is versioned and citable like a preset.

## The file

`paperlens/workflows/<id>.json`, with sibling files (`<id>.card.md`, derive prompts) inlined on
load. Directories in `PAPERLENS_WORKFLOW_DIRS` are read too; bundled ids win.

```jsonc
{"format": 1, "id": "econ-regressions", "version": 1,
 "meta": {"title": "…", "tagline": "…", "authors": [...], "license": "CC-BY-4.0"},
 "card": {"file": "econ-regressions.card.md"},
 "dataset": {"unit": "paper", "key": "pdf_sha256"},
 "steps": [
  {"id": "tables",   "kind": "extract", "preset": "econ-regression-tables@67780825", "source": "supplied",
                     "review": {"mode": "all", "unit": "entry", "blocks": "release"}},
  {"id": "contract", "kind": "check", "check": "schema", "over": ["tables"], "on_fail": "note"},
  {"id": "release",  "kind": "release", "gate": ["tables"]}]}
```

- **Steps are the graph.** A step may only read earlier ones; the last step is the release.
- **Pins are contracts.** `preset` names a schema id. The same contract means the same dataset
  shape; a wording change does not re-version the workflow. Each run records the preset release
  and the prompt hash that actually ran.
- **Kinds.** `extract` (a preset over the PDF; `source: live` runs it on the worker, `source:
  supplied` waits for the pipeline's uploads — the only choice for an import-mode preset),
  `check` (a rule from the registry in `paperlens/checks.py`; writes findings, never data),
  `vocabulary` / `pages` / `crosscheck` (the preset-level update steps, same keys), `release`
  (`gate` lists the steps that must be fresh and reviewed). `derive`, `loop`, `agent`, `k > 1`
  and `input.previous` are grammar-valid and refused at plan time until their release, and so
  is a second extract step: a workflow runs one preset into one dataset for now (claims and
  tables stay two datasets joined by the companion cross-check); linked datasets come later.
- **Review** is an attribute of a step: `mode: none | all | sample`, `unit: entry | paper`,
  `blocks: next | release` (default `release`: later steps run, only the release waits). A
  draft-producing step defaults to `all`, an extract step to `none`. `sample` draws `n` records
  (or a `share`) from the step's new output at random, with a `seed` (default: from the run and
  step) and `stratify` fields (proportional quotas, at least one per stratum when the size allows,
  `unit: paper` draws whole papers); when the output is smaller than the size, every record is
  drawn. The draw is stored (`workflow_sample`); a drawn record is checked when its last verdict
  after the draw is verified or flagged, and disagreed when a flag or a value-changing correction
  followed the draw. The dataset's badge reports the draws: "Sampled 30/412 · 93% agree", with the
  estimated error rate and its 95% interval in the long label, next to the audited share; a
  record verified outside a draw never enters the estimate.
- **A check's `on_fail`**: `note` records findings; `flag` puts every record with a finding at or
  above `severity` in front of a reviewer (a release-blocking gate); `fail` fails the run.

## The checks

| check | reads | finds |
|---|---|---|
| `schema` | any preset | the declared contract on the values as stored: a required field missing, a wrong type, an option not declared |
| `regression-cells` | `econ-regression-tables`, `register-tables` | an se/t/p/CI row naming no coefficient (`se_dangling_ref`) or a non-coefficient (`se_refers_noncoef`), two se rows for one coefficient (`coef_many_se`), a parsed number that is not the printed one (`numeric_mismatch`), stars against the stated level (`stars_vs_level`) or the p-value (`stars_vs_p`), t against coefficient / se (`t_vs_coef_se`), the stated N against the printed N (`n_obs_vs_sample`), columns of one table with different Ns (`n_obs_varies`, a note), row indexes not from 0 (`row_index_gap`) |
| `claims-vs-tables` | `register-claims`, needs the dataset's companion | a cited estimate no coefficient cell of the resolved column equals (`estimate_mismatch`), a cited exhibit no column matches (`exhibit_unlinked`) |

Findings reach the reviewer on the card ("✗ 2 check findings — regression-cells: …"), on the cell
each one names, and in the "check findings first" order of the entries; the Workflow panel links
there. Parameters are declared per check (`t_tolerance`, `star_legend`, `ignore`) and typed.

## The contract

`workflow_id` = `<id>@<sha8>` over the dataset unit and the steps with their pins, checks and
**review declarations** — labels, help texts and a derive prompt's wording are left out. A
dataset names its workflow by contract (`dataset.workflow`); without one it runs its preset's
implicit one-step workflow (the preset, its `meta.update` steps, a release gated on the
extraction), so every dataset has the same Update panel.

## A run

From the dataset page: **Update** plans the run over the staged PDFs — per step what it would do
(extract, attach a document the owner already has under that contract, wait for an upload) and
how many model calls — then starts it. Credits for every planned call are spent at start and
given back per paper that never ran. The page posts each PDF to `POST /api/extract` with
`run_id` and `step_id`; a pipeline posts its files to `POST /api/ingest-pdf` the same way. The
run advances as papers land: checks run, gates open, the release step closes the run. One run
per dataset at a time; a run that has not moved for ten minutes is reconciled with the queue
every two minutes (`workflow_tick_task`).

A gate's items are a worklist: `GET /api/workflow-runs/{rid}/queue/{step}` lists them in draw
order with their verdicts; `/workspace?queue=<run>:<step>` works through them — the sidebar lists
only the drawn papers, the panel only the drawn entries, a verdict advances to the next pending
one across papers, and the done state resumes the run or opens the release.

A review gate with `blocks: next` pauses the run (`awaiting_review:<step>`) until every record of
the step has a verdict or the owner resumes with `override`, which the gate records. A gate with
`blocks: release` lets the run finish and holds the release: `POST /api/datasets/{id}/releases`
answers 409 with the gate until the verdicts are in, or the owner passes `override_gates` /
`allow_stale`, which the release records (`gates`).

## Versions and citation

A workflow with a card (`card.file`) is curated: it is listed on `/workflows`, has a card page
(`/workflow?id=`), and moderators cut numbered releases of it (`POST /api/workflows/{id}/releases`,
the "Release" form on the card). A release freezes the whole document — card and any derive
prompt inlined — next to its contract and, per extract step, the preset release whose contract the
pin matched; it may carry a DOI of its own (`…/releases/{n}/doi`, Zenodo, `requires` relations to
the presets' concept DOIs, `isSourceOf` to the dataset releases built with it). The bundle
(`…/releases/{n}/bundle`) is a loadable workflow folder: `workflow.json`, `card.md`, `steps.md`,
`CITATION.cff`, `README.md`. A run records the release number whose document it matched exactly
(`workflow_run.workflow_release`); the citation on the card names the concept DOI, else the page.

## Provenance

A document remembers its run and step; a release remembers its run and the gate states; the
dataset recipe, `release.json` and the JSON export carry `workflow {id, contract, version,
release, run_id}`. See `REPRODUCIBILITY.md`.

## Endpoints

| | |
|---|---|
| `GET /api/workflows`, `GET /api/workflows/{id}`, `POST /api/workflows/validate` | the bundled workflows |
| `PATCH /api/datasets/{id}` `{workflow}` | declare the dataset's workflow |
| `GET /api/datasets/{id}/workflow` | the panel: steps with states, the active run, the gate |
| `POST …/workflow/plan`, `POST …/workflow/runs`, `GET …/workflow/runs` | plan, start, list |
| `GET /api/workflow-runs/{rid}`, `POST …/advance`, `/resume`, `/complete`, `/cancel`, `/retry`, `GET …/issues` | one run |
