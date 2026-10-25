/**
 * hold.js: N users for a fixed time, judged on the whole run. Used for smoke tests, calibration and confirmation holds.
 *
 *   k6 run -e BASE_URL=https://staging.example.com -e VUS=500 hold.js
 *
 * Env: BASE_URL (required), VUS (default 50), RAMP_UP (60s), DURATION (5m), RAMP_DOWN (30s),
 *      P95_MS / P99_MS / MAX_ERROR_RATE (500 / 1000 / 0.01), LABEL (hold), RESULTS_DIR (results).
 * k6 exits 99 when the criteria fail, 0 when they pass.
 */
import * as journey from './journey.js';
import { env, envNum, seconds, holdThresholds, holdSummary, SUMMARY_TREND_STATS, SYSTEM_TAGS } from './lib.js';

const VUS = envNum('VUS', 50);
const RAMP_UP = env('RAMP_UP', '60s'), DURATION = env('DURATION', '5m'), RAMP_DOWN = env('RAMP_DOWN', '30s');

export const options = {
  scenarios: {
    users: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: RAMP_UP, target: VUS },
        { duration: DURATION, target: VUS },
        { duration: RAMP_DOWN, target: 0 },
      ],
      gracefulRampDown: '10s',
      gracefulStop: '10s',
    },
  },
  thresholds: holdThresholds(journey.ENDPOINTS),
  summaryTrendStats: SUMMARY_TREND_STATS,
  systemTags: SYSTEM_TAGS,
  userAgent: env('USER_AGENT', 'k6-realistic-load-test'),
};

export function setup() {
  if (!__ENV.BASE_URL) throw new Error('set BASE_URL, e.g. -e BASE_URL=https://staging.example.com');
  return journey.setup ? journey.setup() : {};
}

export default function (data) {
  journey.default(data);
}

export function handleSummary(data) {
  // VU-seconds for the per-user rate: half of each ramp (linear) plus the hold
  const vuSeconds = VUS * (seconds(RAMP_UP) / 2 + seconds(DURATION) + seconds(RAMP_DOWN) / 2);
  return holdSummary(data, {
    label: env('LABEL', 'hold'),
    base_url: env('BASE_URL', ''),
    vus: VUS,
    shape: `${RAMP_UP} ramp + ${DURATION} hold + ${RAMP_DOWN} ramp-down`,
    vu_seconds: vuSeconds,
    results_dir: env('RESULTS_DIR', 'results'),
    loadgen: env('LG_INDEX', '0'),
  }, journey.ENDPOINTS);
}
