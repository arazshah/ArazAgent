# Architecture

Phase 1 was a capture layer: take anything the owner sends to a Bale bot
(text, voice, forwards, documents) and get it into Postgres reliably,
transcribing voice along the way. Phase 2 adds exactly one step on top:
classify each capture's text into a typed `items` row with a single LLM
call (see "Phase 2: triage" below). Nothing beyond that — planning,
scoring, scheduling, review — is built yet.

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

## Phase 2: triage

Every inbox row that has text — typed directly, or produced by voice
transcription — is classified exactly once by the configured LLM
(`app/llm.classify_capture`) into the `items` shape (type, title, project,
goal_key, effort_minutes, deadline, decision, decision_reason). The call
happens in `app/triage.triage_inbox_row`, on the same "never raise, never
drop" contract as transcription:

```
 text capture ──────┐
                     │ (background task, same as voice)
 voice capture ──────┼──▶ app/triage.triage_inbox_row
 (after transcript)  │      1. skip if llm.triage_enabled == "false"
                     │      2. classify_capture() — one LLM call, JSON out
                     │      3. INSERT items (decision = 'auto')
                     │      4. UPDATE inbox SET processed_at = now()
                     └──▶ any failure (LLM down, bad JSON, not configured):
                          log a warning, leave processed_at NULL, return.
                          app.recovery.recover_stuck_triage retries rows
                          still unprocessed after 10 minutes.
```

Triage is scheduled as a background task right after the reply is sent for
text/document captures (`app/capture.py`), and runs inline at the end of
`app/transcribe/orchestrator.transcribe_voice_job` for voice, after the
transcript is saved. `inbox.processed_at` is set **only** by triage — a
transcript being `done` is not the same as the row being triaged, so
`inbox_unprocessed` / `count_pending_triage` stay a meaningful "what still
needs triage" queue.

Reusing `app/llm.py` for both the admin "test connection" health check and
`classify_capture` keeps there being exactly one LLM client in the app,
using the same `llm.*` settings (base URL, key, model) configured once in
the admin UI.

Deliberately out of scope for Phase 2 (see `FUTURE.md`): an item browser or
editor in the admin UI, human review/override of a triage decision,
planning/scoring/scheduling on top of `items`, and embeddings/search.

## Phase 3: task management (bot commands only)

Two bot commands operate directly on `items.status`, no new tables or admin
UI:

- **`/tasks`** — lists `items` where `type = 'task' AND status = 'open'`,
  ordered by `deadline` (nulls last) then `created_at`.
- **`/done <id>`** — sets that item's `status = 'done'`. Any `items` row can
  be closed this way, not only ones the LLM tagged `task` — the id is
  whatever `/items` or `/tasks` printed.

Both live in `app/commands.py` and query the pool directly (no new module —
the queries are simple enough not to warrant one). There is still no
priority, no editing, and no way to reopen a closed item from the bot; see
`FUTURE.md`.
