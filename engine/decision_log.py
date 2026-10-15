"""Append-only CSV with one row per cycle. The experiments read this."""
import csv
import os

FIELDS = [
    "timestamp", "observed_rps", "reactive_target", "predictive_target", "forecast_rps",
    "forecast_made_at",
    "current_replicas", "target", "scaled", "uncordon", "cordon", "cold_create",
    "schedulable_nodes", "reason",
]


class DecisionLog:
    def __init__(self, path):
        is_new = not os.path.exists(path) or os.path.getsize(path) == 0
        self._file = open(path, "a", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=FIELDS)
        if is_new:
            self._writer.writeheader()

    def write(self, row):
        self._writer.writerow(row)
        self._file.flush()  # the cluster map and experiments tail this live

    def close(self):
        self._file.close()
