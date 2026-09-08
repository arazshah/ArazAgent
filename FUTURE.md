# Future (deferred out of Phase 1-5 scope)

Phase 1 was capture only; Phase 2 added triage/classification; Phase 3
added basic task open/close; Phase 4 added semantic search; Phase 5 added
an on-demand and optional daily review (see `ARCHITECTURE.md`). Anything
below was identified as plausibly useful but explicitly out of scope for
now — recorded here instead of built, so each phase stays small and
reliable.

- Scoring, planning, or scheduling logic.
- A weekly (as opposed to daily) review cadence, or a configurable day of
  week — review.send_time is a daily HH:MM only for now.
- Task priority, editing, or reopening a closed item from the bot.
- An inbox browser or item editor in the admin UI.
- Charts, dashboards, or a log viewer.
- Multi-user support, OAuth, or 2FA.
- Analytics.
- Notifications or reminders.
- Prometheus / metrics export.
- A real background job queue (Celery, Redis, etc.) — Phase 1 uses
  FastAPI `BackgroundTasks` plus a crude "reprocess anything pending for
  10+ minutes" recovery script, which is sufficient at this volume.
- Horizontal scaling of the admin login rate limiter (currently in-memory,
  single-process — see SECURITY.md).
