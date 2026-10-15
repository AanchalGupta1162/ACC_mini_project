import pytest

import reactive


@pytest.mark.parametrize(
    "pending, cpu, current, expected",
    [
        (2, 10.0, 4, 6),     # pods the scheduler cannot place: add that many
        (0.5, 10.0, 4, 5),   # any unschedulable pod adds at least one
        (0, 160.0, 4, 8),    # twice target utilization: double (HPA rule)
        (0, 90.0, 4, 5),     # just outside the hold band: at least +1
        (0, 85.0, 4, 4),     # inside the ±10% band: hold steady
        (0, 40.0, 8, 4),     # half target utilization: halve
        (0, 0.0, 6, 2),      # idle: clamped to MIN_REPLICAS
        (5, 0.0, 11, 12),    # clamped to MAX_REPLICAS
    ],
)
def test_reactive_target(pending, cpu, current, expected):
    assert reactive.reactive_target(pending, cpu, current) == expected


def test_compute_uses_live_readings(monkeypatch):
    monkeypatch.setattr(reactive, "unschedulable_pods", lambda: 0.0)
    monkeypatch.setattr(reactive, "cpu_utilization_pct", lambda: 160.0)
    assert reactive.compute(current_replicas=3) == 6


def test_compute_abstains_when_prometheus_fails(monkeypatch):
    def boom():
        raise ConnectionError("prometheus unreachable")

    monkeypatch.setattr(reactive, "unschedulable_pods", boom)
    assert reactive.compute(current_replicas=3) is None
