# Queue-First Scheduler (Recommended)

## Architecture Overview
This design introduces durable queues and dedicated workers. It separates scheduling, fetching, analysis, and email into distinct services.

Flow:
1. `scheduler-service` builds and updates a fetch schedule for each stock.
2. `scheduler-service` enqueues `transcript_check_request` jobs to a queue.
3. `fetcher-worker` consumes jobs, calls the Tijori API, and emits `transcript_events`.
4. `analysis-worker` consumes analysis jobs triggered by new available transcripts.
5. `email-worker` consumes outbox jobs and sends notifications.

## Proposed Data Model
New tables and fields are introduced to make scheduling durable and idempotent.

Table: `transcript_fetch_schedule`
- `stock_id`
- `next_check_at`
- `priority`
- `cadence_state`
- `last_status`
- `last_checked_at`
- `last_available_at`
- `attempts`
- `locked_until`

Table: `transcript_events`
- `stock_id`
- `quarter`
- `year`
- `status`
- `source_url`
- `event_date`
- `observed_at`
- `origin`

Table: `analysis_jobs`
- `transcript_id`
- `status`
- `attempts`
- `idempotency_key`
- `created_at`
- `updated_at`

Table: `email_outbox`
- `analysis_id`
- `recipient`
- `status`
- `attempts`
- `scheduled_at`

## Scheduling Algorithm
This algorithm is built for fast detection and stable load.

Core behaviors:
- Priority lanes, with watchlist jobs above group jobs.
- Adaptive cadence that increases polling near expected call dates.
- Jitter applied to scheduled times to avoid thundering herds.
- Provider rate limiting and per-tenant concurrency caps.

Cadence example:
- Upcoming call in 24 hours: poll every 10 minutes.
- Upcoming call in 7 days: poll every 60 minutes.
- No upcoming call known: poll every 4 to 6 hours with jitter.

## Idempotency and Dedupe
Idempotency is required for every job path.

Rules:
- `transcript_events` are unique on `(stock_id, quarter, year)` and `source_url`.
- `analysis_jobs` use `idempotency_key` derived from `(transcript_id, source_url)`.
- Workers treat duplicates as no-ops and update timestamps only.
- Queue uses visibility timeouts so crashed workers do not lose jobs.

## Reliability
Reliability is built into the data model and worker logic.

Mechanisms:
- Retry with exponential backoff recorded in `attempts`.
- Dead-letter queue for permanent failures.
- Reconciliation sweep that re-checks stale schedule rows.
- Status transitions always written to durable tables before external effects.

## Latency Goal
This design targets under 10 minutes from transcript availability to analysis start for watchlist stocks.

## Compatibility With Current UI
The UI can map statuses as follows:
- `transcript_events.status = available` and analysis pending -> `transcript_ready`.
- `analysis_jobs.status = in_progress` -> `analyzing`.
- `analysis_jobs.status = done` -> `analyzed`.
- `analysis_jobs.status = error` -> `analysis_failed`.
- No events and schedule pending -> `fetching` or `no_transcript`.

## Public Interfaces to Document
- Service roles: `scheduler-service`, `fetcher-worker`, `analysis-worker`, `email-worker`.
- Tables: `transcript_fetch_schedule`, `transcript_events`, `analysis_jobs`, `email_outbox`.
- Optional endpoints: `/api/scheduler/status`, `/api/scheduler/trigger`, `/api/webhook/tijori`.
- Job payloads: `transcript_check_request`, `analysis_request`, `email_send_request`.

## Test Scenarios
- New transcript appears and analysis starts within 10 minutes.
- Stock added to watchlist triggers immediate priority check.
- API error triggers retries and does not leave status stuck.
- Worker crash mid-job results in requeue and dedupe.
- Duplicate transcript URLs do not send duplicate emails.
- Upcoming to available transition triggers analysis.
- Rate limit responses lead to adaptive throttling.

## Assumptions and Defaults
- Queue and dedicated worker processes are permitted.
- Target SLA is under 10 minutes from availability to analysis start.
- Webhooks are unknown, so polling remains the default with an optional push path.
