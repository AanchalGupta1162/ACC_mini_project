#!/usr/bin/env bash
# Asserts the reserve-pool topology the engine depends on.
set -euo pipefail
ACTIVE_AGENTS="${ACTIVE_AGENTS:-2}"
RESERVE_AGENTS="${RESERVE_AGENTS:-2}"
SLOTS_PER_NODE="${SLOTS_PER_NODE:-4}"
fail() { echo "FAIL: $*"; exit 1; }
count() { kubectl get nodes -l "$1" -o name | wc -l | tr -d ' '; }
cordoned() { kubectl get nodes -l "$1" -o jsonpath='{range .items[*]}{.spec.unschedulable}{"\n"}{end}' | grep -c true || true; }

kubectl config current-context | grep -q k3d- || fail "not on a k3d context"
[ "$(count role=active)" = "$ACTIVE_AGENTS" ] || fail "expected $ACTIVE_AGENTS active nodes, got $(count role=active)"
[ "$(count role=reserve)" = "$RESERVE_AGENTS" ] || fail "expected $RESERVE_AGENTS reserve nodes, got $(count role=reserve)"
[ "$(cordoned role=active)" = 0 ] || fail "active nodes must be schedulable"
[ "$(cordoned role=reserve)" = "$RESERVE_AGENTS" ] || fail "every reserve node must start cordoned"
for n in $(kubectl get nodes -l 'role in (active,reserve)' -o jsonpath='{.items[*].metadata.name}'); do
  slots=$(kubectl get node "$n" -o jsonpath='{.status.allocatable.autoscaler\.demo/slot}')
  [ "$slots" = "$SLOTS_PER_NODE" ] || fail "$n advertises '$slots' slots, expected $SLOTS_PER_NODE"
done
echo "PASS: $ACTIVE_AGENTS active + $RESERVE_AGENTS cordoned reserve nodes, $SLOTS_PER_NODE slots each"
