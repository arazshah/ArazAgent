# araz-agent — Personal Capture + Triage + Task + Search + Review + Admin Browser + Gatekeeper Layer

Single-user capture assistant for the Bale messenger. Receives text and voice
notes, transcribes voice via AvalAI, classifies each capture into a typed
`items` row (task/note/idea/event) via one LLM call, embeds it for semantic
search, and stores everything in Postgres. Ships with a minimal
server-rendered admin UI for runtime configuration.

Phase 1 was capture only. Phase 2 added triage: every captured text (typed
or transcribed) is classified once by the configured LLM into the `items`
table — see `ARCHITECTURE.md` for the data flow. Phase 3 added basic task
management over that table via bot commands: `/tasks` lists open tasks,
`/done <id>` closes one. Phase 4 added semantic search: every item is
embedded once (`pgvector`), and `/search <query>` finds the nearest items by
meaning, not just keyword. Phase 5 added a periodic review: `/review` on
demand any time, plus an optional once-a-day automatic summary (off by
default — turn on `review.auto_enabled` and set `review.send_time` in the
admin UI). Phase 6 added an item browser to the admin UI (`/admin/items`):
filter by type/status, edit a title, toggle open/done, and a small overview
(counts by type/status, a 7-day capture chart) — no bot needed to see or fix
what triage produced. Phase 7 hardened reliability: the triage and
embedding LLM calls now retry transient AvalAI failures instead of waiting
for the 10-minute recovery sweep (see `ARCHITECTURE.md` for a real bug this
turned up and fixed in the retry logic itself). **Phase 8 adds the Shamsi
(Jalali) calendar**: the triage LLM understands Persian dates the user
actually writes ("۱۵ مهر", "دوشنبه‌ی بعد"), and every deadline shown to a
human — bot replies, the admin item browser — displays in Shamsi.
Storage stays Gregorian throughout. On top of that, open tasks with a
deadline get a one-time reminder before they're due (default: the day
before, on by default — configurable under the "یادآوری" group in the
admin UI).

**Triage is now a gatekeeper, not just a classifier.** Every capture is
scored 0-25 against a "constitution" — weighted 12-month goals, hard "no"
rules, and a rough weekly-capacity budget, all configurable in the admin
UI under "قانون اساسی" (`constitution.goals`, `constitution.hard_rules`,
`constitution.weekly_capacity_hours`) — and gets a real decision: do it
now, schedule it, delegate it, archive it, or decline it. If capacity is
tight, the model is told to only ever answer "schedule" or "decline" —
and this is now enforced in code too, not just prompted: a "do it now"
that slips through when the week's capacity is already spent is
downgraded to "schedule" unconditionally, with the reason and the fact
that it was capped recorded on the item (see `ARCHITECTURE.md`'s
"Capacity guard" section). **And every "do it now" costs something else**:
the model must name a real open item to drop or defer whenever it answers
"do it now" — no trade-off named, no "do it now"; a slip-through is
downgraded to "schedule" the same way the capacity guard works, so the
list can no longer only ever grow (see "Mandatory trade-off" in
`ARCHITECTURE.md`). **And it starts learning from your real behavior**:
completing an item the gatekeeper had said to "decline" or "archive" is
now logged as an override (`decisions_log` — see "Calibration loop" in
`ARCHITECTURE.md`), with a running count and the most recent overrides
visible on the admin item browser. No goals defined yet? That's fine — it
falls back to general judgment until you fill them in. See
`ARCHITECTURE.md`'s "Constitution-driven scoring"
section for exactly how. **And the decision reaches you immediately** —
right after "✅ captured," a second message announces the type, decision,
score, reason, and (when relevant) what to drop instead, whether the
capture came in as text or voice. **The settings page is now a proper
dashboard**: an always-visible status strip up top, and a sidebar
(هسته / رفتار و تصمیم‌گیری / سیستم) that switches between one settings
group at a time in the main panel — instead of every group stacked in one
long scroll. `FUTURE.md` has what this still doesn't
do (it doesn't auto-change `items.status`, and there's no calibration log
of when you override a decision) and the rest of the roadmap: a real goals
table with a CRUD UI, commitments-to-others tracking, a personal CRM, and
more.

> Status: skeleton under active development. Sections below are being filled
> in as each part of the system lands (see commit history / PR).

## Why "never lose a capture"

The whole point of Phase 1 is to accumulate ~100 real captured items so later
phases can be designed against real data. If transcription fails, the LLM
gateway is down, or the audio download fails, the row is still written and the
user still gets a reply. Degrade, never drop.

