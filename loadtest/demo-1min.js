// 1-minute smoke demo: short baseline, rush, hold. Same open-model arrivals as spike.js.
import http from 'k6/http';
import { summarize, TARGET_URL } from './summary.js';

export const options = {
  scenarios: {
    quick: {
      executor: 'ramping-arrival-rate',
      startRate: 20, timeUnit: '1s', preAllocatedVUs: 100, maxVUs: 800,
      stages: [
        { duration: '10s', target: 20 },
        { duration: '30s', target: 110 },   // same 30s rush as spike.js
        { duration: '20s', target: 110 },
      ],
    },
  },
};
export default function () { http.get(TARGET_URL, { timeout: '10s' }); }
export function handleSummary(data) { return summarize(data); }
