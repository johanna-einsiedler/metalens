# Workflows

A preset says what one model call per paper extracts. A **workflow** says which presets run, in
what order, what deterministic checks and dataset-level steps follow, and where a person looks
before a release — a **review step**: every record, a random sample, N coders per paper. The
dataset keeps the state (records, drafts, runs); the workflow declares the process, and is
versioned and citable like a preset.

## The file

`paperlens/workflows/<id>.json`, with sibling files (`<id>.card.md`, derive prompts) inlined on
load. Directories in `PAPERLENS_WORKFLOW_DIRS` are read too; bundled ids win. A workflow written in the
editor (`/workflow-editor`) lives in the `user_workflow` table under a minted id
(`<slug>-<uuid8>`, so it never shadows a bundled one), owned by the account (or the anonymous
session) that saved it, private unless made public, validated on every save under the same
grammar; `workflows.spec_for(conn, wid)` reads it (the files first, then the row). A project runs
it like a bundled workflow (the dataset pins its contract, a run freezes the document); it has
no releases or DOIs of its own yet.

**Proposing a workflow.** The editor's "Start from a question" asks a model (`POST
/api/workflows/propose`, `workflow_propose.py`) for a preset and a workflow from what the user
wants out of the papers. The model sees the questions, the presets available on this surface
with their fields and stamps, the workflows, the checks, the templates and the grammar — never
a paper. It answers inside the grammar: `{"preset": {"use": id} | {"new": {…}}, "workflow":
{…}, "notes", "unanswered"}`. The proposer resolves a reused preset's pin, normalises and
validates a new preset document like one saved in the editor, drops steps of other kinds and
checks not in the registry (and says so), validates the workflow with the new preset's pin
known, and nudges the model once with the problems. The person reviews: a new preset is saved
first (the server assigns its id, the extract step's pin follows), then the workflow goes into
the editor and is saved like any other. Nothing is stamped by a proposal.

```jsonc
{"format": 1, "id": "econ-regressions", "version": 1,
 "meta": {"title": "…", "tagline": "…", "authors": [...], "license": "CC-BY-4.0"},
 "card": {"file": "econ-regressions.card.md"},
 "dataset": {"unit": "paper", "key": "pdf_sha256"},
 "steps": [
  {"id": "tables",   "kind": "extract", "preset": "econ-regression-tables@67780825", "source": "supplied"},
  {"id": "contract", "kind": "check", "check": "schema", "over": ["tables"], "on_fail": "note"},
  {"id": "review",   "kind": "review", "over": ["tables"], "mode": "all", "unit": "entry", "blocks": "release"},
  {"id": "release",  "kind": "release", "gate": ["tables", "review"]}]}
