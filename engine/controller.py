"""One control-loop cycle: read every arm, arbitrate, then act on pods and nodes."""
import time
from pathlib import Path

from arbiter import decide
from config import ENABLE_COLD_PATH

FAULT_DIR_DEFAULT = Path(__file__).resolve().parent / "faults"


class Controller:
    def __init__(self, cluster, reactive_fn=None, predictive_arm=None, log=None, exporter=None,
                 clock=time.time, fault_dir=FAULT_DIR_DEFAULT, allow_cold=ENABLE_COLD_PATH,
                 rps_fn=None, demand_arm=None):
        self.cluster = cluster
        self.reactive_fn = reactive_fn
        self.predictive_arm = predictive_arm
        self.log = log
        self.exporter = exporter
        self.clock = clock
        self.fault_dir = Path(fault_dir)
        self.allow_cold = allow_cold
        self.rps_fn = rps_fn
        self.demand_arm = demand_arm
        self.last_change = 0.0

    def _run_arm(self, name, fn):
        """Arms are killed live by creating engine/faults/fault-<arm> (demo step 4)."""
        if fn is None:
            return None
        if (self.fault_dir / f"fault-{name}").exists():
            print(f"[{name}] killed by fault injection, abstaining", flush=True)
            return None
        return fn()

    def _observed_rps(self):
        """Logged so forecast accuracy can be scored from the CSV alone; Prometheus keeps
        no history across a restart, and a failed read must never cost us a cycle."""
        if self.rps_fn is None:
            return None
        try:
            return round(self.rps_fn(), 2)
        except Exception as exc:
            print(f"[log] could not read current rps: {exc}", flush=True)
            return None

    def run_cycle(self):
        now = self.clock()
        current = self.cluster.current_replicas()
        reactive = self._run_arm("reactive", (lambda: self.reactive_fn(current)) if self.reactive_fn else None)
        predictive = self._run_arm("predictive", self.predictive_arm.compute if self.predictive_arm else None)
        demand = self._run_arm("demand", self.demand_arm.compute if self.demand_arm else None)
        schedulable, cordoned, promoted = self.cluster.node_counts()

        d = decide(reactive, predictive, current, schedulable, cordoned, promoted, now,
                   self.last_change, allow_cold=self.allow_cold, demand=demand)

        # promote capacity first so new pods land on a schedulable node immediately
        for name in self.cluster.reserve_names(unschedulable=True)[: d.uncordon]:
            self.cluster.set_unschedulable(name, False)
            print(f"[arbiter] promoted warm reserve node {name}", flush=True)
        for _ in range(d.cold_create):
            print("[arbiter] reserve pool empty, building a cold node (slow path)", flush=True)
            print(f"[arbiter] cold node ready: {self.cluster.create_cold_node()}", flush=True)
        if d.scale:
            self.cluster.set_replicas(d.target)
            self.last_change = now
        if d.cordon:
            name = self.cluster.reserve_names(unschedulable=False)[-1]
            self.cluster.set_unschedulable(name, True)
            print(f"[arbiter] returned {name} to the reserve pool", flush=True)
        print(f"[arbiter] {d.reason}", flush=True)

        forecast_rps = getattr(self.predictive_arm, "forecast_rps", None)
        observed_rps = self._observed_rps()
        if self.exporter:
            self.exporter.publish(d, reactive, predictive, forecast_rps, current, schedulable, demand=demand)
        if self.log:
            self.log.write({
                "timestamp": round(now, 3),
                "observed_rps": observed_rps,
                "reactive_target": reactive,
                "predictive_target": predictive,
                "demand_target": demand,
                "forecast_rps": forecast_rps,
                "forecast_made_at": getattr(self.predictive_arm, "last_run", None),
                "current_replicas": current,
                "target": d.target,
                "scaled": d.scale,
                "uncordon": d.uncordon,
                "cordon": d.cordon,
                "cold_create": d.cold_create,
                "schedulable_nodes": schedulable,
                "reason": d.reason,
            })
        return d
