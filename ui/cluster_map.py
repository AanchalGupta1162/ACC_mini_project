"""Live cluster map: nodes as boxes, pods as dots, reserve nodes dashed until promoted."""
import csv
import os
from functools import lru_cache
from pathlib import Path

from cluster_state import build_state
from config import RPS_PER_POD
from story import ARMS, events_from_rows, narrate, verdict
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

NAMESPACE = os.environ.get("NAMESPACE", "default")
DECISION_LOG = os.environ.get("DECISION_LOG", "results/live-decisions.csv")

app = FastAPI(title="JioHotstar autoscaler cluster map")


@lru_cache(maxsize=1)
def _core():
    """Loaded on first request so the page logic can be tested without a cluster."""
    from kubernetes import client
    from kubernetes import config as kube_config

    kube_config.load_kube_config()
    return client.CoreV1Api()


def _node_sort_key(node):
    role_order = {"system": 0, "active": 1, "reserve": 2}
    return (role_order.get(node["role"], 3), node["name"])


def read_cluster():
    core = _core()
    nodes = [
        {
            "name": n.metadata.name,
            "role": (n.metadata.labels or {}).get("role"),
            "unschedulable": bool(n.spec.unschedulable),
        }
        for n in core.list_node().items
    ]
    nodes.sort(key=_node_sort_key)
    pods = []
    for p in core.list_namespaced_pod(NAMESPACE, label_selector="tier=streaming").items:
        conditions = p.status.conditions or []
        pods.append({
            "name": p.metadata.name,
            "node": p.spec.node_name,
            "app": (p.metadata.labels or {}).get("app"),
            "phase": p.status.phase,
            "ready": any(c.type == "Ready" and c.status == "True" for c in conditions),
        })
    pods.sort(key=lambda p: p["name"])
    return nodes, pods


def recent_decisions(path=None, limit=300):
    path = Path(path or DECISION_LOG)
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))[-limit:]


def last_decision(path=None):
    rows = recent_decisions(path, limit=1)
    return rows[-1] if rows else None


def p95_ms():
    try:
        from metrics_source import p95_latency_ms
        return p95_latency_ms()
    except Exception:  # Prometheus down or no traffic yet: show "-", never break the page
        return None


@app.get("/api/state")
def state():
    nodes, pods = read_cluster()
    payload = build_state(nodes, pods)
    rows = recent_decisions()
    decision = rows[-1] if rows else None
    payload["decision"] = decision
    ready = payload["summary"]["app_ready"]
    rps = float(decision["observed_rps"]) if decision and decision.get("observed_rps") else 0.0
    payload["story"] = {
        **narrate(decision),
        "verdict": verdict(rps, ready, RPS_PER_POD),
        "traffic_rps": rps,
        "capacity_rps": ready * RPS_PER_POD,
        "rps_per_pod": RPS_PER_POD,
        "arms": {a: (decision or {}).get(f"{a}_target") or None for a in ARMS},
        "events": events_from_rows(rows)[-8:],
        "p95_ms": p95_ms(),
    }
    return JSONResponse(payload)


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).resolve().parent / "cluster_map.html").read_text()
