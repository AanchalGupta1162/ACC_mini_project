import math

import pytest

from config import DEMAND_MIN_POINTS, MAX_REPLICAS, MIN_REPLICAS, RPS_PER_POD, SAFETY_MARGIN
from demand import DemandArm, demand_target, slope_per_second

STEP = 15


def pods_for(rps):
    return math.ceil(round(rps * SAFETY_MARGIN / RPS_PER_POD, 6))


def test_slope_of_a_ramp_is_exact():
    assert slope_per_second([10, 20, 30, 40], STEP) == 10 / STEP


def test_slope_of_flat_series_is_zero():
    assert slope_per_second([50, 50, 50], STEP) == pytest.approx(0, abs=1e-9)


def test_too_few_points_abstains():
    assert demand_target([50.0] * (DEMAND_MIN_POINTS - 1), STEP, lead_seconds=45) is None
    assert demand_target([], STEP, lead_seconds=45) is None


def test_steady_load_sizes_to_current_rate():
    assert demand_target([60.0] * 6, STEP, lead_seconds=45) == max(MIN_REPLICAS, pods_for(60.0))


def test_rising_load_is_sized_for_where_it_will_be_after_the_startup_lead():
    series = [5, 10, 15, 20, 25, 30]  # +5 rps per 15 s step, well below the replica cap
    lead = 45
    projected = 30 + (5 / STEP) * lead
    expected = max(MIN_REPLICAS, min(MAX_REPLICAS, pods_for(projected)))
    assert demand_target(series, STEP, lead_seconds=lead) == expected
    assert expected > demand_target([30.0] * 6, STEP, lead_seconds=lead)


def test_falling_load_is_not_extrapolated_downwards():
    # a falling rate must not make the arm ask for fewer pods than the current rate needs
    assert demand_target([110, 100, 90, 80, 70, 60], STEP, lead_seconds=45) == max(MIN_REPLICAS, pods_for(60.0))


def test_target_is_clamped_to_bounds():
    assert demand_target([5000.0] * 6, STEP, lead_seconds=45) == MAX_REPLICAS
    assert demand_target([0.0] * 6, STEP, lead_seconds=45) == MIN_REPLICAS


def test_non_finite_samples_abstain():
    assert demand_target([10, float("nan"), 30, 40], STEP, lead_seconds=45) is None


def test_arm_abstains_instead_of_raising_when_prometheus_fails():
    def boom():
        raise RuntimeError("prometheus down")
    assert DemandArm(fetch=boom).compute() is None


def test_arm_uses_the_injected_series():
    assert DemandArm(fetch=lambda: [60.0] * 6).compute() == max(MIN_REPLICAS, pods_for(60.0))
