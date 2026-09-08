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

## Phase 4: semantic search

Every `items` row gets an embedding, best-effort, right after triage
creates it — same contract as everything else in this app: a failure never
loses the item, it just leaves `items.embedding` NULL for
`app.recovery.recover_missing_embeddings` to retry later.

```
 triage_inbox_row() inserts the items row
        │
        ▼
 app/embeddings.embed_item(pool, settings, item_id, text)
        │  1. skip if llm.embedding_enabled == "false"
        │  2. app.llm.embed_text() — one embedding call
        │  3. UPDATE items SET embedding = ...::vector
        └──▶ any failure (LLM down, not configured, a dimension
             mismatch after changing llm.embedding_model): log a
             warning, leave embedding NULL, return.

 /search <query> ──▶ app/embeddings.search_items()
        │  embeds the query the same way, then
        │  ORDER BY embedding <=> query_embedding LIMIT N
        └──▶ any failure, or nothing embedded yet: empty list,
             which the bot command reports as "nothing found" —
             indistinguishable from a genuine no-match on purpose.
```

`items.embedding` is an **unconstrained** `vector` column (no fixed
dimension), specifically so changing `llm.embedding_model` in the admin UI
needs no migration. The tradeoff: every row compared with `<=>` must share
one dimension, which holds as long as the model isn't changed on a
populated table — changing it means old embeddings silently stop matching
new queries (both still "work", they just never rank near each other).
There is no ANN index (ivfflat/hnsw) — at personal-assistant volume a
sequential scan is fast enough, and an ANN index needs a fixed dimension
anyway.

Reusing `app/llm.py` again (this time for `embed_text`) keeps the same
single-LLM-client property Phase 2 established.

## Phase 5: periodic review

A read-only report over the same `items`/`inbox` data (`app/review.
build_review_text`): open task count and the next few by deadline, plus a
7-day count by type. No new LLM calls, no new tables.

Two ways to get it:

- **`/review`** — on demand, any time.
- **An automatic daily send** — `app/main._review_loop` polls once a
  minute; when `review.auto_enabled` is `"true"` and local (Tehran) time
  has passed `review.send_time`, it sends the same report to every
  `bale.allowed_user_ids` entry and records `review.last_sent_date` so it
  fires at most once per day. Off by default. The send-once-a-day gate
  (`app/review.is_due`) is a pure function specifically so it has direct
  test coverage without exercising the loop itself — same reasoning as
  `_polling_loop` staying untested while the things it calls are tested.

The auto-send assumes a private Bale chat's `chat_id == user_id`, the same
assumption `app/capture.py` relies on for allowlisting — there is no stored
chat_id to send to otherwise, since a review isn't triggered by an incoming
message.

## Phase 6: admin item browser

`GET /admin/items` (`app/items_view.py` + `app/templates/items.html`) is a
plain server-rendered page over the same `items` table every other phase
already writes to — a small overview (counts by type/status, a 7-day
capture bar chart from `inbox.captured_at`) plus a filterable, paginated
list. Each row is two sibling `<form>`s (never nested — see the Phase 1
lesson on that in git history): one to edit the title, one to toggle
open/done. Both post back to `/admin/items/{id}/edit` and `.../toggle`
under the same session + CSRF checks as every other admin route.

The "go back to where I was" redirect after an edit/toggle is rebuilt
server-side from three individually re-validated hidden fields
(`redirect_type`, `redirect_status`, `redirect_page`) rather than trusting
a round-tripped querystring — form data is client-supplied, and an
unvalidated string ending up in a redirect `Location` header is exactly
the kind of thing worth not doing even when the practical blast radius (an
attacker redirecting their own browser on their own session) is minimal.

Deliberately out of scope: bulk actions, deleting an item, restoring a
deleted item, and any chart beyond the one simple 7-day bar — see
`FUTURE.md`.

## Phase 7: hardening — retries, scoped to what's actually needed

