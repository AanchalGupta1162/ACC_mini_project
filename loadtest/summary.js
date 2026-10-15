// Shared end-of-test summary: one stdout line plus optional full JSON (SUMMARY_JSON=path).
export function summarize(data) {
  const m = data.metrics;
  const dropped = m.dropped_iterations ? m.dropped_iterations.values.count : 0;
  const line =
    `p95=${m.http_req_duration.values['p(95)'].toFixed(0)}ms ` +
    `failed=${(m.http_req_failed.values.rate * 100).toFixed(2)}% ` +
    `reqs=${m.http_reqs.values.count} dropped=${dropped}\n`;
  const out = { stdout: line };
  if (__ENV.SUMMARY_JSON) out[__ENV.SUMMARY_JSON] = JSON.stringify(data, null, 2);
  return out;
}

export const TARGET_URL = __ENV.TARGET_URL || 'http://localhost:8080/play';
