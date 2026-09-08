# Architecture

Phase 1 is a capture layer. It has exactly one job: take anything the owner
sends to a Bale bot (text, voice, forwards, documents) and get it into
Postgres reliably, transcribing voice along the way. Nothing downstream of
that — no triage, no classification, no LLM calls on captured content — is
built yet.

## Data flow

```
                        ┌─────────────────────────┐
 Bale messenger  ──────▶│ POST /webhook/{secret}   │
 (text/voice/doc)       │  or polling getUpdates   │
                        └────────────┬─────────────┘
                                     │ raw update (dict)
                                     ▼
                        ┌─────────────────────────┐
                        │ providers/bale.py        │  ← ALL Bale JSON shape
                        │  parse_update()           │    knowledge lives here
                        └────────────┬─────────────┘
                                     │ IncomingMessage (typed, provider-agnostic)
                                     ▼
                        ┌─────────────────────────┐
                        │ capture.handle_update()   │
                        │  1. allowlist check        │
                        │  2. INSERT inbox (ON       │
                        │     CONFLICT DO NOTHING)   │◀── idempotency guarantee
                        │  3. reply immediately       │
                        └────────────┬─────────────┘
                                     │ voice only: BackgroundTasks
                                     ▼
                        ┌─────────────────────────┐
                        │ transcribe/orchestrator   │
                        │  download audio (kept      │
                        │  permanently) → transcribe  │
                        │  → UPDATE inbox             │
                        └─────────────────────────┘

                        ┌─────────────────────────┐
                        │ app_settings (Postgres)   │◀── admin UI reads/writes
                        │  db → env → default        │    this; capture/providers/
                        │  resolution, 30s cache      │    transcribe read it live
                        └─────────────────────────┘
```

Every step before transcription is synchronous and fast: parse, authorize,
insert, reply. Transcription is the only part that can meaningfully fail or
take time, so it alone is pushed to a background task. This is what makes
"never lose a capture" actually true — the row exists and the user has a
reply before anything that could fail (network to AvalAI, a slow model) even
starts.

## Why a provider-adapter boundary

The Bale Bot API's exact JSON shape was unverified when this was built (see
`scripts/probe_bale.py` and the provisional fixtures in `tests/fixtures/`).
Rather than let that uncertainty leak through the codebase, every field
access on a raw Bale payload is isolated in `app/providers/bale.py`, behind
`.get()` chains that degrade instead of crash, and behind a Protocol
(`app/providers/base.py`) that the rest of the app codes against. If the
probe reveals a different shape, or the project later needs a second
messaging platform, only one file changes.

## Why two configuration layers

`app/bootstrap.py` (env-only, read once at process start) holds exactly what
is needed to reach the database and decrypt secrets — nothing that depends
on the database can live there, because it has to work before the database
connection exists. Everything else — the Bale token, the AvalAI key, the
allowlist, transcription backend choice — lives in `app_settings`
(`app/settings_store.py`), editable from the admin UI with **no restart
required**. The resolution order (db → env → default) means the environment
still works as a bootstrap/recovery path if the UI is ever broken, while the
UI remains the primary way to configure a running instance. A 30-second
cache with explicit invalidation on write keeps this cheap without making it
stale for long.

## Phase 2 hooks

Three things exist now specifically so Phase 2 (triage, planning, drafting)
can be built without a schema migration or an architectural change:

- **`inbox.processed_at`** — currently always `NULL` in Phase 1 (nothing
  processes captures beyond transcription). Phase 2's triage step is
  expected to set this when it has turned an inbox row into one or more
  `items` rows, so `inbox_unprocessed` stays a meaningful "what still needs
  triage" queue.
- **The `items` table** — created by `db/schema.sql` now, written to by
  nothing in Phase 1. Its shape (type, project, goal_key, effort_minutes,
  deadline, decision, decision_reason, status, human_override) anticipates
  the fields a triage/planning step will need to fill in.
- **`app/llm.py`** — Phase 1 uses it only for the admin "test connection"
  health check (a one-token completion). Phase 2's triage/classification
  calls against captured content are expected to extend this module rather
  than add a second LLM client.
