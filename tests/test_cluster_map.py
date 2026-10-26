import cluster_map


def test_last_decision_returns_the_most_recent_row(tmp_path):
    log = tmp_path / "decisions.csv"
    log.write_text("timestamp,target,reason\n1,4,first cycle\n2,6,second cycle\n")
    assert cluster_map.last_decision(log)["reason"] == "second cycle"


def test_last_decision_is_none_without_a_log(tmp_path):
    assert cluster_map.last_decision(tmp_path / "missing.csv") is None


def test_last_decision_is_none_when_the_engine_has_not_written_a_row_yet(tmp_path):
    log = tmp_path / "decisions.csv"
    log.write_text("timestamp,target,reason\n")
    assert cluster_map.last_decision(log) is None


def test_state_endpoint_carries_the_plain_english_story(tmp_path, monkeypatch):
    from config import RPS_PER_POD
    log = tmp_path / "d.csv"
    log.write_text("timestamp,observed_rps,reactive_target,predictive_target,demand_target,current_replicas,target,scaled,uncordon,cold_create\n"
                   "5,50,3,,6,4,6,True,0,0\n")
    monkeypatch.setattr(cluster_map, "DECISION_LOG", str(log))
    monkeypatch.setattr(cluster_map, "read_cluster", lambda: (
        [{"name": "a0", "role": "active", "unschedulable": False}],
        [{"name": f"p{i}", "node": "a0", "app": "playback-api", "phase": "Running", "ready": True} for i in range(2)]))
    monkeypatch.setattr(cluster_map, "p95_ms", lambda: None)
    story = cluster_map.state().body
    import json
    story = json.loads(story)["story"]
    assert story["phase"] == "scaling-up"
    assert story["capacity_rps"] == 2 * RPS_PER_POD
    assert story["arms"]["predictive"] is None and story["arms"]["demand"] == "6"
    assert story["verdict"]["ok"] is (50 <= 2 * RPS_PER_POD)
    assert story["events"][0]["kind"] == "scale"
