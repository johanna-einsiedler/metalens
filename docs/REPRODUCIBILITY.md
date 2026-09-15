# Reproducing an extraction

An extraction is determined by four things, all recorded in every export
(`metadata.engine`, `metadata.preset`, and each paper's `provenance` block):

| What | Where it is recorded | How to get the same one again |
|---|---|---|
| Engine version and commit | `metadata.engine.version`, `metadata.engine.git_sha` | `pipx install maseminer==<version>` or check out the tag `v<version>`; the Docker image `ghcr.io/johanna-einsiedler/maseminer:v<version>` is byte-exact |
| Preset (the data contract) | `metadata.preset.schema_id` (e.g. `masem-direct@eb5f0287`) and the full `metadata.preset.spec` | The schema id is a hash of the preset's fields, entries and confidence groups; the spec itself is in the export and can be dropped into `<data>/presets/` |
| Prompt and parameters | `provenance.prompt_sha256`, `provenance.params` | Render the preset with the same parameters (`POST /api/presets/<id>/render`) and compare the hash |
| Model | `model`, `provenance.resolved_model` | The exact provider model id that answered; see below when it has been retired |

Model outputs are not deterministic even at temperature 0, so "reproduce" means: the same
engine, preset, prompt and model produce an extraction that a reviewer would verify to the
same values. Keep the export; it is the record of what was done.

## When a hosted model is retired

Provider models are withdrawn on a schedule the provider sets. Record the resolved model id,
and for long-lived work prefer one of:

- an open-weights model served locally (Ollama, vLLM, LM Studio) through a custom base URL —
  `paperlens/providers.py` speaks the OpenAI-compatible protocol to any of them;
- a dated provider model id rather than a floating alias.

The prompts, presets and the evidence-location code live in this repository, so the
non-model half of the pipeline is reproducible from the tag alone.

## Citing

Cite the software (`CITATION.cff`) with the engine version, and name the preset schema id and
the model id in the methods section, for example:

> Data were extracted with MASEMiner 2.0.0 (engine commit 1840ca3b), preset
> `masem-direct@eb5f0287`, model `gemini-3.5-flash` (resolved `gemini-3.5-flash-001`), and
> verified by two coders against the source PDFs.

## Dependency pins

Releases commit `uv.lock`; `uv sync --frozen` reproduces the exact environment. The PyPI
wheel pins version ranges only, so for exact replication use the lock file, the Docker image
or the tag.
