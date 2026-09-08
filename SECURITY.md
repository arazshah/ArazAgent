# Security

## Threat model

This is personal infrastructure for a single owner, exposed on the public
internet behind Coolify's reverse proxy at `https://agent.araz.me`. It holds
real credentials (a Bale bot token, an AvalAI API key) and the owner's
private captured notes and voice memos. There is exactly one legitimate
user. The realistic attackers are internet-wide scanners and anyone who
discovers the domain — not a targeted, resourced adversary.

## What protects what

- **The admin UI** (`ADMIN_PATH`, default `/admin`) is protected by a single
  argon2-hashed password (`admin.password_hash`), a signed, `HttpOnly`,
  `Secure`, `SameSite=Lax` session cookie (12-hour expiry) via `itsdangerous`,
  a per-session CSRF token on every mutating form, and a per-IP login rate
  limit (5 failures / 15 minutes, then 429, plus a constant ~250ms delay on
  every login attempt to blunt timing side channels). Incrementing
  `admin.session_epoch` — which happens automatically on password rotation,
  and can be triggered manually from the settings page — invalidates every
  existing session immediately.
- **`ADMIN_PATH` itself** is obscurity, not a security boundary: changing it
  from `/admin` makes the admin UI harder to stumble on but is not a
  substitute for the password.
- **The webhook** (`POST /webhook/{secret}`) is protected by comparing the
  path secret against `bale.webhook_secret` with `secrets.compare_digest`
  (constant-time), returning **404** — not 403 — on any mismatch so a
  scanner cannot distinguish "wrong secret" from "route doesn't exist". The
  optional `X-Telegram-Bot-Api-Secret-Token` header is checked the same way
  if Bale sends it.
- **Secrets at rest** (the Bale token, the webhook secret, the AvalAI key)
  are Fernet-encrypted in `app_settings.value_enc` using
  `SECRET_ENCRYPTION_KEY`. Decryption happens only inside `app/crypto.py`
  and `app/settings_store.py`. Templates and HTTP responses only ever see a
  masked hint (`sk-…a3f9`) — a decrypted secret is never rendered or logged.
  A logging filter additionally redacts values matching known secret
  patterns as a last line of defense.
- **The admin password itself can only be set or rotated by
  `scripts/set_admin_password.py`**, run with direct database/filesystem
  access — never from the admin UI. This means a compromised admin session
  cannot rotate the credential to lock the real owner out, and a
  compromised Bale account cannot reach configuration at all (no admin
  commands exist over the bot, by design).

## What is explicitly NOT protected

- **No 2FA.** A single password is the only gate on the admin UI.
- **No WAF, no IP allowlisting, no rate limiting beyond the login endpoint.**
  The webhook and health-check endpoints are open to the internet, as they
  must be for Bale (and Coolify's proxy) to reach them.
- **No audit of read access.** `settings_audit` records that a key was
  *changed*, and by which IP, but not who viewed the settings page or the
  inbox contents — there is no inbox browser in Phase 1 to view anyway.
- **The login rate limiter is in-memory and single-process.** It resets on
  restart and does not coordinate across multiple app instances. Acceptable
  for a single-user, single-instance deployment; would need a shared store
  (Redis, Postgres) to hold up under horizontal scaling.
- **No dependency/image vulnerability scanning** is wired into CI.

## Incident procedure: suspected credential leak

If the admin password, `SECRET_ENCRYPTION_KEY`, the Bale bot token, or the
AvalAI API key is suspected leaked (accidentally committed, exposed in
logs, shared over an insecure channel):

1. **Admin password**: run `scripts/set_admin_password.py` immediately. This
   invalidates every existing session (it bumps `admin.session_epoch`) as
   part of setting the new password.
2. **Bale bot token**: rotate it via Bale's bot management (BotFather-
   equivalent), then set the new value in the admin UI under Bale settings.
   The provider client rebuilds automatically — no restart needed.
3. **AvalAI API key**: rotate it in the AvalAI dashboard, then set the new
   value in the admin UI under LLM settings.
4. **`SECRET_ENCRYPTION_KEY`**: this one has no rotation path — see the
   README's warning. If it leaks, treat every secret it protects (Bale
   token, webhook secret, AvalAI key) as compromised and rotate all of
   them, then generate a new `SECRET_ENCRYPTION_KEY` and re-enter every
   secret in the admin UI.
5. Check `settings_audit` for unexpected `set`/`clear` entries and their
   `actor_ip` to gauge whether the credential was actually used, not just
   exposed.
