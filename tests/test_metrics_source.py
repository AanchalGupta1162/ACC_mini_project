import pytest

import metrics_source as ms


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def fake_get(payload, captured):
    def _get(url, params, timeout):
        captured.update(url=url, params=params)
        return FakeResponse(payload)
    return _get


def test_instant_query_returns_first_sample(monkeypatch):
    captured = {}
    payload = {"data": {"result": [{"value": [1700000000.0, "3.5"]}]}}
    monkeypatch.setattr(ms.requests, "get", fake_get(payload, captured))
    assert ms.instant("up") == 3.5
    assert captured["url"].endswith("/api/v1/query")


def test_instant_query_empty_is_zero(monkeypatch):
    monkeypatch.setattr(ms.requests, "get", fake_get({"data": {"result": []}}, {}))
    assert ms.instant("up") == 0.0


def test_instant_query_can_require_data(monkeypatch):
    monkeypatch.setattr(ms.requests, "get", fake_get({"data": {"result": []}}, {}))
    with pytest.raises(ValueError):
        ms.instant("up", allow_empty=False)


def test_range_query_uses_numeric_timestamps(monkeypatch):
    captured = {}
    payload = {"data": {"result": [{"values": [[100.0, "1.5"], [115.0, "2.0"]]}]}}
    monkeypatch.setattr(ms.requests, "get", fake_get(payload, captured))
    points = ms.range_query("x", minutes=30, step_seconds=15, end=10_000.0)
    assert points == [(100.0, 1.5), (115.0, 2.0)]
    assert captured["params"]["start"] == 10_000.0 - 1800
    assert captured["params"]["end"] == 10_000.0


def test_range_query_empty_is_empty_list(monkeypatch):
    monkeypatch.setattr(ms.requests, "get", fake_get({"data": {"result": []}}, {}))
    assert ms.range_query("x", minutes=1) == []


def test_queries_target_the_autoscaled_service_only():
    assert 'container="playback-api"' in ms.CPU_UTILIZATION_QUERY
    assert "kube_pod_container_resource_requests" in ms.CPU_UTILIZATION_QUERY
    assert "playback_requests_total" in ms.RPS_QUERY
    assert "kube_pod_status_unschedulable" in ms.UNSCHEDULABLE_QUERY


def test_rps_history_returns_values_only(monkeypatch):
    payload = {"data": {"result": [{"values": [[100.0, "12"], [115.0, "15"]]}]}}
    monkeypatch.setattr(ms.requests, "get", fake_get(payload, {}))
    assert ms.rps_history() == [12.0, 15.0]


def test_p95_latency_is_converted_to_milliseconds(monkeypatch):
    payload = {"data": {"result": [{"value": [1.0, "0.032"]}]}}
    monkeypatch.setattr(ms.requests, "get", fake_get(payload, {}))
    assert ms.p95_latency_ms() == pytest.approx(32.0)


def test_p95_latency_is_unknown_when_prometheus_returns_nan(monkeypatch):
    payload = {"data": {"result": [{"value": [1.0, "NaN"]}]}}
    monkeypatch.setattr(ms.requests, "get", fake_get(payload, {}))
    assert ms.p95_latency_ms() is None
