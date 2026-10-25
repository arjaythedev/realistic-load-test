# Platform notes

Read the section for the target's platform during phase 0. Providers' policies and features change, so when something here matters (an acceptable-use rule, a firewall feature, a price), check the provider's current docs and tell the user what you found and where.

## Contents
- Serverless and edge platforms in general
- Vercel
- Netlify, Cloudflare Pages/Workers
- Behind Cloudflare (proxy/WAF)
- AWS
- Fly.io, Render, Railway, Heroku and other PaaS
- A VPS or your own servers
- Backend-as-a-service (Supabase, Firebase, PlanetScale, Neon …)

## Serverless and edge platforms in general

The compute scales out, so "how many users before it falls over" usually becomes:
1. **What runs out first downstream**: database connections (every function instance opens its own pool unless a pooler is in front), DB CPU, a third-party API's rate limit, a Redis plan's connection cap.
2. **Platform limits**: function concurrency caps per plan, cold starts during a fast ramp, request body or duration limits.
3. **Cost**: requests, function GB-seconds, bandwidth, edge middleware invocations, image optimizations. Estimate it before the test: total requests ≈ Σ(users per step × r × step seconds) over the staircase, plus the holds. Turn on spend limits or alerts if the platform has them.

So for serverless targets, the report should also say where the limit is (DB connections, a rate limit) and what the test cost.

## Vercel

- **Firewall / DDoS mitigation**: Vercel's platform mitigation and Attack Challenge Mode can challenge or block a sudden burst from a few IPs. The user should look at the Firewall settings in the project dashboard and add a custom rule that bypasses or allows the load generators' IPs for the test window, then remove it afterwards. A wall of 403s or challenge pages at low load means the firewall, not capacity.
- **Deployment Protection** (Vercel Authentication or a password on preview deployments) blocks k6. Use the protection bypass for automation (a secret sent as the `x-vercel-protection-bypass` header) on preview targets.
- **Where it runs**: functions run in the project's function region (default `iad1`). Put generators near it. Static and ISR responses are served from the CDN and barely touch the functions. A test dominated by cache hits measures Vercel's CDN.
- **Cost**: usage-based on Pro (function invocations and duration, edge requests, data transfer). Have the user check Spend Management before a big run. Hobby plan limits may cut a test short.
- **Typical first bottleneck**: the database (connection exhaustion without a pooler), then cold starts on fast ramps.

## Netlify, Cloudflare Pages/Workers

- Similar to Vercel: the CDN absorbs static traffic, and functions or Workers scale out. The bill and the downstream database are the limits.
- Cloudflare Workers: the bot and DDoS protections described below apply. Workers have per-request CPU-time limits that show up as errors under heavy computation.

## Behind Cloudflare (proxy/WAF)

- Bot Fight Mode, Super Bot Fight Mode, managed challenges and rate limiting rules will challenge k6 (its user agent and its traffic pattern). Create a WAF custom rule with the action Skip for the generator IPs, covering the relevant security features, for the test window.
- Decide whether to test through Cloudflare (realistic: includes caching and TLS at the edge) or against the origin directly (measures the origin; the origin must accept the generators' IPs).
- Read Cloudflare's current guidance on load testing through its network before a large test.

## AWS

- AWS publishes a policy on network stress testing. Read the current version before a large or high-bandwidth test. Moderate realistic-user tests against your own resources are routine.
- Behind ALB/CloudFront: AWS WAF rate-based rules will block generators. Add an IP set exception for them.
- Lambda: account concurrency limits (often 1,000 by default per region) and RDS connection limits are the classic first failures. RDS Proxy helps.
- ECS/EKS/EC2 autoscaling: autoscaling reacts in minutes. A staircase with 45-second steps outruns it, so the holds (5 minutes or longer) are the real answer. Note the instance count at each level.

## Fly.io, Render, Railway, Heroku and other PaaS

- Fixed-size instances with optional autoscaling. Note the instance size and count, because the result is "N users on 2 × shared-cpu-1x".
- Platform proxies have connection or concurrency settings (Fly's `concurrency` soft/hard limits, Heroku's router 30 s timeout) that can be the first limit.
- Plan-level request or bandwidth limits apply; check the plan.

## A VPS or your own servers

This is the classic case: the limit is CPU, memory, a connection setting or the database on the box.
- Watch the server during the test: `top`/`htop`, `vmstat 5` (CPU steal on shared VPS plans!), DB connections and slow queries, and the app's own metrics.
- Default limits often end the test early: Nginx `worker_connections` (default 768) and `worker_rlimit_nofile`, systemd `LimitNOFILE` for the app, `ufw`/conntrack table size (`nf_conntrack: table full` in dmesg), the DB pool size, and the Node/Python worker count. Raising these is fair, because production should have them right. Note any change in the report.
- Keep-alive mismatches (the app's keep-alive timeout shorter than the proxy's) cause sporadic 502s under load.

## Backend-as-a-service (Supabase, Firebase, PlanetScale, Neon …)

When the browser talks to the BaaS directly, the load test hits the user's BaaS project. That's legitimate (it's their project), but:
- Plan quotas and rate limits (requests per second, concurrent connections, realtime connections) are usually the limit, and some plans pause or bill overages.
- Auth endpoints often have strict per-IP rate limits. Use pre-made test-user tokens rather than logging in thousands of times.
- Check the provider's terms on load testing, and tell the user what the test will do to their quota.
