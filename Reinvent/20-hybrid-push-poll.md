# Hybrid Push + Poll Scheduler

## Purpose
If Tijori supports webhooks, use push events for immediate updates while retaining polling as a safety net.

## Webhook Intake
Webhook processing steps:
1. Validate source and signature if available.
2. Normalize payload into a `transcript_events` row with `origin = webhook`.
3. Enqueue `analysis_request` if the transcript is available and matches watchlist rules.
4. Update `transcript_fetch_schedule` to reduce redundant polling.

## Immediate Queueing
Webhook events should enqueue work in the same queue used by the queue-first design.

Policy:
- `available` events create or update transcripts and enqueue analysis.
- `upcoming` events update the schedule cadence to poll closer to the event date.

## Fallback Polling
Polling remains for safety and consistency.

Behaviors:
- Periodic reconciliation polls check for missed webhook events.
- Polling cadence is lower when webhook traffic is healthy.
- Reconciliation can be a low-priority schedule lane.

## Failure Handling
Failure handling follows the same queue-first rules.

Mechanisms:
- Retry with backoff for webhook processing failures.
- Dedupe by `(stock_id, quarter, year, source_url)`.
- Idempotent updates of transcripts and analysis jobs.

## Test Scenarios
- Webhook arrives for transcript availability and analysis starts quickly.
- Webhook fails and polling still detects new transcripts.
- Duplicate webhook payloads do not trigger duplicate analysis.
- Webhook outage triggers higher polling cadence.