## The probe-first workflow

The Bale Bot API is Telegram-shaped but not identical, and under-documented.
Before trusting any parsing code, run the probe against your real bot:

```bash
export BALE_BOT_TOKEN=...
python scripts/probe_bale.py
```

Send the bot a text message and a voice note, then inspect the printed JSON
and `probe_output.jsonl` (gitignored). Only after that should `app/providers/bale.py`
be trusted as a final shape — it currently follows the Telegram-like shape
described in the project brief as a provisional best guess, isolated in that
one file.

## Running locally (Docker Compose)

```bash
cp .env.example .env   # fill in the bootstrap variables, see below
make keys               # generates SECRET_ENCRYPTION_KEY and SESSION_SECRET
make up                 # docker compose up --build
make password            # set the admin password (writes argon2 hash to DB)
```

Then open `http://localhost:8000/admin` (or whatever `ADMIN_PATH` is set to),
log in, and fill in the Bale/LLM/Transcription settings. Nothing below the
bootstrap layer requires a container restart — settings changes take effect
immediately via `providers/registry.reload()`.

## Generating the three secrets

- `SECRET_ENCRYPTION_KEY`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `SESSION_SECRET`: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `bale.webhook_secret`: same as `SESSION_SECRET` generator, set from the admin UI (not `.env`)

`make keys` runs the first two for you.

## Setting the admin password

```bash
python scripts/set_admin_password.py
```

Prompts for a password (never pass it as an argv arg), prints the argon2
hash, and can write it directly into `app_settings`. Until a hash exists in
either the DB or `ADMIN_PASSWORD_HASH`, the admin UI returns 503.

## Deploying on Coolify

1. New Resource → Docker Compose, point at this repo.
2. Set environment variables (`DATABASE_URL`, `SECRET_ENCRYPTION_KEY`,
   `SESSION_SECRET`, `TZ=Asia/Tehran`, etc.) in the Coolify UI.
3. Set the domain to `agent.araz.me`, port `8000`. Coolify's reverse proxy
   terminates TLS; the app only ever speaks plain HTTP.
4. Attach a **persistent volume** for `/app/data` — this holds the voice
   note audio. Losing this volume loses the voice notes permanently.
5. Deploy.
6. Exec into the container (or run locally against `DATABASE_URL`) to run
   `scripts/set_admin_password.py`.
7. Log into `/admin`, fill in Bale/LLM/Transcription settings, use the
   **Test connection** buttons to verify each.
8. Register the webhook from the Settings page (or `make webhook`).

Coolify's own `SERVICE_FQDN_*` magic env vars are version-dependent — set the
domain and port from the Coolify UI rather than relying on them; see the
commented-out line in `docker-compose.yml`.

## Switching to polling

Set `bale.mode = polling` in the admin UI. **Delete the webhook first**
(`make webhook-delete`) — Bale will not deliver both webhook and polling
`getUpdates` cleanly at once. In polling mode the HTTP server still runs
(health checks, admin UI); a background task long-polls `getUpdates` with an
offset persisted in `app_settings`.

## Switching transcription backends

`transcription.backend` in the admin UI: `avalai` (default, calls the AvalAI
Whisper-compatible endpoint) or `local` (faster-whisper, requires the
optional `local` extra — `pip install .[local]` — and is not in the default
Docker image, to keep it small).

## Backups

- **Database**: standard `pg_dump`/`pg_restore` against `DATABASE_URL`, or
  Coolify's own Postgres backup feature if using its managed database.
- **Audio volume**: back up the named `/app/data` volume (e.g.
  `docker run --rm -v <volume>:/data -v $(pwd):/backup alpine tar czf /backup/audio-backup.tgz /data`).
  This is the only copy of every voice note's source audio — treat it as
  seriously as the database.

## ⚠️ Losing `SECRET_ENCRYPTION_KEY`

`SECRET_ENCRYPTION_KEY` encrypts every secret stored in `app_settings`
(the Bale bot token, the AvalAI API key, the webhook secret, the admin
password hash is *not* encrypted with it but everything else marked secret
is). **If this key is lost, every stored secret becomes permanently
unrecoverable** — there is no recovery path. You must then:

1. Rotate the Bale bot token (via BotFather-equivalent on Bale) immediately.
2. Rotate the AvalAI API key immediately.
3. Generate a new `SECRET_ENCRYPTION_KEY` and re-enter all secrets in the
   admin UI.

Back up `SECRET_ENCRYPTION_KEY` somewhere outside this repository and outside
the Coolify server itself (e.g. a password manager).
