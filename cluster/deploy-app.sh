#!/usr/bin/env bash
# Build playback-api and import it into every node (this is the warm-image pre-pull).
set -euo pipefail
cd "$(dirname "$0")/.."
CLUSTER="${CLUSTER:-jiohotstar}"
docker build -t playback-api:1.0 app/
k3d image import playback-api:1.0 --cluster "$CLUSTER"
kubectl apply -f k8s/playback-api.yaml -f k8s/mocks.yaml
kubectl rollout status deployment/playback-api --timeout=180s
kubectl get pods -o wide
