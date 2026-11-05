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
  (`gate` lists the steps that must be fresh and reviewed). `agent`
  and `input.previous` are grammar-valid and refused at plan time until their release, and so
  is a second extract step: a workflow runs one preset into one dataset for now (claims and
  tables stay two datasets joined by the companion cross-check); linked datasets: see below.
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

## Linked datasets and a joint review

A workflow may extract two sides of the same papers into two datasets — the regression tables
and the claims of economics articles — when each extract step names its dataset role:

```jsonc
{"id": "tables", "kind": "extract", "dataset": "tables", "preset": "econ-regression-tables@…", "source": "supplied"},
{"id": "claims", "kind": "extract", "dataset": "claims", "preset": "register-claims@…", "source": "supplied"},
{"id": "links",  "kind": "check", "check": "claims-vs-tables", "over": ["claims"]},
{"id": "joint",  "kind": "review", "over": ["tables", "claims"], "review": {"mode": "all", "unit": "paper", "blocks": "release"}}
```

Such a workflow lives on a **workflow dataset** of its own, one level above the presets' datasets:

- the tables dataset under the tables preset, the claims dataset under the claims preset — each a
  plain dataset with its own records, releases and page;
- the workflow dataset, which carries the workflow, links one dataset per role and holds no records
  itself. Its page lists the linked datasets with their presets, the papers one row per PDF with
  each side's document beside it, what the dataset-level steps made (a derive step's table), the
  Workflow panel with the runs, and one release for all of them.

