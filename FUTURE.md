# Future (deferred out of Phase 1-7 scope)

Phase 1 was capture only; Phase 2 added triage/classification; Phase 3
added basic task open/close; Phase 4 added semantic search; Phase 5 added
an on-demand and optional daily review; Phase 6 added an item browser to
the admin UI; Phase 7 hardened retries for AvalAI calls (see
`ARCHITECTURE.md`). Anything below was identified as plausibly useful but
explicitly out of scope for now — recorded here instead of built, so each
phase stays small and reliable.

- Scoring, planning, or scheduling logic.
- A weekly (as opposed to daily) review cadence, or a configurable day of
  week — review.send_time is a daily HH:MM only for now.
- Task priority, or reopening a closed item from the bot (the admin item
  browser can now do both edit and reopen — the bot still can't).
- Bulk actions, deleting an item, or restoring a deleted one in the admin
  item browser.
- Charts beyond the one simple 7-day capture bar, a dedicated dashboard
  page, or a raw log viewer.
- An inbox (as opposed to items) browser — captures that never became an
  item (e.g. a document with no caption) still aren't visible anywhere.
- Multi-user support, OAuth, or 2FA.
- Analytics.
- Notifications or reminders.
- Prometheus / metrics export — genuinely deferred, not just relabeled:
  there is no scrape target configured for this deployment, so an
  endpoint nobody reads would be built-for-show. Worth revisiting the
  moment there's an actual Prometheus/Grafana instance to point at it.
- A real background job queue (Celery, Redis, etc.) and horizontal
  scaling of the admin login rate limiter (currently in-memory,
  single-process — see SECURITY.md) — both still deferred for the same
  reason Phase 1 gave: this runs as one Coolify container, not a fleet.
  Phase 7 evaluated these against the roadmap's original Phase 7 sketch
  and confirmed neither is warranted yet; see ARCHITECTURE.md's "Phase 7"
  section for what was built instead and why.
