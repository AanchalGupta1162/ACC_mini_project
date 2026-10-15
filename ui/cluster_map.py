"""Live cluster map: nodes as boxes, pods as dots, reserve nodes dashed until promoted."""
import csv
import os
from functools import lru_cache
from pathlib import Path

from cluster_state import build_state
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


def last_decision(path=None):
    path = Path(path or DECISION_LOG)
    if not path.exists():
        return None
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-1] if rows else None


@app.get("/api/state")
def state():
    nodes, pods = read_cluster()
    payload = build_state(nodes, pods)
    payload["decision"] = last_decision()
    return JSONResponse(payload)


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).resolve().parent / "cluster_map.html").read_text()