```

- **Steps are the graph.** A step may only read earlier ones; the last step is the release.
- **Pins are contracts.** `preset` names a schema id. The same contract means the same dataset
  shape; a wording change does not re-version the workflow. Each run records the preset release
  and the prompt hash that actually ran.
- **Kinds.** `extract` (a preset over the PDF; `source: live` runs it on the worker, `source:
  supplied` waits for the pipeline's uploads — the only choice for an import-mode preset),
  `check` (a rule from the registry in `paperlens/checks.py` — `schema`, `regression-cells`,
  `claims-vs-tables`, `genai-work-record`; writes findings, never data),
  `vocabulary` / `pages` / `crosscheck` (the preset-level update steps, same keys), `review` (a
  person looks, below), `release` (`gate` lists the steps that must be fresh and reviewed).
  `agent` steps are grammar-valid and refused at plan time until their release. Several extract
  steps are fine when each names its dataset role (linked datasets, below); `k` runs with a
  `vote`, `input.previous` and loops run.
- **A review is a step.** `{"kind": "review", "over": [steps], "template": "<id>", "mode": "all" | "sample", "unit":
  "entry" | "paper" | "row", "blocks": "next" | "release" | "none", "coders": N, "new_only": true, "where": {…},
  "purpose": "audit" | "routed" | "monitor" | "calibrate" | "adjudicate"}`: `over` names
  the earlier extract steps it reads (several: a joint review, each paper's documents of those
  steps in one worklist), or one derive step (the review is the draft's commit). `blocks`
  (default `release`) says whether an unsatisfied gate holds the next step or only the release.
  `sample` draws `n` records (or a `share`) from the run's new output at random, with a `seed`
  (default: from the run and step) and `stratify` fields of the read presets (proportional quotas,
  at least one per stratum when the size allows, `unit: paper` draws whole papers); when the
  output is smaller than the size, every record is drawn. The draw is stored (`workflow_sample`);
  a drawn record is checked when its last verdict after the draw is verified or flagged, and
  disagreed when a flag or a value-changing correction followed the draw. The dataset's badge
  reports the draws: "Sampled 30/412 · 93% agree", with the estimated error rate and its 95%
  interval in the long label, next to the audited share; a record verified outside a draw never
  enters the estimate. `new_only: false` reviews every paper of the sides on every run, not only
  the run's new ones. `unit: row` reviews the ROWS of a paper table (`table`, one of the read
  preset's): `mode: all` every row, `mode: sample` `per_paper` of them per paper, drawn with a
  seed of the step's `seed` and the paper's id (a paper's draw never moves when papers are
  added; `n` and `share` do not apply); the gate's items carry the drawn row's `unit_key`
  (`"<table>#<_rid>"`), the verdict is the paper's (read from its first record), and a draw of
  rows never enters the dataset's sample estimate. `where` keeps what meets it — the rows, for
  `unit: row` (keys are the table's columns); the papers, otherwise (keys are paper fields,
  `paper_metadata.<field>` too), the same small grammar as a template block's. `template` names
  a review template (below): how the coder checks the records and what they answer; it must be
  `for` the preset of every step the review reads. `table`, `per_paper`, `where` and `template`
  are part of the contract. A data step no review step covers is never reviewed: an extract step's
  records count as AI-only, a draft is committed at once. The form before — `"review": {…}` on
  the extract or derive step — is still read: it becomes the review step `<step>_review` right
  after it, with the same contract (below).
- **A check's `on_fail`**: `note` records findings; `flag` puts every record with a finding at or
  above `severity` in front of a reviewer (a release-blocking gate); `fail` fails the run.

## The checks

| check | reads | finds |
|---|---|---|
| `schema` | any preset | the declared contract on the values as stored: a required field missing, a wrong type, an option not declared |
| `rules` | any preset | what the workflow's own rules say (`params.rules`, below): a comparison between fields, a range, a pattern, a sum across a table — each rule its own code and severity |
| `regression-cells` | `econ-regression-tables`, `register-tables` | an se/t/p/CI row naming no coefficient (`se_dangling_ref`; a p or t row with a label of its own that names no coefficient is a test statistic and passes) or a non-coefficient (`se_refers_noncoef`), two se rows for one coefficient (`coef_many_se`), a parsed number that is not the printed one (`numeric_mismatch`), stars against the stated level (`stars_vs_level`) or the p-value (`stars_vs_p`), t against coefficient / se (`t_vs_coef_se`), the stated N against the printed N (`n_obs_vs_sample`), columns of one table with different Ns (`n_obs_varies`, a note), row indexes not from 0 (`row_index_gap`) |
| `claims-vs-tables` | `register-claims`, needs the dataset's companion | a cited estimate no coefficient cell of the resolved column equals (`estimate_mismatch`), a cited exhibit no column matches (`exhibit_unlinked`) |

Findings reach the reviewer on the card ("✗ 2 check findings — regression-cells: …"), on the cell
each one names, and in the "check findings first" order of the entries; the Workflow panel links
there. Parameters are declared per check (`t_tolerance`, `star_legend`, `ignore`) and typed.

### Rules

A check step with `check: rules` carries its rules in `params.rules`, written against the preset's
fields and validated with the workflow — no code:

```json
{"id": "plausible", "kind": "check", "check": "rules", "over": ["tables"], "on_fail": "flag", "params": {"rules": [
  {"code": "ci_order", "require": {"ci_low": {"lte": {"field": "ci_high"}}}},
  {"code": "p_in_range", "severity": "warn", "require": {"p_value": {"between": [0, 1]}}},
  {"code": "star_level", "unit": "cells", "where": {"significance_stars": {"gte": 1}}, "require": {"significance_level": {"lte": 0.1}}},
  {"code": "shares_sum", "unit": "paper", "require": {"sum(arms.share)": {"eq": 1, "tol": 0.01}}},
  {"code": "too_many", "unit": "paper", "when": {"count(regressions)": {"gt": 40}}, "message": "more columns than any paper prints"}
]}}
```

A rule names a `code` and a `severity` (`error`, the default, `warn` or `info`), the `unit` it
reads — an entry (the default), the `paper`, the rows of an entry's child table (`"cells"`) or of
a paper table (`"paper.<table>"`) — and either what must hold (`require`: a finding when it does
not) or what is a finding when it holds (`when`); `where` narrows the units the rule applies to.
A condition is the review grammar's `where` form, extended: `{<term>: value}` is equality,
`{<term>: {op: arg, …}}` combines operators — `eq ne in not_in null not_null gt gte lt lte
between matches`, `tol` beside a numeric `eq` or `ne` — several terms are AND, and `$all`, `$any`,
`$not` nest. A term is a field of the unit, `paper.<field>`, `entry.<field>` from a child row, or
an aggregate over a collection: `sum(cells.numeric_value)`, `count(cells)`, `min`, `max`, `mean`,
`len(<field>)`; an argument may refer to another term (`{"lte": {"field": "ci_high"}}`). A value
that is missing makes a comparison undecided, never a finding (the `schema` check says what is
required). Findings name the record and the cell, so they reach the review card and `on_fail:
flag` puts the record in front of a reviewer like any other check's. `rules.suggest(preset)`
offers rules the field names imply (an ordered `_low`/`_high` pair, a probability in [0, 1], a
count of at least one, a plausible year) for a builder to show; nothing applies them by itself.

## The contract

`workflow_id` = `<id>@<sha8>` over the dataset unit and the steps with their pins, checks and
**review declarations** — labels, help texts, a review step's id and a derive prompt's wording
are left out. A review step over one data step is hashed as that step's `review` attribute (the
form before review steps stood on their own), so a workflow keeps its id across the two
spellings; a review over several steps, or one saying what only a step can say (`new_only:
false`), is hashed as the step it is. A dataset names its workflow by contract
(`dataset.workflow`); without one it runs its preset's implicit one-step workflow (the preset,
its `meta.update` steps, a release gated on the extraction), so every dataset has the same
Update panel.

## A run

From the dataset page: **Update** plans the run over the staged PDFs — per step what it would do
(extract, attach a document the owner already has under that contract, wait for an upload) and
how many model calls — then starts it. Credits for every planned call are spent at start and
given back per paper that never ran. The page posts each PDF to `POST /api/extract` with
`run_id` and `step_id`; a pipeline posts its files to `POST /api/ingest-pdf` the same way. The
run advances as papers land: checks run, gates open, the release step closes the run. A check
step runs on the worker as its own job (`workflow_step_job`, as a derive step does) and the run
waits for it; without a queue (local mode) it runs in the advance. Inside the advance a long
check held the run's lock and one transaction for all of it: the GenAI-at-work passes check over
114 papers never got through. One run
per dataset at a time; every two minutes (`workflow_tick_task`) the jobs of a run that has not
moved for ten minutes, and any job that has itself waited ten minutes, are reconciled with the
queue — the second because every poll of the run's page touches the run, so a watched run never
looked idle while the queue had dropped its jobs. A check step's job the queue lost is queued
again (up to three tries); a derive step's fails the run. The tick runs for at most five
minutes; a call it has on a thread when that time runs out finishes there, and the tick's
connection closes after it.

A gate's items are a worklist: `GET /api/workflow-runs/{rid}/queue/{step}` lists them in draw
order with their verdicts; `/workspace?queue=<run>:<step>` works through them — the sidebar lists
only the drawn papers, the panel only the drawn entries, a verdict advances to the next pending
one across papers, and the done state resumes the run or opens the release.

A review step with `blocks: next` pauses the run (`awaiting_review:<step>`) until every record of
the gate has a verdict or the owner resumes with `override`, which the gate records. A gate with
`blocks: release` lets the run finish and holds the release: `POST /api/datasets/{id}/releases`
answers 409 with the gate until the verdicts are in, or the owner passes `override_gates` /
`allow_stale`, which the release records (`gates`).

## Projects

A **project** is a workflow's instance: one owner, the datasets it links (one per preset role),
the people who code for it, its runs, its review tasks and its releases (`paperlens/projects.py`,
`/project?id=`, `POST /api/projects {workflow, title?, datasets?}`, `GET /api/projects`, `GET
/api/projects/{id}` — members, papers, runs, its tasks, the owner's people, the
viewer's role — `PATCH`/`DELETE /api/projects/{id}`, `GET`/`POST`/`DELETE
/api/projects/{id}/people[/{user}]`). Internally it is the **workflow dataset** of the linked
datasets (below): the row whose id is their `group_id`; a person added to the project codes every
linked dataset's papers. A **task** is a review step of the workflow over every run that opened
it (not cancelled, failed or merely planned): each run's step-run brings its own papers, and the
task sums their gates, lists every run's papers in its matrix (each with the `step_run_id` and
`run_id` it belongs to) and names the runs (`runs`); its `queue` is the oldest run whose papers are
still open. `GET /api/projects/{id}/tasks` gives them, `?run=` one run's tasks alone (what a
release's statistics read); `POST /api/projects/{id}/tasks/{step_run_id}/assign {document_id,
coders}` moves a paper within its run until a verdict was given. `GET /api/me/tasks` lists the
caller's tasks the same way — one per project and review step, their share summed over the runs,
the queue of the oldest batch still open; the workspace shows them under "My review tasks". A
queue whose papers are done names the same task's other open batches (`more`), and the queue
bar offers "Next papers of this task". A workflow over one preset runs on a dataset of that preset and is not a project row.

**Access follows the task.** A person added to a project is in its pool: they see the project's
page and have a label (`coder_1` …), nothing of its papers yet. A task that assigns them a paper
opens every paper of the datasets that task reads (`outputs.datasets` of the review step-run),
for as long as the run is live and they are in the pool; a run that is done keeps their own
coding readable; taken out of the pool, they read nothing. A grant on a plain dataset (one not
in a project) opens every paper of it at once, as before. **The owner's hand on a task**: `PATCH
/api/projects/{id}/tasks/{step_id} {coders: N | [emails] | null}` says how many of the project's
people review each paper of that task, or which ones, over the workflow's own `coders`
(`project_task_config`); it is read when the task's gate opens, and a gate still waiting for
coders assigns at once — a solo pilot runs a two-coder judgment with one.

**A project release** (`POST /api/projects/{id}/releases {notes, allow_stale, override_gates}`,
`GET …/releases[/{n}[/export]]`; `project_release`) is one number for the project: every linked
dataset released under its own number (kept at its latest when unchanged), the workflow's contract
and its newest finished run, the review statistics per task and coder (what each gate asked, what
was checked and disagreed, each coder's share), one badge over all the datasets (the badge rule
over their summed counts, "across N datasets"), and the project's gate as it stood — held unless
overridden, which the release records. The export is a zip of `project.json`, `README.md` and
every member's own release files under `members/<role>/`. The project moved since a release when a
member's data moved since its own latest release, a member was released or linked since, or a
newer run finished. A member's frozen findings and sample draws are read from its project's runs.

## Linked datasets and a joint review

A workflow may extract two sides of the same papers into two datasets — the regression tables
and the claims of economics articles — when each extract step names its dataset role:

```jsonc
{"id": "tables", "kind": "extract", "dataset": "tables", "preset": "econ-regression-tables@…", "source": "supplied"},
{"id": "claims", "kind": "extract", "dataset": "claims", "preset": "register-claims@…", "source": "supplied"},
{"id": "links",  "kind": "check", "check": "claims-vs-tables", "over": ["claims"]},
{"id": "joint",  "kind": "review", "over": ["tables", "claims"], "mode": "all", "unit": "paper", "blocks": "release"}
```

Such a workflow lives on a **workflow dataset** of its own, one level above the presets' datasets:

- the tables dataset under the tables preset, the claims dataset under the claims preset — each a
  plain dataset with its own records, releases and page;
- the workflow dataset, which carries the workflow, links one dataset per role and holds no records
  itself. Its page lists the linked datasets with their presets, the papers one row per PDF with
  each side's document beside it, what the dataset-level steps made (a derive step's table), the
  Workflow panel with the runs, and one release for all of them.

Each role is filled from the page's "Link or make a dataset…": a dataset the owner has under the
role's preset is linked, or a new one is made for the role and filled on the Import page (the
pipeline's JSON + PDF files; the page returns to the workflow when they are in) or on the extract
page (the preset's model call per paper — not offered for an import-only preset). Once every side
has papers, **Run the checks** starts a run whose supplied steps take nothing new and runs the
checks and the review over what the datasets hold; "Start a run with uploads" takes new papers'
files uploaded into the run instead.

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

A review step over two sides is a joint review: one gate over the documents the run made in the
steps it names, whose worklist walks each paper's documents together in the workspace queue,
across both datasets — or, when the run brought no papers (Run the checks over what the datasets
hold), over every paper of those sides. `unit: paper` with `mode: all` reviews every new paper;
`mode: sample` draws papers. The step-run records the datasets it read (`outputs.datasets`).
While a gate waits, the panel's primary action is "Review now · n".

The workspace reviews a paper of a workflow dataset (or of a joint queue) on one page: the sidebar
lists each paper once, its sides counted together; each regression table is one card — its panels
stacked as printed, every cell editable, one verdict for the whole table — with the claims whose
results cite it underneath, each with its own verdict and its estimate against the table. Claims
citing no table of the paper (a figure, the text) have a pill of their own. A claim's "edit ↗"
opens it in the claims document, where every field is editable.

A joint review step may say what it asks of each side: `"asks": {"tables": ["target_regressors",
"se_type"]}` (keys are extract steps of `over`, values entry fields of their preset). A side with
`asks` shows, under each table, only those fields — one line per column, the target regressor picked
from the column's coefficient rows and marked in the table — instead of the column's whole
specification; a side without it shows every field. On the claims side the asked fields are
edited in place under the table (a child, such as the results, as an editable table whose rows
citing the table are marked). `asks` is display: it does not change the workflow's contract. The
bundled `econ` asks what the extraction validation checks and records.
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
because nothing happens until the files arrive. The Import page closes a side's uploads when its
batch is in, so the run goes on by itself once every side has its files; the panel's fallback
closes a side that gets no files ("Go on without the missing files") or runs the checks over what
is already there when there is nothing to upload.

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
 "output": {"label": "Corpus edge", "fields": [{"name": "cause_concept", "type": "string", "required": true}, "…"]}},
{"id": "review_edges", "kind": "review", "over": ["edges"], "mode": "all", "unit": "entry", "blocks": "release"}
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
records new since the commit and carried ones, accept / reject per row, then the commit. Without a
review step over the derive step the draft is committed at once; with one, the gate holds the
release (or the run, `blocks: next`) until the commit — the gate sits on the review step's run,
which names the draft (`review.draft {id, step, version}`). A transform-only step runs inside the
run, free; model calls run on the worker with the
server key when the run spends credits (planned per chunk, refunded on failure), inline when there is
no queue, and otherwise the run pauses as `needs_key:<step>` until the owner gives a key on the
derived page (used once, never stored). The committed unit is listed with the dataset's analysis
units, frozen into releases (`derived` in the snapshot) and exported as `tables/<step>.json` with
its sources' quotes as evidence. Correcting a record after the unit was made makes it stale.

## Coders

A dataset has one owner. The owner may let other accounts **code** it: `POST
/api/datasets/{id}/coders {email}` (the Coders card on the dataset page; a grant on a workflow
dataset covers its linked datasets). A coder reads the owner's papers — the pages, the quotes,
the records as imported — and codes them in a **coding of their own**: their values for the
editable fields and table rows, their verdict, their note, their paper-level values and their
marks (`record_coding`, `document_coding`, `coding_event`, `coder_highlight`). Nothing a coder
does touches the record as stored, its verification status or its events: no coder sees
another's coding, the owner sees every coding (a chip per coder on each record; *view as* a
coder's coding, read-only), and the badge counts a coder's verdict as it counts the owner's.
Coders may not add or delete entries, change the paper's identity, set a field across records
or delete a document: the shape of the data stays the owner's. A listed coder's verdicts and
edits always land in their coding — the owner's too, once they list themselves.

A review step may say `coders: N`: when its gate opens, every paper of it is assigned to N of
the dataset's coders (round-robin in join order, so the load is even; `workflow_assignment`),
the gate's items become one per (record, coder), each coder's queue (`GET
/api/workflow-runs/{rid}/queue/{step}`, `/workspace?queue=`) lists their own papers, and the
gate is satisfied when every assigned coder gave their verdict — never by the owner's. With
fewer coders than N the gate waits (`needs_coders`) and assigns once the owner has added
enough; the owner may move a paper between coders (`POST …/assign`) until a verdict was given.
`coders` is part of the workflow's contract. In local mode (one machine, one owner, no accounts)
the local owner is every project's one person: a task's gate assigns to them and their verdicts
and answers are a coding; a task of two waits for one more until its config says one. An owner who
is one of the people gets their own share of a task's queue, as the document view already codes them.

Exports carry every coding (`codings` per record, `paper_codings` per paper, coders by label —
`coder_1`, `coder_2` … — with emails for the owner alone, each with its `answers`); a release
freezes them (`codings` in the snapshot, `codings.json` in the zip) next to the paper-level
values, and a new coding counts as a change since the last release.

## Review purposes and the stamp

A review step may say what it is for. `purpose` is read by the stamp, not by the contract: adding
it moves no workflow id, and the defaults it sets (below) are what is hashed.

- `audit` — a first look at a model step. Its size is the review's own: every paper (`mode:
  all`), or a random draw (`mode: sample`, `n` or `share`, `seed`, `stratify`) of papers (`unit:
  paper`), of entries (`unit: entry`) or of a paper table's rows (`unit: row`, `per_paper`). When
  its gate closes, the agreement a person had with the model feeds the preset's stamp.
- `routed` — the papers a rule sends on, named by `where` (the chain's `route` compute writes
  `paper.routed`; `{"paper_metadata.routed": true}` picks them up). Never stamped: these papers
  were chosen, not drawn.
- `monitor` — a draw of each run's new papers (`mode: sample`, `new_only: true`) that extends the
  stamp's count and holds nothing: `blocks: none`, and the step stays out of the release gate.
- `calibrate` — two or more coders per paper (`coders: 2`), so the coders' own agreement is known —
  the baseline an audit's "X% agree with the model" is read against. Sized like an audit: every
  paper, or a random draw of papers, entries or rows. A template with `blind`
  keeps the model's values from the coders; without one the validator warns.
- `adjudicate` — the units an earlier coders' review of the same step disagreed on (one took the
  model's value, another flagged or changed it), each settled by a further verdict. The earlier
  review must hold the run (`blocks: next`) so its disagreements are known when this step opens;
  `mode: all`. Never stamped.

The **stamp**: when an `audit`, `monitor` or `calibrate` gate first closes (not when the owner
overrides it), one row per dataset the units belong to goes into `validation`: the preset pin
(`schema_id`), the model, the workflow contract, the step, how many papers and units were checked,
how many disagreed, the agreement with its Wilson interval, the number of coders and — with two or
more — how often the coders answered alike (`human_agreement`), the fields the step's `asks` named,
the template. A preset's card (`stamp`) shows the rows at its newest pin: the latest audit, the
monitoring since it (pooled), the calibration; `lapsed` when that pin is no longer the preset's
current one — an audit at the new pin renews it. A workflow's card lists the stamp per review step
(`stamps.steps`), the dataset page's Workflow card the row each review last wrote here, and a
release freezes the dataset's rows with its badge (`credibility.validations`).

## Chains: pass steps

A **chain** (`paperlens/chains/<chain>/`) is registered code a workflow runs per paper — the
project-specific knowledge (prompts, taxonomy, rules) is the chain's content, the engine knows the
registry (`paperlens/chains/__init__.py`, the pattern of `checks.REGISTRY`). A step of kind `pass`
names a pass at its PIN:

```jsonc
{"id": "estimands", "kind": "pass", "pass": "genai_work.estimands@3f1a9c2e", "reads": ["exhibits"],
 "k": 2, "match": "genai_work.match_estimands", "params": {"threshold": 6}}
