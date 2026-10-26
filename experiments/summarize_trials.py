#!/usr/bin/env python3
"""Summarize a trials.csv: mean +/- sd per mode, plus replica-seconds (cost) read from Prometheus.

Replica-seconds is the integral of the Deployment's requested replicas over each trial, so a mode
that wins on failures only by pinning the maximum is visible as a cost.
"""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from config import DEPLOYMENT, NAMESPACE  # noqa: E402
from exp_metrics import pod_seconds, summary_rows  # noqa: E402
from metrics_source import range_query  # noqa: E402

STEP = 15
SPEC_Q = f'sum(kube_deployment_spec_replicas{{namespace="{NAMESPACE}",deployment="{DEPLOYMENT}"}})'
KEYS = ["failed_pct", "p95_ms", "unavailable_pod_seconds", "pending_pod_seconds", "replica_seconds"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trials_csv", type=Path)
    args = parser.parse_args()
    rows = list(csv.DictReader(args.trials_csv.open()))
    for r in rows:
        start, end = float(r["start"]), float(r["end"])
        points = range_query(SPEC_Q, (end - start) / 60, STEP, end)
        r["replica_seconds"] = round(pod_seconds(points, STEP)) if points else ""
    print(f"| mode | n | {' | '.join(KEYS)} |")
    print("|" + "---|" * (len(KEYS) + 2))
    for row in summary_rows(rows, KEYS):
        cells = [f"{m:.1f} ± {s:.1f}" if m is not None else "n/a" for m, s in (row[k] for k in KEYS)]
        print(f"| {row['mode']} | {row['n']} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
