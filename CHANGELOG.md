# Changelog

All notable changes to the engine. Versions are git tags (`vX.Y.Z`); the same version is
published to PyPI as `maseminer` and deployed as the hosted Metalens / MASEMiner service.

## 2.0.0 — unreleased

The first release of the new engine. Version numbers continue from the previous MASEMiner
app (last release 1.1.0), which this engine replaces; its source is tagged `legacy-sqlite`.

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
- Reader checks: a record of a public dataset has a page (`/record?id=…`) showing what the
  extractor read and the passages it cited; a reader drops their OWN copy of the PDF, which is
  hashed against the owner's, rendered for that reader alone and searched for the quotes, so they
  see them highlighted and can confirm or flag the record (with a proposed correction). Reader
  checks are counted and shown, never adopted as the maintainer's verdict; the copy is deleted
  when unused for the retention period or on request (`paperlens/review.py`). Dashboards and
  the devkit link every row to its record page; `release.json` carries the `site` to link to.

## 1.1.0 and earlier

- The previous MASEMiner application (FastAPI + SQLite), now retired.