```

`pass` is `<chain>.<name>@<sha8>`, the sha over the pass's prompt files, its JSON schema and what
the code puts around them (`inputs_version`), so a prompt edit re-versions every workflow naming
the pass, as a preset pin does (`chains.pin`; a stale pin is an error). `reads` names the earlier
pass steps whose outputs the pass declared it reads, one per declared read. `k` runs the pass k
times per paper and `match` names the chain's matcher that folds the runs into one (a later
release). `params` are typed by the registration. `model` is the run's choice, not the contract's.

A pass's code gets a context: `ctx.pages` and `ctx.text()` (the page text with `=== PAGE i ===`
markers), `ctx.paper`, `ctx.read("<pass>")` (the primary output of the step that runs it),
`ctx.params`, `ctx.prompt(name)`/`ctx.schema()`, and `ctx.call(system, user, schema, max_tokens=…)`
— one JSON answer through `providers.generate_json`; a cut answer raises `passes.Truncated` so the
pass can ask again in a shorter form. It returns one JSON object in its own shape.

Outputs sit in `workflow_pass_output` per (run, step, paper, pass index) with the prompt sha, an
inputs sha (the text, the outputs read, the params), the model, usage and calls. An output made
under the same shas by any earlier run is REUSED without a call; one made outside Metalens is
SUPPLIED (`POST /api/workflow-runs/{rid}/passes/{step}/{sha}` with `{output, prompt_sha256?}`) and
stands in for the call. A paper enters a run that starts on passes through
`POST /api/workflow-runs/{rid}/papers` (a PDF of the run's plan; its page text is stored once per
PDF): its passes run in order in one job — a pass with its output stored is skipped, a failure
stops the paper's chain. A pass step is the barrier across papers: done when every planned
paper's job is terminal (the owner closes the uploads with `…/complete`), its outputs counting
papers, failures, reuse, calls and usage; `GET …/passes/{step}` lists a step's outputs per paper,
`GET …/passes/{step}/{sha}` serves one with its provenance.

A chain run starts from the Add papers page (`/extract?dataset=<id>&workflow=1`): the staged PDFs
are planned, the run started, and each PDF posted into the run, where its passes run as it
arrives; a run that still waits for papers offers "Upload the papers" on the dataset page's
Workflow panel, which opens the same page with `&run=<id>`. The panel names pass and compute steps
with the registered code, the runs per paper, the last run's papers, reuse, failures, calls and
findings by code.

A chain's failed papers are retried with `POST /api/workflow-runs/{rid}/retry` (the key with it on
an own-key run) while the run is live: the paper's failed jobs of every pass and compute step go
back to pending and its chain runs again from the PDF and page text the run kept, a pass with
its output stored skipped. The page text of a PDF no live run and no document holds goes with the
retention sweep.

### Repeated runs

`k` on a pass step runs the pass k times per paper — each run stored with its index — and the
chain's matcher named by `match` (a registered entry of kind `match`: `fn(ctx, outputs) ->
{primary, stability}`, free to call the model once for what it cannot align) folds them into the
PRIMARY output the later passes and computes read, stored with its `stability` (the matcher's own
record, plus `n_runs`, `k` and the runs that failed). A paper lands when at least
`chain.runs_needed(k)` runs succeed (all of one, else at least half and at least two). The
step-run's outputs count papers by their number of runs (`by_runs`); `GET …/passes/{step}/{sha}
?role=run&pass_index=i` serves one run.

The GenAI-at-work chain's matchers (`genai_work.match_exhibits`, `match_estimands`,
`match_estimates`, `match_design`) take the first run as the primary and align every further run
to it by content (tasks by name, arms by label and use, outcomes by measure, estimands by
structure and wording; the leftovers paired by the model once). The primary comes back with
`_runs` (`found`, `of`, `alt`) on its tasks, estimates, study, tasks and arms — the contract the
record card reads — and, for the design, the cells a majority of three readings settles adopted
(`settled_by`); the stability record holds the alignments, the per-unit statuses, the issues
(`runs_disagree`, `not_in_all_runs`, `task_count_differs`, `runs_settled`, `runs_unsettled`,
`runs_wording`) and the metrics for the assembly and the checks to lift.

The chain's `genai_work.assemble` compute turns the four passes into the record (derived cells,
canonical ids, the assembly, every quote verified, every check) and the Metalens document under
`genai-work-extraction`: one entry per task with its arms, outcomes and result rows, an evidence
row behind every cell, the review fields (task descriptions, treatments, composite outcomes,
claims, exhibits and cited cells). Its `_record_chain` carries, per task, how many readings found
it and the fields the readings disagreed on, so the record card paints support and candidates
as it does for a pipeline.

### Checks over pass steps

A registered check may apply to passes instead of documents: its registry entry says
`applies_to: {unit: "papers", passes: [<chain>.<name>, …]}` and a step's `over` then names pass
steps of those passes in that order. The check reads each step's primary outputs by paper (pdf
sha) with their stability records and the papers' page texts, and its findings name the paper
(`pdf_sha256`) rather than a record. `genai-work-passes` runs the GenAI-at-work chain's whole
issue list over its four extraction passes.

```json
{"id": "passes_check", "kind": "check", "check": "genai-work-passes", "over": ["exhibits", "estimands", "estimates", "design"],
 "on_fail": "note", "severity": "error"}
