// lib.js: shared helpers for hold.js, staircase.js and journey.js. Copy unchanged; edit journey.js instead.
import { sleep } from 'k6';
import exec from 'k6/execution';

export function env(name, fallback) {
  const v = __ENV[name];
  return v === undefined || v === '' ? fallback : v;
}
export function envNum(name, fallback) {
  const v = env(name, undefined);
  return v === undefined ? fallback : Number(v);
}
export function randBetween(min, max) {
  return min + Math.random() * (max - min);
}

/** Picks one entry of [{weight, ...}] with probability proportional to weight. */
export function pickWeighted(items) {
  const total = items.reduce((s, it) => s + it.weight, 0);
  let x = Math.random() * total;
  for (const it of items) if ((x -= it.weight) < 0) return it;
  return items[items.length - 1];
}

/** "90s", "5m", "1m30s", "1h" -> seconds */
export function seconds(d) {
  let s = 0;
  String(d).replace(/(\d+(?:\.\d+)?)(ms|h|m|s)/g, (_, n, u) => { s += Number(n) * { ms: 0.001, s: 1, m: 60, h: 3600 }[u]; });
  return s;
}

// The staircase judges each step by the requests made during it. It registers a function that returns the current
// step, and think() re-tags the VU after every pause, so one long iteration that crosses a step boundary is split
// correctly. Outside the staircase this does nothing.
let stepOf = null;
export function setStepper(fn) { stepOf = fn; }
export function retag() { if (stepOf) exec.vu.metrics.tags.step = String(stepOf()); }

/** A human pause: sleeps a uniform random time in [min, max] seconds. Use it for every think time in journey.js. */
export function think(min, max) {
  sleep(randBetween(min, max));
  retag();
}

/** The pass/fail criteria. Defaults: p95 < 500 ms, p99 < 1000 ms, errors < 1 %. Override with env vars. */
export const CRITERIA = {
  p95: envNum('P95_MS', 500),
  p99: envNum('P99_MS', 1000),
  err: envNum('MAX_ERROR_RATE', 0.01),
};

export function passes(p95, p99, errRate) {
  return p95 < CRITERIA.p95 && p99 < CRITERIA.p99 && errRate < CRITERIA.err;
}

/** Thresholds for a whole-run test (hold.js), plus no-op thresholds that make k6 report every endpoint separately. */
export function holdThresholds(endpoints) {
  const t = {
    http_req_duration: [`p(95)<${CRITERIA.p95}`, `p(99)<${CRITERIA.p99}`],
    http_req_failed: [`rate<${CRITERIA.err}`],
  };
  for (const name of endpoints) {
    t[`http_req_duration{name:${name}}`] = ['max>=0'];
    t[`http_req_failed{name:${name}}`] = ['rate>=0'];
    t[`http_reqs{name:${name}}`] = ['count>=0'];
  }
  return t;
}

export const SUMMARY_TREND_STATS = ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'];
// No `url` tag: /posts/<id> would create one time series per post, all kept in k6's memory. `name` groups them.
export const SYSTEM_TAGS = ['status', 'method', 'name', 'group', 'check', 'error', 'error_code', 'scenario', 'expected_response'];

const f = (x, d = 1) => (x === undefined || x === null || Number.isNaN(x) ? '-' : Number(x).toFixed(d));

/** Per-endpoint numbers from a k6 summary (needs holdThresholds' no-op thresholds). */
export function endpointStats(metrics, endpoints) {
  const out = {};
  for (const name of endpoints) {
    const d = metrics[`http_req_duration{name:${name}}`], r = metrics[`http_reqs{name:${name}}`], e = metrics[`http_req_failed{name:${name}}`];
    if (!r || !r.values.count) { out[name] = { count: 0 }; continue; }
    out[name] = { count: r.values.count, p50_ms: d.values.med, p95_ms: d.values['p(95)'], p99_ms: d.values['p(99)'], max_ms: d.values.max, error_rate: e ? e.values.rate : 0 };
  }
  return out;
}

/** handleSummary for hold.js: text to stdout, and <RESULTS_DIR>/<LABEL>-<VUS>vu-<time>.json/.txt */
export function holdSummary(data, meta, endpoints) {
  const m = data.metrics;
  const dur = m.http_req_duration ? m.http_req_duration.values : {};
  const err = m.http_req_failed ? m.http_req_failed.values.rate : 0;
  const reqs = m.http_reqs ? m.http_reqs.values : { count: 0, rate: 0 };
  const ok = passes(dur['p(95)'], dur['p(99)'], err);
  const eps = endpointStats(m, endpoints);
  const perVu = reqs.count / meta.vu_seconds;
  const lines = [
    '',
    `================ ${meta.label}: ${meta.vus} users ================`,
    `  target        ${meta.base_url}`,
    `  shape         ${meta.shape}`,
    `  requests      ${reqs.count}  (${f(reqs.rate)} req/s overall; ${f(perVu, 3)} req/s per user)`,
    `  latency       p50 ${f(dur.med)} ms   p95 ${f(dur['p(95)'])} ms   p99 ${f(dur['p(99)'])} ms   max ${f(dur.max)} ms`,
    `  errors        ${f(err * 100, 2)} %`,
    `  criteria      p95 < ${CRITERIA.p95} ms, p99 < ${CRITERIA.p99} ms, errors < ${f(CRITERIA.err * 100, 2)} %`,
    `  VERDICT       ${ok ? 'PASS' : 'FAIL'}`,
    '',
    `  ${'endpoint'.padEnd(34)} ${'share'.padStart(6)} ${'p50'.padStart(7)} ${'p95'.padStart(7)} ${'p99'.padStart(7)} ${'err%'.padStart(6)}`,
  ];
  for (const [name, s] of Object.entries(eps)) {
    const share = reqs.count ? (100 * s.count) / reqs.count : 0;
    lines.push(`  ${name.slice(0, 34).padEnd(34)} ${f(share).padStart(5)}% ${f(s.p50_ms).padStart(7)} ${f(s.p95_ms).padStart(7)} ${f(s.p99_ms).padStart(7)} ${f((s.error_rate || 0) * 100, 2).padStart(6)}`);
  }
  const text = lines.join('\n') + '\n';
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const base = `${meta.results_dir}/${meta.label}-${meta.vus}vu-${stamp}`;
  const json = {
    meta: { ...meta, finished_at: new Date().toISOString(), criteria: CRITERIA },
    result: { pass: ok, requests: reqs.count, rps: reqs.rate, per_user_rps: perVu, p50_ms: dur.med, p95_ms: dur['p(95)'], p99_ms: dur['p(99)'], max_ms: dur.max, error_rate: err, endpoints: eps },
  };
  return { stdout: text, [`${base}.txt`]: text, [`${base}.json`]: JSON.stringify(json, null, 2) };
}
