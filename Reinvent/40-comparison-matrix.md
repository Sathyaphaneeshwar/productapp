# Scheduler Comparison Matrix

## Side-by-Side Comparison

| Dimension | Current Implementation | Queue-First (Recommended) | Hybrid Push + Poll | Adaptive Polling Lite |
| --- | --- | --- | --- | --- |
| Latency | 5+ minutes typical, longer during slow polls | <10 minutes target with priority queues | <10 minutes, often faster with webhooks | Improved over current, but limited by in-app load |
| Reliability | No durable queue, no retry tracking | Durable jobs, retries, DLQ, reconciliation | Same as queue-first plus webhook safety | Better than current, weaker than queue-first |
| Efficiency | Sequential per-stock calls, coarse cadence | Adaptive cadence, rate limits, batchable | Lowest API load when webhooks are reliable | Moderate savings, still polling-heavy |
| Operational Complexity | Low | Medium, requires queue and workers | Medium-high, adds webhook intake | Low-medium, no external infra |
| User-Visible Impact | Status stalls and delayed emails | Faster updates, fewer stuck statuses | Fastest and most accurate | Moderate improvement |

## Recommendation Summary
The queue-first design is the best fit for reliability and near-real-time updates. The hybrid model is ideal if webhooks are available. Adaptive polling lite is a viable stepping stone when new infrastructure cannot be deployed yet.
