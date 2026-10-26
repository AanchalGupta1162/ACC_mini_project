from cluster_state import build_state


def node(name, role, unschedulable=False):
    return {"name": name, "role": role, "unschedulable": unschedulable}


def pod(name, node_name, app="playback-api", phase="Running", ready=True):
    return {"name": name, "node": node_name, "app": app, "phase": phase, "ready": ready}


NODES = [
    node("server-0", "system"),
    node("agent-0", "active"),
    node("agent-1", "active"),
    node("agent-2", "reserve", unschedulable=True),
    node("agent-3", "reserve", unschedulable=True),
]


def test_pods_are_grouped_onto_their_nodes():
    state = build_state(NODES, [pod("p1", "agent-0"), pod("p2", "agent-0"), pod("p3", "agent-1")])
    by_name = {n["name"]: n for n in state["nodes"]}
    assert [p["name"] for p in by_name["agent-0"]["pods"]] == ["p1", "p2"]
    assert len(by_name["agent-1"]["pods"]) == 1


def test_cordoned_reserve_is_a_warm_reserve():
    state = build_state(NODES, [])
    by_name = {n["name"]: n for n in state["nodes"]}
    assert by_name["agent-2"]["state"] == "warm-reserve"
    assert by_name["agent-0"]["state"] == "active"
    assert by_name["server-0"]["state"] == "system"


def test_uncordoned_reserve_shows_as_promoted():
    nodes = [node("agent-2", "reserve", unschedulable=False)]
    assert build_state(nodes, [])["nodes"][0]["state"] == "promoted"


def test_cold_built_node_is_flagged():
    nodes = [node("k3d-cold-42-0", "active")]
    assert build_state(nodes, [])["nodes"][0]["state"] == "cold"


def test_unplaced_pods_are_reported_as_pending():
    state = build_state(NODES, [pod("p1", "agent-0"), pod("p9", None, phase="Pending", ready=False)])
    assert [p["name"] for p in state["pending"]] == ["p9"]


def test_summary_counts_replicas_ready_and_schedulable_nodes():
    pods = [pod("p1", "agent-0"), pod("p2", "agent-1", ready=False, phase="Pending")]
    summary = build_state(NODES, pods)["summary"]
    assert summary["pods"] == 2
    assert summary["ready"] == 1
    assert summary["schedulable_nodes"] == 2  # two active agents; reserves are cordoned


def test_summary_counts_only_the_autoscaled_app_for_capacity():
    pods = [pod("p1", "agent-0"), pod("m1", "agent-0", app="catalog-mock"), pod("m2", "agent-1", app="catalog-mock")]
    summary = build_state(NODES, pods)["summary"]
    assert summary["app_pods"] == 1 and summary["app_ready"] == 1
