from config import RPS_PER_POD
from story import capacity_rps, events_from_rows, narrate, verdict, winning_arms


def row(**kw):
    base = {"timestamp": "100", "observed_rps": "40", "reactive_target": "3", "predictive_target": "",
            "demand_target": "5", "current_replicas": "4", "target": "5", "scaled": "True",
            "uncordon": "0", "cordon": "0", "cold_create": "0", "reason": "scale 4->5"}
    base.update(kw)
    return base


def test_capacity_is_ready_pods_times_calibrated_rate():
    assert capacity_rps(5, RPS_PER_POD) == 5 * RPS_PER_POD


def test_verdict_ok_when_capacity_covers_traffic():
    assert verdict(2 * RPS_PER_POD - 1, 2, RPS_PER_POD)["ok"] is True


def test_verdict_behind_when_traffic_exceeds_ready_capacity():
    v = verdict(2 * RPS_PER_POD + 5, 2, RPS_PER_POD)
    assert v["ok"] is False
    assert "warming" in v["text"] or "behind" in v["text"].lower()


def test_winning_arm_is_the_one_matching_the_final_target_and_ties_are_listed():
    assert winning_arms(row(demand_target="5", reactive_target="5", target="5")) == ["reactive", "demand"]


def test_abstaining_arm_is_never_the_winner():
    assert winning_arms(row(predictive_target="", demand_target="5", reactive_target="3", target="5")) == ["demand"]


def test_narrate_scaling_up_names_the_winning_arm_and_both_pod_counts():
    n = narrate(row(current_replicas="4", target="9", demand_target="9", observed_rps="80"))
    assert n["phase"] == "scaling-up"
    assert "4" in n["headline"] and "9" in n["headline"]
    assert "demand" in n["detail"]


def test_narrate_scaling_down_and_steady():
    assert narrate(row(current_replicas="9", target="4", scaled="True"))["phase"] == "scaling-down"
    assert narrate(row(current_replicas="4", target="4", scaled="False"))["phase"] == "steady"


def test_narrate_without_a_decision_says_engine_is_not_running():
    assert narrate(None)["phase"] == "waiting"


def test_events_include_only_cycles_that_changed_something_newest_last():
    rows = [row(timestamp="1", scaled="False", target="4"),
            row(timestamp="2", current_replicas="4", target="9"),
            row(timestamp="3", scaled="False", uncordon="1"),
            row(timestamp="4", scaled="False", cold_create="1")]
    ev = events_from_rows(rows)
    assert [e["t"] for e in ev] == [2.0, 3.0, 4.0]
    assert "4" in ev[0]["text"] and "9" in ev[0]["text"]
    assert "reserve" in ev[1]["text"].lower()
    assert "cold" in ev[2]["text"].lower()
