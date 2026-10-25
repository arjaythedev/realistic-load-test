---
name: realistic-load-test
description: End-to-end load testing of the user's own website or API with realistic k6 virtual users. Profiles what one real user does (requests per second, endpoint mix, read/write split, think time) from production logs or by browsing the site, writes k6 scripts where one VU behaves like one person, helps size and create load-generator VMs, runs a staircase and then confirmation holds against pass/fail criteria (default p95 < 500 ms, p99 < 1 s, errors < 1 %), and reports how many concurrent users the system handles and what breaks first. Use it whenever someone wants to load test, stress test, or capacity test their app, asks "how many users can my site handle", wants to know if their Vercel/Netlify/Fly/AWS/VPS deployment will survive a launch or traffic spike, mentions k6, virtual users, or simulating real traffic, even when they don't say "load test".
---

# Realistic load test

The goal is one number the user can trust: **how many concurrently active real users their system serves while staying inside their criteria**, plus what breaks first beyond that. "Real users" is the important part. A tool that hammers one URL as fast as it can measures something, but not that. Here one k6 virtual user (VU) is one person with the app open: they load a page, the page fires its API calls, they read for a few seconds, maybe click or write something, and so on. Because each VU sleeps between actions, N users produce roughly N × r requests per second, where r is the per-user rate measured in phase 1. That is a closed model, and it is what makes "2,500 users" mean 2,500 people.

The work has six phases. Each one produces a file in a `loadtest/` folder in the user's project, so the work can resume across sessions and the user can review it:

| Phase | Output |
|---|---|
| 0. Scope and permission | `loadtest/PLAN.md` |
| 1. Profile a real user | `loadtest/profile.json`, `loadtest/PROFILE.md` |
| 2. Write the virtual user | `loadtest/journey.js` (+ `lib.js`, `staircase.js`, `hold.js`) |
| 3. Calibrate (low load) | the calibration table in `PROFILE.md` |
| 4. Load generators | `loadtest/INFRA.md` |
| 5. Run: staircase, then holds | `loadtest/results/…` |
| 6. Report | `loadtest/REPORT.md` |

Check whether `loadtest/` already exists before starting, and pick up where it left off.

Tell the user at each phase boundary what you found and what comes next. Phases 0, 4 and 5 involve their money, their production system, and possibly other people's traffic, so get an explicit go-ahead before you create VMs or send real load.

## Phase 0: Scope and permission

Ask, in one message, for what you can't infer from the repo:

1. **Target URL(s)**, and whether it's production, staging, or a preview deployment. Recommend staging or a production-like preview when one exists. If it must be production, plan a low-traffic window.
2. **Do they own or operate it?** Only test systems the user controls or has written permission to test. If the domain looks like someone else's (a big public site, a competitor, a domain the repo doesn't deploy), stop and say why: load testing a system you don't own is indistinguishable from a denial-of-service attack. Third-party services the app calls (Stripe, OpenAI, SendGrid, auth providers) are also someone else's: the journey must not push load at them (see phase 2).
3. **Criteria.** Offer the defaults and explain them briefly: **p95 < 500 ms, p99 < 1000 ms, HTTP error rate < 1 %**, measured end to end by k6 over the whole step or hold. If they have SLAs or SLOs, use those instead. Per-endpoint criteria are fine too (e.g. checkout p95 < 300 ms), and `references/criteria.md` covers how to pick them.
4. **What they want to learn:** the max concurrent users ("how far does it go"), or whether it survives a specific number ("can it handle 5,000 users at launch"). The second one is a single hold at that number plus margin, which is cheaper.
5. **Hosting.** Read it from the repo when you can (vercel.json, fly.toml, Dockerfile, serverless config, Terraform). It changes what "the limit" means. Serverless and edge platforms scale out, so the limit is usually the database, connection limits, a rate limiter, a downstream API, or **the bill**. Read `references/platforms.md` for the target's platform before going further, because some platforms block load-test traffic or charge per request.

Write `loadtest/PLAN.md`: target, environment, ownership confirmation (as the user stated it), criteria, the question, the platform notes, and a rough cost estimate (VMs plus any per-request platform cost).

## Phase 1: Profile a real user

You need:

- **r**: requests per second that one active user generates against the system under test (not static assets served by a CDN, unless the user wants those included)
- **the endpoint mix**: which routes, in what proportion, read vs write
- **think time**: how long people pause between actions
- **journeys**: the common paths through the app (land → browse → open item → add to cart …) and roughly what share of users take each

There are two sources. Use logs when they exist: they show what thousands of real users actually did. Use the browser when there are no logs, or the app is pre-launch, or to see the exact request sequence behind one action. Often you use both: logs for rates and mix, the browser for the request sequence and payloads.

### From production logs

