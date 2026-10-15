#!/usr/bin/env bash
# Load the Grafana dashboard (the sidecar picks up any ConfigMap with grafana_dashboard=1).
set -euo pipefail
cd "$(dirname "$0")/.."
kubectl -n monitoring create configmap autoscaler-dashboard \
  --from-file=autoscaler.json=dashboards/autoscaler.json \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n monitoring label configmap autoscaler-dashboard grafana_dashboard=1 --overwrite >/dev/null
echo "dashboard loaded; Grafana: http://localhost:3000 (admin/admin)"
