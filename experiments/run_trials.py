#!/usr/bin/env python3
"""Scaling-lag and variance experiments: repeat the spike per mode and record what it cost users.

Modes: off (fixed replicas), hpa (Kubernetes' own autoscaler), reactive, predictive,
demand (demand arm only: no history to leak from earlier trials), both (reactive+predictive),
full (both + demand arm). Workloads: spike (4.5 min, forecaster cannot warm up)
or matchday (30 min repeating cycle, forecaster can).
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from config import DEPLOYMENT, NAMESPACE  # noqa: E402
from exp_metrics import mean_std, pod_seconds, time_to_capacity  # noqa: E402
from metrics_source import range_query  # noqa: E402

STEP = 15
SPIKE_START_OFFSET = 60  # spike.js holds the baseline for 1 minute first
MODES = ["off", "hpa", "hpa-tuned", "reactive", "predictive", "demand", "both", "full"]
HPA_FILES = {"hpa": "hpa.yaml", "hpa-tuned": "hpa-tuned.yaml"}  # tuned = 50% CPU target
WORKLOADS = {"spike": "spike.js", "matchday": "matchday.js"}
PENDING_Q = (f'sum(kube_pod_status_phase{{phase="Pending",namespace="{NAMESPACE}",pod=~"{DEPLOYMENT}-.*"}})'
             " or vector(0)")
UNAVAILABLE_Q = (f'sum(kube_deployment_status_replicas_unavailable{{namespace="{NAMESPACE}",'
                 f'deployment="{DEPLOYMENT}"}}) or vector(0)')
AVAILABLE_Q = f'sum(kube_deployment_status_replicas_available{{namespace="{NAMESPACE}",deployment="{DEPLOYMENT}"}})'
FIELDS = ["workload", "mode", "trial", "start", "end", "pending_pod_seconds", "unavailable_pod_seconds",
          "time_to_capacity_s", "p95_ms", "failed_pct"]


def run_trial(mode, trial, results, required, settle, workload="spike"):
    subprocess.run([str(ROOT / "cluster" / "reset.sh")], check=True, stdout=subprocess.DEVNULL)
    time.sleep(settle)

    engine = None
    if mode in HPA_FILES:
        subprocess.run(["kubectl", "apply", "-f", str(ROOT / "k8s" / HPA_FILES[mode])],
                       check=True, stdout=subprocess.DEVNULL)
    elif mode != "off":
        engine = subprocess.Popen(
            [str(ROOT / ".venv/bin/python"), str(ROOT / "engine" / "main.py"), "--mode", mode,
             "--log-csv", str(results / f"{workload}-{mode}-{trial}-decisions.csv")],
            stdout=subprocess.DEVNULL)
        time.sleep(5)
        if engine.poll() is not None:  # e.g. port 9900 already held by another engine
            raise RuntimeError(f"engine ({mode}) exited at start-up with code {engine.returncode}; "
                               "is `make engine` or an old engine still running?")

    summary_path = results / f"{workload}-{mode}-{trial}-k6.json"
    start = time.time()
    try:
        subprocess.run(["k6", "run", "--quiet", str(ROOT / "loadtest" / WORKLOADS[workload])],
                       env={**os.environ, "SUMMARY_JSON": str(summary_path)}, check=True)
        if engine and engine.poll() is not None:
            raise RuntimeError(f"engine ({mode}) died during the trial; this run is invalid")
    finally:
        if engine:
            engine.terminate()
            engine.wait(timeout=30)
        if mode in HPA_FILES:
            subprocess.run(["kubectl", "delete", "-f", str(ROOT / "k8s" / HPA_FILES[mode]), "--ignore-not-found"],
                           check=False, stdout=subprocess.DEVNULL)
    end = time.time()
    time.sleep(2 * STEP)  # let Prometheus scrape the tail of the run

    minutes = (end - start) / 60
    k6 = json.loads(summary_path.read_text())["metrics"]
    return {
        "workload": workload, "mode": mode, "trial": trial, "start": round(start), "end": round(end),
        "pending_pod_seconds": pod_seconds(range_query(PENDING_Q, minutes, STEP, end), STEP),
        "unavailable_pod_seconds": pod_seconds(range_query(UNAVAILABLE_Q, minutes, STEP, end), STEP),
        "time_to_capacity_s": None if workload != "spike" else time_to_capacity(range_query(AVAILABLE_Q, minutes, STEP, end),
                                               required, start + SPIKE_START_OFFSET),
        "p95_ms": round(k6["http_req_duration"]["values"]["p(95)"]),
        "failed_pct": round(100 * k6["http_req_failed"]["values"]["rate"], 2),
    }


def fmt(mean, std):
    return f"{'n/a':>20}" if mean is None else f"{mean:>11.1f} ± {std:<6.1f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=["hpa", "reactive", "both", "full"])
    parser.add_argument("--workload", choices=list(WORKLOADS), default="spike")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--required-replicas", type=int, default=10, help="replicas needed to serve PEAK_RPS")
    parser.add_argument("--settle", type=int, default=45, help="idle seconds after reset before each trial")
    parser.add_argument("--results", type=Path, default=ROOT / "results")
    args = parser.parse_args()

    args.results.mkdir(parents=True, exist_ok=True)
    out = args.results / "trials.csv"
    is_new = not out.exists() or out.stat().st_size == 0
    if not is_new and out.read_text().splitlines()[0] != ",".join(FIELDS):
        sys.exit(f"{out} has a different header (older run?). Use a fresh --results directory.")
    rows = []
    with out.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        for trial in range(1, args.trials + 1):
            # interleaved, and the order flips every other trial, so laptop drift and any
            # order effect (the first pair hinted at one) do not favour a single mode
            for mode in (args.modes if trial % 2 else args.modes[::-1]):
                print(f"=== {mode} trial {trial}/{args.trials} ===", flush=True)
                row = run_trial(mode, trial, args.results, args.required_replicas, args.settle, args.workload)
                writer.writerow(row)
                f.flush()
                rows.append(row)
                print(f"    {row}", flush=True)

    keys = ["pending_pod_seconds", "unavailable_pod_seconds", "time_to_capacity_s", "p95_ms", "failed_pct"]
    print(f"\n{'mode':<11}" + "".join(f"{k:>20}" for k in keys))
    for mode in args.modes:
        mine = [r for r in rows if r["mode"] == mode]
        print(f"{mode:<11}" + "".join(fmt(*mean_std([r[k] for r in mine])) for k in keys))


if __name__ == "__main__":
    main()
