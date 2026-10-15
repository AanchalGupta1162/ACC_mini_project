#!/usr/bin/env bash
# Full match-day run: the engine against the repeating trace, then forecast accuracy and churn.
# This is also the warm-up the presentation run needs (the seasonal term needs 2 cycles).
set -euo pipefail
cd "$(dirname "$0")/.."
CYCLES="${CYCLES:-4}"

cluster/reset.sh >/dev/null
rm -f results/matchday-decisions.csv
.venv/bin/python engine/main.py --log-csv results/matchday-decisions.csv >results/matchday-engine.log 2>&1 &
ENGINE=$!
trap 'kill $ENGINE 2>/dev/null || true' EXIT

CYCLES="$CYCLES" k6 run --quiet loadtest/matchday.js | tee results/matchday-k6.txt
sleep 170   # let the final forecasts reach the time they were predicting
kill $ENGINE 2>/dev/null || true
sleep 2

{
  .venv/bin/python experiments/analyze_log.py mape results/matchday-decisions.csv
  .venv/bin/python experiments/analyze_log.py churn results/matchday-decisions.csv
} | tee results/matchday-analysis.txt
