"""Arbitration: take the bigger of the two arms, clamp it, and work out the node moves.

Pure decision logic only. Nothing here touches the cluster; reserve_pool.py does that.
"""
from dataclasses import dataclass

from config import COOLDOWN_SECONDS, MAX_REPLICAS, MIN_REPLICAS, PODS_PER_NODE


@dataclass(frozen=True)
class Decision:
    target: int | None  # clamped max of the arms; None when both arms abstained
    scale: bool         # patch Deployment replicas this cycle
    uncordon: int       # warm reserve nodes to promote now (seconds)
    cordon: int         # reserve nodes to hand back (0 or 1)
    cold_create: int    # brand-new nodes to build because the pool is empty (slow path)
    reason: str


def clamp(n):
    return max(MIN_REPLICAS, min(MAX_REPLICAS, n))


def nodes_needed(replicas):
    return -(-replicas // PODS_PER_NODE)  # ceiling division


def decide(reactive, predictive, current, schedulable_nodes, cordoned_reserves, promoted_reserves,
           now, last_change, allow_cold=True):
    arms = f"(reactive={reactive}, predictive={predictive})"
    candidates = [t for t in (reactive, predictive) if t is not None]
    if not candidates:
        return Decision(None, False, 0, 0, 0, f"BOTH arms abstained, no change this cycle {arms}")

    target = clamp(max(candidates))
    in_cooldown = now - last_change < COOLDOWN_SECONDS
    scale = target != current and not in_cooldown

    # Capacity is reconciled every cycle, even inside the cooldown, so pods never sit
    # unschedulable waiting for the next replica change.
    shortfall = max(0, nodes_needed(max(target, current)) - schedulable_nodes)
    uncordon = min(shortfall, cordoned_reserves)
    cold_create = (shortfall - uncordon) if allow_cold else 0
    cordon = int(
        scale
        and target < current
        and uncordon == 0
        and promoted_reserves > 0
        and nodes_needed(target) < schedulable_nodes
    )

    parts = []
    if scale:
        parts.append(f"scale {current} -> {target}")
    elif target != current:
        parts.append(f"within cooldown, holding {current} (wants {target})")
    else:
        parts.append(f"hold {current}")
    if uncordon:
        parts.append(f"promote {uncordon} warm reserve node(s)")
    if shortfall > uncordon:
        parts.append("reserve pool EMPTY")
        if cold_create:
            parts.append(f"building {cold_create} cold node(s), this is the slow path")
        else:
            parts.append("extra pods will stay Pending")
    if cordon:
        parts.append("return 1 reserve node to the pool")
    return Decision(target, scale, uncordon, cordon, cold_create, "; ".join(parts) + " " + arms)
