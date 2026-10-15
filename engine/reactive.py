"""Reactive arm: respond to pressure that already exists.

This reproduces the HPA's reactive decision (proportional to CPU vs request, with the
HPA's tolerance band) and adds the HPA's blind spot: pods the scheduler cannot place.
"""
import math

from config import CPU_TOLERANCE, MAX_REPLICAS, MIN_REPLICAS, TARGET_CPU_UTILIZATION
from metrics_source import cpu_utilization_pct, unschedulable_pods


def clamp(n):
    return max(MIN_REPLICAS, min(MAX_REPLICAS, n))


def reactive_target(pending, cpu_pct, current):
    if pending > 0:
        target = current + max(1, int(pending))
    else:
        ratio = cpu_pct / TARGET_CPU_UTILIZATION
        if abs(ratio - 1) <= CPU_TOLERANCE:
            target = current  # close enough to target, hold steady
        elif ratio > 1:
            target = max(current + 1, math.ceil(round(current * ratio, 6)))
        else:
            target = math.ceil(round(current * ratio, 6))
    return clamp(target)


def compute(current_replicas):
    """Return a replica recommendation, or None to abstain for this cycle."""
    try:
        return reactive_target(unschedulable_pods(), cpu_utilization_pct(), current_replicas)
    except Exception as e:
        print(f"[reactive] abstaining this cycle: {e}", flush=True)
        return None
