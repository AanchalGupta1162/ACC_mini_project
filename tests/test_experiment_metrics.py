import pytest

from exp_metrics import (actuals_from_rows, align_forecasts, count_events, forecasts_from_rows,
                         mape, mean_std, pod_seconds, time_to_capacity)


def test_pod_seconds_integrates_a_series():
    assert pod_seconds([(0, 0), (15, 2), (30, 1)], 15) == 45


def test_time_to_capacity_measures_from_the_spike_start():
    assert time_to_capacity([(0, 2), (60, 4), (75, 10), (90, 12)], required=10, start_ts=60) == 15


def test_time_to_capacity_is_none_when_capacity_never_arrives():
    assert time_to_capacity([(0, 12), (60, 2), (75, 3)], required=10, start_ts=60) is None


def test_mean_std():
    assert mean_std([1, 2, 3]) == (2.0, 1.0)
    assert mean_std([5]) == (5.0, 0.0)
    assert mean_std([None, 4, 6]) == (5.0, pytest.approx(1.41421356))
    assert mean_std([]) == (None, None)


def test_forecasts_are_deduplicated_by_the_time_they_were_made():
    rows = [
        {"forecast_rps": "50.0", "forecast_made_at": "100.0"},
        {"forecast_rps": "50.0", "forecast_made_at": "100.0"},  # same forecast, logged again
        {"forecast_rps": "", "forecast_made_at": ""},           # arm abstained
        {"forecast_rps": "70.0", "forecast_made_at": "130.0"},
    ]
    assert forecasts_from_rows(rows) == [(100.0, 50.0), (130.0, 70.0)]


def test_forecasts_are_paired_with_what_actually_happened_at_the_horizon():
    forecasts = [(100.0, 50.0), (130.0, 70.0), (500.0, 10.0)]
    actuals = [(215.0, 44.0), (250.0, 70.0)]
    assert align_forecasts(forecasts, actuals, horizon_seconds=120, tolerance_seconds=15) == [
        (50.0, 44.0), (70.0, 70.0)
    ]


def test_mape():
    assert mape([(110, 100), (90, 100)]) == pytest.approx(10.0)


def test_mape_ignores_near_zero_actuals():
    assert mape([(1.0, 0.0), (0.9, 1.0)]) == pytest.approx(10.0)


def test_mape_raises_when_nothing_is_usable():
    with pytest.raises(ValueError):
        mape([(1.0, 0.0)])


def test_count_events_separates_scaling_from_node_moves():
    rows = [
        {"scaled": "True", "target": "6", "current_replicas": "2", "uncordon": "1", "cordon": "0", "cold_create": "0"},
        {"scaled": "True", "target": "2", "current_replicas": "6", "uncordon": "0", "cordon": "1", "cold_create": "0"},
        {"scaled": "False", "target": "", "current_replicas": "2", "uncordon": "0", "cordon": "0", "cold_create": "1"},
    ]
    assert count_events(rows) == {"scale_up": 1, "scale_down": 1, "uncordon": 1, "cordon": 1, "cold_create": 1}


def test_actuals_from_rows_reads_the_logged_traffic():
    rows = [{"timestamp": "100.0", "observed_rps": "10.5"},
            {"timestamp": "115.0", "observed_rps": "12.0"}]
    assert actuals_from_rows(rows) == [(100.0, 10.5), (115.0, 12.0)]


def test_actuals_from_rows_skips_cycles_with_no_reading():
    rows = [{"timestamp": "100.0", "observed_rps": ""},
            {"timestamp": "115.0", "observed_rps": "12.0"},
            {"timestamp": "130.0"}]
    assert actuals_from_rows(rows) == [(115.0, 12.0)]


def test_summary_rows_groups_by_mode_and_reports_n():
    from exp_metrics import summary_rows
    rows = [
        {"mode": "hpa", "p95_ms": "1000", "failed_pct": "5"},
        {"mode": "hpa", "p95_ms": "1200", "failed_pct": "7"},
        {"mode": "full", "p95_ms": "10", "failed_pct": "0"},
    ]
    out = {r["mode"]: r for r in summary_rows(rows, ["p95_ms", "failed_pct"])}
    assert out["hpa"]["n"] == 2
    assert out["hpa"]["p95_ms"] == (1100.0, pytest.approx(141.42, abs=0.01))
    assert out["full"]["n"] == 1 and out["full"]["failed_pct"] == (0.0, 0.0)


def test_summary_rows_ignores_missing_values():
    from exp_metrics import summary_rows
    rows = [{"mode": "hpa", "time_to_capacity_s": ""}, {"mode": "hpa", "time_to_capacity_s": "30"}]
    assert summary_rows(rows, ["time_to_capacity_s"])[0]["time_to_capacity_s"] == (30.0, 0.0)
