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
