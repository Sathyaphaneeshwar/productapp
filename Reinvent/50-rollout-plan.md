# Rollout Plan

## Phase 0: Instrument Current System
Goals:
- Add metrics and logs to measure poll duration, API latency, and analysis start lag.
- Capture rates of stuck statuses and missing transitions.

## Phase 1: Shadow Scheduler
Goals:
- Deploy queue-first scheduler in shadow mode.
- Write to new schedule tables but do not trigger analysis or emails.
- Compare detected transcript events against current system.

## Phase 2: Switch Analysis Trigger
Goals:
- Route analysis jobs through the queue-first pipeline.
- Keep legacy poll loop for detection only.
- Validate email timeliness and dedupe behavior.

## Phase 3: Deprecate Legacy Poll Loop
Goals:
- Disable the legacy poll loop for watchlist and group checks.
- Rely fully on the queue-first scheduler and workers.

## Rollback
Rollback steps:
- Disable the new scheduler services.
- Re-enable legacy polling and analysis triggers.
- Preserve new tables for later reactivation.
