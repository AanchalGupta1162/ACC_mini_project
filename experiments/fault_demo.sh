#!/usr/bin/env bash
# Fallback safety: kill each arm in turn and confirm the engine degrades exactly as designed.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=results/fault-demo.log
mkdir -p engine/faults results
rm -f engine/faults/fault-* "$LOG" results/fault-demo.csv

.venv/bin/python engine/main.py --no-exporter --log-csv results/fault-demo.csv >"$LOG" 2>&1 &
ENGINE=$!
trap 'kill $ENGINE 2>/dev/null || true' EXIT
sleep 35

echo "--- killing the predictive arm"
touch engine/faults/fault-predictive
sleep 35

echo "--- killing the demand arm, then the reactive arm (every arm now dead)"
touch engine/faults/fault-demand
sleep 20
before="$(kubectl get deploy playback-api -o jsonpath='{.spec.replicas}')"
touch engine/faults/fault-reactive
sleep 40
after="$(kubectl get deploy playback-api -o jsonpath='{.spec.replicas}')"

echo "--- reviving all arms"
rm -f engine/faults/fault-*
sleep 35
kill $ENGINE 2>/dev/null || true
sleep 2

fail=0
check() { if grep -q "$1" "$LOG"; then echo "PASS: $2"; else echo "FAIL: $2"; fail=1; fi; }
check '\[predictive\] killed by fault injection' "predictive arm abstains when killed"
check '\[reactive\] killed by fault injection'   "reactive arm abstains when killed"
check '\[demand\] killed by fault injection'     "demand arm abstains when killed"
check 'ALL arms abstained, no change this cycle' "engine makes no change when every arm is down"
if [ "$before" = "$after" ]; then
  echo "PASS: replicas frozen at $before while every arm was down"
else
  echo "FAIL: replicas drifted $before -> $after with every arm down"; fail=1
fi
if tail -6 "$LOG" | grep -q 'reactive=[0-9]'; then
  echo "PASS: engine recovered after the arms came back"
else
  echo "FAIL: engine did not recover"; fail=1
fi
exit $fail
