// Scaling-lag experiment: calm baseline, then a sudden rush (the "first ball" moment).
import http from 'k6/http';
import { summarize, TARGET_URL } from './summary.js';

const BASE = Number(__ENV.BASE_RPS || 20);
const PEAK = Number(__ENV.PEAK_RPS || 110);

export const options = {
  scenarios: {
    spike: {
      executor: 'ramping-arrival-rate', // open model: load does not back off when the app slows
      startRate: BASE,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 800,
      stages: [
        { duration: '1m', target: BASE },   // baseline (spike starts 60s in)
        { duration: '30s', target: PEAK },  // rush
        { duration: '2m', target: PEAK },   // hold
        { duration: '1m', target: BASE },   // recover
      ],
    },
  },
};

export default function () {
  http.get(TARGET_URL, { timeout: '10s' });
}

export function handleSummary(data) {
  return summarize(data);
}
