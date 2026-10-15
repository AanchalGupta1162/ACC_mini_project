"""playback-api: the one real autoscaled service.

CPU-bound /play endpoint so load is visible in metrics, a deliberate warm-up delay so
scaling lag is visible, and Prometheus counters so the predictive arm can forecast RPS.
The same image also runs the cosmetic mock services (gateway, catalog) with no CPU burn.
"""
import os
import time

from flask import Flask, Response, jsonify
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

SERVICE_NAME = os.environ.get("SERVICE_NAME", "playback-api")
WORK_ITERATIONS = int(os.environ.get("WORK_ITERATIONS", "120000"))
STARTUP_DELAY_SECONDS = float(os.environ.get("STARTUP_DELAY_SECONDS", "20"))
START_TIME = time.monotonic()

REQUESTS = Counter("playback_requests_total", "Requests served", ["service", "endpoint"])
LATENCY = Histogram(
    "playback_request_seconds",
    "Request latency in seconds",
    ["service"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
READY = Gauge("playback_ready", "1 once the pod has finished warming up", ["service"])

app = Flask(__name__)


def _is_ready():
    return time.monotonic() - START_TIME >= STARTUP_DELAY_SECONDS


@app.route("/play")
def play():
    started = time.perf_counter()
    x = 0
    for _ in range(WORK_ITERATIONS):  # simulate transcoding/manifest work
        x += 1
    LATENCY.labels(SERVICE_NAME).observe(time.perf_counter() - started)
    REQUESTS.labels(SERVICE_NAME, "/play").inc()
    return "ok", 200


@app.route("/healthz")
def healthz():
    # models app warm-up (JVM, connection pools): the pod exists but cannot serve yet
    READY.labels(SERVICE_NAME).set(1 if _is_ready() else 0)
    if not _is_ready():
        return "warming up", 503
    return "ok", 200


@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route("/")
def index():
    return jsonify(service=SERVICE_NAME, ready=_is_ready(), work_iterations=WORK_ITERATIONS)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
