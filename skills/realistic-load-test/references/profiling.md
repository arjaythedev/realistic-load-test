# Profiling a real user

Goal: the numbers in PROFILE.md are the foundation of the test. If r (requests per second per active user) is off by 2×, the final "users supported" is off by 2×. Spend the effort here.

## Contents
- Where to get logs, per source
- When there are only analytics (no request logs)
- Sanity checks on a log-derived profile
- Browser profiling
- Privacy

## Where to get logs

Ask for the busiest representative window you can get: at least a few hours, including the daily peak. A launch day or a marketing spike is ideal if the test is about surviving one. If a CLI or MCP connector for the source is available and logged in, offer to pull the logs through it, and confirm the time range first.

| Source | How to get request logs | Notes |
|---|---|---|
| Nginx / Apache / Caddy | `/var/log/nginx/access.log*` (rotated files are `.gz`; the analyzer reads them) | Add `$request_time` at the end of the log format to get latency |
| Vercel | Log drains (to a file endpoint, Datadog, Axiom, …), or export from the dashboard's logs/observability view. `vercel logs` shows recent runtime logs only | Drain JSON has `proxy.*` fields, which the analyzer reads. Edge and function entries of one request share an id and are de-duplicated. `proxy.vercelCache` = HIT means the CDN answered and the origin never saw it |
| Netlify, Render, Railway, Fly | Log drains / log shipping to a file or provider | Usually JSON lines; use `--field` overrides if keys differ |
| Cloudflare | Logpush (HTTP requests dataset), or Logs Engine / Instant Logs | `CacheCacheStatus` tells you what hit the origin |
| AWS ALB / CloudFront | Access logs in S3 (space-separated), or CloudWatch Logs Insights → export CSV | ALB format isn't combined format; export the needed columns as CSV with a header |
| API Gateway / Lambda | CloudWatch access logs (JSON format if configured) | |
| Datadog / Grafana Loki / Elastic | Log explorer → export CSV or JSON | Make sure the export includes timestamp, method, path, status, client IP, user agent |
| App-level request logs (pino, winston, Rails, Django) | Their JSON lines | Often include a user id: use `--field user=…`, which gives much better sessions than IP + user agent |

Field overrides look like `--field path=req.url --field user=req.user.id --field time=ts` (dotted paths into the JSON).

If the logs mix several sites, pass `--host`. If the site has no CDN and serves its own static files, pass `--include-static`, because those requests hit the server under test.

## When there are only analytics

Google Analytics, Plausible, PostHog, Mixpanel and similar give sessions, pageviews and events, not HTTP requests. Combine them with a browser capture:

- From analytics: pageviews (or key events) per session, average session duration, the share of sessions that reach each funnel step, and peak concurrent users or sessions per hour.
- From the browser (HAR): requests per pageview for each page type.
- r ≈ (pageviews per session × requests per pageview) ÷ session duration in seconds.
- Peak concurrent users ≈ sessions in the peak hour × average session minutes ÷ 60.

Write the arithmetic into PROFILE.md so the user can check it.

## Sanity checks on a log-derived profile

Read PROFILE.md with the user before writing k6 code. Things that commonly go wrong:

- **Shared IPs merge people.** Offices, universities, mobile carriers (CGNAT) and corporate VPNs put many users behind one IP. Without a user-id field, those users become one long, busy "user": r goes up and the user count goes down. Look at the top sessions by request count; if a few "users" make thousands of requests, use a user-id or session-cookie field, or exclude those keys.
- **Bots that look human.** The bot filter is a user-agent regex. Scrapers with browser user agents get through. Very regular inter-request gaps, or one IP walking every product ID, point at them.
- **Polling and background requests.** A dashboard that polls `/api/notifications` every 10 s makes r depend on how long tabs stay open, not on human actions. Model it in the journey as a separate loop (or a `constant-arrival-rate` side scenario), because pollers keep going while the person is thinking.
- **Health checks, cron jobs, webhooks** (Stripe, GitHub) are server-to-server traffic. They're dropped or should be. Add them back as a fixed-rate side scenario only if they're significant.
- **CDN-cached responses.** If 90 % of `GET /` are CDN hits, the origin sees 10 % of them. Decide with the user whether the test is about the origin (exclude cache hits, or hit the origin directly) or the whole stack (include them, and know the CDN absorbs most of it).
- **Low-traffic windows** give unstable think times. More data beats more cleverness.
- **Timestamp resolution.** Combined logs have 1-second resolution, so burst grouping (`--burst 2`) is approximate. That's fine for think times of several seconds.

## Browser profiling

Best: the user drives and you record. `npx playwright open --save-har=loadtest/session.har <url>` opens a browser; they use the app the way a customer would for 3–5 minutes, covering the main journeys, then close the window. Their pauses are real think times, and you see what the app actually does.

When you drive (Playwright MCP, Claude in Chrome, or a Playwright script):
- Walk each important journey: landing, browse, search, detail page, sign up / log in, the main write action (post, comment, add to cart, checkout in test mode).
- After each action, collect the network requests. Separate first-party API calls from static assets and third-party calls.
- Note request bodies and headers for writes (CSRF tokens, `Next-Action` headers for Next.js server actions, GraphQL operation names), and which values come from earlier responses.
- Your own pauses aren't human. Take think times from logs or analytics, or use cautious defaults (5–15 s between actions; 20–60 s for writing content) and label them as assumptions.

Frameworks hide requests in different places:
- **Next.js App Router**: navigation fetches RSC payloads (`?_rsc=…` with an `RSC: 1` header), and server actions are `POST`s to the page URL with a `Next-Action` header. Both hit the server; include them.
- **SPAs** (React, Vue): the HTML is usually static (CDN) and the load is in the API calls.
- **GraphQL**: one URL, many operations. Tag each request with the operation name (`POST /graphql GetFeed`), because "the GraphQL endpoint" is not one workload.
- **Supabase / Firebase / other BaaS** apps call the provider directly from the browser. That is load on the user's provider project: include it only with the user's explicit agreement, and check the provider's plan limits.

## Privacy

Logs contain IP addresses and often user identifiers. Process them locally. profile.json and PROFILE.md contain only aggregates (no IPs or user ids), so they're safe to commit. Don't commit raw logs or HAR files: HARs contain cookies and tokens. Add `loadtest/*.har` and raw log files to `.gitignore`.
