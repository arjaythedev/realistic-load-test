# realistic-load-test

A Claude skill that finds out **how many real users your website or API can handle**.

Most load tests hammer one URL as fast as possible. That tells you a requests-per-second number, not how many
people your app can serve. This skill makes every k6 virtual user behave like one of your actual users: the same
pages, the same API calls, the same read/write mix, the same pauses to read and think. It then finds the
highest number of those users your system serves while staying inside your latency and error criteria.

## What it does

1. **Scope.** Confirms you own the target, picks staging vs production, sets criteria (default p95 < 500 ms,
   p99 < 1 s, errors < 1 %, or your SLOs), and estimates the cost.
2. **Profile a real user.** Reads your production logs (Nginx, Vercel, Cloudflare, ALB, JSON, CSV) or records a
   browser session, and works out requests per second per active user, endpoint mix, think time and journeys.
3. **Write the virtual user.** Generates a k6 `journey.js` with weighted journeys, real IDs, test accounts and
   safe handling of writes and third-party side effects.
4. **Calibrate.** A low-load run proves the virtual user matches the profile (rate within ±15 %, mix within a few
   points).
5. **Load generators.** Sizes and creates VMs (DigitalOcean, Hetzner, AWS, GCP, or Grafana Cloud k6), tunes
   them, and monitors them so the client is never the bottleneck.
6. **Run.** A fast staircase estimate, then 5-minute confirmation holds (median of 3) around it.
7. **Report.** The number, the per-endpoint latencies, what breaks first and the evidence for it, caveats,
   and teardown.

## Install

Claude Code, as a plugin:

```
/plugin marketplace add arjaythedev/realistic-load-test
/plugin install realistic-load-test@realistic-load-test
```

Or clone the repo and copy `skills/realistic-load-test/` into `~/.claude/skills/` (personal) or `.claude/skills/` (one project).

Then ask Claude something like *"how many users can my app at staging.example.com handle?"*.

## Requirements

- [k6](https://grafana.com/docs/k6/latest/set-up/install-k6/) locally (for smoke tests and calibration)
- Python 3.9+ (profile scripts use only the standard library)
- Optional: Node (for `npx playwright` browser recording), a cloud CLI (`doctl`, `hcloud`, `aws`, `gcloud`)

## Only test what you own

Load testing a system you don't control is indistinguishable from a denial-of-service attack. The skill asks
you to confirm ownership and refuses obvious third-party targets. It also keeps load away from third-party APIs
your app calls, and warns about firewall, rate-limit and billing effects on your hosting platform.

## Layout

```
skills/realistic-load-test/
  SKILL.md                 the workflow
  references/              profiling, journey writing, criteria, load generators, platform notes, report template
  scripts/                 analyze_logs.py, har_to_profile.py, compare_profile.py, setup-loadgen.sh,
                           loadgen-monitor.sh, run-holds.sh
  templates/               lib.js, hold.js, staircase.js, journey.example.js (k6)
```

`evals/` holds the test cases used to develop the skill (runnable with `claude plugin eval`); they are not part of
the installed skill.

The method (closed-model users with think time, staircase with per-step criteria, median-of-3 holds,
generator monitoring) comes from the $12 server benchmark series on [benchmarks.arjaythedev.com](https://benchmarks.arjaythedev.com).
