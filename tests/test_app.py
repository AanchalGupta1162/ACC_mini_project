import app as app_module


def test_play_returns_ok_and_counts(monkeypatch):
    monkeypatch.setattr(app_module, "WORK_ITERATIONS", 10)
    response = app_module.app.test_client().get("/play")
    assert response.status_code == 200
    assert response.data == b"ok"


def test_healthz_is_not_ready_during_warmup(monkeypatch):
    monkeypatch.setattr(app_module, "STARTUP_DELAY_SECONDS", 10_000)
    assert app_module.app.test_client().get("/healthz").status_code == 503


def test_healthz_ready_after_warmup(monkeypatch):
    monkeypatch.setattr(app_module, "STARTUP_DELAY_SECONDS", 0)
    assert app_module.app.test_client().get("/healthz").status_code == 200


def test_metrics_exposes_request_counter(monkeypatch):
    monkeypatch.setattr(app_module, "WORK_ITERATIONS", 1)
    client = app_module.app.test_client()
    client.get("/play")
    body = client.get("/metrics").data.decode()
    assert "playback_requests_total" in body