The original roadmap sketched Phase 7 as "operational hardening": a real
background job queue, a distributed rate limiter, Prometheus metrics. All
three assume multiple processes or an external consumer this deployment
doesn't have — one Coolify container, no Prometheus/Grafana pointed at it
anywhere. Building them now would be hardening against a failure mode
that doesn't exist yet, at the cost of new infrastructure (Redis, a
metrics scraper) nobody runs. FUTURE.md keeps all three explicitly
deferred, with the reasoning spelled out there rather than just repeated.

What *is* a real, already-observed gap: `app/llm.py`'s two background-job
LLM calls — `classify_capture` (Phase 2) and `embed_text` (Phase 4) — made
no retry attempt at all, unlike transcription, which has retried transient
AvalAI failures (`app/retry.py`, née `app/transcribe/retry.py`) since
Phase 1. A single dropped connection meant waiting for
`app.recovery`'s 10-minute sweep instead of just trying again in a few
seconds. Phase 7 moved that retry helper to `app/retry.py` (it's no
longer transcription-specific) and wired it into both functions.

Auditing that helper while extending its use turned up a real bug, live
in production since Phase 1: `is_transient()` treated `httpx.
TimeoutException`/`NetworkError` as retryable, but the openai SDK — the
only HTTP client every call site here uses — never lets a raw httpx
network or timeout error escape; it wraps them in its own
`APIConnectionError`/`APITimeoutError`, neither of which is an httpx
exception or carries a `status_code`. So the one failure mode retries
exist for — the connection dropping or timing out — was silently never
retried; only explicit 429/5xx *responses* were. Fixed by checking for
`openai.APIConnectionError` directly (it covers `APITimeoutError`, a
subclass). `app/retry.py` also gained direct unit tests (`tests/
test_retry.py`) — previously it was exercised only through a stubbed
transcription backend that bypassed the real retry path entirely, so this
bug had no test that could have caught it.

`test_chat_connection` (the admin "test connection" button) deliberately
does **not** retry — a human is watching a spinner for that one, and
turning a broken config into a 40-second wait before showing the error is
worse than showing it immediately.

## Phase 8: Shamsi (Jalali) calendar

Storage stays Gregorian — `items.deadline` is unchanged, still a plain SQL
`date`, and `app/triage.py`'s `_clean_deadline()` still expects
`YYYY-MM-DD`. Shamsi is entirely a presentation and LLM-prompt-context
concern, isolated in the new `app/jalali.py`:

- **Prompt context**: `app/llm.py`'s triage system prompt now states
  today's date in *both* calendars (Gregorian, for the output format; Shamsi,
  because that's what a Persian-speaking user actually writes — "۱۵ مهر",
  "دوشنبه‌ی بعد"). The model still must resolve to Gregorian `YYYY-MM-DD`
  for storage; only its ability to *understand* the input changed.
- **Display**: every place a stored deadline reaches a human —
  `/items`, `/tasks`, `/search`, `/review` (`app/commands.py`,
  `app/review.py`), and the admin item browser (`app/templates/items.html`,
  via a `jalali` Jinja filter registered in `app/admin/routes.py._templates()`)
  — now shows it as `format_deadline()`'s Shamsi string ("۱۵ دی ۱۴۰۴")
  instead of the raw ISO date.
- **New dependency**: `jdatetime` (pure Python, no C extension) does the
  actual Gregorian↔Jalali conversion; `app/jalali.py` only adds Persian-digit
  formatting and month names on top.

Nothing about `/done <id>`, `/tasks` filtering, or `items.status` changed —
this phase is display-only.

## Feature: deadline reminders

