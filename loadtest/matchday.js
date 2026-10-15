// JioHotstar match day compressed into a repeating 10-minute cycle.
// The shape is what makes the pattern forecastable: the same phases in the same order.
import http from 'k6/http';
import { summarize, TARGET_URL } from './summary.js';

const BASE = Number(__ENV.BASE_RPS || 20);
const PEAK = Number(__ENV.PEAK_RPS || 110);
const PERIOD_MIN = Number(__ENV.PERIOD_MIN || 10); // 10 min = 40 samples at 15s = SEASONAL_PERIODS
const CYCLES = Number(__ENV.CYCLES || 3);

// phase: [share of a cycle, share of PEAK traffic]
const PHASES = [
  [0.15, 0.20], // pre-match ramp
  [0.10, 0.35], // toss
  [0.10, 0.50], // teams out
  [0.10, 0.80], // first ball
  [0.05, 0.65], // settling
  [0.05, 0.85], // boundary burst
  [0.10, 0.30], // innings break dip
  [0.10, 0.45], // chase begins
  [0.10, 0.70], // chase climb
  [0.10, 1.00], // final overs peak
  [0.05, 0.25], // post match
];

function matchdayStages() {
  const cycleSeconds = PERIOD_MIN * 60;
  const stages = [];
  for (let c = 0; c < CYCLES; c++) {
    for (const [sharePart, shareLoad] of PHASES) {
      stages.push({
        duration: `${Math.round(cycleSeconds * sharePart)}s`,
        target: Math.round(BASE + (PEAK - BASE) * shareLoad),
      });
    }
  }
  return stages;
}

export const options = {
  scenarios: {
    matchday: {
      executor: 'ramping-arrival-rate',
      startRate: BASE,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 800,
      stages: matchdayStages(),
    },
  },
};

export default function () {
  http.get(TARGET_URL, { timeout: '10s' });
}

export function handleSummary(data) {
  return summarize(data);
}
