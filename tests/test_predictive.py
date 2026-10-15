import numpy as np
import pytest

import predictive

RNG = np.random.default_rng(42)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def rising_fetch(calls):
    def fetch():
        calls.append(1)
        return list(np.linspace(20.0, 100.0, 60))
    return fetch


def test_forecast_needs_minimum_history():
    assert predictive.forecast_rps([20.0] * (predictive.MIN_POINTS - 1)) is None


def test_forecast_extrapolates_a_rising_ramp():
    series = np.linspace(20, 100, 60) + RNG.normal(0, 1, 60)
    forecast = predictive.forecast_rps(series, horizon_steps=8, seasonal_periods=None)
    assert len(forecast) == 8
    assert forecast[-1] > 100


def test_forecast_of_flat_traffic_stays_flat():
    series = 50 + RNG.normal(0, 1, 60)
    forecast = predictive.forecast_rps(series, horizon_steps=8, seasonal_periods=None)
    assert np.all(np.abs(forecast - 50) < 10)


def test_forecast_is_never_negative():
    forecast = predictive.forecast_rps(np.linspace(100, 2, 60), horizon_steps=8, seasonal_periods=None)
    assert np.all(forecast >= 0)


def test_seasonal_forecast_tracks_the_repeating_match_day_cycle():
    t = np.arange(120)
    series = 60 + 40 * np.sin(2 * np.pi * t / 40)  # 40 steps of 15s = a 10-minute cycle
    forecast = predictive.forecast_rps(series, horizon_steps=8, seasonal_periods=40)
    expected = 60 + 40 * np.sin(2 * np.pi * np.arange(120, 128) / 40)
    assert np.max(np.abs(forecast - expected)) < 8


def test_seasonal_falls_back_to_trend_before_two_full_seasons():
    series = 50 + RNG.normal(0, 1, 60)  # fewer than 2 x 40 points
    assert len(predictive.forecast_rps(series, horizon_steps=8, seasonal_periods=40)) == 8


def test_replicas_for_rps_scales_with_the_calibrated_capacity():
    # a pod is sized for RPS_PER_POD requests, derated by the safety margin
    per_pod = predictive.RPS_PER_POD / predictive.SAFETY_MARGIN
    assert predictive.replicas_for_rps(per_pod * 5) == 5
    assert predictive.replicas_for_rps(per_pod * 5 + 0.5) == 6


def test_replicas_for_rps_stays_within_bounds():
    assert predictive.replicas_for_rps(0.0) == 2
    assert predictive.replicas_for_rps(10_000.0) == 12


def test_arm_abstains_during_warmup():
    arm = predictive.PredictiveArm(fetch=lambda: [20.0] * 10, clock=Clock())
    assert arm.compute() is None
    assert arm.forecast_rps is None


def test_arm_sizes_replicas_for_the_forecast_peak():
    arm = predictive.PredictiveArm(fetch=rising_fetch([]), clock=Clock(), seasonal_periods=None)
    target = arm.compute()
    assert 7 <= target <= 12  # peak > 100 rps -> at least 100*1.2/20 = 6
    assert arm.forecast_rps > 100
    assert arm.last_run == 1000.0


def test_arm_caches_its_result_for_the_interval():
    calls, clock = [], Clock()
    arm = predictive.PredictiveArm(fetch=rising_fetch(calls), clock=clock, interval=30, seasonal_periods=None)
    first = arm.compute()
    clock.t += 15
    assert arm.compute() == first
    assert len(calls) == 1
    clock.t += 15
    arm.compute()
    assert len(calls) == 2


def test_arm_abstains_when_prometheus_fails():
    def boom():
        raise ConnectionError("prometheus unreachable")

    arm = predictive.PredictiveArm(fetch=boom, clock=Clock())
    assert arm.compute() is None
    assert arm.forecast_rps is None