```

The chain's later steps: `genai_work.claims` and `genai_work.headline_plan` are passes over the
earlier ones (two and four to five calls per paper), `genai_work.assemble` reads all six passes
into the document, and `genai_work.route` (a fields compute, `threshold` param) writes the
paper's `flags`, `routed`, `route_reasons` and `triage_score` from the record it recomputes.
The bundled `genai-work-chain` workflow strings them together: six passes, the assemble compute
(dataset role `extraction`), the record and chain checks, the route compute, then the three review
tasks and the release of `genai-work` v4.

Seeding: `POST …/papers` with `dispatch=false` stages a paper without running it (its own page
text may come along as `pages`, its identity as `title`, `doi`, `abstract`, `journal`); the pass
outputs supplied afterwards complete its chain. `scripts/seed_genai_chain.py` does this from the
review project's files, `scripts/compare_chain.py` compares the result with the project's own. With `--manifest` the seeder
seeds a PROJECT the way `seed_accounts.py` does (owner, people, tasks), then stages and supplies.

From the browser, a chain project's Workflow card takes papers two ways: "Add papers and run the
passes" (the extract page; the passes run on your key) and "Upload papers with results": each PDF
with a `<name>.json` beside it —

```json
{"pages": ["page 1 text", "…"], "title": "…", "doi": "…", "abstract": "…", "journal": "…", "year": 2025, "authors": ["…"], "peer_reviewed": "no",
 "outputs": {"exhibits": {…}, "estimands": {…}, "estimates": {…}, "design": {…}, "claims": {…}, "headline_plan": {…}},
 "prompt_sha256": {"exhibits": "810861b1ac"}, "stability": {"design": {…}},
 "runs": [{"step": "design", "pass_index": 1, "output": {…}}]}
