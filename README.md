# araz-agent — Personal Capture + Triage + Task Layer (Phase 3)

Single-user capture assistant for the Bale messenger. Receives text and voice
notes, transcribes voice via AvalAI, classifies each capture into a typed
`items` row (task/note/idea/event) via one LLM call, and stores everything in
Postgres. Ships with a minimal server-rendered admin UI for runtime
configuration.

Phase 1 was capture only. Phase 2 added triage: every captured text (typed
or transcribed) is classified once by the configured LLM into the `items`
table — see `ARCHITECTURE.md` for the data flow. **Phase 3 adds basic task
management** over that table via bot commands: `/tasks` lists open tasks,
`/done <id>` closes one. No planning, scoring, scheduling, or review flow
yet; see `FUTURE.md` for what's still deferred.

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
