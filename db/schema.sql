-- Idempotent schema, executed on every application startup (see app/db.py).
-- Rationale: a managed Postgres (e.g. Coolify) may never run
-- docker-entrypoint-initdb.d, and redeploys must be safe to re-apply.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS inbox (
  id                  bigserial PRIMARY KEY,
  source              text        NOT NULL,
  raw_text            text,
  audio_path          text,
  audio_duration_s    int,
  transcript_status   text        NOT NULL DEFAULT 'n/a',
  transcript_error    text,
  transcript_backend  text,
  provider            text        NOT NULL DEFAULT 'bale',
  provider_update_id  bigint,
  provider_message_id bigint,
  provider_chat_id    bigint,
  raw_update          jsonb       NOT NULL DEFAULT '{}',
  captured_at         timestamptz NOT NULL DEFAULT now(),
  processed_at        timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS inbox_provider_update_uniq
  ON inbox (provider, provider_update_id)
  WHERE provider_update_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS inbox_unprocessed
  ON inbox (captured_at) WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS inbox_pending_transcript
  ON inbox (id) WHERE transcript_status = 'pending';

-- Runtime configuration, editable from the admin UI.
CREATE TABLE IF NOT EXISTS app_settings (
  key          text        PRIMARY KEY,
  value_plain  text,                              -- non-secret values
  value_enc    bytea,                             -- Fernet-encrypted secrets
  is_secret    boolean     NOT NULL DEFAULT false,
  group_name   text        NOT NULL,              -- bale | llm | transcription | system | admin
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT app_settings_exactly_one_value CHECK (
    (value_plain IS NOT NULL)::int + (value_enc IS NOT NULL)::int = 1
  )
);

-- Audit trail. Records THAT a key changed, never its value.
CREATE TABLE IF NOT EXISTS settings_audit (
  id          bigserial PRIMARY KEY,
  key         text        NOT NULL,
  action      text        NOT NULL,   -- set | clear
  actor_ip    text,
  changed_at  timestamptz NOT NULL DEFAULT now()
);

-- Written by app/triage.py (Phase 2): one row per inbox item the LLM
-- classified. decision is always 'auto' for now; human_override/
-- override_reason are reserved for a future manual-review UI.
CREATE TABLE IF NOT EXISTS items (
  id              bigserial PRIMARY KEY,
  inbox_id        bigint REFERENCES inbox(id) ON DELETE SET NULL,
  type            text NOT NULL,
  title           text NOT NULL,
  project         text,
  goal_key        text,
  effort_minutes  int,
  deadline        date,
  decision        text,
  decision_reason text,
  status          text NOT NULL DEFAULT 'open',
  human_override  text,
  override_reason text,
  meta            jsonb NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS items_open ON items (status, deadline);

-- Phase 4: best-effort semantic search over items (app/embeddings.py).
-- Unconstrained `vector` (no fixed dimension) rather than vector(1536) so
-- app_settings' llm.embedding_model can change without a migration — every
-- row must still share one dimension for `<=>` comparisons to work, which
-- holds as long as the model isn't changed on a populated table. No ANN
-- index (ivfflat/hnsw): at personal-assistant volume a sequential scan
-- with `<=>` is fast enough, and an ANN index needs a fixed dimension.
ALTER TABLE items ADD COLUMN IF NOT EXISTS embedding vector;

-- Feature 1 of the post-Phase-8 roadmap: a once-per-task deadline
-- reminder (app/reminders.py). NULL until sent; set exactly once so a
-- task is never reminded twice.
ALTER TABLE items ADD COLUMN IF NOT EXISTS reminded_at timestamptz;

-- The "constitution" (app/constitution.py, app/llm.py): a 0-25 weighted
-- score against the goals/hard-rules/capacity configured in app_settings,
-- computed alongside `decision` (do_now/schedule/delegate/archive/decline
-- — see app.llm.VALID_DECISIONS) at triage time.
ALTER TABLE items ADD COLUMN IF NOT EXISTS score int;

-- Calibration loop (app/decisions_log.py): recorded whenever the user's
-- real action contradicts the gatekeeper — today, specifically, completing
-- an item the model had decided to "decline" or "archive". Raw material
-- for eventually tuning scoring/hard-rules toward the user's actual
-- judgment, not itself a feedback mechanism yet — see FUTURE.md.
CREATE TABLE IF NOT EXISTS decisions_log (
  id              bigserial PRIMARY KEY,
  item_id         bigint NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  decision        text NOT NULL,
  score           int,
  override_action text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS decisions_log_item_id ON decisions_log (item_id);

-- Commitments to other people (app/commitments.py): the same `items` row,
-- but tagged with who it's owed to. Higher-stakes than a personal task —
-- breaking a promise to someone else costs trust in a way a missed
-- personal task doesn't — so it's tracked as a distinct, filterable
-- dimension rather than a new table; everything else about the item
-- (triage, status, capacity accounting) stays exactly the same.
ALTER TABLE items ADD COLUMN IF NOT EXISTS commitment_to text;
CREATE INDEX IF NOT EXISTS items_open_commitments
  ON items (deadline) WHERE commitment_to IS NOT NULL AND status = 'open';
