# Current Scheduler Baseline

## Scope
This baseline covers transcript fetching, analysis triggering, and email sending for watchlist and group workflows.

## Current Control Flow
The current scheduler is an in-process thread started inside the Flask app.

Key flow:
1. `SchedulerService` starts a background loop that checks every second if a poll is due.
2. A poll cycle runs every 300 seconds by default.
3. Each poll gathers watchlist stocks and active group stocks, then processes them sequentially.
4. For each stock, the scheduler calls the Tijori API to fetch available transcripts and upcoming calls.
5. When a transcript becomes available, it may trigger analysis for the latest fiscal quarter if the stock is still in the watchlist and not in an active group.
6. `GroupResearchService` runs after the watchlist poll to detect group-level readiness and trigger group research runs.

Relevant code locations:
- `backend/services/scheduler_service.py`
- `backend/services/transcript_service.py`
- `backend/services/analysis_worker.py`
- `backend/services/group_research_service.py`
- `backend/app.py`

## State Model
Primary tables used:
- `transcripts` stores per-stock quarter metadata, status, and analysis state.
- `transcript_checks` stores per-stock poll status (`idle` or `checking`).
- `transcript_analyses` stores analysis output and timestamps.

Status mapping in `/api/watchlist` (from `backend/app.py`):
- If `analysis_status = in_progress`, UI status is `analyzing`.
- If `transcript_checks.status = checking`, UI status is `fetching`.
- If `transcripts.status = upcoming`, UI status is `upcoming`.
- If `transcripts.status = available` and analysis exists, UI status is `analyzed`.
- If `transcripts.status = available` and analysis failed, UI status is `analysis_failed`.
- If `transcripts.status = available` and no analysis yet, UI status is `transcript_ready`.
- If no transcript and not checking, UI status is `no_transcript`.

## Latency Sources
Primary sources of delay:
- Poll interval is fixed at 5 minutes.
- Per-stock API calls are sequential within a poll cycle.
- Single poll lock prevents overlapping cycles even if a cycle is slow.
- Analysis runs in a separate thread, but can be delayed by long transcript downloads and LLM calls.

## Reliability Gaps
Current reliability weaknesses:
- No durable queue for transcript checks or analysis jobs.
- No explicit retry or backoff tracking for failing jobs.
- No job idempotency keys to safely replay across restarts.
- Scheduler state is in-memory only, so a process restart resets timing and loses in-flight state.

## Observed Failure Modes
- Stocks remain stuck in `no_transcript`, `upcoming`, or `transcript_ready` due to delayed polling or missed transitions.
- Analysis can be delayed by an hour or more when polls are slow or scheduler fails to run.
- Different users see inconsistent states because the scheduler runs globally and uses only coarse status tracking.
- Transcripts can be detected but analysis is skipped due to timing or in-flight checks that do not reconcile quickly.
