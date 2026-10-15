"""Every Prometheus read the engine makes. Both arms use these; nothing here writes."""
import time

import requests

from config import DEPLOYMENT, HISTORY_MINUTES, NAMESPACE, PROM_URL, STEP_SECONDS

_APP = f'namespace="{NAMESPACE}",container="{DEPLOYMENT}"'

# requests/second served by the autoscaled service (what the predictive arm forecasts)
RPS_QUERY = f'sum(rate(playback_requests_total{{service="{DEPLOYMENT}"}}[1m]))'
# CPU as a percentage of the pods' CPU request, like the HPA
CPU_UTILIZATION_QUERY = (
    f"100 * sum(rate(container_cpu_usage_seconds_total{{{_APP}}}[1m]))"
    f' / sum(kube_pod_container_resource_requests{{{_APP},resource="cpu"}})'
)
# pods the scheduler cannot place (phase=Pending would also count ContainerCreating)
UNSCHEDULABLE_QUERY = (
    f'sum(kube_pod_status_unschedulable{{namespace="{NAMESPACE}",pod=~"{DEPLOYMENT}-.*"}}) or vector(0)'
)


def instant(promql, allow_empty=True):
    r = requests.get(f"{PROM_URL}/api/v1/query", params={"query": promql}, timeout=5)
    r.raise_for_status()
    result = r.json()["data"]["result"]
    if not result:
        if allow_empty:
            return 0.0
        raise ValueError(f"no data for: {promql}")
    return float(result[0]["value"][1])


def range_query(promql, minutes, step_seconds=STEP_SECONDS, end=None):
    end = time.time() if end is None else end
    params = {"query": promql, "start": end - minutes * 60, "end": end, "step": step_seconds}
    r = requests.get(f"{PROM_URL}/api/v1/query_range", params=params, timeout=10)
    r.raise_for_status()
    result = r.json()["data"]["result"]
    return [(float(ts), float(v)) for ts, v in result[0]["values"]] if result else []


def unschedulable_pods():
    return instant(UNSCHEDULABLE_QUERY)


def cpu_utilization_pct():
    # no data means "unknown", not "idle": raise so the reactive arm abstains
    return instant(CPU_UTILIZATION_QUERY, allow_empty=False)


def current_rps():
    return instant(RPS_QUERY)


def rps_history(minutes=HISTORY_MINUTES, step_seconds=STEP_SECONDS):
    return [v for _, v in range_query(RPS_QUERY, minutes, step_seconds)]
