// Capacity calibration: step the request rate up against a FIXED replica count and
// watch where latency crosses the acceptable line. Gives RPS_PER_POD for the engine.
import http from 'k6/http';
import { summarize, TARGET_URL } from './summary.js';

const START = Number(__ENV.START_RPS || 5);
const STEP = Number(__ENV.STEP_RPS || 5);
const STEPS = Number(__ENV.STEPS || 8);
const STEP_SECONDS = Number(__ENV.STEP_SECONDS || 30);

export const options = {
  scenarios: {
    calibrate: {
      executor: 'ramping-arrival-rate',
      startRate: START,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 600,
      stages: Array.from({ length: STEPS }, (_, i) => ({
        duration: `${STEP_SECONDS}s`,
        target: START + STEP * (i + 1),
      })),
    },
  },
};

export default function () {
  http.get(TARGET_URL, { timeout: '10s' });
}

export function handleSummary(data) {
  return summarize(data);
}
