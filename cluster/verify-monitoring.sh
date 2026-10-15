#!/usr/bin/env bash
# Asserts that every query the engine depends on returns data.
set -euo pipefail
PROM="${PROM_URL:-http://localhost:9090}"
q() {
  curl -fsG "$PROM/api/v1/query" --data-urlencode "query=$1" |
    python3 -c 'import json,sys; r=json.load(sys.stdin)["data"]["result"]; print(r[0]["value"][1] if r else "EMPTY")'
}
for _ in $(seq 1 36); do curl -fs "$PROM/-/ready" >/dev/null 2>&1 && break; sleep 5; done
curl -fs "$PROM/-/ready" >/dev/null || { echo "FAIL: Prometheus not ready at $PROM"; exit 1; }

# warm the app so the counters exist, then wait for two scrapes
for _ in $(seq 1 30); do curl -fs http://localhost:8080/play >/dev/null || true; done
for _ in $(seq 1 24); do [ "$(q 'sum(rate(playback_requests_total[1m]))')" != EMPTY ] && break; sleep 5; done

SEL='namespace="default",container="playback-api"'
declare -a CHECKS=(
  'sum(rate(playback_requests_total{service="playback-api"}[1m]))'
  "sum(rate(container_cpu_usage_seconds_total{$SEL}[1m]))"
  "sum(kube_pod_container_resource_requests{$SEL,resource=\"cpu\"})"
  'sum(kube_pod_status_unschedulable{namespace="default",pod=~"playback-api-.*"}) or vector(0)'
  'kube_deployment_status_replicas_available{namespace="default",deployment="playback-api"}'
)
for expr in "${CHECKS[@]}"; do
  v=$(q "$expr")
  [ "$v" != EMPTY ] || { echo "FAIL: no data for: $expr"; exit 1; }
  printf 'ok  %-10s <= %s\n' "$v" "$expr"
done
echo "PASS: every engine metric is available"