```

Only `outputs` is required (one entry per pass step, by step id); the rest stands in for what the
engine would otherwise extract or not know. `scripts/seed_genai_chain.py --write-bundles DIR` writes
these from the review project's files. The Workflow card draws the steps as a flow diagram: an
arrow for every dependency a step declares, a dot for each step's state, the step a run is on outlined.
The columns that do not fit the card's width go on in a second row of boxes underneath; an arrow into
a later row leaves to the right, runs back along the gap between the rows and comes in from the left.
Every arrow is straight segments with right-angle corners.

### Compute steps

A step of kind `compute` runs a chain's registered deterministic code per paper, free:

```jsonc
{"id": "extraction", "kind": "compute", "fn": "genai_work.assemble", "reads": ["exhibits", "estimands", "estimates", "design"],
 "preset": "genai-work-extraction@ddf3b7fd", "dataset": "extraction"}
{"id": "route", "kind": "compute", "fn": "genai_work.route", "edits": "extraction", "writes": ["paper.routed", "paper.route_reasons"],
 "findings": ["record"], "params": {"threshold": 6}}
```

A compute that PRODUCES documents (`preset`, an optional `dataset` role) builds one document per
paper from the outputs of the passes it `reads` (`ctx.read`, `ctx.pages`, `ctx.paper`,
`ctx.preset`); the engine persists it through the ordinary path — quotes located, the contract
checked — with the passes as provenance (`chain.kind: compute`, `stages` naming each pass, its
pin and output), then judges the step like an extract step: its dataset, its gate, its release.
`reextract` re-makes a staged paper already under the pin, replacing its document. A compute
that EDITS (`edits` the documents of an earlier data step, `writes` the fields it may touch:
an entry field, `<child>[].<field>`, `paper.<field>`) returns `{records: {id: {path: value}},
paper: {field: value}, findings: [...]}`; every edit lands through the audited path as a
`corrected` event by the engine, so later steps are staled by it. Both read the findings of the
check steps they name (`findings`, as `ctx.findings[step]`). A paper whose inputs failed fails
its job; the step's outputs count the papers made or edited. Each paper of a compute step runs
as its own worker job (`compute_job` → `passes.run_compute`; inline when there is no queue), and
the step is the barrier: locating a long record's quotes in a long paper takes minutes, so the
request that completes the last pass returns at once and the run shows "computing · n of m" as
the papers land. At most twenty papers of a compute step wait in the queue at once
(`COMPUTE_IN_FLIGHT`); the rest stay pending and each paper that lands dispatches the next — the
queue drops a job not started within the hour, and a step that queued a hundred papers at once
lost most of them. A compute job the queue no longer has (dropped, timed out, forgotten) goes back
to pending and is queued again, up to three tries (`COMPUTE_MAX_TRIES`), then fails; a second
attempt at a paper waits on the first and is skipped once it landed. A document step's job
prepares the costly part first, writing nothing and holding no lock (`passes.prepare_document`:
the record built, its quotes located in the PDF, the pages rendered), then locks the job row,
checks it is still pending or queued and writes the document in one short transaction — so a
paper never gets two documents, and no transaction stays open for the minutes a long or scanned
paper takes. A retry re-pends a failed compute job; advance dispatches it once its inputs are there.

### References across passes

A pass registration may declare `ids` (paths that mint ids, `estimands[].estimand_id`) and
`refs` (`{"path": "estimates[].estimand_id", "to": "estimands:estimands[].estimand_id"}`, the
earlier pass named by its read name; without one, this pass's own ids). After every pass the
engine checks them: a value at `path` that no id was minted for is a `dangling_ref` finding on the
paper (`dangling_path`, `bad_ref`, `duplicate_id`, `missing_id` likewise), counted on the output
and per code on the step-run (`findings_by_code`). The registered check `refs` follows ids
across a document's records and paper tables (`params.refs: [["results[].estimand_id",
"paper.model_estimands[].estimand_id"]]`; `paper.` addresses the paper block, a trailing `[]` a
list of ids).

## Review templates and questions

A preset keeps the data contract and the prompt. A **review template**
(`paperlens/templates/<id>.json`, `PAPERLENS_TEMPLATE_DIRS` too) says how a person checks records
of one contract and what they answer — the review declaration a preset's `display.review` used to
carry, with QUESTIONS:

```jsonc
{"format": 1, "id": "genai-work-judgment", "meta": {"title": "…"}, "for": "genai-work-extraction",
 "layout": "stages", "items": "evidence_items", "verify": "paper",
 "blind": {"fields": ["task_label", "ai_role", "…"], "paper_fields": "unshown"},
 "stages": [
  {"id": "design", "label": "Study design", "help": "…", "evidence": "none",
   "blocks": [{"unit": "paper", "show": [], "sections": [{"label": "Screening", "fields": ["screen_ai_treatment", "…"]}],
               "questions": [{"name": "scale", "type": "enum", "options_from": "scale", "required": true}, "…"]}]},
  {"id": "units", "label": "Units", "help": "…",
   "blocks": [{"unit": "entries", "show": ["task_name", "task_description"], "mask_numbers": true,
               "questions": [{"name": "category", "type": "enum", "options_from": "task_label", "allow_other": true, "required": true},
                             {"name": "verdict", "type": "enum", "options": ["ok", "not_a_task", "unsure"], "required": true}],
               "add": {"label": "a task the list lacks", "fields": [{"name": "task_name", "type": "string"}]}},
              {"unit": "arms", "show": ["arm_label", "treatment"], "group_by_entry": true, "marks": [{"when": "is_baseline", "text": "(reference arm)"}],
               "questions": [{"name": "category", "type": "enum", "options_from": [{"field": "control_type", "prefix": "ref:", "label_prefix": "reference arm · "},
                                                                                   {"field": "ai_role", "prefix": "ai:", "label_prefix": "treatment arm · "}], "required": true}]},
              {"unit": {"distinct": "model_estimands.subgroup"}, "where": {"subgroup": {"not_in": [null, "", "full_sample"]}},
               "questions": [{"name": "verdict", "type": "enum", "options": ["ok", "not_in_paper", "unsure"]}]}]},
  {"id": "headline", "label": "Headline",
   "blocks": [{"unit": "paper_metadata.claims", "where": {"kind": {"in": ["abstract"]}}, "show": ["sentence", "task", "arms", "outcome"], "title": "{sentence}", "mask_numbers": true,
               "questions": [{"name": "verdict", "type": "enum", "options": ["unit_right", "unit_wrong", "unsure"], "required": true}]}]}]}
