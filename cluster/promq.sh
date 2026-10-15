#!/usr/bin/env bash
# promq.sh '<promql>'  ->  prints the first scalar value, or "n/a"
set -euo pipefail
curl -fsG "${PROM_URL:-http://localhost:9090}/api/v1/query" --data-urlencode "query=$1" |
  python3 -c 'import json,sys
r = json.load(sys.stdin)["data"]["result"]
print(round(float(r[0]["value"][1]), 3) if r else "n/a")'
