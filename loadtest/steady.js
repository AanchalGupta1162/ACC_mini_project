// Flat load (calibration) or jittered load (stability/churn experiment).
import http from 'k6/http';
import { summarize, TARGET_URL } from './summary.js';

const RATE = Number(__ENV.RATE || 20);
const DURATION_S = Number(__ENV.DURATION_S || 300);
const JITTER = Number(__ENV.JITTER || 0); // e.g. 0.4 = random ±40% every 15s

const flat = {
  executor: 'constant-arrival-rate',
  rate: RATE,
  timeUnit: '1s',
  duration: `${DURATION_S}s`,
  preAllocatedVUs: 50,
  maxVUs: 600,
};

const jittered = {
  executor: 'ramping-arrival-rate',
  startRate: RATE,
  timeUnit: '1s',
  preAllocatedVUs: 50,
  maxVUs: 600,
  stages: Array.from({ length: Math.ceil(DURATION_S / 15) }, () => ({
    duration: '15s',
    target: Math.max(1, Math.round(RATE * (1 + JITTER * (2 * Math.random() - 1)))),
  })),
};

export const options = { scenarios: { steady: JITTER > 0 ? jittered : flat } };

export default function () {
  http.get(TARGET_URL, { timeout: '10s' });
}

export function handleSummary(data) {
  return summarize(data);
}