```

A stage holds **blocks**, each over a **unit**: `paper`, `entries` (one card per entry), a child's
key (its rows, `group_by_entry` under their entry), `paper_metadata.<table>` (a paper table's rows)
or `{"distinct": "<child or table>.<field>"}` (the distinct values of a column, with how many rows
carry each). `show` lists the unit's fields drawn on the card — editable into the coder's coding as
any cell, read-only when the block says `mask_numbers` (digits become `#`, in the shown text, the
stage's items and the quotes). `where` keeps the rows that meet it: `{"<column>": value | {"in" |
"not_in" | "not_null" | "null" | "gt" | "gte" | "lt" | "lte" | "eq" | "sampled": …}}` (a key
`paper_metadata.<field>` reads the paper block). `marks` annotate a row (`when` a boolean field,
`when_nonempty` any field; `text` may name fields in braces), `title` names the card. `questions`
are field declarations the coder answers — `name`, `type` (string, text, integer, number, boolean,
enum, multi, list), `label`, `help`, `options` or `options_from` (an enum or multi field of the
preset, or several with a value `prefix` and a `label_prefix`), `allow_other`, `required`,
`required_when` (the same small grammar, over the unit's values and its answers). `add` lets the
coder add a unit the paper has and the list lacks: its `fields` are answered like questions, under
a key of the coder's own. `sections` and `inline` lay the block out as a stage does; `stack: true`
draws the shown fields one under the other — a task's name, its description beneath — not side by side.
`layout: "rows"` draws the block's units as one table instead of a card each: a row per unit (its
number and page tag first), the shown fields in `columns` — a field's name, a list of names stacked
in one cell, or `{"label", "fields", "wide"}` (`wide`: a long text gets room) — then a control per
question; the owner reads every coder's answers in a last column. `grid` (rows only: `{"of",
"row", "column", "panel"?, "tip"?}`, fields of the unit) draws above the rows the exhibit the units
sit in, as printed: the row of the template's `items` table whose caption line names the unit's
`of` value (`**Table 2. Caption**` names `Table 2`), its pipe tables one per panel; each unit is
placed by its panel, row label and column (case and spacing aside), its cell numbered as its row
and its `(SE)` line beneath marked, and a unit with no place is named under the grid. A stage with
a grid opens its evidence pane on that exhibit. `layout: "flags"` makes the grid where the coder
works: a click on a cited cell answers `grid.flag` (`{"question", "answer"}`: its value is wrong);
`grid.mapping` (`{"field", "sep", "question", "answer", "note"?}`) splits each cell's mapping text at
`sep` and shows each part once where it holds — over every cited cell, a panel, a column or a row,
else in the cell — and a click on a part answers the mapping question for every cell it covers
(with `note`, "wrong: <part>" is written into that question's answer, so the coding says which part
was wrong); a click on any other cell adds it as a unit the extraction missed, its panel, row, column
and value (`grid.value`) filled in. Beneath the grid only what needs words is listed as rows: the
flagged units (✕ takes the flags back), the added ones, any the grid could not place. `confirm`
(`{"label", "answers", "paper"?}`) is one button that gives every unit its `answers` for the
questions still open (where their `required_when` holds) and the paper its `paper` answers; the
cells show ✓ once confirmed. The owner reads the same grid and every unit as a row. The extraction
check (`genai-work-check`) works so: the drawn table with its mapping, a click per error, then
"Everything else correct".

**Planted errors.** A template with a `flags` block may say `plants: {"share", "seed", "kinds"}`
(`paperlens/plants.py`): about `share` of each coder's papers show one deliberate error on the
table — a cited value altered (`value`: two digits swapped, the last digit moved, the sign flipped,
the stars changed) or one part of a cell's mapping swapped for a part another cited cell of the
exhibit has (`mapping`). The draw is a function of the seed, the run, the step, the paper and the
coder, and no plant is drawn on a paper the coder has already answered on. The record is never
touched: the coder's task view carries the plant beside the data (`task.shown`), the workspace
draws it from there and never saves it, and for a value the exhibit's transcription in the pane is
altered to match (the view only). The plant is **caught** when the coder's first answer to its
question (the value's or the mapping's flag question) is not the answer `confirm` gives, **missed**
when it is; a catch is told at once (the answer's response carries `caught`, the workspace says
"well spotted" and shows the cell as it truly is), a miss is not. Plants live in `review_plant`;
the owner reads them per paper in the owner's task view (`plants`), per coder on the task
(`gate.plants`: planted, caught, missed, open — never in a coder's queue or project page), and all
of them through `GET /api/workflow-runs/{id}/plants?step=` — the list an analysis of the answers
leaves out. `plants` is outside the template's id. The extraction check plants in a third of the
papers (`share: 0.33`), values and mappings. `blind.fields`
names the fields whose model values never reach the coder's payload (nor the quotes on them, the
model's ratings, the items' `bears_on`); `blind.paper_fields: "unshown"` drops every paper field
no block shows. `evidence: "none"` on a stage lists no items (the PDF still opens at the stage's first drawn unit — a block with `only_sampled` — at its located passage). A question may carry a field's name
only when the block does not show that field — a blind answer to the question the model answered.
A block's `title` may not name a field in `blind.fields`; a head inherited from the preset's child
that names one falls back to the shown fields, so a head never prints what the coder is to answer.

**Answers** live in the coder's coding, never on the record: `record_coding.answers` for the
entry's own unit (`""`) and a child's rows (`"<child>#<_rid>"`), `document_coding.answers` for the
paper (`""`), a paper table's rows (`"<table>#<_rid>"`), distinct values
(`"<path>.<field>=<value>"`) and added units (`"<unit>#+<id>"`) — keyed template → unit → question;
every write is a `coding_event` (status `noted`), never a disagreement. A review step with
`template` opens its gate with the template (`review.template`, `review.template_id`); the queue
names the **task** (`<run>:<step>`), and `GET /api/documents/{id}/view?task=` serves the document
through it: a coder gets the projection — blind values gone, numbers masked, the template under
`template`, their `answers`; in a review of rows also `task.sampled` (the drawn rows' keys and
their columns' values), which a block's `only_sampled` and a `where` with `sampled:
"<table>.<column>"` read, so the check task shows the drawn exhibit and its cells alone — and
`POST /api/documents/{id}/answers {task, record_id?, unit, answers}` writes theirs (422 for a
value outside the options or an unknown unit); `{task, items: [{record_id?, unit, answers}], via?}`
writes several units in one transaction (every item checked first: all land or none), and `via`
(`confirm` for the one-click confirmation) is kept on each answer's coding event, so a bulk
confirmation stays visible in the audit trail. The workspace
draws a template stage as its blocks, a card per unit with the questions under the shown fields,
and says how many required answers are owed; `done` (and a verdict sent with `task`) is refused
while any is missing. The owner reads every coder's answers under each unit (`answers_by_coder`)
with the coders' disagreements marked, unblinded, and writes none. Exports and releases carry the
answers with each coding. `template_id` = `<id>@<sha8>` over what the template asks (units,
filters, the questions' names, types, options and requiredness); it is recorded on the gate and
enters the workflow's contract when a step names it. `GET /api/templates`, `GET
/api/templates/{id}` (resolved against its preset) and `POST /api/templates/validate` describe them;
a preset's own `display.review` keeps working as its implicit template, without questions.

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
| `GET /api/workflows`, `GET /api/workflows/{id}`, `POST /api/workflows/validate` | the bundled workflows, and the caller's own and the public user workflows (`personal`, `owned`); validate answers every problem at once with the normalised document |
| `POST /api/workflows {spec, visibility?}`, `PATCH /api/workflows/{id} {spec?, visibility?}`, `DELETE /api/workflows/{id}` | a user workflow: saved under a minted id (422 `{message, errors}` otherwise, a pin to someone else's private preset included), edited by its owner (the version moves, the contract follows), deleted when nothing runs it (409 otherwise) |
| `GET /api/workflows/grammar`, `GET /api/checks`, `GET /api/presets/{id}/rules` | what the editor offers: the step kinds and review options this engine accepts, the checks registry with its parameters, the rules a preset's field names imply |
| `POST /api/workflows/propose {questions, context?, feedback?, previous?, use_credits \| api_key+model \| base_url+model}` | a model proposes a preset to use (or a new preset document) and a workflow around it for what the user wants from the papers; the proposer validates both (a new preset's pin known before it is saved) and answers with the documents, path-prefixed problems, what it dropped, the calls per paper; one credit per proposal, a revision with feedback free, a failure refunded |
| `PATCH /api/datasets/{id}` `{workflow}` | declare the dataset's workflow |
| `GET /api/datasets/{id}/workflow` | the panel: steps with states, the active run, the gate |
| `GET /api/datasets/{id}/workflow/steps/{step}` | one step in detail, for the panel under the diagram: its declaration and, run by run (newest first), the papers it ran on and what came of each — a pass's calls and findings, an extraction's records, a check's findings by severity, a review's items checked per paper and per coder; error messages for the owner only |
| `POST …/workflow/plan`, `POST …/workflow/runs`, `GET …/workflow/runs` | plan, start, list |
| `GET /api/workflow-runs/{rid}`, `POST …/advance`, `/resume`, `/complete`, `/cancel`, `/retry`, `GET …/issues` | one run |
| `GET /api/workflow-runs/{rid}/queue/{step}[?coder=]`, `POST …/assign` | a gate's worklist (a coder's own); who reviews a paper |
| `GET /api/templates`, `GET /api/templates/{id}`, `POST /api/templates/validate` | the review templates |
| `GET /api/documents/{id}/view?task=<run>:<step>`, `POST /api/documents/{id}/answers` | a document through a task's template; a coder's answers on a unit |
| `GET/POST /api/datasets/{id}/coders`, `DELETE …/coders/{user}`, `GET /api/datasets?coder=1` | the dataset's coders; the datasets I code |
