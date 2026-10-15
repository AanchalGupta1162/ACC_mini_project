#!/usr/bin/env python3
"""Reserve-pool promotion latency: the headline number.

Three paths to one extra pod being Ready:
  active : a node that is already live and has room
  warm   : uncordon a pre-pulled reserve node  (the engine's fast path)
  cold   : build a brand-new k3d node and pull the image  (what happens with no reserves)
"""
import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from config import CLUSTER_NAME, DEPLOYMENT, NAMESPACE, PODS_PER_NODE  # noqa: E402
from exp_metrics import mean_std  # noqa: E402
from reserve_pool import ClusterAdapter  # noqa: E402

ACTIVE_NODES = 2


def app_pods(core):
    pods = core.list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT}").items
    return [p for p in pods if p.metadata.deletion_timestamp is None]


def is_ready(pod):
    return any(c.type == "Ready" and c.status == "True" for c in (pod.status.conditions or []))


def wait_for(predicate, timeout=300, poll=0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(poll)
    raise TimeoutError("condition not met in time")


def reset():
    subprocess.run([str(ROOT / "cluster" / "reset.sh")], check=True, stdout=subprocess.DEVNULL)


def fill_active_nodes(cluster, core):
    """Scale so every active slot is taken and exactly one pod has nowhere to go."""
    full = ACTIVE_NODES * PODS_PER_NODE
    cluster.set_replicas(full + 1)
    wait_for(lambda: sum(map(is_ready, app_pods(core))) == full
             and sum(1 for p in app_pods(core) if p.spec.node_name is None) == 1)


def measure_active(cluster, core):
    reset()
    before = {p.metadata.name for p in app_pods(core)}
    t0 = time.time()
    cluster.set_replicas(len(before) + 1)  # active nodes still have free slots
    wait_for(lambda: any(p.metadata.name not in before and is_ready(p) for p in app_pods(core)))
    return time.time() - t0


def measure_warm(cluster, core, node):
    reset()
    fill_active_nodes(cluster, core)
    t0 = time.time()
    cluster.set_unschedulable(node, False)  # the engine's promotion action
    wait_for(lambda: any(p.spec.node_name == node and is_ready(p) for p in app_pods(core)))
    return time.time() - t0


def measure_cold(cluster, core):
    reset()
    # take the reserve pool out of the picture so the only option is building a node
    reserves = cluster.reserve_names(unschedulable=True)
    for n in reserves:
        subprocess.run(["kubectl", "label", "node", n, "role=parked", "--overwrite"],
                       check=True, stdout=subprocess.DEVNULL)
    try:
        fill_active_nodes(cluster, core)
        t0 = time.time()
        node = cluster.create_cold_node()
        wait_for(lambda: any(p.spec.node_name == node and is_ready(p) for p in app_pods(core)))
        elapsed = time.time() - t0
    finally:
        for n in reserves:
            subprocess.run(["kubectl", "label", "node", n, "role=reserve", "--overwrite"],
                           check=True, stdout=subprocess.DEVNULL)
    return elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--skip-cold", action="store_true", help="cold builds are slow; skip them")
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "promotion_latency.csv")
    args = parser.parse_args()

    cluster = ClusterAdapter()
    core = cluster.core
    rows = []
    for repeat in range(1, args.repeats + 1):
        rows.append({"path": "active", "node": "-", "repeat": repeat,
                     "seconds": round(measure_active(cluster, core), 2)})
        print(rows[-1], flush=True)
        for node in cluster.reserve_names(unschedulable=True):
            rows.append({"path": "warm", "node": node, "repeat": repeat,
                         "seconds": round(measure_warm(cluster, core, node), 2)})
            print(rows[-1], flush=True)
        if not args.skip_cold:
            rows.append({"path": "cold", "node": "built", "repeat": repeat,
                         "seconds": round(measure_cold(cluster, core), 2)})
            print(rows[-1], flush=True)
    reset()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["path", "node", "repeat", "seconds"])
        writer.writeheader()
        writer.writerows(rows)
    print()
    for path in ("active", "warm", "cold"):
        mean, std = mean_std([r["seconds"] for r in rows if r["path"] == path])
        if mean is not None:
            print(f"{path:<8} {mean:6.2f}s ± {std:.2f}s   (n={sum(1 for r in rows if r['path'] == path)})")


if __name__ == "__main__":
    main()
