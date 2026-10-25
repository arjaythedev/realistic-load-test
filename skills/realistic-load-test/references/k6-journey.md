# Writing journey.js

`journey.js` is the only file you write per app. `hold.js` and `staircase.js` import it, so the smoke test, the calibration, the staircase and the holds all use the same user. Start from `templates/journey.example.js`.

## Contents
- The contract
- Turning a profile into journeys
- Think time
- Authentication
- Test data and writes
- Errors that are expected
- Special traffic: polling, websockets, streaming
- Per-endpoint criteria
- k6 pitfalls

## The contract

```js
export const ENDPOINTS = ['GET /', 'GET /api/feed', ...];  // every `name` tag the journey uses
export function setup() { ... }                          // optional, runs once; its return value is passed to default()
export default function (data) { ... }                    // ONE iteration of one user
```

Use `think(min, max)` from lib.js for every pause, not `sleep()`. It also keeps the staircase's per-step accounting right.

## Turning a profile into journeys

1. Group the profile's endpoints into the user actions that produce them (the HAR sequence shows which requests go together). Example: "open product" = `GET /api/products/:id` + `GET /api/products/:id/reviews`.
2. Chain actions into 2–5 journeys that cover most of the traffic (browse, engage, create, buy …). Give each a weight.
3. Solve for the weights and loop counts that reproduce the endpoint shares, roughly: if 30 % of requests are product views and 17 % are landings, users open ~1.7 products per visit. Don't over-engineer this. Calibration (phase 3) tells you if you're close, and you adjust from there.
4. An endpoint with < 0.5 % of traffic can be left out unless it's expensive (search, export, checkout, report generation). An expensive rare endpoint can matter more than a cheap common one. Ask the user which operations they suspect are heavy, and check p95 in the profile.

Parallel requests the browser sends together go in one `http.batch([...])`. k6 sends a batch in parallel over up to 6 connections per host (`batchPerHost`), like a browser.

## Think time

Uniform ranges are fine: use roughly the p25–p75 of the profile's think time for normal actions, and wider ranges for writing content. Real distributions have long tails (someone reading for 2 minutes). The tail mostly lowers r, and calibration catches that. Start each VU with `think(0, 5)` on its first iteration so VUs don't move in lockstep, and end each iteration with an idle pause (the gap between visits or actions).

Calibrate r, not think time: if the measured per-user rate is 20 % too high, stretch the pauses by ~20 %.

## Authentication

Never use the user's own account or real customers' accounts.

- **Pre-created test accounts with tokens** (best for scale): create N accounts on the target (a seed script, an admin API, or a SQL insert on staging), and save `[{email, token}]` or `[{email, cookie}]` to `accounts.json`. Load it with `SharedArray` and give each VU its own account (`__VU`-indexed). With several load generators, offset by `LG_INDEX` so they don't share accounts. Tokens must stay valid for the whole test, so check their expiry.
- **Log in once per VU**: on `__ITER === 0`, POST to the login endpoint and keep the cookie (k6 has a per-VU cookie jar, so session cookies just work) or the token in a module-level variable. If login is expensive (bcrypt/argon2 is deliberately slow), thousands of VUs logging in at the ramp will measure the password hash. That's real for a launch stampede, but usually not what you want to measure. Ramp slowly, or use pre-made tokens.
- **Third-party auth** (Auth0, Clerk, Cognito, Firebase Auth, Supabase Auth): don't load test the provider's login. Use tokens minted for test users (most providers have a testing or backend API for that, or long-lived test sessions), and check that the app validates them without calling the provider on every request.
- **CSRF**: extract the token from the page or cookie (`res.html().find('input[name=csrf]').attr('value')`) and send it the way the browser does.

## Test data and writes

- Read IDs from earlier responses whenever the real app does, so the access pattern follows real popularity. Otherwise load a seeded list from JSON. Avoid always hitting the same ID: that measures a cache.
- Mark everything the test writes (`loadtest-` prefix, a test flag, dedicated test accounts) so it can be deleted afterwards. Agree on the cleanup query with the user.
- Side effects: emails, SMS, push notifications, payments, webhooks to partners, LLM calls. On staging, point them at sandbox or test modes. On production, skip those actions or use test accounts that the app treats specially. If neither is possible, leave the action out and say so in the report. The cost of a mistake is real money and real messages to real people.
- If the dataset on staging is much smaller than production, results will be optimistic (indexes fit in memory, queries are fast). Note it as a caveat, or seed to production size.

## Errors that are expected

`http_req_failed` counts every status ≥ 400 as a failure. When some are normal (404 for a deleted item, 409 for a double like, 401 that triggers a token refresh), declare them per request:

```js
http.post(url, body, { tags: { name: 'POST /like' }, responseCallback: http.expectedStatuses(200, 201, 409) });
```

Don't silence 429 (rate limited) or 5xx. Those are exactly what the test is looking for.

## Special traffic: polling, websockets, streaming

- **Polling** (notifications, presence, dashboards): in a closed-model journey, poll inside the think time (e.g. `think()` in chunks with a poll between them), because a real tab keeps polling while the person reads.
- **WebSockets**: `k6/websockets` (or `k6/ws`). One connection per VU for the session, with messages at the profiled rate. Count connections against the generator's port limits.
- **Server-sent events / streaming responses** (LLM chat): k6 waits for the whole response. Time to first byte is `res.timings.waiting`, total is `duration`. For LLM-backed endpoints, ask whether the test should call the real model (cost, provider rate limits) or a stub.

## Per-endpoint criteria

hold.js applies the global criteria to all requests. For an endpoint with its own SLO, add a threshold in hold.js's options after the generated ones:

```js
options.thresholds['http_req_duration{name:POST /api/checkout}'] = ['p(95)<300'];
```

Use the same pattern inside staircase.js's per-step loop (`http_req_duration{step:${users},name:POST /api/checkout}`) if it must also stop the staircase.

## k6 pitfalls

- `responseType: 'none'` when you don't read the body. Bodies held in memory are the main cost of many VUs.
- Never tag with raw URLs (the `url` system tag is disabled in the templates). One time series per distinct URL exhausts memory.
- `open()` only works in the init context (top level), not inside `default()`.
- Module-level variables are per VU, not shared. `SharedArray` is read-only and shared.
- Each VU keeps its connections alive between iterations, like a browser. That's realistic, and it means N VUs ≈ N open connections on the target.
- k6 speaks HTTP/2 automatically over HTTPS when the server offers it.
- If `handleSummary` can't write its files, check that `results/` exists (`mkdir -p results`).
