#!/usr/bin/env bash
# Idempotent: server = role=system, first ACTIVE_AGENTS agents = role=active,
# the rest = role=reserve and cordoned. Every agent advertises a fixed number of app slots.
set -euo pipefail
ACTIVE_AGENTS="${ACTIVE_AGENTS:-2}"
SLOTS_PER_NODE="${SLOTS_PER_NODE:-4}"   # must equal PODS_PER_NODE in engine/config.py

server=$(kubectl get nodes -l 'node-role.kubernetes.io/control-plane' -o jsonpath='{.items[0].metadata.name}')
kubectl label node "$server" role=system --overwrite >/dev/null

# original agents only; nodes the engine created on the cold path are handled by the engine
agents=$(kubectl get nodes -l '!node-role.kubernetes.io/control-plane' -o jsonpath='{.items[*].metadata.name}' \
  | tr ' ' '\n' | grep -v -- '-cold' | sort)

i=0
for node in $agents; do
  if [ "$i" -lt "$ACTIVE_AGENTS" ]; then role=active; else role=reserve; fi
  kubectl label node "$node" role="$role" --overwrite >/dev/null
  kubectl patch node "$node" --subresource=status --type=json \
    -p "[{\"op\":\"add\",\"path\":\"/status/capacity/autoscaler.demo~1slot\",\"value\":\"$SLOTS_PER_NODE\"}]" >/dev/null
  if [ "$role" = reserve ]; then kubectl cordon "$node" >/dev/null; else kubectl uncordon "$node" >/dev/null; fi
  i=$((i + 1))
done
kubectl get nodes -L role
