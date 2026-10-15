"""Predictive arm: forecast request rate ~2 minutes ahead and size capacity for it.

Holt-Winters (statsmodels) refit every cycle on the last ~30 minutes from Prometheus.
No training, no saved model: it is exponential smoothing over level, trend and season.
"""
import math
import time
import warnings

import numpy as np
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from config import (
    HORIZON_STEPS,
    MAX_REPLICAS,
    MIN_POINTS,
    MIN_REPLICAS,
    PREDICTIVE_INTERVAL_SECONDS,
    RPS_PER_POD,
    SAFETY_MARGIN,
    SEASONAL_PERIODS,
)
from metrics_source import rps_history


def forecast_rps(series, horizon_steps=HORIZON_STEPS, seasonal_periods=SEASONAL_PERIODS):
    """Forecast the next horizon_steps samples, or None while history is too short."""
    values = np.asarray(series, dtype=float)
    if len(values) < MIN_POINTS:
        return None  # cold start: the reactive arm covers this window
    # additive Holt-Winters needs two full seasons; otherwise fall back to Holt's linear trend
    seasonal = seasonal_periods if seasonal_periods and len(values) >= 2 * seasonal_periods else None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # statsmodels convergence chatter
        model = ExponentialSmoothing(
            values,
            trend="add",
            seasonal="add" if seasonal else None,
            seasonal_periods=seasonal,
        ).fit()
        forecast = np.asarray(model.forecast(horizon_steps), dtype=float)
    if not np.all(np.isfinite(forecast)):
        raise ValueError("non-finite forecast")
    return np.clip(forecast, 0.0, None)


def replicas_for_rps(rps):
    """Capacity calibration: how many pods serve this request rate at acceptable latency."""
    target = math.ceil(round(rps * SAFETY_MARGIN / RPS_PER_POD, 6))
    return max(MIN_REPLICAS, min(MAX_REPLICAS, target))


class PredictiveArm:
    def __init__(self, fetch=None, clock=time.time, interval=PREDICTIVE_INTERVAL_SECONDS,
                 seasonal_periods=SEASONAL_PERIODS):
        self._fetch = fetch or rps_history
        self._clock = clock
        self._interval = interval
        self._seasonal_periods = seasonal_periods
        self.last_run = None
        self.target = None
        self.forecast_rps = None  # forecast request rate at the end of the horizon

    def compute(self):
        now = self._clock()
        if self.last_run is not None and now - self.last_run < self._interval:
            return self.target  # the arbiter ticks every 15s, this arm only every 30s
        self.last_run = now
        try:
            forecast = forecast_rps(self._fetch(), seasonal_periods=self._seasonal_periods)
            if forecast is None:
                print("[predictive] warming up, not enough history yet", flush=True)
                self.target, self.forecast_rps = None, None
            else:
                self.target = replicas_for_rps(float(forecast.max()))
                self.forecast_rps = float(forecast[-1])
        except Exception as e:
            print(f"[predictive] abstaining this cycle: {e}", flush=True)
            self.target, self.forecast_rps = None, None
        return self.target