In storage the workflow dataset is the group's own row: its id is the `group_id` its members carry
(each with its `group_role`). It is made from **My workspace → Workflows → New workflow**, or from a
dataset's page (**Add to a workflow**, offered right after an import): the dataset joins in the role
whose preset it uses, and the other roles are linked or created from the workflow dataset's page
(one of your datasets under that role's preset, or an empty new one). A check that needs a
companion finds it among the members. A run starts from the workflow dataset and spans its
members: a new paper needs one file per role (the Import page tagged with the run and the step
lands each in its dataset), and every step is judged against the dataset it writes to or reads. A
member's page points to its workflow dataset. Deleting the workflow dataset frees its members:
they keep their records and become plain datasets of their preset again. A workflow over one preset
needs no workflow dataset; it runs on the dataset of that preset.

A `review` step is a joint review stage: one gate over the documents the run made in the steps it
names, whose worklist walks each paper's documents together in the workspace queue, across both
datasets. `unit: paper` with `mode: all` reviews every new paper; `mode: sample` draws papers.

The workspace reviews a paper of a workflow dataset (or of a joint queue) on one page: the sidebar
lists each paper once, its sides counted together; each regression table is one card — its panels
stacked as printed, every cell editable, one verdict for the whole table — with the claims whose
results cite it underneath, each with its own verdict and its estimate against the table. Claims
citing no table of the paper (a figure, the text) have a pill of their own. A claim's "edit ↗"
opens it in the claims document, where every field is editable.
Releasing the workflow dataset releases each member under its own number and gate (the response
lists them, and names a member whose gate still holds); a release cut from one member releases the
others with it, so the datasets stay in step.

While a run is on, the panel says which step it is on and what it is doing — waiting for uploads
("1 of 2 arrived"), extracting ("3 of 10 papers"), model calls, waiting for a review or a key —
and, where it can be measured, roughly how long it still takes: from this run's own pace once a
paper has landed, else from the last finished steps of the same kind on the server. No estimate
is shown for a step that waits for people, or before anything could be measured. A run that
waits for people reads "waiting for you" instead of "running": for uploads it lists what each
supplied step has received ("none uploaded yet", "1 of 2 arrived") and how long it has waited,
because nothing happens until the files arrive or the owner presses Uploads complete.

## Pipelines: k runs, a vote, a follow-up step

A live extract step may run its preset several times per paper and merge the runs:

```jsonc
{"id": "claims", "kind": "extract", "preset": "register-claims@…", "source": "live",
 "k": 3, "vote": {"identity": ["cause", "effect"], "fields": {"statement": "first", "tags": "union"}}}
```

Each run is a document of its own (`role = run`, hidden from every listing, never in a dataset,
reading the final's PDF and pages); the vote matches entries across runs on the `identity`
fields and fills each field by rule — `majority` (ties to the lowest run, flagged), `first`,
`union`, `all`; lists, tables and sub-entries by union, text by first. The final document — the
one the dataset holds and a person reviews — carries the union of the runs' quotes labelled by
run (`Table 2 · majority (r1, r2)`), and every record carries what the engine knows about it:
`support` (n of k runs), `candidates` (the values the runs disagreed on, one chip each in the
review; adopting one is logged as a correction), `flags` (`contested:<field>`, `minority`,
`tie:<field>`, `duplicate_in_run:<r>`). The `contested first` triage puts those first.

A later extract step may read the runs instead of the paper — an adjudication:

```jsonc
{"id": "graph", "kind": "extract", "preset": "claim-graph@…", "source": "live", "input": {"previous": ["claims"]}}
```

Its preset is a follow-up preset: its prompt reads `${previous}` (the runs' outputs, entry by
entry under `r1.claims[0]`, with their quotes) and cites evidence by reference (`{"ref":
"r2.claims[3].sign", "field": "claims[0].sign"}`), never a quote of its own; the engine resolves
every reference to the runs' real quotes (exact → row → table → entry), records the hop trace per
record, and checks the refs (`bad_ref`, `unknown_source`, `dangling_ref`, `quoted_directly`,
`unreferenced_row`, …). The step it reads is part of its pipeline: it plans no papers of its own,
and the dataset holds the follow-up's documents. One follow-up per pipeline in this release. A
paper of such a step costs k + 1 model calls (planned at the run's start); the jobs run under a
longer timeout and report which call they are on.

## Loops

```jsonc
{"id": "fix", "kind": "loop", "steps": ["tables", "numbers"], "until": {"check": "numbers", "max": 0}, "max_iterations": 2}
```

When the named check still finds problems (at or above its `severity`; more than `max` per
paper) after the inner steps ran, the live extract step runs again for those papers with the
findings fed into the prompt ("an earlier extraction of this paper was checked and these
problems were found; fix them"), replacing their documents, then the check runs again — at most
`max_iterations` times. A credit run spends the calls again; an own-key run pauses as
`needs_key:<step>` until the owner gives the key. The run keeps its PDFs until it ends.

## Derive steps

A `derive` step makes a unit of rows over the records of an extract step — a taxonomy of the
claims, the edges of a corpus graph, the columns per table — by a chain of calls:

```jsonc
{"id": "edges", "kind": "derive", "over": "claims",
 "input": {"unit": "entries", "columns": ["cause", "effect", "sign", "_study"], "filter": [{"column": "type", "op": "eq", "value": "edge"}]},
 "identity": ["cause_concept", "effect_concept"], "group_by": "_study",
 "calls": [{"id": "harmonise", "kind": "llm", "prompt": {"file": "edges.harmonise.md"}},
           {"id": "merge", "kind": "transform", "op": "group", "by": ["cause_concept", "effect_concept"],
            "fields": {"n_claims": "count", "n_papers": "n_papers", "sign": "majority", "studies": {"op": "union", "column": "_study"}}}],
 "output": {"label": "Corpus edge", "fields": [{"name": "cause_concept", "type": "string", "required": true}, "…"]},
 "review": {"mode": "all", "blocks": "release"}}
```

`input` names the unit of the extract step's preset (`entries`, a table field, a child), the
columns the calls read (default: every non-system column plus `_study`) and a filter (`eq | ne |
in | not_in | empty | not_empty`). A model call (`kind: llm`) reads `${rows}` (the first call: the
input rows, one line each under a short id, `[NEW]` on records added since the committed version)
or `${previous}` (a later call: the earlier call's rows), plus `${existing}` (the committed rows),
`${output_schema}`, `${dataset}` and `${new_count}`; over 60 000 characters the rows are split by
`group_by` (default: per paper) and the chunks' answers merged on the `identity`, at most 24 calls.
A transform (`op: group`) groups what it reads by `by` and fills the other output fields by rule —
`count`, `n_papers`, `first`, `majority`, `union`, `all`, `sum`, `mean`, `min`, `max` (a rule may
name its column: `{"op": "union", "column": "_study"}`). Every output row carries `_id` (a hash of
the identity fields, stable across versions) and `sources` (the record ids it was made from; a row
without one is dropped, as a page section without a source is).

A draft is reviewed as a change against the committed version on `/derived?dataset=…`: per row a
computed verdict (kept · revised · new · removed), the cells that changed, the sources split into the
records new since the commit and carried ones, accept / reject per row, then the commit. `review.mode:
none` commits the draft at once; `all` holds the release (or the run, `blocks: next`) until the
commit. A transform-only step runs inside the run, free; model calls run on the worker with the
server key when the run spends credits (planned per chunk, refunded on failure), inline when there is
no queue, and otherwise the run pauses as `needs_key:<step>` until the owner gives a key on the
derived page (used once, never stored). The committed unit is listed with the dataset's analysis
units, frozen into releases (`derived` in the snapshot) and exported as `tables/<step>.json` with
its sources' quotes as evidence. Correcting a record after the unit was made makes it stale.

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
