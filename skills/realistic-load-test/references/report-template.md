# REPORT.md template

Fill this in from the results files. Raw numbers in tables beat adjectives. Every claim about a bottleneck needs a piece of evidence (a metric, a log line, an error message).

```markdown
# Load test: <app> (<environment>), <date>

**<N> concurrent users** pass the criteria (p95 < <X> ms, p99 < <Y> ms, errors < <Z> %), about <RPS> requests/second.
At <N + step> users <what failed: e.g. p99 1.4 s on POST /api/checkout>. First bottleneck: <component>, because <evidence>.

In traffic terms: <N> concurrent users ≈ <sessions/hour> sessions per hour at the observed <M>-minute average session,
about <multiple>× the busiest hour in the profiled logs (<peak> concurrent on <date>).

## The simulated user
- Source: <logs from X, window> / <browser walkthrough> / <analytics + browser>
- <r> req/s per user; <reads>% reads / <writes>% writes; journeys: <name share, …>
- Calibration: k6 per-user rate <r_k6> vs profile <r> (<diff>%); endpoint shares within <max diff> points
- Left out: <endpoints, third-party calls, side effects, and why>

## Setup
- Target: <URL>, <platform, instance sizes/count, DB size/plan, region>
- Load generators: <count × size, region>, max CPU <x> %, max k6 memory <y> MB (never the bottleneck)
- Criteria source: <defaults / their SLO>
- Data: <dataset size vs production; test accounts; writes and cleanup>

## Results

Staircase (<step> s steps): estimate <E> users, first failing step <F>.

Confirmation holds (60 s ramp + 5 min hold), median of 3:

| users | req/s | p50 ms | p95 ms | p99 ms | errors % | passes | generator CPU % | server notes |
|---:|---:|---:|---:|---:|---:|:-:|---:|---|
| … | | | | | | 3/3 | | |

Per endpoint at <N> users:

| endpoint | share | p50 | p95 | p99 | errors % |
|---|---:|---:|---:|---:|---:|

## What breaks first, and what to do about it
<the evidence; the first two or three improvements, ordered by expected impact; how to verify each with a rerun>

## Caveats
<staging vs production, data size, cache warmth, single region, what wasn't tested, run-to-run spread>

## Cost
<generator hours and cost; platform usage the test generated, if usage-billed>

## Reproduce
<the exact commands: profile, calibration, staircase, holds>
```
