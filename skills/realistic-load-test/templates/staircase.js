/**
 * staircase.js: the fast estimate. One run that keeps adding users in steps and stops at the first failing step.
 *
 *   k6 run -e BASE_URL=https://staging.example.com -e START=200 -e STEP=100 -e MAX=3000 staircase.js
 *
 * Each step is judged only on the requests made during it (every request is tagged step:<users>; think() in
 * lib.js re-tags after each pause). A failing step aborts the run after a 15 s grace period. The summary prints a
 * per-step table and writes <RESULTS_DIR>/staircase.txt and estimate.txt (the last passing step).
 *
 * Env: BASE_URL (required), START (first step, default 100), STEP (users added per step, default 100),
 *      MAX (last step, default 2000), STEP_DUR (seconds per step, default 45), CLIMB (seconds to move between steps,
 *      default 10; new users arriving all at once is a burst some servers fail on, so keep STEP small), P95_MS / P99_MS / MAX_ERROR_RATE, RESULTS_DIR (results).
 * Running from several load generators: give each START, STEP and MAX divided by the number of generators, start
 * them at the same moment, and add up the users per step.
 */
import exec from 'k6/execution';
import * as journey from './journey.js';
import { env, envNum, setStepper, retag, CRITERIA, passes, SUMMARY_TREND_STATS, SYSTEM_TAGS } from './lib.js';

const START = envNum('START', 100), STEP = envNum('STEP', 100), MAX = envNum('MAX', 2000);
const DUR = envNum('STEP_DUR', 45), CLIMB = envNum('CLIMB', 10), RAMP = Math.max(20, CLIMB);

const steps = []; // [users, start_s, end_s]
for (let t = RAMP, v = START; v <= MAX; t += DUR, v += STEP) steps.push([v, t, t + DUR]);

// options are only read from the init context with __VU == 0, so VUs skip building them
let stages = [], thresholds = {};
if (__VU === 0) {
  stages = [{ duration: `${RAMP}s`, target: START }];
  steps.forEach(([users], i) => {
    if (i > 0) stages.push({ duration: `${CLIMB}s`, target: users });
    stages.push({ duration: `${DUR - (i > 0 ? CLIMB : 0)}s`, target: users });
  });
  for (const [users] of steps) {
    const abort = (t) => ({ threshold: t, abortOnFail: true, delayAbortEval: '15s' });
    thresholds[`http_req_duration{step:${users}}`] = [abort(`p(95)<${CRITERIA.p95}`), abort(`p(99)<${CRITERIA.p99}`)];
    thresholds[`http_req_failed{step:${users}}`] = [abort(`rate<${CRITERIA.err}`)];
    thresholds[`http_reqs{step:${users}}`] = ['count>=0']; // only to get the per-step count in the summary
  }
}

export const options = {
  scenarios: { staircase: { executor: 'ramping-vus', startVUs: 0, stages, gracefulRampDown: '5s', gracefulStop: '5s' } },
  thresholds,
  summaryTrendStats: ['med', 'p(95)', 'p(99)', 'max'],
  systemTags: SYSTEM_TAGS,
  userAgent: env('USER_AGENT', 'k6-realistic-load-test'),
};

function currentStep() {
  const el = exec.instance.currentTestRunDuration / 1000;
  const cur = steps.find(([, a, b]) => el >= a && el < b) || (el < RAMP ? steps[0] : steps[steps.length - 1]);
  return cur[0];
}

export function setup() {
  if (!__ENV.BASE_URL) throw new Error('set BASE_URL, e.g. -e BASE_URL=https://staging.example.com');
  return journey.setup ? journey.setup() : {};
}

export default function (data) {
  setStepper(currentStep);
  retag();
  journey.default(data);
}

export function handleSummary(data) {
  const m = data.metrics;
  const out = env('RESULTS_DIR', 'results');
  const lines = [`criteria: p95 < ${CRITERIA.p95} ms, p99 < ${CRITERIA.p99} ms, errors < ${CRITERIA.err * 100} %   (${DUR} s steps)`,
    `${'users'.padStart(7)} ${'req/s'.padStart(8)} ${'p50'.padStart(8)} ${'p95'.padStart(8)} ${'p99'.padStart(8)} ${'err%'.padStart(6)}  verdict`];
  let est = 0, firstFail = null;
  for (const [users] of steps) {
    const d = m[`http_req_duration{step:${users}}`], e = m[`http_req_failed{step:${users}}`], r = m[`http_reqs{step:${users}}`];
    if (!r || !r.values.count) break; // the run ended before this step
    const p95 = d.values['p(95)'], p99 = d.values['p(99)'], err = e ? e.values.rate : 0;
    // k6 marks a threshold not-ok when it crossed at any point (that is what aborts the run), even if the late
    // requests of an aborted step pull the final percentile back under the line, so check both.
    const crossed = [d, e].some((x) => x && Object.values(x.thresholds || {}).some((t) => !t.ok));
    const ok = passes(p95, p99, err) && !crossed;
    lines.push(`${String(users).padStart(7)} ${(r.values.count / DUR).toFixed(1).padStart(8)} ${d.values.med.toFixed(1).padStart(8)} ${p95.toFixed(1).padStart(8)} ${p99.toFixed(1).padStart(8)} ${(err * 100).toFixed(2).padStart(6)}  ${ok ? 'PASS' : 'FAIL'}`);
    if (!ok) { firstFail = users; break; }
    est = users;
  }
  const atMax = firstFail === null && steps.length && est === steps[steps.length - 1][0];
  lines.push(`ESTIMATE ${est} users (last passing step${firstFail ? `; first failing step ${firstFail}` : ''}${atMax ? `; still passing at MAX=${MAX}, raise MAX` : ''})`);
  const text = lines.join('\n') + '\n';
  return { stdout: '\n' + text, [`${out}/staircase.txt`]: text, [`${out}/estimate.txt`]: `${est}\n` };
}
