"""Publish the engine's own decisions to Prometheus so Grafana can draw every arm."""
from prometheus_client import Counter, Gauge, start_http_server

from config import EXPORTER_PORT

REACTIVE = Gauge("autoscaler_reactive_target", "Replicas the reactive arm asked for")
PREDICTIVE = Gauge("autoscaler_predictive_target", "Replicas the predictive arm asked for")
DEMAND = Gauge("autoscaler_demand_target", "Replicas the demand arm asked for")
FINAL = Gauge("autoscaler_final_target", "Replicas the arbiter chose (max of the arms)")
CURRENT = Gauge("autoscaler_current_replicas", "Replicas currently requested of Kubernetes")
FORECAST_RPS = Gauge("autoscaler_forecast_rps", "Forecast request rate at the end of the horizon")
SCHEDULABLE = Gauge("autoscaler_schedulable_nodes", "Worker nodes that can accept app pods")
ARM_UP = Gauge("autoscaler_arm_up", "1 when the arm produced a recommendation", ["arm"])
PROMOTIONS = Counter("autoscaler_reserve_promotions_total", "Warm reserve nodes promoted")
COLD_NODES = Counter("autoscaler_cold_nodes_total", "Brand-new nodes built because the pool was empty")


class Exporter:
    def __init__(self, port=EXPORTER_PORT):
        start_http_server(port)

    @staticmethod
    def publish(decision, reactive, predictive, forecast_rps, current, schedulable, demand=None):
        # a gauge of -1 means "this arm abstained this cycle", which keeps the Grafana line readable
        REACTIVE.set(reactive if reactive is not None else -1)
        PREDICTIVE.set(predictive if predictive is not None else -1)
        DEMAND.set(demand if demand is not None else -1)
        FINAL.set(decision.target if decision.target is not None else -1)
        CURRENT.set(current)
        FORECAST_RPS.set(forecast_rps if forecast_rps is not None else -1)
        SCHEDULABLE.set(schedulable)
        ARM_UP.labels("reactive").set(0 if reactive is None else 1)
        ARM_UP.labels("predictive").set(0 if predictive is None else 1)
        ARM_UP.labels("demand").set(0 if demand is None else 1)
        if decision.uncordon:
            PROMOTIONS.inc(decision.uncordon)
        if decision.cold_create:
            COLD_NODES.inc(decision.cold_create)
