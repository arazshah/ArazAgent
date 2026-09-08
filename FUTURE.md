# Future (deferred out of Phase 1-8 + gatekeeper scope)

Phase 1 was capture only; Phase 2 added triage/classification; Phase 3
added basic task open/close; Phase 4 added semantic search; Phase 5 added
an on-demand and optional daily review; Phase 6 added an item browser to
the admin UI; Phase 7 hardened retries for AvalAI calls; Phase 8 added the
Shamsi calendar; a post-Phase-8 feature added once-per-task deadline
reminders; another added constitution-driven scoring/decisions; another
added the immediate decision announcement (see `ARCHITECTURE.md`).
Anything below was identified as plausibly useful but explicitly out of
scope for now — recorded here instead of built, so each phase stays small
and reliable. This list is also the standing menu of "what's next"
options offered after each phase:

- Auto-changing `items.status` based on `decision` (e.g. "archive"/
  "decline" auto-closing the item) — decision is informational only today;
  the user still has to act on it.
- A real `goals` table with an admin CRUD page, once goals stabilize —
  `constitution.goals` is one delimited settings string on purpose, to
  avoid building a goals UI before the goals themselves are known.
- Actually *using* `decisions_log` (built — see ARCHITECTURE.md's
  "Calibration loop" section) to tune anything: today it only records
  overrides (completing something marked "decline"/"archive") and shows
  them on the admin item browser. Nothing reads the log to adjust
  `constitution.goals`/`constitution.hard_rules` or the scoring prompt —
  that calibration pass is still a human looking at the list, not
  automated.
- Anything beyond tagging+listing commitments (built — see
  ARCHITECTURE.md's "Commitments to other people" section): no reminder
  logic distinguishes a commitment from a personal task yet (same
  once-only lead-time reminder for both), no escalation for an overdue
  commitment, and no per-person view ("everything you owe علی").
- A lightweight personal CRM (last-contact tracking, "you haven't talked
  to X in a while" nudges).
- A content pipeline that turns finished technical work (e.g. commits,
  closed items) into draft posts automatically.
- Anything beyond flagging a near-duplicate (built — see
  ARCHITECTURE.md's "Duplicate detection via embeddings" section): no
  merge action, no "these look the same, keep which one?" prompt from the
  bot or admin UI — the flag is informational (an announcement line plus
  `items.meta.possible_duplicate_of`) and the user still resolves it
  manually (e.g. closing one of the two).
- Repeated/escalating reminders for an overdue task — the reminder feature
  fires exactly once, ever, per task.
- A weekly (as opposed to daily) review cadence, or a configurable day of
  week — review.send_time is a daily HH:MM only for now.
- Task priority, or reopening a closed item from the bot (the admin item
  browser can now do both edit and reopen — the bot still can't).
- Editing or deleting an item from the bot (`/edit`, `/delete`) — only
  `/done` exists; full edit/delete is admin-UI-only.
- Recurring tasks ("every Monday, remind me to...").
- Bulk actions, deleting an item, or restoring a deleted one in the admin
  item browser.
- Charts beyond the one simple 7-day capture bar, a dedicated dashboard
  page, or a raw log viewer.
- An inbox (as opposed to items) browser — captures that never became an
  item (e.g. a document with no caption) still aren't visible anywhere.
- OCR / text extraction from documents and images — a captured file is
  stored but never read or triaged today.
- Productivity analytics (tasks closed per week, time-to-close, breakdown
  by the already-stored but unused `project`/`goal_key` fields).
- Exporting items as CSV/JSON for backup or migration.
- Two-way calendar sync (e.g. pushing `type='event'` items to Google
  Calendar).
- Multi-user support, OAuth, or 2FA.
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
