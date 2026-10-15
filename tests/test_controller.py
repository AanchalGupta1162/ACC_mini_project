import csv

from controller import Controller
from decision_log import FIELDS, DecisionLog


class FakeCluster:
    def __init__(self, replicas=2, active=2, reserves=("agent-2", "agent-3"), promoted=()):
        self.replicas = replicas
        self.active = active
        self.cordoned = {name: name not in promoted for name in reserves}
        self.calls = []

    def current_replicas(self):
        return self.replicas

    def node_counts(self):
        promoted = sum(1 for cordoned in self.cordoned.values() if not cordoned)
        return self.active + promoted, len(self.cordoned) - promoted, promoted

    def reserve_names(self, unschedulable):
        return sorted(n for n, c in self.cordoned.items() if c == unschedulable)

    def set_unschedulable(self, name, value):
        self.cordoned[name] = value
        self.calls.append(("unschedulable", name, value))

    def set_replicas(self, replicas):
        self.replicas = replicas
        self.calls.append(("replicas", replicas))

    def create_cold_node(self):
        self.active += 1
        self.calls.append(("cold_node",))
        return "k3d-jiohotstar-cold-1"


class FakeArm:
    def __init__(self, target, forecast_rps=None, last_run=None):
        self.target = target
        self.forecast_rps = forecast_rps
        self.last_run = last_run

    def compute(self):
        return self.target


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_scale_up_promotes_a_warm_reserve_before_adding_pods(tmp_path):
    cluster = FakeCluster(replicas=8)
    Controller(cluster, reactive_fn=lambda current: 12, clock=Clock(), fault_dir=tmp_path).run_cycle()
    assert cluster.calls == [("unschedulable", "agent-2", False), ("replicas", 12)]


def test_both_arms_abstaining_touches_nothing(tmp_path):
    cluster = FakeCluster(replicas=12)
    c = Controller(cluster, reactive_fn=lambda current: None, predictive_arm=FakeArm(None),
                   clock=Clock(), fault_dir=tmp_path)
    assert c.run_cycle().target is None
    assert cluster.calls == []


def test_scale_down_returns_the_last_promoted_reserve(tmp_path):
    cluster = FakeCluster(replicas=12, promoted=("agent-2", "agent-3"))
    Controller(cluster, reactive_fn=lambda current: 3, predictive_arm=FakeArm(4),
               clock=Clock(), fault_dir=tmp_path).run_cycle()
    assert cluster.calls == [("replicas", 4), ("unschedulable", "agent-3", True)]


def test_cold_path_builds_a_node_when_the_pool_is_empty(tmp_path):
    cluster = FakeCluster(replicas=12, active=2, reserves=())
    Controller(cluster, reactive_fn=lambda current: 12, clock=Clock(), fault_dir=tmp_path,
               allow_cold=True).run_cycle()
    assert ("cold_node",) in cluster.calls


def test_cooldown_applies_across_cycles(tmp_path):
    cluster, clock = FakeCluster(replicas=3), Clock()
    targets = iter([6, 8])
    controller = Controller(cluster, reactive_fn=lambda current: next(targets), clock=clock, fault_dir=tmp_path)
    controller.run_cycle()
    clock.t += 15
    controller.run_cycle()
    assert cluster.calls == [("replicas", 6)]


def test_fault_file_kills_an_arm_live(tmp_path):
    (tmp_path / "fault-reactive").touch()
    cluster = FakeCluster(replicas=2)
    Controller(cluster, reactive_fn=lambda current: 12, predictive_arm=FakeArm(5),
               clock=Clock(), fault_dir=tmp_path).run_cycle()
    assert cluster.calls == [("replicas", 5)]


def test_cycle_is_logged_to_csv(tmp_path):
    path = tmp_path / "decisions.csv"
    log = DecisionLog(path)
    arm = FakeArm(5, forecast_rps=83.5, last_run=990.0)
    Controller(FakeCluster(), predictive_arm=arm, log=log, clock=Clock(), fault_dir=tmp_path).run_cycle()
    log.close()
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames == FIELDS
    assert len(rows) == 1
    assert (rows[0]["target"], rows[0]["scaled"], rows[0]["forecast_rps"]) == ("5", "True", "83.5")
    assert rows[0]["reactive_target"] == ""


def test_decision_log_appends_without_repeating_the_header(tmp_path):
    path = tmp_path / "decisions.csv"
    for _ in range(2):
        log = DecisionLog(path)
        log.write({"timestamp": 1, "reason": "x"})
        log.close()
    lines = path.read_text().splitlines()
    assert lines[0].startswith("timestamp,")
    assert len(lines) == 3


def test_cycle_logs_the_observed_rps_so_mape_survives_prometheus_restarts(tmp_path):
    path = tmp_path / "decisions.csv"
    log = DecisionLog(str(path))
    c = Controller(FakeCluster(), reactive_fn=lambda cur: 3, predictive_arm=FakeArm(4, forecast_rps=55.5),
                   log=log, clock=lambda: 1000.0, rps_fn=lambda: 42.25)
    c.run_cycle()
    log.close()
    row = list(csv.DictReader(open(path)))[0]
    assert row["observed_rps"] == "42.25"


def test_cycle_logs_a_blank_observed_rps_when_the_metric_read_fails(tmp_path):
    path = tmp_path / "decisions.csv"
    log = DecisionLog(str(path))

    def boom():
        raise RuntimeError("prometheus down")

    c = Controller(FakeCluster(), reactive_fn=lambda cur: 3, log=log, clock=lambda: 1000.0, rps_fn=boom)
    c.run_cycle()
    log.close()
    row = list(csv.DictReader(open(path)))[0]
    assert row["observed_rps"] == ""