Ask what they have: Vercel (`vercel logs`, log drains), Cloudflare, Nginx or Apache access logs, AWS ALB or CloudWatch, Datadog, Grafana Loki, PostHog, or a DB of request logs. If an MCP connector or CLI for it is available, offer to pull logs through it. Otherwise ask for an export. A busy representative window is best: a peak day, or at least a few hours including the daily peak.

Then run the analyzer:

```bash
python3 <skill>/scripts/analyze_logs.py access.log --out loadtest/profile.json --md loadtest/PROFILE.md
```

It reads combined/common log format, JSON lines (including Vercel's `proxy.*` fields), and CSV with a header. It drops static assets and bots, turns paths into route templates (`/posts/123` → `/posts/:id`), splits traffic into sessions (user-id field if one exists, else IP + user agent, 30-minute idle gap), and reports r, the mix, the read/write split, think time between actions, requests per action, and peak concurrency. Run with `--help` for field overrides when a log format isn't detected. Read `references/profiling.md` for how to sanity-check the numbers, because log-derived profiles have known traps (NAT'd IPs merging users, health checks, cached responses that never reach the origin).

### From the browser

Use whatever browser automation is available (Playwright MCP, Claude in Chrome, or the Playwright CLI). Walk through each important journey the way a person would, and capture the network requests. Better still, ask the user to click through the app themselves for a few minutes while a HAR is recorded, because their pauses are real think times:

```bash
npx playwright open --save-har=loadtest/session.har https://their-site.example
python3 <skill>/scripts/har_to_profile.py loadtest/session.har --out loadtest/browser-profile.json --md loadtest/BROWSER.md
```

The HAR summary lists every same-site request in order, grouped into user actions, with methods, route templates, status codes, request bodies for writes, which auth mechanism is used (cookie or bearer, values redacted), and the third-party hosts the page calls (which the journey must leave out). If you browse with an MCP tool instead, collect the same information from its network request list.

A browser walkthrough gives you the request sequence but not real usage frequencies. When there are no logs, ask the user for the rough proportions ("of 100 visitors, how many sign in? how many post?"). If they don't know, use cautious assumptions and write them down as assumptions in PROFILE.md.

### Write PROFILE.md

Whatever the source, PROFILE.md ends with a short, plain summary the user confirms before you write any k6 code, for example:

> One active user makes about **0.12 req/s** (one action every ~15 s, ~1.8 API requests per action). 91 % reads / 9 % writes. Journeys: browse (70 %), read + like (25 %), post (5 %). Peak observed: ~340 concurrent users at 18:00 UTC, so testing to 5× peak = 1,700 users covers a launch spike.

## Phase 2: Write the virtual user

Copy `templates/lib.js`, `templates/staircase.js` and `templates/hold.js` into `loadtest/` unchanged, and write `loadtest/journey.js` starting from `templates/journey.example.js`. Read `references/k6-journey.md` before writing it. The essentials:

- **One VU = one person.** Journeys are functions with `sleep()` think times drawn from the profile's distribution (uniform ranges around the observed quartiles are fine). Pick a journey per iteration by weight. Add a random 0–5 s sleep on the first iteration so VUs don't move in lockstep.
- **Reproduce what the page does, not just the HTML request.** If opening a product page fires `GET /api/product/:id` and `GET /api/reviews?product=:id` together, the action issues both (use `http.batch` for requests the browser sends in parallel).
- **Real IDs and data.** Pull IDs from previous responses (the feed returns post IDs; open one of those), or from a seeded list in a JSON file loaded through `SharedArray`. Hitting the same ID every time measures a cache.
- **Authentication.** Never use the user's own account. Use dedicated test accounts: pre-created users with tokens or session cookies in a JSON file (one per VU when the app rate-limits per user), or a login step in `setup()` / on the VU's first iteration if login is part of real behaviour. Read `references/k6-journey.md` for the options.
- **Writes are real writes.** On production they create real rows, send real emails, and charge real cards. Agree with the user what writes do: run against staging, mark test data (a `loadtest-` prefix or a flag) so it can be cleaned up, and stub or skip anything that triggers a third-party call that costs money or reaches real people (payments, emails, SMS, LLM calls). Write the decision into PROFILE.md.
- **Tag every request** with `tags: {name: 'GET /posts/:id'}` and export the list of names as `ENDPOINTS`. lib.js uses it for per-endpoint stats. Don't leave raw URLs as metric tags: one series per URL eats k6's memory.
- Use `responseType: 'none'` for requests whose body you don't read, and `check()` the status on every request.

Smoke-test locally first: `mkdir -p results && k6 run -e BASE_URL=… -e VUS=2 -e DURATION=30s -e RAMP_UP=5s hold.js` (k6 doesn't create `results/` itself). Every check should pass, and every endpoint in `ENDPOINTS` should appear in the summary.

## Phase 3: Calibrate

Before scaling up, prove that the virtual user matches the real one. Run 20–50 VUs for 3–5 minutes (low enough that the system is relaxed, high enough for stable numbers), from a laptop or one small VM:

```bash
k6 run -e BASE_URL=… -e VUS=30 -e DURATION=4m -e LABEL=calibrate -e RESULTS_DIR=results hold.js
python3 <skill>/scripts/compare_profile.py loadtest/profile.json loadtest/results/calibrate-*.json
```

`compare_profile.py` compares the measured per-VU request rate and endpoint shares with the profile. Aim for r within ±15 % and each major endpoint's share within a few percentage points. If they're off, adjust think times or journey weights and run it again. Add the final table to PROFILE.md. This step is what makes the final user count believable, so don't skip it.

## Phase 4: Load generators

Read `references/load-generators.md`. In short:

- **Size from the profile.** Peak RPS = max users × r. A k6 VU that mostly sleeps costs roughly 1–3 MB of RAM (more with large responses or TLS). A 4 vCPU / 8 GB VM comfortably drives about 8–12k sleepy VUs over HTTPS. Keep each generator under ~70 % CPU so the client is never the bottleneck. Use several generators when you need more VUs, more than ~25k connections to one target IP, or more source IPs.
- **Placement.** Use the same cloud region as the target (or as the region the target's users are in, for a CDN-fronted site), unless the question is "what do users in Europe see".
- **Provider.** Ask which cloud they already have an account with. Give them exact commands for it (the reference has DigitalOcean, Hetzner, AWS, GCP), or do it for them when the CLI is installed and logged in. Always tell them the hourly price and remind them to delete the VMs afterwards. A full afternoon of testing usually costs a few dollars. If they'd rather not manage VMs, Grafana Cloud k6 runs the same scripts (`k6 cloud run`), and the reference explains the trade-offs.
- **Setup** with `scripts/setup-loadgen.sh` (installs k6, raises file and port limits) and `scripts/loadgen-monitor.sh` (records the generator's own CPU and memory, as evidence that the client wasn't the limit).
- **Firewalls and DDoS protection.** Many hosts challenge or block a burst from a few IPs (Vercel Firewall / Attack Challenge Mode, Cloudflare, AWS WAF rate rules). Have the user allow-list the generator IPs for the test window, or the test will measure the firewall. `references/platforms.md` has the specifics.

Record the VMs, IPs, sizes, regions, costs, and the teardown command in `loadtest/INFRA.md`.

## Phase 5: Run

Get an explicit "go" first, and tell the user what is about to happen (target, peak users, duration, expected peak RPS).

1. **Warm-up** (discard): ~10 % of the expected capacity for 2 minutes, so caches, connection pools, JITs and autoscalers are warm.
2. **Staircase** (fast estimate, 5–15 minutes): `staircase.js` adds users in steps and judges each step against the criteria using only the requests from that step. It aborts at the first failing step. Choose START at roughly 20 % of the expected max and STEP at about 10 %. Steps should last ≥ 40 s and contain at least a few thousand requests each, or p99 is noise. Pass the criteria as env vars (`P95_MS`, `P99_MS`, `MAX_ERROR_RATE`).
3. **Confirmation holds**: the staircase is optimistic for some systems and pessimistic for others (bursty step changes hurt systems that grow pools or scale out slowly). Confirm with full holds (`hold.js`: 60 s ramp, 5 min hold, 30 s ramp-down) at the staircase estimate, then one step above and one below. Run each level 3 times with a 60 s cooldown and use the **median**. `scripts/run-holds.sh 1500 2000 2500` does exactly that on one generator. The answer is the highest level whose median passes.
4. **While it runs**, watch the generator monitor (CPU < 70 %) and whatever server-side metrics the user has (CPU, memory, DB connections, autoscaler instance count, error logs), because those explain *why* a level failed. With several generators, start them together and add up their users (`references/load-generators.md` shows how).

Stop immediately if errors spike at levels far below the expected capacity (that usually means a firewall, a rate limiter, or broken auth rather than capacity), or if the user asks you to.

If the question is "can it handle N users", skip the staircase: run holds at N and at 1.5 × N.

## Phase 6: Report

Write `loadtest/REPORT.md` using `references/report-template.md`. Lead with the answer ("**3,500 concurrent users** pass p95 < 500 ms / p99 < 1 s / errors < 1 %; at 4,000 the p99 climbs to 1.4 s"), then the user profile it rests on, the per-level table (users, RPS, p50, p95, p99, error %, generator CPU, pass/fail), the per-endpoint latency at the highest passing level, what broke first and the evidence for it, caveats (staging vs production differences, warm caches, data size, what wasn't tested), and a conversion to business terms: concurrent users ≈ sessions per hour × average session minutes ÷ 60, so 3,500 concurrent at 6-minute sessions ≈ 35,000 sessions per hour.

Finish by reminding the user to delete the load generators (give the exact command from INFRA.md), remove any firewall allow-list rules, and clean up test data.
