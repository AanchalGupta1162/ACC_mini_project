#!/usr/bin/env bash
# Create the k3d cluster: 1 server + agents, with host ports for the app, Prometheus and Grafana.
set -euo pipefail
cd "$(dirname "$0")/.."
CLUSTER="${CLUSTER:-jiohotstar}"
AGENTS="${AGENTS:-4}"            # 2 active + 2 reserve

if ! k3d cluster list -o json | grep -q "\"name\":\"$CLUSTER\""; then
  k3d cluster create "$CLUSTER" \
    --servers 1 --agents "$AGENTS" \
    --k3s-arg "--disable=traefik@server:*" \
    --port "8080:30080@loadbalancer" \
    --port "9090:30090@loadbalancer" \
    --port "3000:30300@loadbalancer" \
    --wait --timeout 300s
fi
kubectl config use-context "k3d-$CLUSTER"
kubectl wait --for=condition=Ready nodes --all --timeout=180s
cluster/label-nodes.sh