The first item off the post-Phase-8 roadmap: a once-per-task reminder
before its deadline. `items` gained one more column, `reminded_at`
(`NULL` until sent, same "best-effort column bolted on with `ALTER TABLE
... ADD COLUMN IF NOT EXISTS`" pattern as `embedding`), so a reminder is
never sent twice and survives a restart (the "already sent" state lives in
Postgres, not memory).

```
 app/main._reminder_loop (polls once a minute)
        │
        ▼
 app/reminders.due_reminders(pool, settings)
        │  SQL: type='task' AND status='open' AND deadline IS NOT NULL
        │       AND reminded_at IS NULL
        │       AND deadline - reminder.lead_hours <= now() (Tehran)
        └──▶ for each row: send format_reminder_text(), then
             mark_reminded() — stamps reminded_at so it can never
             fire again for that item.
```

Two settings (group "reminder"): `reminder.enabled` (default `true` —
unlike the daily review, this defaults **on**, since a missed deadline is
a worse failure mode than one unwanted extra message) and
`reminder.lead_hours` (default `24`). Deadlines are plain dates with no
time-of-day, so "the deadline" is treated as midnight Tehran time at the
start of that day — `reminder.lead_hours=24` means reminders start
arriving from the beginning of the day *before* the deadline.

Scoped deliberately to `type = 'task'` only (not `event`), matching what
was asked for; broadening it is a one-line SQL change if wanted later. No
repeated nagging for overdue tasks — one reminder, ever, per task; see
`FUTURE.md`. `due_reminders()` is tested directly against real rows, same
"test the DB/pure logic, leave the infinite loop itself untested" split as
`app.review.is_due()`.

## Constitution-driven scoring: from classifier to gatekeeper

Every phase through Phase 8 made triage a better **classifier** — type,
title, deadline, embedding, Shamsi dates. None of it made triage say "no."
`items.decision` existed since Phase 2 but every row got the same literal
value, `'auto'` — a placeholder marking "the LLM touched this," not an
actual decision. `app/constitution.py` and the rewritten
`app/llm._TRIAGE_SYSTEM_PROMPT` turn triage into an actual gatekeeper:

```
 app/triage.triage_inbox_row()
        │
        ▼
 app/constitution.build_constitution_context(pool, settings)
        │  - goals: parsed from the constitution.goals setting
        │    ("title:weight; title2:weight2" — free text, not a table;
        │    see the module docstring for why)
        │  - hard_rules: free text from constitution.hard_rules
        │  - remaining_capacity_hours: constitution.weekly_capacity_hours
        │    minus the summed effort_minutes of every open task (a rough
        │    "how full is your plate right now" proxy, not a strict
        │    weekly ledger)
        ▼
 app/llm.classify_capture(..., constitution)
        │  prompt states the goals (weighted), hard rules, and remaining
        │  capacity; if capacity is near zero, the model is explicitly
        │  told to only ever answer "schedule" or "decline", never
        │  "do_now"
        ▼
 items.decision  ∈ {do_now, schedule, delegate, archive, decline}
 items.score     0-25 (goal alignment ×3, compounding value ×3, economic
                  value ×2, irreversibility ×2, minus real time/mental
                  cost ×2 — all judged by the model, not computed in code)
 items.meta      {"what_to_drop_instead": "..."} when decision is
                  "do_now" under tight capacity — the model is asked what
                  open item should be dropped in exchange, so accepting
                  new work is never free
```

Decision and score do not (yet) change `items.status` — that's still a
natural next step, not built now; see FUTURE.md. What is built: the
decision is announced immediately, and it's also visible any time
afterward via `/items` and the admin item browser.

With no goals configured (`constitution.goals` empty — the expected state
until they're figured out), the prompt tells the model to score on general
judgment (impact/value/irreversibility) instead of goal alignment, and
`goal_key` becomes free text rather than a fixed set. Nothing breaks;
scoring is just less targeted until goals exist. `app.constitution`'s
parsing and capacity math are pure/DB-only and fully unit-tested
independent of the LLM.

## Capacity guard: enforced in code, not just prompted

The prompt tells the model to never answer `"do_now"` once
`remaining_capacity_hours` is at or below zero — but a system prompt is a
request the model can still ignore or misjudge, not a guarantee. Bugs and
model drift both fail the same way here: a "do it now" landing in an
already-full week. `app.triage._apply_capacity_guard` is the code-level
backstop that makes the rule actually hold:

```
 decision = _clean_decision(result.get("decision"))          # from the LLM
 decision, decision_reason, capacity_capped = _apply_capacity_guard(
     decision, decision_reason, constitution["remaining_capacity_hours"]
 )
```

If `decision == "do_now"` and `remaining_capacity_hours <= 0`, the
decision is downgraded to `"schedule"` unconditionally, `decision_reason`
gets a note explaining the downgrade appended (or set outright if there
was none), and `items.meta.capacity_capped = true` records that this
happened — visible later in the admin item browser or a calibration pass,
not silently lost. Every other decision (`schedule`, `delegate`,
`archive`, `decline`) is left untouched: none of them claim time this
week, so none of them need capping — only `"do_now"` can violate the
"there's no room left" fact the constitution computed. This is a pure
function (no I/O), unit-tested directly (`test_apply_capacity_guard_*` in
`tests/test_triage.py`) independent of any LLM response shape.

## Immediate decision announcement

The gatekeeper's decision reaches the user right after capture, not only
on request. `app/triage.triage_inbox_row` takes an optional `notify:
Callable[[str], Awaitable[None]] | None` — a one-argument "send this
message back" callback — and calls it once, right after the item is saved
(and after the best-effort embedding attempt), with a message built by
`_format_decision_announcement`: type + decision + title, the score, the
reason, and (when present) what to drop instead.

```
 text/document capture (app/capture.py)      voice capture, after
   _schedule_triage() closes over            transcription
   (provider, chat_id) from the                (app/transcribe/orchestrator.py)
   incoming message and builds notify            builds the same kind of
        │                                        notify closure over
        │                                        (provider, msg.chat_id)
        ▼                                             │
 app.state.triage (app/main.py._triage)  ◀─────────────┘
   a thin pass-through: (inbox_id, text, notify) -> triage_inbox_row(...)
        │
        ▼
 app.triage.triage_inbox_row(..., notify=notify)
   on success: build the announcement, await notify(announcement),
   catching and logging any failure — a message that fails to send
   never undoes the item already written to Postgres
```

`notify` is `None` wherever there's no live chat to reply into —
`app.recovery`'s sweep and `scripts/stats.py --recover` both call the
same `triage` callable with only `(inbox_id, text)`, relying on the
default, so recovered items are triaged and stored exactly as before but
silently (no one is watching a chat for them).

`TriageJob` (`app/capture.py`) grew a third parameter for this
(`Callable[[int, str | None, NotifyFn | None], Awaitable[None]]`); every
place that implements or fakes that callable — `app/main.py`,
`app/transcribe/orchestrator.py`, and their tests — was updated to accept
it, whether or not it's used.

## Settings page: dashboard layout

`/admin/settings` grew from one long column of stacked cards (every
settings group rendered at once) into a dashboard: a status strip that's
always visible at the top, plus a sidebar (grouped into "هسته", "رفتار و
تصمیم‌گیری", "سیستم") that switches which single group's card is shown in
the main panel. This is a server-rendered tab, not a JS one — the sidebar
is a plain `<a href="?tab=constitution">` per group, `settings_page` reads
`request.query_params["tab"]`, validates it against the known tab ids
(`_resolve_tab`, defaulting to `"bale"` for anything unrecognized), and the
template only renders the one matching `groups[active_tab]` (or the
`admin`/`webhook` pseudo-tabs, which aren't `GROUPS` entries but are
included in the same sidebar and `tab=` mechanism). No client-side
JavaScript, no page-state to lose on a slow connection — a bookmarked
`?tab=constitution` link always lands on the right panel, and every
POST handler that mutates a group (`settings_submit`, `settings_test`,
`settings_clear`, `webhook_register`, `webhook_delete`) redirects back
with the same `tab=` so saving a group keeps you looking at it instead of
bouncing to the first tab.

`_GROUP_LABELS`/`_GROUP_ICONS` (existing) plus new `_EXTRA_TAB_LABELS`/
`_EXTRA_TAB_ICONS` (for `admin`/`webhook`) are merged into `_TAB_LABELS`/
`_TAB_ICONS` for the sidebar; `_NAV_SECTIONS` is the fixed
`(section_label, (tab_id, ...))` grouping tuple that controls sidebar
order. `_ALL_TAB_IDS = frozenset(_TAB_LABELS)` is what `_resolve_tab`
validates against — an unknown or missing `tab` query param never 404s,
it just falls back to `bale`.

Type and decision labels (the emoji + Persian text like "🟢 همین حالا")
used to live as separate near-identical dicts in `app/commands.py` and
`app/admin/routes.py`. Since the announcement needed the same labels a
third time, they're now one shared `app/labels.py`.
