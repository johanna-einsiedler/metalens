-- PaperLens Phase 1 record spine — Postgres schema.
--
-- Built AUTH-READY (plan §"Phase 1 is auth-ready"): every ownable row carries
-- nullable owner_user_id + session_id from day one; Phase 2 only adds the users
-- FK and populates the column — no ownership-semantics migration. All scoping
-- goes through current_principal() (see principal.py), never hardcoded session_id.
--
-- UUIDs are minted in Python (uuid4) so no pgcrypto/uuid-ossp extension is
-- required. JSONB everywhere flexible per-schema data lives.

-- ── paper: the universal metadata record, DOI-deduped (plan §3.5) ────────────
CREATE TABLE IF NOT EXISTS paper (
    id              uuid PRIMARY KEY,
    doi             text UNIQUE,                 -- normalized; NULL when none extracted
    openalex_id     text,
    title           text,
    authors         jsonb,
    year            integer,
    publication_date date,
    journal         text,
    issn            text[],
    publisher       text,
    work_type       text,
    license         text,
    is_oa           boolean,
    oa_status       text,
    oa_pdf_url      text,
    author_keywords text[],
    openalex_topics jsonb,
    primary_topic   text,
    jel_codes       text[],
    mesh            jsonb,
    sdg             jsonb,
    supplementary_links jsonb,
    code_links      jsonb,
    data_links      jsonb,
    referenced_works text[],
    funders         jsonb,
    raw_metadata    jsonb,                       -- verbatim paper_metadata (round-trip source)
    access_tier     text DEFAULT 'user_supplied',
    retention       text DEFAULT 'retained',
    last_enriched_at timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS paper_topics_gin ON paper USING gin (openalex_topics);
CREATE INDEX IF NOT EXISTS paper_jel_gin    ON paper USING gin (jel_codes);

-- per-field provenance: EXTRACTED vs ENRICHED vs PREDICTED (the differentiator)
CREATE TABLE IF NOT EXISTS paper_field_provenance (
    id          uuid PRIMARY KEY,
    paper_id    uuid NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    field       text NOT NULL,
    source      text NOT NULL,   -- extracted_llm|crossref|unpaywall|openalex|datacite|s2
    method      text NOT NULL,   -- extracted|api|predicted
    confidence  real,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pfp_paper_idx ON paper_field_provenance(paper_id);

-- ── schema: serialized preset vocabulary (presets stay source of truth) ──────
CREATE TABLE IF NOT EXISTS schema (
    id             text PRIMARY KEY,             -- "<preset_id>@<schema_version>"
    preset_id      text,
    schema_version text,
    field_defs     jsonb,
    source         text DEFAULT 'preset',
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- ── extraction_document: one ingested canonical-JSON doc -> many records ─────
-- Holds the document-level structure needed to reconstruct the publishable form
-- exactly (core array key/shape, top-level extras, verbatim paper_metadata).
CREATE TABLE IF NOT EXISTS extraction_document (
    id                 uuid PRIMARY KEY,
    paper_id           uuid REFERENCES paper(id) ON DELETE SET NULL,
    schema_id          text REFERENCES schema(id),
    source_job_id      text,
    core_key           text NOT NULL,
    core_shape         text NOT NULL,            -- list | table
    had_top_evidence   boolean NOT NULL DEFAULT false,
    top_extras         jsonb NOT NULL DEFAULT '{}'::jsonb,
    paper_metadata_raw jsonb,
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- ── record: ONE extracted entry (plan §3 granularity decision) ───────────────
CREATE TABLE IF NOT EXISTS record (
    id                  uuid PRIMARY KEY,
    document_id         uuid NOT NULL REFERENCES extraction_document(id) ON DELETE CASCADE,
    paper_id            uuid REFERENCES paper(id) ON DELETE SET NULL,
    dataset_id          uuid,                    -- FK added with dataset table
    schema_id           text REFERENCES schema(id),
    entry_index         integer NOT NULL,
    field_values        jsonb NOT NULL,
    extraction          jsonb,                   -- {model, resolved_model, prompt_sha256, date, job_id}
    verification_status text NOT NULL DEFAULT 'unverified',
    owner_user_id       uuid,                    -- auth-ready (NULL in Phase 1)
    session_id          text,                    -- anonymous scoping
    source_job_id       text,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS record_paper_idx   ON record(paper_id);
CREATE INDEX IF NOT EXISTS record_doc_idx     ON record(document_id);
CREATE INDEX IF NOT EXISTS record_schema_idx  ON record(schema_id);
CREATE INDEX IF NOT EXISTS record_dataset_idx ON record(dataset_id);
-- powers the paper coverage/passport lookup (plan §3.5)
CREATE INDEX IF NOT EXISTS record_coverage_idx ON record(paper_id, schema_id, dataset_id);
-- full-text over the entry payload
-- Full-text over the entry payload. A stored generated column (not a bare
-- expression index) so the @@ predicate reliably hits the GIN index.
ALTER TABLE record ADD COLUMN IF NOT EXISTS field_values_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(field_values::text, ''))) STORED;
CREATE INDEX IF NOT EXISTS record_fts_col_idx ON record USING gin (field_values_tsv);
DROP INDEX IF EXISTS record_fts_idx;   -- retire the unused expression index

-- ── evidence_span: per-evidence-item, placement-tagged for faithful round-trip
CREATE TABLE IF NOT EXISTS evidence_span (
    id          uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES extraction_document(id) ON DELETE CASCADE,
    record_id   uuid REFERENCES record(id) ON DELETE CASCADE,   -- NULL for caption/doc-level
    ord         integer NOT NULL,
    placement   text NOT NULL,                  -- entry | top
    entry_index integer,
    field_path  text,
    snippet     text,
    page        integer,
    source      text,
    rect        jsonb
);
CREATE INDEX IF NOT EXISTS evidence_doc_idx    ON evidence_span(document_id);
CREATE INDEX IF NOT EXISTS evidence_record_idx ON evidence_span(record_id);

-- ── field_confidence: exploded extraction_confidence (non-publishable) ───────
CREATE TABLE IF NOT EXISTS field_confidence (
    id          uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES extraction_document(id) ON DELETE CASCADE,
    record_id   uuid REFERENCES record(id) ON DELETE CASCADE,
    block       text NOT NULL,
    level       text,
    notes       text
);
CREATE INDEX IF NOT EXISTS confidence_doc_idx ON field_confidence(document_id);

-- ── verification (Phase 3): record-level credibility events ─────────────────
-- The promoted human-override: who verified/flagged what, when, and the diff of
-- what they changed. record.verification_status is the projection of the latest
-- event; dataset badges are COMPUTED from these (never hand-assigned).
CREATE TABLE IF NOT EXISTS verification_event (
    id               uuid PRIMARY KEY,
    record_id        uuid NOT NULL REFERENCES record(id) ON DELETE CASCADE,
    verifier_user_id uuid,                       -- who (nullable: anon/community)
    verifier_kind    text,                       -- maintainer | community | paperlens
    status           text NOT NULL,              -- verified | flagged | unverified
    diff             jsonb,                       -- [{field_path, original_value, final_value}]
    notes            text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS verification_event_record_idx ON verification_event(record_id);

-- ── tables reserved for later phases (created now so FKs/queries are stable) ──
CREATE TABLE IF NOT EXISTS dataset (
    id            uuid PRIMARY KEY,
    slug          text UNIQUE,
    schema_id     text REFERENCES schema(id),
    title         text,
    description   text,
    owner_user_id uuid,
    session_id    text,                          -- anonymous scoping (claimable on login)
    visibility    text DEFAULT 'private',
    license       text,
    version       integer DEFAULT 1,
    git_pr_url    text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
-- idempotent add for already-created dev DBs (CREATE TABLE IF NOT EXISTS won't alter)
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS session_id text;

-- ── Phase 4: views as data (the observatory is a saved view, not an app) ─────
-- "view" is a SQL reserved word, so the table is saved_view. A view REFERENCES
-- datasets/records via its query; it never copies them, so it recomputes on read.
CREATE TABLE IF NOT EXISTS theme (
    id            uuid PRIMARY KEY,
    owner_user_id uuid,
    name          text,
    tokens        jsonb,                         -- palette / logo / accent (org branding)
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS saved_view (
    id            uuid PRIMARY KEY,
    owner_user_id uuid,
    session_id    text,
    title         text,
    view_type     text,                          -- observatory | aggregate | forest | table
    dataset_ids   jsonb,                         -- references, never copies
    query         jsonb,                         -- {schema_id?, verification_status?, ...}
    viz_config    jsonb,                         -- {kind, group_by, measure, value_field}
    theme_id      uuid REFERENCES theme(id),
    visibility    text DEFAULT 'public',
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS saved_view_owner_idx   ON saved_view(owner_user_id);
CREATE INDEX IF NOT EXISTS saved_view_session_idx ON saved_view(session_id);
CREATE INDEX IF NOT EXISTS dataset_owner_idx   ON dataset(owner_user_id);
CREATE INDEX IF NOT EXISTS dataset_session_idx ON dataset(session_id);

-- ── Phase 2: accounts (email+password primary; GitHub OAuth later) ───────────
-- Ownership is additive: owner_user_id was nullable on every ownable row from
-- Phase 1, so accounts just populate it. No API key ever lives here.
CREATE TABLE IF NOT EXISTS users (
    id            uuid PRIMARY KEY,
    email         text UNIQUE NOT NULL,
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
    token       text PRIMARY KEY,                -- opaque; set as an httpOnly cookie
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);

-- ── Private storage: own the document row (not just its records) ──────────────
-- Makes the per-document authorization gate a single cheap lookup. Idempotent
-- (matches the ADD COLUMN IF NOT EXISTS convention used above).
ALTER TABLE extraction_document ADD COLUMN IF NOT EXISTS owner_user_id uuid;
ALTER TABLE extraction_document ADD COLUMN IF NOT EXISTS session_id    text;
CREATE INDEX IF NOT EXISTS extraction_document_owner_idx   ON extraction_document(owner_user_id);
CREATE INDEX IF NOT EXISTS extraction_document_session_idx ON extraction_document(session_id);
-- Backfill legacy documents from their records so pre-existing data is gated too.
UPDATE extraction_document d SET
  owner_user_id = COALESCE(d.owner_user_id,
                  (SELECT r.owner_user_id FROM record r
                   WHERE r.document_id = d.id AND r.owner_user_id IS NOT NULL LIMIT 1)),
  session_id    = COALESCE(d.session_id,
                  (SELECT r.session_id FROM record r
                   WHERE r.document_id = d.id AND r.session_id IS NOT NULL LIMIT 1))
WHERE d.owner_user_id IS NULL AND d.session_id IS NULL;

-- Account profile: a citation name used to attribute the user's public datasets.
ALTER TABLE users ADD COLUMN IF NOT EXISTS citation_name text;

-- ── Datasets carry their extraction recipe (the DEFAULT reused when adding papers) ──
-- A dataset is the project spine: reopen it, add papers that inherit model/prompt/schema.
-- The recipe is a default only; each record keeps its own extraction.model. The prompt
-- is browser-supplied and only persisted here when the user chooses to save the dataset.
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS prompt     text;
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS model      text;
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS updated_at timestamptz;
-- Original upload filename (available at /api/extract as pdf.filename; nicer than title).
ALTER TABLE extraction_document ADD COLUMN IF NOT EXISTS filename text;

-- ── Parsed-document cache: reusable markdown + per-page text, deduped by PDF hash ──
-- The parsed text used to be recomputed from the PDF on every extraction/highlight and
-- thrown away. We now persist it ONCE per distinct PDF (content hash) in object storage
-- (text/<sha>.md + text/<sha>.pages.json), so it can be re-used for a "parsed text"
-- preview, faster locate, and re-extraction under a different preset. The row is just
-- the hash→object-key map; the text itself lives in object storage, NOT the DB.
CREATE TABLE IF NOT EXISTS parsed_document (
    pdf_sha256 text PRIMARY KEY,
    n_pages    integer,
    md_key     text,                        -- object key: text/<sha>.md
    pages_key  text,                        -- object key: text/<sha>.pages.json
    char_len   integer,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE extraction_document ADD COLUMN IF NOT EXISTS pdf_sha256 text;
CREATE INDEX IF NOT EXISTS extraction_document_sha_idx ON extraction_document(pdf_sha256);

-- A "screened — no records" sentinel: a record with empty field_values that marks a paper
-- as attempted-but-yielded-nothing, so a 0-record paper can still be part of a dataset
-- (coverage/provenance). Excluded from data-record counts; shown as "screened" in review.
ALTER TABLE record ADD COLUMN IF NOT EXISTS screened_empty boolean NOT NULL DEFAULT false;

-- ── Metalens credits: per-user extraction allowance for server-key runs ──────────
-- Logged-in users can be granted N credits and run extractions WITHOUT their own API
-- key (Metalens supplies a server key + a fixed model); each such run decrements the
-- balance by 1. Balance = credits_granted - credits_used; every change is also logged
-- to credit_ledger for the account-page usage view. Own-key runs never touch credits.
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_granted integer NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_used    integer NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS credit_ledger (
    id          uuid PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta       integer NOT NULL,             -- -1 extraction, +N grant, +1 refund
    reason      text,                          -- 'extraction' | 'grant' | 'refund'
    document_id uuid,
    model       text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS credit_ledger_user_idx ON credit_ledger(user_id);

-- ── Dataset full-text search (title + description + the PROMPT/preset it used) ────
-- A published dataset carries its extraction recipe (prompt + schema_id); this makes
-- that recipe searchable so people can find datasets — and the prompt/preset behind
-- them — by keyword in the catalogue. Weighted: title (A) > description (B) > prompt +
-- schema_id (C). Generated column + GIN, mirroring record.field_values_tsv.
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(prompt, '') || ' ' || coalesce(schema_id, '')), 'C')
    ) STORED;
CREATE INDEX IF NOT EXISTS dataset_search_idx ON dataset USING gin (search_tsv);

-- ── Personal presets: user-owned extraction presets alongside datasets & analyses ─
-- A preset that lives in the DB (not the presets/*.json files) and belongs to a user.
-- Resolved by id GLOBALLY (so the worker/persist path can build its schema grammar
-- without a principal); the PICKER is principal-scoped (own + public). Publishing a
-- dataset built with a personal preset flips the preset to public (usable by others).
-- Mirrors the dataset ownership columns (owner_user_id + session_id claimable on login).
CREATE TABLE IF NOT EXISTS personal_preset (
    id              text PRIMARY KEY,          -- "<slug>-<uuid8>"; unique across file + DB presets
    owner_user_id   uuid,
    session_id      text,
    visibility      text NOT NULL DEFAULT 'private',   -- private | public
    title           text NOT NULL,
    tagline         text,
    description     text,
    mode            text NOT NULL DEFAULT 'extraction',
    prompt          text NOT NULL,
    sub_views       jsonb,                      -- review-tab grammar (same shape as file presets)
    template_params jsonb,
    accent_color    text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS personal_preset_owner_idx   ON personal_preset(owner_user_id);
CREATE INDEX IF NOT EXISTS personal_preset_session_idx ON personal_preset(session_id);
