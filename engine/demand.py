"""Demand arm: size to the request rate that is arriving now, projected over the pod startup lead.

Reactive CPU is capped by the pod CPU limit and lags warming pods; the forecast needs ten minutes
of history. This arm needs three samples. It is a lead-time-aware rate rule, not a forecaster:
it extrapolates the recent slope only upwards, and only as far as pods take to become ready.
"""
import math

import numpy as np

from config import (
    DEMAND_LEAD_SECONDS,
    DEMAND_MIN_POINTS,
    MAX_REPLICAS,
    MIN_REPLICAS,
    RPS_PER_POD,
    SAFETY_MARGIN,
    STEP_SECONDS,
)
from metrics_source import recent_rps


def slope_per_second(values, step_seconds):
    """Least-squares slope of the series, in units per second."""
    y = np.asarray(values, dtype=float)
    x = np.arange(len(y), dtype=float) * step_seconds
    return float(np.polyfit(x, y, 1)[0])


def demand_target(series, step_seconds, lead_seconds):
    """Replicas for the request rate expected when pods started now become ready, or None."""
    values = np.asarray(series, dtype=float)
    if len(values) < DEMAND_MIN_POINTS or not np.all(np.isfinite(values)):
        return None
    projected = values[-1] + max(0.0, slope_per_second(values, step_seconds)) * lead_seconds
    target = math.ceil(round(projected * SAFETY_MARGIN / RPS_PER_POD, 6))
    return max(MIN_REPLICAS, min(MAX_REPLICAS, target))


class DemandArm:
    def __init__(self, fetch=None, step_seconds=STEP_SECONDS, lead_seconds=DEMAND_LEAD_SECONDS):
        self._fetch = fetch or recent_rps
        self._step = step_seconds
        self._lead = lead_seconds

    def compute(self):
        try:
            return demand_target(self._fetch(), self._step, self._lead)
        except Exception as e:
            print(f"[demand] abstaining this cycle: {e}", flush=True)
            return None
