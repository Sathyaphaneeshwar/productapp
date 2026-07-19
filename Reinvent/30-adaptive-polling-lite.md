# Adaptive Polling Lite (In-App)

## Purpose
A lighter-weight improvement that avoids external queue infrastructure. It uses a SQLite-backed schedule and an in-app worker pool.

## SQLite-Backed Schedule Table
Proposed table: `transcript_fetch_schedule`

Fields:
- `stock_id`
- `next_check_at`
- `priority`
- `attempts`
- `last_checked_at`
- `last_status`
- `locked_until`

## Worker Pool Loop
Core behaviors:
- A fixed-size worker pool fetches due rows ordered by `next_check_at` and `priority`.
- Each worker locks rows by setting `locked_until` to avoid duplicate work.
- Simple exponential backoff on failures using `attempts`.

## Limitations
- No durable queue across processes.
- Less isolation between API serving and background work.
- Limited throughput compared to a dedicated queue and workers.

## Test Scenarios
- Due items are fetched concurrently and complete within the target window.
- Failure triggers backoff and rescheduling.
- Process restart resets in-memory state but schedule rows remain.
