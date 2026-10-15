#!/usr/bin/env bash
# Back to the demo baseline: min replicas, reserves empty and cordoned, cold nodes removed.
set -euo pipefail
cd "$(dirname "$0")/.."
CLUSTER="${CLUSTER:-jiohotstar}"
kubectl scale deployment/playback-api --replicas=2 >/dev/null
for node in $(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep -- '-cold' || true); do
  kubectl drain "$node" --ignore-daemonsets --delete-emptydir-data --timeout=60s >/dev/null 2>&1 || true
  k3d node delete "$node" >/dev/null 2>&1 || true
  # deleting the container leaves the Node object registered, so remove that too
  kubectl delete node "$node" --ignore-not-found >/dev/null 2>&1 || true
done
for node in $(kubectl get nodes -l role=reserve -o jsonpath='{.items[*].metadata.name}'); do
  kubectl drain "$node" --ignore-daemonsets --delete-emptydir-data --pod-selector=app=playback-api --timeout=120s >/dev/null
done
kubectl rollout status deployment/playback-api --timeout=180s
kubectl get nodes -L role
