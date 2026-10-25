# Pass/fail criteria

## Defaults

| Metric | Default | Why |
|---|---|---|
| p95 latency | < 500 ms | 19 of 20 requests feel instant-ish. A page making several API calls still renders in about a second |
| p99 latency | < 1000 ms | The slow tail: users who make many requests per session hit it regularly. A p99 that runs away while p95 looks fine is the first sign of queueing |
| HTTP error rate | < 1 % | Status ≥ 400 or connection errors, excluding statuses declared as expected. Above 1 %, a typical session (20+ requests) more often than not sees an error |

Latency is measured end to end by k6: DNS, TCP and TLS (on new connections), sending, waiting and receiving, through every proxy, CDN and load balancer on the way. It's what a user near the load generator would see, minus browser rendering.

These defaults are generous for a typical web app. They describe "still works", not "fast". Pick tighter values when the user has them.

## Using the user's SLAs/SLOs

- Translate the SLO into the same percentile. "99 % of requests under 300 ms" → `P99_MS=300`. If they only have a p95 target, keep p99 at about 2× p95 as a tail guard.
- An availability SLO ("99.9 % successful") → `MAX_ERROR_RATE=0.001`. Make sure enough requests run for that to be measurable: you need ≥ 10,000 requests per step or hold to tell 0.1 % apart from noise.
- Per-endpoint SLOs (checkout, search, login) → per-endpoint thresholds (see k6-journey.md). Endpoints without an SLO fall back to the global criteria.
- Rate limits count. If the app returns 429s under load, that is a failure from the user's point of view, even if the limiter is doing its job. Report it separately ("capacity is fine, but the limiter at 100 req/min per IP blocks …").

## Picking the question

- **"What's the max?"**: staircase, then confirmation holds around the estimate. The answer is the highest level whose median of 3 holds passes.
- **"Will it survive N?"**: holds at N and at a safety margin (1.5–2× N). Ask where N comes from: expected launch traffic, a marketing campaign, last year's peak × growth.
- **"How does it fail?"**: after finding the max, one short run well past it (1.5–2× the max), watching what breaks first and whether it recovers when load drops (graceful degradation vs a crash that needs a restart). Only on staging, or with the user watching production.
- **Soak** (memory leaks, connection leaks, disk filling): a long hold (1–8 h) at ~70 % of the max. Only if they ask, since it costs generator hours.

## Statistics that keep the answer honest

- **Steps and holds need enough samples.** p99 from 300 requests is three requests. Aim for ≥ 2,000 requests per staircase step (users × r × step seconds) and ≥ 10,000 per hold. Lengthen steps when r is low.
- **Median of 3.** A single run can be off by a step either way (noisy neighbours, autoscaler timing, GC pauses). Run each level of the final answer 3 times with a cooldown, and report the median and how many of the 3 passed.
- **Warm-up runs are thrown away.** Cold caches, JITs, connection pools and autoscalers make the first minutes slower.
- **The client must not be the bottleneck.** Generator CPU < 70 % throughout, no `dial: i/o timeout` or `too many open files`. If a generator was saturated, the run doesn't count.
