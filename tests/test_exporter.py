from arbiter import Decision
from exporter import Exporter
from prometheus_client import REGISTRY


def sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {})


def publish(reactive, predictive, decision, forecast_rps=50.0, current=4, schedulable=2):
    Exporter.publish(decision, reactive, predictive, forecast_rps, current, schedulable)


def test_publishes_what_each_arm_asked_for():
    publish(3, 7, Decision(7, True, 0, 0, 0, "scale"))
    assert sample("autoscaler_reactive_target") == 3
    assert sample("autoscaler_predictive_target") == 7
    assert sample("autoscaler_final_target") == 7
    assert sample("autoscaler_arm_up", {"arm": "reactive"}) == 1


def test_an_abstaining_arm_is_published_as_minus_one_so_the_graph_stays_readable():
    publish(None, 5, Decision(5, True, 0, 0, 0, "scale"))
    assert sample("autoscaler_reactive_target") == -1
    assert sample("autoscaler_arm_up", {"arm": "reactive"}) == 0
    assert sample("autoscaler_arm_up", {"arm": "predictive"}) == 1


def test_both_arms_down_publishes_no_target():
    publish(None, None, Decision(None, False, 0, 0, 0, "BOTH arms abstained"), forecast_rps=None)
    assert sample("autoscaler_final_target") == -1
    assert sample("autoscaler_forecast_rps") == -1


def test_node_moves_are_counted():
    before_warm = sample("autoscaler_reserve_promotions_total")
    before_cold = sample("autoscaler_cold_nodes_total")
    publish(12, 12, Decision(12, True, 2, 0, 1, "promote and build"))
    assert sample("autoscaler_reserve_promotions_total") == before_warm + 2
    assert sample("autoscaler_cold_nodes_total") == before_cold + 1
