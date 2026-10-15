#!/usr/bin/env bash
# Prometheus + kube-state-metrics + Grafana, plus the ServiceMonitor for playback-api.
set -euo pipefail
cd "$(dirname "$0")/.."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update >/dev/null
helm repo update >/dev/null
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  -f k8s/monitoring-values.yaml \
  --wait --timeout 15m
kubectl apply -f k8s/servicemonitor.yaml
kubectl -n monitoring get pods -o wide
