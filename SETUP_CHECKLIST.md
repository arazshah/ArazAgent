# Setup Checklist

Ordered manual steps for the owner. Everything not listed here (application
code, CI, Docker images) is already done.

1. **`gh auth login`** — authenticate the GitHub CLI, if you haven't already
   (needed only if you plan to manage the repo from the CLI yourself; this
   session used the GitHub MCP tools instead since `gh` was not installed).
2. **Create the bot on Bale** and obtain its bot token. (Bale's bot-creation
   flow is not yet confirmed in detail — expect something BotFather-like.)
3. **Find your own numeric Bale user id.** Run `scripts/probe_bale.py`
   (`export BALE_BOT_TOKEN=...` first), send the bot any text message, and
   read `from.id` (or wherever the probe output shows it) out of the printed
   update. This becomes `bale.allowed_user_ids` in the admin UI — without it
   set, the bot silently drops every message.
4. **Generate the three secrets:**
   - `SECRET_ENCRYPTION_KEY`:
     `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `SESSION_SECRET`:
     `python -c "import secrets; print(secrets.token_urlsafe(48))"`
   - `bale.webhook_secret`: same generator as `SESSION_SECRET`, entered in the
     admin UI (not `.env`) once the app is running.
   - Or run `make keys` for the first two at once.
5. **Obtain the AvalAI API key** from the AvalAI dashboard.
6. **Run the probe and paste the JSON back.** Send the bot both a text
   message and a voice note, then share the printed JSON (and/or
   `probe_output.jsonl`) so `app/providers/bale.py` can be confirmed or
   corrected against the real shape — it currently follows a Telegram-shaped
   best guess, isolated in that one file specifically so this is a one-file
   change if it's wrong.
7. **Deploy on Coolify:**
   - New Resource → Docker Compose, pointed at this repo/branch.
   - Set environment variables (`DATABASE_URL`, `SECRET_ENCRYPTION_KEY`,
     `SESSION_SECRET`, `TZ=Asia/Tehran`, etc.) in the Coolify UI.
   - Set the domain to `agent.araz.me`, port `8000`.
   - Confirm a **persistent volume** is attached for `/app/data` — losing it
     loses every voice note's audio permanently.
   - Deploy.
8. **Set the admin password:** exec into the running container (or run
   locally against the same `DATABASE_URL`) and run
   `python scripts/set_admin_password.py`.
9. **Log into `/admin`**, fill in the Bale / LLM / Transcription settings,
   use each group's **Test connection** button to verify, then register the
   webhook from the Webhook panel (or `make webhook`).
